"""
Lightweight Last.fm API wrapper using requests directly.
Gives us full control over timeouts and rate limit handling.
"""
import requests

BASE_URL = "https://ws.audioscrobbler.com/2.0/"
TIMEOUT = 6  # seconds per request — fail fast if rate limited or slow


class LastFmError(Exception):
    pass


class RateLimitError(LastFmError):
    pass


def _call(api_key: str, method: str, **params) -> dict:
    """Make a single Last.fm API call. Raises RateLimitError immediately if limited."""
    payload = {
        "method": method,
        "api_key": api_key,
        "format": "json",
        **params
    }
    try:
        resp = requests.get(BASE_URL, params=payload, timeout=TIMEOUT)
        data = resp.json()
    except requests.exceptions.Timeout:
        raise LastFmError(f"Last.fm request timed out for {method}")
    except Exception as e:
        raise LastFmError(f"Last.fm request failed: {e}")

    if "error" in data:
        if data["error"] == 29:
            raise RateLimitError("Last.fm rate limit hit")
        raise LastFmError(f"Last.fm error {data['error']}: {data.get('message')}")

    return data


def get_similar_artists(api_key: str, artist: str, limit: int = 10) -> list[tuple[str, float]]:
    """Returns list of (artist_name, similarity_score) tuples."""
    data = _call(api_key, "artist.getSimilar", artist=artist, limit=limit, autocorrect=1)
    similar = data.get("similarartists", {}).get("artist", [])
    return [(a["name"], float(a.get("match", 0)) * 100) for a in similar]


def get_tag_top_artists(api_key: str, tag: str, limit: int = 20) -> list[tuple[str, float]]:
    """Returns list of (artist_name, score) for a genre tag."""
    data = _call(api_key, "tag.getTopArtists", tag=tag, limit=limit)
    artists = data.get("topartists", {}).get("artist", [])
    return [(a["name"], 100 - (i * 5)) for i, a in enumerate(artists)]


def get_similar_tracks(api_key: str, track: str, artist: str, limit: int = 20) -> list[tuple[str, str, float]]:
    """Returns list of (track_name, artist_name, similarity_score) tuples."""
    data = _call(api_key, "track.getSimilar", track=track, artist=artist, limit=limit, autocorrect=1)
    similar = data.get("similartracks", {}).get("track", [])
    return [(t["name"], t["artist"]["name"], float(t.get("match", 0)) * 100) for t in similar]
