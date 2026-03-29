---
sidebar_position: 2
title: Music & Suno
---

# Music & Suno

OpenVoiceUI includes a full music system with two playlists, a built-in player, and AI music generation via Suno. The agent can play, stop, skip, and generate music entirely through voice commands.

## Playlists

The music system manages three playlist sources:

| Playlist | Directory | Description |
|----------|-----------|-------------|
| `library` | `music/` | User-uploaded tracks (MP3, WAV, OGG, M4A, WebM) |
| `generated` | `generated_music/` | AI-generated songs from Suno |
| `spotify` | (virtual) | Spotify streaming mode (metadata only, no local files) |

Each playlist has its own metadata JSON file (`music_metadata.json` for library, `generated_metadata.json` for generated) and an optional `order.json` that controls track ordering.

### Track metadata fields

Tracks support rich metadata for DJ-style announcements:

- `title`, `artist`, `genre`, `energy`
- `description` — short blurb about the track
- `duration_seconds` — track length
- `phone_number`, `ad_copy` — for radio-style ad reads
- `fun_facts` — array of trivia the agent can mention
- `dj_intro_hints` — suggested intro lines

## Music Player

The frontend music player supports:

- **Play / Pause / Resume / Stop** — standard transport controls
- **Skip / Next** — randomly selects a different track from the current playlist
- **Volume** — 0-100 range, stored as 0.0-1.0 internally
- **Shuffle** — toggle random playback order
- **Playlist switching** — switch between library, generated, and Spotify playlists
- **Track search** — fuzzy matching by name, filename, or title (with smart quote normalization)

### Track Reservation System

When the agent announces a track (via `[MUSIC_PLAY]`), a **30-second reservation** is created server-side. This prevents race conditions between the agent's tool call and the frontend's text tag detection, both of which can try to start playback.

The flow:

1. Agent response includes `[MUSIC_PLAY:Track Name]`
2. Server creates a reservation with a unique ID (valid for 30 seconds)
3. Frontend calls `sync` to pick up the reserved track
4. Frontend calls `confirm` with the reservation ID after playback starts
5. Reservation is cleared

If no confirmation arrives within 30 seconds, the reservation expires automatically.

### DJ Transitions

The frontend can signal that a track is about to end by POSTing to `/api/music/transition`. The server pre-queues a random next track and provides DJ hints (title, duration, description, fun facts). The agent can poll `GET /api/music/transition` to check for pending transitions and deliver a DJ-style intro.

## Suno AI Music Generation

OpenVoiceUI integrates with the [Suno API](https://sunoapi.org) for AI music generation. The agent triggers generation with a tag in its response, and the system handles the entire async flow.

### Generation Flow

1. Agent includes `[SUNO_GENERATE:description of the song]` in a response
2. Frontend detects the tag and calls `POST /api/suno?action=generate`
3. Server submits the job to Suno API (V5 model) and returns a `job_id`
4. Frontend polls `GET /api/suno?action=status&job_id=<id>` every few seconds
5. When Suno reports `SUCCESS`, the server downloads the audio file (up to 50 MB, with SSRF protection)
6. Audio is saved to `generated_music/` with a slug-based filename
7. Metadata is written to `generated_metadata.json`
8. The song appears in the completed songs queue for frontend notification

Alternatively, if `SUNO_CALLBACK_URL` is configured, the Suno API sends a webhook to `/api/suno/callback` when generation completes, skipping the polling step.

### Generation Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Description** | Plain text prompt | Suno auto-generates lyrics from the description |
| **Custom lyrics** | Prompt contains `[Verse`, `[Chorus`, `[Hook`, or `[Bridge` markers, or `lyrics` param is set | Suno uses the provided lyrics directly |

Additional parameters:

- `style` — musical style description (e.g., "Catchy, Radio-friendly")
- `title` — song title
- `instrumental` — `true` for instrumental-only (no vocals)
- `vocal_gender` — `m` or `f`

### Webhook Security

When `SUNO_WEBHOOK_SECRET` is configured, the callback endpoint verifies an HMAC-SHA256 signature from the `X-Suno-Signature` header before processing.

Suno returns 2 clips per generation. The server takes only the first one to avoid duplicates.

### Song Deletion

Deleting a generated song renames the file to `.deleted` instead of removing it. The original file is always preserved on disk.

## Agent Tags

The agent controls music through tags embedded in its text responses. The frontend strips these tags before rendering or speaking the text.

| Tag | Description |
|-----|-------------|
| `[MUSIC_PLAY]` | Play a random track from the current playlist |
| `[MUSIC_PLAY:track name]` | Play a specific track (fuzzy title match) |
| `[MUSIC_STOP]` | Stop music playback |
| `[MUSIC_NEXT]` | Skip to the next track |
| `[SUNO_GENERATE:description]` | Generate a new AI song (~45 seconds) |
| `[SOUND:name]` | Play a soundboard effect |

Tags are processed once per response (deduplicated by tag type). The agent should always include spoken text alongside a tag so TTS has something to say.

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUNO_API_KEY` | Yes (for generation) | API key from sunoapi.org |
| `SUNO_CALLBACK_URL` | No | Explicit webhook URL. If not set, auto-derived from `DOMAIN` env var |
| `SUNO_WEBHOOK_SECRET` | No | HMAC secret for webhook signature verification |
| `DOMAIN` | No | Used to auto-derive callback URL (`https://{DOMAIN}/api/suno/callback`) |

### Profile Configuration

Music is enabled per-profile via the `features` section:

```json
{
  "features": {
    "music": true
  }
}
```

## API Endpoints

### Music Player

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/music/<filename>` | Serve a library audio file |
| `GET` | `/generated_music/<filename>` | Serve a generated audio file |
| `GET` | `/api/music?action=list` | List tracks in current playlist |
| `GET` | `/api/music?action=play&track=<name>` | Play a track (omit `track` for random) |
| `GET` | `/api/music?action=pause` | Pause current track |
| `GET` | `/api/music?action=resume` | Resume paused track |
| `GET` | `/api/music?action=stop` | Stop playback |
| `GET` | `/api/music?action=skip` | Skip to next track |
| `GET` | `/api/music?action=next` | Alias for skip |
| `GET` | `/api/music?action=next_up` | Pre-select next track without playing |
| `GET` | `/api/music?action=volume&volume=<0-100>` | Set volume (omit value to query) |
| `GET` | `/api/music?action=status` | Current playback state |
| `GET` | `/api/music?action=shuffle` | Toggle shuffle mode |
| `GET` | `/api/music?action=sync` | Get reserved or current track for frontend sync |
| `GET` | `/api/music?action=confirm&reservation_id=<id>` | Confirm playback of reserved track |
| `GET` | `/api/music?action=spotify&track=<name>&artist=<name>` | Enter Spotify streaming mode |
| `POST` | `/api/music/transition` | Signal track ending, pre-queue next |
| `GET` | `/api/music/transition` | Poll for pending DJ transition |
| `POST` | `/api/music/upload` | Upload a track (multipart `file` field) |
| `GET` | `/api/music/playlists` | List all playlists with track counts |
| `DELETE` | `/api/music/track/<playlist>/<filename>` | Delete a track |
| `PUT` | `/api/music/track/<playlist>/<filename>/metadata` | Update track metadata |
| `GET` | `/api/music/playlist/<playlist>/order` | Get saved track order |
| `POST` | `/api/music/playlist/<playlist>/order` | Save track order |

All music actions accept an optional `playlist` query parameter (`library`, `generated`, or `spotify`).

### Suno Generation

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET/POST` | `/api/suno?action=generate` | Submit a generation job |
| `GET/POST` | `/api/suno?action=status&job_id=<id>` | Poll job status |
| `GET/POST` | `/api/suno?action=list` | List all generated songs |
| `GET/POST` | `/api/suno?action=credits` | Check remaining Suno API credits |
| `POST` | `/api/suno/callback` | Webhook endpoint for Suno completion |
| `GET` | `/api/suno/completed` | Poll for completed songs (frontend notification queue) |
| `POST` | `/api/suno/completed` | Clear song(s) from notification queue |
| `DELETE` | `/api/suno/song/<filename>` | Archive a generated song (renames to `.deleted`) |
