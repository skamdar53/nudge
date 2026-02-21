from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from supabase import create_client
from dotenv import load_dotenv
from lastfm import get_similar_artists, get_tag_top_artists, RateLimitError
import os
import random
import time
import threading
from datetime import date, datetime, timezone

load_dotenv()

app = FastAPI()

scope = "user-top-read user-library-read playlist-read-private"
MAX_HEARD_IT_SKIPS = 3
POOL_REFRESH_DAYS = 7  # rebuild artist pool once a week

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
    return spotipy.Spotify(auth=token_info["access_token"])

def get_current_user_id():
    return get_spotify_client().current_user()["id"]


# --- Artist pool ---

def build_artist_pool(user_id: str, token: str):
    """
    Runs in a background thread on login.
    Calls Last.fm once per top artist, resolves Spotify IDs,
    and stores everything in the artist_pool table.
    Only rebuilds if the pool is older than POOL_REFRESH_DAYS.
    """
    try:
        # Check if pool was recently built
        existing = sb.table("artist_pool").select("created_at") \
            .eq("user_id", user_id).limit(1).execute()

        if existing.data:
            created = existing.data[0]["created_at"]
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created_dt).total_seconds() / 86400
            if age_days < POOL_REFRESH_DAYS:
                return  # Pool is fresh, skip rebuild

        sp = spotipy.Spotify(auth=token)
        top_artists = sp.current_user_top_artists(limit=10, time_range="medium_term")
        top_artist_names = [a["name"] for a in top_artists["items"]]
        top_artist_ids = set(a["id"] for a in top_artists["items"])

        rows = []
        seen_spotify_ids = set()

        for source_artist in top_artist_names[:4]:  # 4 artists max to stay under rate limit
            try:
                similar = get_similar_artists(LASTFM_API_KEY, source_artist, limit=15)
                time.sleep(0.3)  # small delay between Last.fm calls
            except RateLimitError:
                break  # stop immediately if rate limited, use what we have
            except Exception:
                continue

            for sim_name, score in similar:
                try:
                    results = sp.search(q=f"artist:{sim_name}", type="artist", limit=1)
                    if not results["artists"]["items"]:
                        continue
                    artist = results["artists"]["items"][0]
                    if artist["id"] in top_artist_ids:
                        continue
                    if artist["id"] in seen_spotify_ids:
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
            # Clear old pool and insert fresh
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
    mid = [c for c in candidates if 40 <= c["popularity"] < 70]
    mainstream = [c for c in candidates if c["popularity"] >= 70]

    for bucket in [niche, mid, mainstream]:
        bucket.sort(key=lambda x: x["score"], reverse=True)

    pool = niche[:5] + mid[:5] + mainstream[:2]
    if not pool:
        pool = candidates

    weights = [max(c["score"], 1) for c in pool]
    return random.choices(pool, weights=weights, k=1)[0]


def pick_album(user_id: str, genre_filter: str = None) -> dict:
    """
    Reads from the pre-computed artist pool in Supabase.
    Makes only Spotify album calls — no Last.fm calls at request time.
    """
    sp = get_spotify_client()

    # Build exclusion list from top tracks + heard albums
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

    # Get artist pool
    if genre_filter:
        # Genre mode: pull fresh from Last.fm tag (this is rare so rate limit risk is low)
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
                        "score": score
                    })
            except Exception:
                continue
    else:
        # Taste mode: read from pre-computed pool
        pool_data = sb.table("artist_pool").select("*").eq("user_id", user_id).execute()
        if not pool_data.data:
            raise HTTPException(status_code=503, detail="Your music profile is still loading. Try again in 30 seconds.")
        random.shuffle(pool_data.data)
        artist_pool = [{
            "spotify_id": r["spotify_id"],
            "artist_name": r["artist_name"],
            "popularity": r["popularity"],
            "score": r["similarity_score"]
        } for r in pool_data.data]

    # Fetch albums from artists in pool
    candidates = []
    for artist in artist_pool[:12]:
        try:
            albums = sp.artist_albums(artist["spotify_id"], album_type="album", limit=3)
            for album in albums["items"]:
                url = album["external_urls"]["spotify"]
                if url not in known_urls:
                    candidates.append({
                        "album_name": album["name"],
                        "artist": artist["artist_name"],
                        "popularity": artist["popularity"],
                        "score": artist["score"],
                        "release_date": album["release_date"],
                        "spotify_url": url,
                        "image": album["images"][0]["url"] if album["images"] else None,
                    })
        except Exception:
            continue

    if not candidates:
        raise HTTPException(status_code=404, detail="No new albums found. Try a genre filter.")

    return weighted_popularity_pick(candidates)


# --- Request models ---

class PreferencesRequest(BaseModel):
    liked_genres: list[str]
    explore_genres: list[str]


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

    # Create user in DB if new
    existing = sb.table("users").select("id").eq("id", user_id).execute()
    is_new = not existing.data
    if is_new:
        sb.table("users").insert({
            "id": user_id,
            "display_name": user["display_name"],
            "onboarding_complete": False
        }).execute()

    # Kick off pool build in background — doesn't block login response
    thread = threading.Thread(
        target=build_artist_pool,
        args=(user_id, access_token),
        daemon=True
    )
    thread.start()

    return {
        "logged_in_as": user["display_name"],
        "user_id": user_id,
        "onboarding_complete": not is_new,
        "pool_building": True
    }

@app.get("/pool-status")
def pool_status():
    """Let the frontend poll this to know when the pool is ready."""
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
    """
    Returns today's album. If one exists for today, returns it immediately.
    If not, generates one from the pre-computed artist pool (fast — no Last.fm calls).
    """
    user_id = get_current_user_id()
    today = date.today().isoformat()

    # Return existing rec if already generated today
    existing = sb.table("recommendations").select("*") \
        .eq("user_id", user_id).eq("date", today).execute()
    if existing.data:
        rec = existing.data[0]
        return {
            "todays_nudge": rec,
            "skips_remaining": MAX_HEARD_IT_SKIPS - rec["skip_count"]
        }

    # Check explore genres for 25% chance injection
    if not genre_filter:
        prefs = sb.table("preferences").select("explore_genres").eq("user_id", user_id).execute()
        if prefs.data and prefs.data[0]["explore_genres"]:
            if random.random() < 0.25:
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
    """
    Marks today's album as heard, adds to permanent exclusion list,
    and generates a new one (max 3 times per day).
    """
    user_id = get_current_user_id()
    today = date.today().isoformat()

    existing = sb.table("recommendations").select("*") \
        .eq("user_id", user_id).eq("date", today).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="No recommendation found for today.")

    rec = existing.data[0]
    if rec["skip_count"] >= MAX_HEARD_IT_SKIPS:
        raise HTTPException(status_code=400, detail="No more skips today. Come back tomorrow.")

    # Add to permanent exclusion list
    sb.table("heard_albums").upsert({
        "user_id": user_id,
        "spotify_url": rec["spotify_url"]
    }).execute()

    # Generate a fresh pick
    pick = pick_album(user_id, rec.get("genre_filter"))

    updated = {
        "album_name": pick["album_name"],
        "artist": pick["artist"],
        "spotify_url": pick["spotify_url"],
        "image": pick["image"],
        "heard_it": False,
        "skip_count": rec["skip_count"] + 1
    }
    sb.table("recommendations").update(updated).eq("id", rec["id"]).execute()

    return {
        "todays_nudge": {**updated, "date": today},
        "skips_remaining": MAX_HEARD_IT_SKIPS - updated["skip_count"]
    }
