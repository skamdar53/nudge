from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from supabase import create_client
from dotenv import load_dotenv
from lastfm import get_similar_artists, get_tag_top_artists, get_similar_tracks, RateLimitError
import os
import random
import time
import threading
import uuid
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

scope = "user-top-read user-library-read playlist-read-private user-read-recently-played"
MAX_HEARD_IT_SKIPS = 3
POOL_REFRESH_DAYS = 7

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")


# --- Spotify helpers ---

def get_spotify_oauth():
    return SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
        scope=scope,
        cache_path=".spotify_cache"
    )

def get_spotify_client():
    sp_oauth = get_spotify_oauth()
    token_info = sp_oauth.get_cached_token()
    if not token_info:
        raise HTTPException(status_code=401, detail="Not logged in. Visit /login first.")
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
    # retries=0 so rate limits fail fast instead of waiting hours to retry
    return spotipy.Spotify(auth=token_info["access_token"], retries=0, requests_timeout=10)

def get_current_user_id():
    return get_spotify_client().current_user()["id"]


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

        valid_idx = 0
        for i, c in enumerate(top):
            tid = track_ids[i]
            if tid and tid in feat_map:
                sim = audio_similarity(feat_map[tid], user_profile)
                # 65% artist/popularity signal, 35% timbre match
                c['score'] = c['score'] * 0.65 + sim * 100 * 0.35
                valid_idx += 1

        # Re-sort after rescoring; remaining candidates keep original order
        top.sort(key=lambda x: x['score'], reverse=True)
        return top + candidates[20:]
    except Exception as e:
        print(f"Audio re-rank skipped: {e}")
        return candidates


# --- Artist pool ---

def build_artist_pool(user_id: str, token: str):
    """
    Runs in a background thread on login.
    Also tries to build the user's sound profile from audio features.
    Rebuilds the pool only if older than POOL_REFRESH_DAYS.
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

        sp = spotipy.Spotify(auth=token, retries=0, requests_timeout=10)
        top_artists = sp.current_user_top_artists(limit=10, time_range="medium_term")
        top_artist_names = [a["name"] for a in top_artists["items"]]
        top_artist_ids = set(a["id"] for a in top_artists["items"])

        # --- Try to build audio fingerprint ---
        try:
            top_tracks = sp.current_user_top_tracks(limit=20, time_range="medium_term")
            track_ids = [t["id"] for t in top_tracks["items"]]
            profile = build_sound_profile(sp, track_ids)
            if profile:
                sb.table("users").update({"sound_profile": profile}).eq("id", user_id).execute()
                print(f"Sound profile built for {user_id}: {profile}")
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
                    try:
                        results = sp.search(q=f"artist:{name}", type="artist", limit=1)
                        if not results["artists"]["items"]:
                            continue
                        artist = results["artists"]["items"][0]
                        if artist["id"] in top_artist_ids or artist["id"] in seen_spotify_ids:
                            continue
                        seen_spotify_ids.add(artist["id"])
                        rows.append({
                            "user_id": user_id,
                            "artist_name": artist["name"],
                            "spotify_id": artist["id"],
                            "popularity": artist.get("popularity", 50),
                            "similarity_score": score * 0.7,
                            "source_artist": f"genre:{genre}",
                        })
                    except Exception:
                        continue
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
                try:
                    time.sleep(0.15)
                    results = sp.search(q=f"artist:{sim_name}", type="artist", limit=1)
                    if not results["artists"]["items"]:
                        continue
                    artist = results["artists"]["items"][0]
                    if artist["id"] in top_artist_ids or artist["id"] in seen_spotify_ids:
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
                except Exception:
                    continue

        if rows:
            sb.table("artist_pool").delete().eq("user_id", user_id).execute()
            sb.table("artist_pool").insert(rows).execute()

    except Exception as e:
        print(f"build_artist_pool error: {e}")


def pool_is_ready(user_id: str) -> bool:
    result = sb.table("artist_pool").select("id").eq("user_id", user_id).limit(1).execute()
    return bool(result.data)


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


def get_signal_boosts(user_id: str) -> dict:
    """
    Returns a dict of artist_name (lowercase) → boost multiplier.
    Positive: liked (3×), listened (2.5×), clicked_spotify (1.8×)
    Negative: disliked (0.4×)
    All signals decay toward 1.0 over 90 days.
    """
    BASE = {
        "liked": 3.0,
        "listened": 2.5,
        "clicked_spotify": 1.8,
        "disliked": 0.4,
    }
    try:
        data = sb.table("signals").select("artist_name, signal_type, created_at") \
            .eq("user_id", user_id).execute()
        now = datetime.now(timezone.utc)
        boosts = {}
        for sig in data.data:
            name = sig["artist_name"].lower()
            base = BASE.get(sig["signal_type"])
            if base is None:
                continue
            # Decay toward 1.0 over 90 days (retains at least 30% of effect)
            try:
                created = datetime.fromisoformat(sig["created_at"].replace("Z", "+00:00"))
                age_days = (now - created).days
                decay = max(0.3, 1.0 - age_days / 90)
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


def pick_album(user_id: str, genre_filter: str = None) -> dict:
    sp = get_spotify_client()

    # Exclusion list: top tracks + permanently heard albums
    known_urls = set()
    try:
        tracks = sp.current_user_top_tracks(limit=50, time_range="medium_term")
        for t in tracks["items"]:
            known_urls.add(t["album"]["external_urls"]["spotify"])
    except Exception:
        pass

    heard = sb.table("heard_albums").select("spotify_url").eq("user_id", user_id).execute()
    for r in heard.data:
        known_urls.add(r["spotify_url"])

    # Get signal boosts (from clicks + confirmed listens)
    boosts = get_signal_boosts(user_id)

    # Build artist pool
    if genre_filter:
        try:
            tag_artists = get_tag_top_artists(LASTFM_API_KEY, genre_filter, limit=20)
        except RateLimitError:
            raise HTTPException(status_code=503, detail="Music service busy. Try again in a few minutes.")
        except Exception:
            raise HTTPException(status_code=404, detail=f"Couldn't find artists for '{genre_filter}'.")

        artist_pool = []
        for name, score in tag_artists[:10]:
            try:
                results = sp.search(q=f"artist:{name}", type="artist", limit=1)
                if results["artists"]["items"]:
                    a = results["artists"]["items"][0]
                    artist_pool.append({
                        "spotify_id": a["id"],
                        "artist_name": a["name"],
                        "popularity": a.get("popularity", 50),
                        "score": score * boosts.get(a["name"].lower(), 1.0)
                    })
            except Exception:
                continue
    else:
        pool_data = sb.table("artist_pool").select("*").eq("user_id", user_id).execute()
        if not pool_data.data:
            raise HTTPException(status_code=503, detail="Your music profile is still loading. Try again in 30 seconds.")
        random.shuffle(pool_data.data)
        artist_pool = [{
            "spotify_id": r["spotify_id"],
            "artist_name": r["artist_name"],
            "popularity": r["popularity"],
            # Apply signal boost to base similarity score
            "score": r["similarity_score"] * boosts.get(r["artist_name"].lower(), 1.0)
        } for r in pool_data.data]

    # Fetch albums from artists in pool — try up to 20 to have enough candidates
    candidates = []
    for artist in artist_pool[:20]:
        try:
            albums = sp.artist_albums(artist["spotify_id"], album_type="album", limit=5)
            for album in albums["items"]:
                url = album["external_urls"]["spotify"]
                if url not in known_urls:
                    candidates.append({
                        "album_name": album["name"],
                        "artist": artist["artist_name"],
                        "popularity": artist["popularity"],
                        "score": artist["score"],
                        "spotify_url": url,
                        "image": album["images"][0]["url"] if album["images"] else None,
                    })
        except Exception:
            continue

    if not candidates:
        raise HTTPException(status_code=404, detail="No new albums found. Try a genre filter.")

    # Try audio re-ranking if user has a sound profile
    try:
        user_data = sb.table("users").select("sound_profile").eq("id", user_id).execute()
        sound_profile = user_data.data[0].get("sound_profile") if user_data.data else None
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
    signal_type: str  # 'clicked_spotify'

class ReactionRequest(BaseModel):
    friend_id: str
    emoji: str  # '🔥' | '👀' | '❤️'


# --- Routes ---

@app.get("/")
def root():
    return {"message": "Nudge API is running"}

@app.get("/login")
def login():
    return RedirectResponse(get_spotify_oauth().get_authorize_url())

@app.get("/callback")
def callback(code: str):
    sp_oauth = get_spotify_oauth()
    token_info = sp_oauth.get_access_token(code)
    access_token = token_info["access_token"]
    sp = spotipy.Spotify(auth=access_token)
    user = sp.current_user()
    user_id = user["id"]

    existing = sb.table("users").select("id").eq("id", user_id).execute()
    if not existing.data:
        sb.table("users").insert({
            "id": user_id,
            "display_name": user["display_name"],
            "onboarding_complete": False
        }).execute()

    thread = threading.Thread(
        target=build_artist_pool,
        args=(user_id, access_token),
        daemon=True
    )
    thread.start()

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return RedirectResponse(frontend_url)

@app.post("/rebuild-pool")
def rebuild_pool():
    """Manually trigger a pool rebuild — useful after clearing the pool."""
    sp = get_spotify_client()
    token_info = get_spotify_oauth().get_cached_token()
    user_id = sp.current_user()["id"]
    # Force rebuild by clearing the existing pool first
    sb.table("artist_pool").delete().eq("user_id", user_id).execute()
    thread = threading.Thread(
        target=build_artist_pool,
        args=(user_id, token_info["access_token"]),
        daemon=True
    )
    thread.start()
    return {"rebuilding": True}

@app.get("/pool-status")
def pool_status():
    user_id = get_current_user_id()
    ready = pool_is_ready(user_id)
    count = 0
    if ready:
        r = sb.table("artist_pool").select("id", count="exact").eq("user_id", user_id).execute()
        count = r.count or 0
    return {"ready": ready, "artist_count": count}

@app.post("/preferences")
def save_preferences(prefs: PreferencesRequest):
    user_id = get_current_user_id()
    sb.table("preferences").upsert({
        "user_id": user_id,
        "liked_genres": prefs.liked_genres,
        "explore_genres": prefs.explore_genres
    }).execute()
    sb.table("users").update({"onboarding_complete": True}).eq("id", user_id).execute()
    return {"status": "saved"}

@app.get("/preferences")
def get_preferences():
    user_id = get_current_user_id()
    result = sb.table("preferences").select("*").eq("user_id", user_id).execute()
    return result.data[0] if result.data else {"liked_genres": [], "explore_genres": []}

@app.get("/today")
def get_today(genre_filter: str = None):
    user_id = get_current_user_id()
    today = date.today().isoformat()

    existing = sb.table("recommendations").select("*") \
        .eq("user_id", user_id).eq("date", today).execute()
    if existing.data:
        rec = existing.data[0]
        return {
            "todays_nudge": rec,
            "skips_remaining": MAX_HEARD_IT_SKIPS - rec["skip_count"]
        }

    if not genre_filter:
        prefs = sb.table("preferences").select("explore_genres").eq("user_id", user_id).execute()
        if prefs.data and prefs.data[0]["explore_genres"]:
            if random.random() < 0.40:
                genre_filter = random.choice(prefs.data[0]["explore_genres"])

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
def heard_it():
    user_id = get_current_user_id()
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
def record_signal(req: SignalRequest):
    """
    Called when a user clicks 'Open in Spotify' — a strong intent signal.
    Used to boost similar artists in future pool scoring.
    """
    user_id = get_current_user_id()
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

@app.get("/feel")
def feel_search(song: str, artist: str = ""):
    """
    Search a song, get back songs with the same feel.
    Combines Spotify's recommendation engine + Last.fm listening-pattern similarity.
    """
    sp = get_spotify_client()

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
                similar.append({
                    "name": t["name"],
                    "artist": t["artists"][0]["name"],
                    "spotify_url": url,
                    "image": t["album"]["images"][0]["url"] if t["album"]["images"] else None,
                    "popularity": t.get("popularity", 50),
                })
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
                    similar.append({
                        "name": t["name"],
                        "artist": t["artists"][0]["name"],
                        "spotify_url": url,
                        "image": t["album"]["images"][0]["url"] if t["album"]["images"] else None,
                        "popularity": t.get("popularity", 50),
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"Last.fm similar tracks failed: {e}")

    if not similar:
        raise HTTPException(status_code=404, detail="Couldn't find similar songs. Try a different track.")

    return {"seed": seed, "similar": similar[:15]}


@app.post("/check-listened")
def check_listened():
    """
    Called on app open. Checks Spotify's recently_played to see if the user
    actually listened to their last recommendation. Stores a 'listened' signal
    if confirmed — the strongest feedback the system has.
    """
    user_id = get_current_user_id()
    sp = get_spotify_client()

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
        return {"checked": True, "error": str(e)}


@app.get("/search-tracks")
def search_tracks(q: str = ""):
    """
    Autocomplete endpoint for the Feel tab search.
    Returns up to 6 matching tracks with name, artist, and thumbnail.
    """
    if not q or len(q.strip()) < 2:
        return {"tracks": []}
    sp = get_spotify_client()
    try:
        results = sp.search(q=q.strip(), type="track", limit=6)
        tracks = []
        for t in results["tracks"]["items"]:
            img = None
            if t["album"]["images"]:
                # Pick smallest image for the dropdown thumbnail
                img = t["album"]["images"][-1]["url"]
            tracks.append({
                "name": t["name"],
                "artist": t["artists"][0]["name"],
                "image": img,
            })
        return {"tracks": tracks}
    except Exception as e:
        return {"tracks": []}


# ── Friends ────────────────────────────────────────────────────────────────────

VALID_EMOJIS = {"🔥", "👀", "❤️"}

@app.get("/invite-link")
def get_invite_link():
    """Returns (or generates) the user's personal invite link."""
    user_id = get_current_user_id()
    row = sb.table("users").select("invite_code").eq("id", user_id).execute()
    code = row.data[0].get("invite_code") if row.data else None
    if not code:
        code = str(uuid.uuid4())[:8]
        sb.table("users").update({"invite_code": code}).eq("id", user_id).execute()
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return {"link": f"{frontend_url}?invite={code}", "code": code}


@app.post("/accept-invite")
def accept_invite(code: str):
    """Called after login when an ?invite= param is in the URL."""
    user_id = get_current_user_id()
    inviter = sb.table("users").select("id").eq("invite_code", code).execute()
    if not inviter.data:
        raise HTTPException(status_code=404, detail="Invite code not found.")
    inviter_id = inviter.data[0]["id"]
    if inviter_id == user_id:
        return {"status": "self"}

    # Check if already friends
    a, b = sorted([user_id, inviter_id])
    existing = sb.table("friendships") \
        .select("id").eq("user_id_a", a).eq("user_id_b", b).execute()
    if existing.data:
        return {"status": "already_friends"}

    sb.table("friendships").insert({"user_id_a": a, "user_id_b": b}).execute()
    return {"status": "accepted"}


@app.get("/friends")
def get_friends():
    """Returns friends list with their today's nudge and reactions. No Spotify calls."""
    user_id = get_current_user_id()
    today = date.today().isoformat()

    # Collect friend IDs from both sides of the friendship
    f1 = sb.table("friendships").select("user_id_b").eq("user_id_a", user_id).execute()
    f2 = sb.table("friendships").select("user_id_a").eq("user_id_b", user_id).execute()
    friend_ids = [r["user_id_b"] for r in f1.data] + [r["user_id_a"] for r in f2.data]

    if not friend_ids:
        return {"friends": []}

    users_data = sb.table("users").select("id, display_name") \
        .in_("id", friend_ids).execute()
    user_map = {u["id"]: u for u in users_data.data}

    # Today's nudge for each friend — already stored in Supabase, no Spotify needed
    recs = sb.table("recommendations").select("*") \
        .in_("user_id", friend_ids).eq("date", today).execute()
    rec_map = {r["user_id"]: r for r in recs.data}

    # Reactions on friends' nudges today
    all_reactions = sb.table("nudge_reactions").select("*") \
        .in_("nudge_owner_id", friend_ids).eq("date", today).execute()
    reactions_by_owner: dict[str, list] = {}
    for r in all_reactions.data:
        reactions_by_owner.setdefault(r["nudge_owner_id"], []).append({
            "emoji": r["emoji"],
            "reactor_id": r["reactor_id"],
        })

    # My own reactions so the UI can show which one I already picked
    my_reactions = sb.table("nudge_reactions").select("nudge_owner_id, emoji") \
        .eq("reactor_id", user_id).eq("date", today).execute()
    my_reaction_map = {r["nudge_owner_id"]: r["emoji"] for r in my_reactions.data}

    friends = []
    for fid in friend_ids:
        rec = rec_map.get(fid)
        friends.append({
            "user_id": fid,
            "display_name": user_map.get(fid, {}).get("display_name", "Friend"),
            "nudge": rec,
            "reactions": reactions_by_owner.get(fid, []),
            "my_reaction": my_reaction_map.get(fid),
        })

    return {"friends": friends}


@app.post("/react")
def react_to_nudge(req: ReactionRequest):
    """React to a friend's nudge. One reaction per person per day, swappable."""
    if req.emoji not in VALID_EMOJIS:
        raise HTTPException(status_code=400, detail="Invalid emoji.")
    user_id = get_current_user_id()
    today = date.today().isoformat()
    sb.table("nudge_reactions").upsert({
        "reactor_id": user_id,
        "nudge_owner_id": req.friend_id,
        "date": today,
        "emoji": req.emoji,
    }, on_conflict="reactor_id,nudge_owner_id,date").execute()
    return {"reacted": True}
