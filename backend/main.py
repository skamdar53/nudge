from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import pylast
import os
import random

load_dotenv()

app = FastAPI()

scope = "user-top-read user-library-read playlist-read-private"

def get_lastfm():
    return pylast.LastFMNetwork(api_key=os.getenv("LASTFM_API_KEY"))

def get_spotify_oauth():
    return SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
        scope=scope,
        cache_path=".spotify_cache"  # persists token to file across server restarts
    )

def get_spotify_client():
    sp_oauth = get_spotify_oauth()
    token_info = sp_oauth.get_cached_token()
    if not token_info:
        raise HTTPException(status_code=401, detail="Not logged in. Visit /login first.")
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info["refresh_token"])
    return spotipy.Spotify(auth=token_info["access_token"])

@app.get("/")
def root():
    return {"message": "Nudge API is running"}

@app.get("/login")
def login():
    auth_url = get_spotify_oauth().get_authorize_url()
    return RedirectResponse(auth_url)

@app.get("/callback")
def callback(code: str):
    sp_oauth = get_spotify_oauth()
    token_info = sp_oauth.get_access_token(code)
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
    return {"logged_in_as": user["display_name"], "user_id": user["id"]}

@app.get("/taste")
def get_taste_profile():
    """Returns the user's top artists and their genres"""
    sp = get_spotify_client()

    top_artists = sp.current_user_top_artists(limit=20, time_range="medium_term")

    artists = []
    genre_counts = {}

    for artist in top_artists["items"]:
        artists.append({
            "name": artist["name"],
            "id": artist["id"],
            "genres": artist["genres"],
            "popularity": artist["popularity"]
        })
        for genre in artist["genres"]:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1

    top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "top_artists": artists,
        "top_genres": [g[0] for g in top_genres]
    }

@app.get("/albums-in-playlists")
def get_albums_in_playlists():
    """Returns all album IDs already in the user's playlists (so we can exclude them)"""
    sp = get_spotify_client()

    album_ids = set()
    playlists = sp.current_user_playlists(limit=50)

    for playlist in playlists["items"]:
        tracks = sp.playlist_tracks(playlist["id"], fields="items(track(album(id)))")
        for item in tracks["items"]:
            if item["track"] and item["track"]["album"]:
                album_ids.add(item["track"]["album"]["id"])

    return {"album_count": len(album_ids), "album_ids": list(album_ids)}

@app.get("/recommend")
def recommend_album(genre_filter: str = None):
    """
    Recommends one album based on the user's top Spotify artists,
    using Last.fm to find similar artists and filter by vibe/genre.
    """
    sp = get_spotify_client()
    fm = get_lastfm()

    # Step 1: Get user's top artist names from Spotify
    top_artists = sp.current_user_top_artists(limit=20, time_range="medium_term")
    top_artist_names = [a["name"] for a in top_artists["items"]]
    top_artist_ids = set(a["id"] for a in top_artists["items"])

    # Step 2: Get known albums from top tracks (all time ranges) to exclude
    known_album_ids = set()
    for time_range in ["short_term", "medium_term", "long_term"]:
        try:
            tracks = sp.current_user_top_tracks(limit=50, time_range=time_range)
            for track in tracks["items"]:
                known_album_ids.add(track["album"]["id"])
        except Exception:
            continue

    # Step 3: Find candidate artists via Last.fm
    similar_artist_names = []

    if genre_filter:
        # Genre mode: search Last.fm for top artists in that genre/tag directly
        try:
            tag = fm.get_tag(genre_filter)
            top_tag_artists = tag.get_top_artists(limit=30)
            similar_artist_names = [a.item.name for a in top_tag_artists]
        except Exception:
            raise HTTPException(status_code=404, detail=f"Couldn't find artists for genre '{genre_filter}'. Try a different tag like 'jazz', 'indie', 'soul', 'rock'.")
    else:
        # Taste mode: find artists similar to the user's top artists on Last.fm
        for artist_name in top_artist_names[:5]:
            try:
                fm_artist = fm.get_artist(artist_name)
                similar = fm_artist.get_similar(limit=10)
                for s in similar:
                    similar_artist_names.append(s.item.name)
            except Exception:
                continue

    if not similar_artist_names:
        raise HTTPException(status_code=404, detail="Couldn't find similar artists. Try a different genre filter.")

    # Step 4: Look up similar artists on Spotify, get their albums
    candidates = []
    seen_artists = set()
    random.shuffle(similar_artist_names)

    for artist_name in similar_artist_names[:20]:
        if artist_name in seen_artists:
            continue
        seen_artists.add(artist_name)
        try:
            results = sp.search(q=f"artist:{artist_name}", type="artist", limit=1)
            if not results["artists"]["items"]:
                continue
            artist = results["artists"]["items"][0]
            if artist["id"] in top_artist_ids:
                continue  # skip artists they already know
            albums = sp.artist_albums(artist["id"], album_type="album", limit=5)
            for album in albums["items"]:
                if album["id"] not in known_album_ids:
                    candidates.append({
                        "album_name": album["name"],
                        "artist": artist["name"],
                        "release_date": album["release_date"],
                        "spotify_url": album["external_urls"]["spotify"],
                        "image": album["images"][0]["url"] if album["images"] else None,
                    })
        except Exception:
            continue

    if not candidates:
        raise HTTPException(status_code=404, detail="No new albums found. Try without a genre filter.")

    recommendation = random.choice(candidates)
    return {"todays_nudge": recommendation}
