from fastapi import FastAPI, HTTPException, Cookie, Depends, Header
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheHandler, MemoryCacheHandler
from supabase import create_client
from dotenv import load_dotenv
from lastfm import get_similar_artists, get_tag_top_artists, get_similar_tracks, RateLimitError
import os
import random
import time
import threading
from datetime import date, datetime, timedelta, timezone

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://nudge-gray.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SPOTIFY_SCOPE = "user-top-read user-library-read playlist-read-private user-read-recently-played"
MAX_HEARD_IT_SKIPS = 3
POOL_REFRESH_DAYS = 7
MAX_ALBUMS_PER_ARTIST = 2
EXPLORE_GENRE_CHANCE = 0.40

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


# --- Supabase token cache (survives Railway restarts) ---

class SupabaseCacheHandler(CacheHandler):
    """Stores Spotify OAuth tokens in Supabase so they survive Railway restarts."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def get_cached_token(self):
        try:
            row = sb.table("spotify_tokens").select("token_json") \
                .eq("user_id", self.user_id).execute()
            if row.data:
                return row.data[0]["token_json"]
        except Exception as e:
            print(f"SupabaseCacheHandler.get error: {e}")
        return None

    def save_token_to_cache(self, token_info):
        try:
            sb.table("spotify_tokens").upsert({
                "user_id": self.user_id,
                "token_json": token_info,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            print(f"SupabaseCacheHandler.save error: {e}")


# --- Spotify helpers ---

def build_spotify_oauth(cache_handler):
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_handler=cache_handler,
    )

def get_spotify_token(user_id: str) -> dict:
    # get_cached_token validates and auto-refreshes (saving new token via cache_handler)
    token_info = build_spotify_oauth(SupabaseCacheHandler(user_id)).get_cached_token()
    if not token_info:
        raise HTTPException(status_code=401, detail="Not logged in. Visit /login first.")
    return token_info

def build_spotify_client(access_token: str):
    # retries=0 so rate limits fail fast instead of waiting hours to retry
    return spotipy.Spotify(auth=access_token, retries=0, requests_timeout=10)

def get_spotify_client(user_id: str):
    return build_spotify_client(get_spotify_token(user_id)["access_token"])

def start_pool_build(user_id: str, access_token: str) -> None:
    threading.Thread(
        target=build_artist_pool,
        args=(user_id, access_token),
        daemon=True,
    ).start()

def get_user_id(nudge_uid: str = Cookie(None), x_nudge_uid: str = Header(None)) -> str:
    """FastAPI dependency: extracts user's Spotify ID from cookie or X-Nudge-UID header."""
    uid = nudge_uid or x_nudge_uid
    if not uid:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return uid


# --- Audio feature helpers ---

AUDIO_DIMS = ['energy', 'acousticness', 'danceability', 'valence', 'instrumentalness']

def build_sound_profile(sp, track_ids: list) -> dict | None:
    """
    Try to get audio features for the user's top tracks.
    Returns a profile dict or None if the API isn't available.
    """
    try:
        features = sp.audio_features(track_ids[:20])
        valid = [f for f in features if f]
        if not valid:
            return None
        return {d: sum(f[d] for f in valid) / len(valid) for d in AUDIO_DIMS}
    except Exception as e:
        print(f"Audio features unavailable: {e}")
        return None

def audio_similarity(track_features: dict, user_profile: dict) -> float:
    """0.0 = nothing in common, 1.0 = perfect sonic match."""
    diffs = [abs(track_features.get(d, 0.5) - user_profile.get(d, 0.5)) for d in AUDIO_DIMS]
    return 1.0 - (sum(diffs) / len(diffs))

def audio_rerank(sp, candidates: list, user_profile: dict) -> list:
    """
    Re-score candidates by blending their original score with audio similarity.
    Makes only 2 batched API calls regardless of how many candidates there are.
    """
    try:
        top = candidates[:20]
        album_ids = [c['spotify_url'].split('/')[-1].split('?')[0] for c in top]

        # Batch fetch full album objects — includes first page of tracks
        albums_data = sp.albums(album_ids)
        track_ids = []
        for album in albums_data.get('albums', []):
            if album and album.get('tracks', {}).get('items'):
                track_ids.append(album['tracks']['items'][0]['id'])
            else:
                track_ids.append(None)

        valid_ids = [t for t in track_ids if t]
        if not valid_ids:
            return candidates

        # Batch audio features — one call for all tracks
        features_list = sp.audio_features(valid_ids)
        feat_map = {f['id']: f for f in features_list if f}

        for i, c in enumerate(top):
            tid = track_ids[i]
            if tid and tid in feat_map:
                sim = audio_similarity(feat_map[tid], user_profile)
                # 65% artist/popularity signal, 35% timbre match
                c['score'] = c['score'] * 0.65 + sim * 100 * 0.35

        # Re-sort after rescoring; remaining candidates keep original order
        top.sort(key=lambda x: x['score'], reverse=True)
        return top + candidates[20:]
    except Exception as e:
        print(f"Audio re-rank skipped: {e}")
        return candidates


# --- Artist pool ---

def search_artist(sp, name: str) -> dict | None:
    """Resolve an artist name to a Spotify artist object, or None if unavailable."""
    try:
        results = sp.search(q=f"artist:{name}", type="artist", limit=1)
        items = results["artists"]["items"]
        return items[0] if items else None
    except Exception:
        return None


def build_artist_pool(user_id: str, token: str):
    """
    Runs in a background thread on login. Builds the user's sound profile,
    the similar-artist pool, and the album cache that pick_album reads from.
    Rebuilds only if the existing pool is older than POOL_REFRESH_DAYS.
    """
    try:
        existing = sb.table("artist_pool").select("created_at") \
            .eq("user_id", user_id).limit(1).execute()

        if existing.data:
            created = existing.data[0]["created_at"]
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created_dt).total_seconds() / 86400
            if age_days < POOL_REFRESH_DAYS:
                return

        sp = build_spotify_client(token)
        top_artists = sp.current_user_top_artists(limit=10, time_range="medium_term")
        top_artist_names = [a["name"] for a in top_artists["items"]]
        top_artist_ids = {a["id"] for a in top_artists["items"]}

        # --- Try to build audio fingerprint + cache top-track album URLs ---
        try:
            top_tracks = sp.current_user_top_tracks(limit=50, time_range="medium_term")
            track_ids = [t["id"] for t in top_tracks["items"]]
            top_track_urls = [t["album"]["external_urls"]["spotify"] for t in top_tracks["items"]]
            profile = build_sound_profile(sp, track_ids)
            update_payload: dict = {
                "top_track_urls": top_track_urls,
                "top_tracks_cached_at": datetime.now(timezone.utc).isoformat(),
            }
            if profile:
                update_payload["sound_profile"] = profile
                print(f"Sound profile built for {user_id}: {profile}")
            sb.table("users").update(update_payload).eq("id", user_id).execute()
        except Exception as e:
            print(f"Sound profile skipped: {e}")

        # --- Also seed from liked_genres to diversify beyond top artists ---
        rows = []
        seen_spotify_ids = set()

        try:
            prefs = sb.table("preferences").select("liked_genres").eq("user_id", user_id).execute()
            liked_genres = prefs.data[0].get("liked_genres", []) if prefs.data else []
            for genre in liked_genres[:3]:
                try:
                    tag_artists = get_tag_top_artists(LASTFM_API_KEY, genre, limit=10)
                    time.sleep(0.3)
                except Exception:
                    continue
                for name, score in tag_artists[:6]:
                    artist = search_artist(sp, name)
                    if not artist or artist["id"] in top_artist_ids or artist["id"] in seen_spotify_ids:
                        continue
                    seen_spotify_ids.add(artist["id"])
                    rows.append({
                        "user_id": user_id,
                        "artist_name": artist["name"],
                        "spotify_id": artist["id"],
                        "popularity": artist.get("popularity", 50),
                        # Genre seeds are a weaker signal than Last.fm artist similarity
                        "similarity_score": score * 0.7,
                        "source_artist": f"genre:{genre}",
                    })
        except Exception:
            pass

        # --- Build similar artist pool from Last.fm ---

        for source_artist in top_artist_names[:4]:
            try:
                similar = get_similar_artists(LASTFM_API_KEY, source_artist, limit=15)
                time.sleep(0.3)
            except RateLimitError:
                break
            except Exception:
                continue

            for sim_name, score in similar:
                time.sleep(0.15)
                artist = search_artist(sp, sim_name)
                if not artist or artist["id"] in top_artist_ids or artist["id"] in seen_spotify_ids:
                    continue
                seen_spotify_ids.add(artist["id"])
                rows.append({
                    "user_id": user_id,
                    "artist_name": artist["name"],
                    "spotify_id": artist["id"],
                    "popularity": artist.get("popularity", 50),
                    "similarity_score": score,
                    "source_artist": source_artist,
                })

        if rows:
            sb.table("artist_pool").delete().eq("user_id", user_id).execute()
            sb.table("artist_pool").insert(rows).execute()

        # --- Pre-fetch and cache albums for every artist in the pool ---
        # rows is empty when Last.fm gave us nothing new — fall back to the stored pool
        pool_artists = rows
        if not pool_artists:
            existing_pool = sb.table("artist_pool").select("spotify_id, artist_name") \
                .eq("user_id", user_id).execute()
            pool_artists = existing_pool.data

        album_rows = []
        seen_album_urls: set[str] = set()
        for artist_row in pool_artists:
            try:
                time.sleep(0.1)
                albums = sp.artist_albums(artist_row["spotify_id"], album_type="album", limit=5)
                for album in albums["items"]:
                    url = album["external_urls"]["spotify"]
                    if url in seen_album_urls:
                        continue
                    seen_album_urls.add(url)
                    album_rows.append({
                        "user_id": user_id,
                        "artist_spotify_id": artist_row["spotify_id"],
                        "artist_name": artist_row["artist_name"],
                        "album_name": album["name"],
                        "spotify_url": url,
                        "image_url": album["images"][0]["url"] if album["images"] else None,
                    })
            except Exception:
                continue

        if album_rows:
            sb.table("album_pool").delete().eq("user_id", user_id).execute()
            sb.table("album_pool").insert(album_rows).execute()
            print(f"album_pool: cached {len(album_rows)} albums for {user_id}")

    except Exception as e:
        print(f"build_artist_pool error: {e}")


# --- Recommendation ---

def weighted_popularity_pick(candidates: list) -> dict:
    niche = [c for c in candidates if c["popularity"] < 40]
    mid   = [c for c in candidates if 40 <= c["popularity"] < 70]
    main  = [c for c in candidates if c["popularity"] >= 70]

    for bucket in [niche, mid, main]:
        bucket.sort(key=lambda x: x["score"], reverse=True)

    pool = niche[:5] + mid[:5] + main[:2]
    if not pool:
        pool = candidates

    weights = [max(c["score"], 1) for c in pool]
    return random.choices(pool, weights=weights, k=1)[0]


SIGNAL_WEIGHTS = {
    "liked": 3.0,
    "listened": 2.5,
    "clicked_spotify": 1.8,
    "disliked": 0.4,
}
SIGNAL_DECAY_DAYS = 90


def get_signal_boosts(user_id: str) -> dict:
    """
    Returns a dict of artist_name (lowercase) → boost multiplier.
    Weights above 1.0 favour the artist, below 1.0 suppress them, and every
    signal decays toward a neutral 1.0 over SIGNAL_DECAY_DAYS.
    """
    try:
        data = sb.table("signals").select("artist_name, signal_type, created_at") \
            .eq("user_id", user_id).execute()
        now = datetime.now(timezone.utc)
        boosts = {}
        for sig in data.data:
            name = sig["artist_name"].lower()
            base = SIGNAL_WEIGHTS.get(sig["signal_type"])
            if base is None:
                continue
            # Retains at least 30% of the original effect no matter how old
            try:
                created = datetime.fromisoformat(sig["created_at"].replace("Z", "+00:00"))
                age_days = (now - created).days
                decay = max(0.3, 1.0 - age_days / SIGNAL_DECAY_DAYS)
            except Exception:
                decay = 1.0
            decayed = 1.0 + (base - 1.0) * decay
            # Positive boosts: take highest; debuffs: take lowest
            if decayed >= 1.0:
                boosts[name] = max(boosts.get(name, 1.0), decayed)
            else:
                boosts[name] = min(boosts.get(name, 1.0), decayed)
        return boosts
    except Exception:
        return {}


def collect_known_urls(sp, user_id: str) -> set[str]:
    """Albums to exclude: the user's top tracks, plus everything already marked as heard."""
    known_urls: set[str] = set()
    try:
        user_row = sb.table("users").select("top_track_urls").eq("id", user_id).execute()
        cached_urls = user_row.data[0].get("top_track_urls") if user_row.data else None
        if cached_urls:
            known_urls.update(cached_urls)
        else:
            # Fallback for first-time users before pool build completes
            tracks = sp.current_user_top_tracks(limit=50, time_range="medium_term")
            known_urls.update(t["album"]["external_urls"]["spotify"] for t in tracks["items"])
    except Exception:
        pass

    heard = sb.table("heard_albums").select("spotify_url").eq("user_id", user_id).execute()
    known_urls.update(r["spotify_url"] for r in heard.data)
    return known_urls


def genre_artist_pool(sp, genre_filter: str, boosts: dict) -> list:
    """Artists for a one-off genre request, resolved live from a Last.fm tag."""
    try:
        tag_artists = get_tag_top_artists(LASTFM_API_KEY, genre_filter, limit=20)
    except RateLimitError:
        raise HTTPException(status_code=503, detail="Music service busy. Try again in a few minutes.")
    except Exception:
        raise HTTPException(status_code=404, detail=f"Couldn't find artists for '{genre_filter}'.")

    pool = []
    for name, score in tag_artists[:10]:
        artist = search_artist(sp, name)
        if not artist:
            continue
        pool.append({
            "spotify_id": artist["id"],
            "artist_name": artist["name"],
            "popularity": artist.get("popularity", 50),
            "score": score * boosts.get(artist["name"].lower(), 1.0),
        })
    return pool


def stored_artist_pool(user_id: str, boosts: dict) -> list:
    """The user's pre-built pool, shuffled so repeat picks don't follow the same order."""
    pool_data = sb.table("artist_pool").select("*").eq("user_id", user_id).execute()
    if not pool_data.data:
        raise HTTPException(status_code=503, detail="Your music profile is still loading. Try again in 30 seconds.")
    random.shuffle(pool_data.data)
    return [{
        "spotify_id": r["spotify_id"],
        "artist_name": r["artist_name"],
        "popularity": r["popularity"],
        "score": r["similarity_score"] * boosts.get(r["artist_name"].lower(), 1.0),
    } for r in pool_data.data]


def candidates_from_spotify(sp, artist_pool: list, known_urls: set) -> list:
    """Genre path: albums are fetched live, since these artists aren't in album_pool."""
    candidates = []
    for artist in artist_pool[:40]:
        try:
            albums = sp.artist_albums(artist["spotify_id"], album_type="album", limit=5)
            added = 0
            for album in albums["items"]:
                if added >= MAX_ALBUMS_PER_ARTIST:
                    break
                url = album["external_urls"]["spotify"]
                if url in known_urls:
                    continue
                candidates.append({
                    "album_name": album["name"],
                    "artist": artist["artist_name"],
                    "popularity": artist["popularity"],
                    "score": artist["score"],
                    "spotify_url": url,
                    "image": album["images"][0]["url"] if album["images"] else None,
                })
                added += 1
        except Exception:
            continue
    return candidates


def candidates_from_cache(user_id: str, artist_pool: list, known_urls: set) -> list:
    """Normal path: zero Spotify calls — albums come from the pre-built album_pool."""
    artists_by_id = {a["spotify_id"]: a for a in artist_pool}
    cached = sb.table("album_pool").select(
        "artist_spotify_id, artist_name, album_name, spotify_url, image_url"
    ).eq("user_id", user_id).execute()

    candidates = []
    per_artist_count: dict[str, int] = {}
    for row in cached.data:
        artist_id = row["artist_spotify_id"]
        artist = artists_by_id.get(artist_id)
        if not artist or row["spotify_url"] in known_urls:
            continue
        if per_artist_count.get(artist_id, 0) >= MAX_ALBUMS_PER_ARTIST:
            continue
        candidates.append({
            "album_name": row["album_name"],
            "artist": row["artist_name"],
            "popularity": artist["popularity"],
            "score": artist["score"],
            "spotify_url": row["spotify_url"],
            "image": row["image_url"],
        })
        per_artist_count[artist_id] = per_artist_count.get(artist_id, 0) + 1
    return candidates


def pick_album(user_id: str, genre_filter: str = None) -> dict:
    sp = get_spotify_client(user_id)
    known_urls = collect_known_urls(sp, user_id)
    boosts = get_signal_boosts(user_id)

    if genre_filter:
        artist_pool = genre_artist_pool(sp, genre_filter, boosts)
        candidates = candidates_from_spotify(sp, artist_pool, known_urls)
    else:
        artist_pool = stored_artist_pool(user_id, boosts)
        candidates = candidates_from_cache(user_id, artist_pool, known_urls)

    if not candidates:
        raise HTTPException(status_code=404, detail="No new albums found. Try a genre filter.")

    # Try audio re-ranking if user has a sound profile
    try:
        profile_row = sb.table("users").select("sound_profile").eq("id", user_id).execute()
        sound_profile = profile_row.data[0].get("sound_profile") if profile_row.data else None
        if sound_profile:
            candidates = audio_rerank(sp, candidates, sound_profile)
    except Exception as e:
        print(f"Audio re-rank error: {e}")

    return weighted_popularity_pick(candidates)


# --- Request models ---

class PreferencesRequest(BaseModel):
    liked_genres: list[str]
    explore_genres: list[str]

class SignalRequest(BaseModel):
    artist_name: str
    album_name: str
    spotify_url: str
    signal_type: str  # 'clicked_spotify' | 'liked' | 'disliked'


# --- Routes ---

@app.get("/")
def root():
    return {"message": "Nudge API is running"}

@app.get("/login")
def login():
    oauth = build_spotify_oauth(MemoryCacheHandler())
    return RedirectResponse(oauth.get_authorize_url())

@app.get("/callback")
def callback(code: str):
    # Exchange auth code for tokens — MemoryCacheHandler prevents reading/writing .cache file
    temp_oauth = build_spotify_oauth(MemoryCacheHandler())
    token_info = temp_oauth.get_access_token(code)
    access_token = token_info["access_token"]
    sp = spotipy.Spotify(auth=access_token)
    user = sp.current_user()
    user_id = user["id"]

    # Persist token to Supabase (survives Railway restarts)
    SupabaseCacheHandler(user_id).save_token_to_cache(token_info)

    existing = sb.table("users").select("id").eq("id", user_id).execute()
    if not existing.data:
        sb.table("users").insert({
            "id": user_id,
            "display_name": user["display_name"],
            "onboarding_complete": False
        }).execute()

    start_pool_build(user_id, access_token)

    response = RedirectResponse(f"{FRONTEND_URL}?uid={user_id}")
    response.set_cookie(
        key="nudge_uid",
        value=user_id,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )
    return response

@app.post("/rebuild-pool")
def rebuild_pool(user_id: str = Depends(get_user_id)):
    """Manually trigger a pool rebuild — useful after clearing the pool."""
    token_info = get_spotify_token(user_id)
    # Force rebuild by clearing the existing pool first
    sb.table("artist_pool").delete().eq("user_id", user_id).execute()
    start_pool_build(user_id, token_info["access_token"])
    return {"rebuilding": True}

@app.get("/pool-status")
def pool_status(user_id: str = Depends(get_user_id)):
    result = sb.table("artist_pool").select("id", count="exact").eq("user_id", user_id).execute()
    count = result.count or 0
    return {"ready": count > 0, "artist_count": count}

@app.post("/preferences")
def save_preferences(prefs: PreferencesRequest, user_id: str = Depends(get_user_id)):
    sb.table("preferences").upsert({
        "user_id": user_id,
        "liked_genres": prefs.liked_genres,
        "explore_genres": prefs.explore_genres
    }).execute()
    sb.table("users").update({"onboarding_complete": True}).eq("id", user_id).execute()
    return {"status": "saved"}

@app.get("/preferences")
def get_preferences(user_id: str = Depends(get_user_id)):
    result = sb.table("preferences").select("*").eq("user_id", user_id).execute()
    return result.data[0] if result.data else {"liked_genres": [], "explore_genres": []}

@app.get("/today")
def get_today(genre_filter: str = None, force: bool = False, user_id: str = Depends(get_user_id)):
    today = date.today().isoformat()

    if not force:
        existing = sb.table("recommendations").select("*") \
            .eq("user_id", user_id).eq("date", today).execute()
        if existing.data:
            rec = existing.data[0]
            return {
                "todays_nudge": rec,
                "skips_remaining": MAX_HEARD_IT_SKIPS - rec["skip_count"]
            }
    else:
        # Clear today's rec so a fresh one is generated with updated pool/prefs
        sb.table("recommendations").delete() \
            .eq("user_id", user_id).eq("date", today).execute()

    if not genre_filter:
        prefs = sb.table("preferences").select("explore_genres").eq("user_id", user_id).execute()
        explore_genres = prefs.data[0]["explore_genres"] if prefs.data else None
        if explore_genres and random.random() < EXPLORE_GENRE_CHANCE:
            genre_filter = random.choice(explore_genres)

    pick = pick_album(user_id, genre_filter)

    row = {
        "user_id": user_id,
        "date": today,
        "album_name": pick["album_name"],
        "artist": pick["artist"],
        "spotify_url": pick["spotify_url"],
        "image": pick["image"],
        "genre_filter": genre_filter,
        "heard_it": False,
        "skip_count": 0
    }
    sb.table("recommendations").insert(row).execute()

    return {
        "todays_nudge": row,
        "skips_remaining": MAX_HEARD_IT_SKIPS
    }

@app.post("/heard-it")
def heard_it(user_id: str = Depends(get_user_id)):
    today = date.today().isoformat()

    existing = sb.table("recommendations").select("*") \
        .eq("user_id", user_id).eq("date", today).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="No recommendation found for today.")

    rec = existing.data[0]
    if rec["skip_count"] >= MAX_HEARD_IT_SKIPS:
        raise HTTPException(status_code=400, detail="No more skips today. Come back tomorrow.")

    sb.table("heard_albums").upsert({
        "user_id": user_id,
        "spotify_url": rec["spotify_url"]
    }).execute()

    new_skip_count = rec["skip_count"] + 1

    try:
        pick = pick_album(user_id, rec.get("genre_filter"))
    except HTTPException:
        # Pool exhausted — cap the counter and tell the user gracefully
        sb.table("recommendations").update({"skip_count": new_skip_count}) \
            .eq("id", rec["id"]).execute()
        raise HTTPException(status_code=503, detail="No more new albums to suggest right now. Come back tomorrow!")

    updated = {
        "album_name": pick["album_name"],
        "artist": pick["artist"],
        "spotify_url": pick["spotify_url"],
        "image": pick["image"],
        "heard_it": False,
        "skip_count": new_skip_count
    }
    sb.table("recommendations").update(updated).eq("id", rec["id"]).execute()

    return {
        "todays_nudge": {**updated, "date": today},
        "skips_remaining": MAX_HEARD_IT_SKIPS - new_skip_count
    }

@app.post("/signal")
def record_signal(req: SignalRequest, user_id: str = Depends(get_user_id)):
    """
    Called when a user clicks 'Open in Spotify' or rates an album.
    Used to boost/penalize similar artists in future pool scoring.
    """
    # Avoid duplicate signals for the same album+type
    existing = sb.table("signals").select("id") \
        .eq("user_id", user_id) \
        .eq("spotify_url", req.spotify_url) \
        .eq("signal_type", req.signal_type) \
        .execute()
    if not existing.data:
        sb.table("signals").insert({
            "user_id": user_id,
            "artist_name": req.artist_name,
            "album_name": req.album_name,
            "spotify_url": req.spotify_url,
            "signal_type": req.signal_type
        }).execute()
    return {"recorded": True}

def summarize_track(track: dict) -> dict:
    images = track["album"]["images"]
    return {
        "name": track["name"],
        "artist": track["artists"][0]["name"],
        "spotify_url": track["external_urls"]["spotify"],
        "image": images[0]["url"] if images else None,
        "popularity": track.get("popularity", 50),
    }


@app.get("/feel")
def feel_search(song: str, artist: str = "", user_id: str = Depends(get_user_id)):
    """
    Search a song, get back songs with the same feel.
    Combines Spotify's recommendation engine + Last.fm listening-pattern similarity.
    """
    sp = get_spotify_client(user_id)

    # Find the seed track on Spotify
    query = f"track:{song} artist:{artist}" if artist else f"track:{song}"
    results = sp.search(q=query, type="track", limit=1)
    if not results["tracks"]["items"]:
        raise HTTPException(status_code=404, detail="Song not found. Try including the artist name.")

    seed_track = results["tracks"]["items"][0]
    seed = {
        "name": seed_track["name"],
        "artist": seed_track["artists"][0]["name"],
        "spotify_url": seed_track["external_urls"]["spotify"],
        "image": seed_track["album"]["images"][0]["url"] if seed_track["album"]["images"] else None,
    }

    similar = []
    seen_urls = {seed["spotify_url"]}

    # Spotify recommendations — strong for feel/vibe matching
    try:
        recs = sp.recommendations(seed_tracks=[seed_track["id"]], limit=15)
        for t in recs["tracks"]:
            url = t["external_urls"]["spotify"]
            if url not in seen_urls:
                seen_urls.add(url)
                similar.append(summarize_track(t))
    except Exception as e:
        print(f"Spotify recommendations failed: {e}")

    # Last.fm similar tracks — crowdsourced feel matching, surfaces niche picks
    try:
        lastfm_similar = get_similar_tracks(
            LASTFM_API_KEY, seed_track["name"], seed_track["artists"][0]["name"], limit=20
        )
        for track_name, artist_name, _ in lastfm_similar[:10]:
            try:
                res = sp.search(q=f"track:{track_name} artist:{artist_name}", type="track", limit=1)
                if not res["tracks"]["items"]:
                    continue
                t = res["tracks"]["items"][0]
                url = t["external_urls"]["spotify"]
                if url not in seen_urls:
                    seen_urls.add(url)
                    similar.append(summarize_track(t))
            except Exception:
                continue
    except Exception as e:
        print(f"Last.fm similar tracks failed: {e}")

    if not similar:
        raise HTTPException(status_code=404, detail="Couldn't find similar songs. Try a different track.")

    return {"seed": seed, "similar": similar[:15]}


@app.post("/check-listened")
def check_listened(user_id: str = Depends(get_user_id)):
    """
    Called on app open. Checks Spotify's recently_played to see if the user
    actually listened to their last recommendation. Stores a 'listened' signal
    if confirmed — the strongest feedback the system has.
    """
    sp = get_spotify_client(user_id)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()

    recent_recs = sb.table("recommendations").select("*") \
        .eq("user_id", user_id) \
        .in_("date", [today, yesterday]) \
        .order("date", desc=True) \
        .execute()

    if not recent_recs.data:
        return {"checked": False}

    rec = recent_recs.data[0]
    rec_url = rec["spotify_url"]

    # Don't double-signal
    already = sb.table("signals").select("id") \
        .eq("user_id", user_id) \
        .eq("spotify_url", rec_url) \
        .eq("signal_type", "listened") \
        .execute()
    if already.data:
        return {"checked": True, "already_signaled": True}

    try:
        recently_played = sp.current_user_recently_played(limit=50)
        played_urls = {
            item["track"]["album"]["external_urls"]["spotify"]
            for item in recently_played["items"]
        }
        if rec_url in played_urls:
            sb.table("signals").insert({
                "user_id": user_id,
                "artist_name": rec["artist"],
                "album_name": rec["album_name"],
                "spotify_url": rec_url,
                "signal_type": "listened"
            }).execute()
            return {"checked": True, "listened": True}
        return {"checked": True, "listened": False}
    except Exception as e:
        print(f"recently_played check failed: {e}")
        return {"checked": True, "listened": False}


@app.get("/search-tracks")
def search_tracks(q: str = "", user_id: str = Depends(get_user_id)):
    """
    Autocomplete endpoint for the Feel tab search.
    Returns up to 6 matching tracks with name, artist, and thumbnail.
    """
    if not q or len(q.strip()) < 2:
        return {"tracks": []}
    sp = get_spotify_client(user_id)
    try:
        results = sp.search(q=q.strip(), type="track", limit=6)
        tracks = []
        for t in results["tracks"]["items"]:
            images = t["album"]["images"]
            tracks.append({
                "name": t["name"],
                "artist": t["artists"][0]["name"],
                # Smallest image is last — right size for a dropdown thumbnail
                "image": images[-1]["url"] if images else None,
            })
        return {"tracks": tracks}
    except Exception:
        return {"tracks": []}

