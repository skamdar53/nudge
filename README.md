# nudge

> one album a day, tailored to you.

nudge is a music discovery app that gives you a single album recommendation each day based on your Spotify listening history. The goal is to fight decision fatigue — instead of endlessly scrolling, you get one thoughtful pick and the option to explore from there.

**Live:** https://nudge-gray.vercel.app

---

## Features

- **Daily Nudge** — one album recommendation per day, with up to 3 "already heard it" skips
- **Feel** — search any song and get tracks with the same sonic vibe
- **Friends** — see what your friends got nudged today, react with emojis, invite via link
- **Learns over time** — the more you interact, the better the picks get

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Frontend | React + Vite, deployed on Vercel |
| Backend | FastAPI (Python), deployed on Railway |
| Database | Supabase (Postgres) |
| Music data | Spotify API + Last.fm API |

---

## How the Recommendation System Works

This is the core of nudge. It runs in several stages.

### 1. Artist Pool (built on login, refreshed every 7 days)

When you first log in, nudge builds a personalized **artist pool** in the background using two signals:

**From your listening history:**
- Fetches your top 10 artists on Spotify (medium-term)
- For each, calls Last.fm's `artist.getSimilar` to get 15 similar artists
- Searches each on Spotify to get their ID, popularity, and metadata
- Stores them in Supabase with a `similarity_score` (0–100) and `source_artist`

**From your genre preferences:**
- During onboarding you pick genres you love and genres you want to explore
- For each liked genre, calls Last.fm's `tag.getTopArtists` to get genre-representative artists
- These get added to the pool with a slightly discounted score (`similarity_score × 0.7`) to deprioritize them relative to your actual listening taste

The result is a pool of ~40–80 artists stored per user, representing your musical neighborhood.

---

### 2. Sound Profile (built alongside the pool)

nudge also builds an audio fingerprint from your top 20 Spotify tracks using Spotify's audio features API. It averages five dimensions:

- **Energy** — intensity and activity
- **Acousticness** — how acoustic vs. electronic
- **Danceability** — rhythmic regularity and groove
- **Valence** — musical positivity/mood
- **Instrumentalness** — likelihood of no vocals

This profile is stored in Supabase and used later for re-ranking.

---

### 3. Signal Boosts (learned from your behavior)

Every interaction you have teaches nudge something. Signals are stored per artist and decay over 90 days:

| Signal | Multiplier | Triggered by |
|--------|-----------|--------------|
| `liked` | 3.0× | Thumbs up on a recommendation |
| `listened` | 2.5× | Auto-detected via Spotify's recently_played |
| `clicked_spotify` | 1.8× | Opening the album in Spotify |
| `disliked` | 0.4× | Thumbs down |

Decay formula: `decayed = 1.0 + (base - 1.0) × max(0.3, 1.0 - age_days / 90)`

This means a `liked` signal starts at 3× boost and fades to ~1.6× at 90 days, never fully disappearing. Positive boosts stack by taking the highest; negative signals take the lowest.

---

### 4. Picking an Album

When you open the app, `/today` runs `pick_album()` which does the following:

**Step 1 — Build the exclusion list**
Fetches your top 50 Spotify tracks' albums + all albums you've previously marked as heard. These are excluded from candidates.

**Step 2 — Apply signal boosts**
Each artist in the pool gets its `similarity_score` multiplied by its signal boost (or 1.0 if no signals yet).

**Step 3 — Genre exploration (40% chance)**
If you selected explore genres during onboarding, there's a 40% chance nudge replaces the pool with artists pulled from Last.fm for a random explore genre. This is how you discover outside your comfort zone.

**Step 4 — Fetch album candidates**
Iterates through up to 20 artists in the pool. For each, fetches up to 5 albums from Spotify. Builds a candidate list with `album_name`, `artist`, `popularity`, `score`, `spotify_url`, and `image`.

**Step 5 — Audio re-ranking**
If you have a sound profile, nudge re-scores the top 20 candidates by blending:
```
final_score = artist_score × 0.65 + audio_similarity × 100 × 0.35
```
Audio similarity is `1.0 - mean(|track_feature - profile_feature|)` across all 5 dimensions. This is done in just 2 batched API calls (albums → track IDs → audio features).

**Step 6 — Weighted popularity pick**
Candidates are bucketed by Spotify popularity:
- **Niche** (< 40): top 5 by score
- **Mid-tier** (40–70): top 5 by score
- **Mainstream** (≥ 70): top 2 by score

A weighted random pick is made from this combined pool of 12, using `score` as the weight. This ensures you get a healthy mix of underground and recognizable artists rather than always being pushed toward the most popular.

The result is saved to Supabase for the day so subsequent requests return instantly without re-running the pipeline.

---

### 5. Listened Detection

Every time you open the app, nudge silently calls `/check-listened`. It fetches your last 50 recently played tracks from Spotify and checks if any of them are from your previous day's recommended album. If yes, it records a `listened` signal (2.5× boost) for that artist — the strongest feedback loop in the system, and the one that requires zero effort from you.

---

### 6. Feel Tab

The Feel tab lets you search any song and get tracks with the same vibe. It runs two searches in parallel and merges the results:

1. **Spotify recommendations** — uses Spotify's seed track API, strong for audio feature matching (tempo, energy, key)
2. **Last.fm similar tracks** — crowdsourced listening patterns, better for surfacing niche picks that share an artist/cultural DNA

Results are split into:
- **Same feel** — popularity ≥ 45 (well-known tracks in the same lane)
- **Under the radar** — popularity < 45 (lesser-known, same energy)

---

## Running Locally

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create `backend/.env`:
```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
LASTFM_API_KEY=...
SUPABASE_URL=...
SUPABASE_KEY=...       # use the service_role key, not the publishable key
FRONTEND_URL=http://localhost:5173
```

```bash
uvicorn main:app --host 127.0.0.1 --port 8888
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` → `http://127.0.0.1:8888` so no CORS config needed locally.

Open `http://localhost:5173`.

---

## Deployment

| Service | Platform | Notes |
|---------|----------|-------|
| Frontend | Vercel | Auto-deploys on push to `main`. `vercel.json` rewrites `/api/*` to Railway. |
| Backend | Railway | Set all `.env` vars in Railway's Variables tab. Uses `Procfile` to start uvicorn. |
| Database | Supabase | Use the `service_role` key in backend env — the publishable key is blocked by RLS. |

### Spotify Setup
The Spotify app must be in **Extended Quota Mode** for public use. In development mode, add test users manually via **Spotify Developer Dashboard → your app → User Management** (up to 25 users).

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `users` | Spotify user ID, display name, onboarding status, sound profile |
| `preferences` | liked_genres, explore_genres per user |
| `artist_pool` | Pre-computed similar artists per user with scores |
| `recommendations` | Daily album pick per user, skip count, heard status |
| `heard_albums` | Permanent exclusion list of skipped albums |
| `signals` | User interaction events (liked, disliked, listened, clicked) |
| `spotify_tokens` | OAuth tokens stored in Supabase to survive server restarts |
| `friendships` | Bidirectional friend pairs |
| `nudge_reactions` | Emoji reactions on friends' daily nudges |
