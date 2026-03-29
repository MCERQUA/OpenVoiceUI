---
sidebar_position: 2
title: Architecture
---

# OpenVoiceUI Architecture Reference

> Single source of truth for how every system works. Reference this document on every PR review.
> Last updated: 2026-02-27

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Request Flow](#request-flow)
3. [Agent Tag System](#agent-tag-system)
4. [Display Pipeline (Security Critical)](#display-pipeline)
5. [Frontend Architecture](#frontend-architecture)
6. [Backend Architecture](#backend-architecture)
7. [Canvas System](#canvas-system)
8. [Music System](#music-system)
9. [Voice Pipeline](#voice-pipeline)
10. [Profile System](#profile-system)
11. [Security Model](#security-model)
12. [Configuration & Environment](#configuration)

---

## System Overview

```
Browser (Chrome)
    |
    |  POST /api/conversation?stream=1
    v
Flask Server (port 15003)
    |
    |  WebSocket (persistent)
    v
OpenClaw Gateway (port 18791)
    |
    |  API call
    v
LLM (GLM-4.7-flash via Z.AI)
```

**Three main systems:**
- **Flask server** (`server.py` / `app.py`) — Routes, TTS, profile management
- **OpenClaw Gateway** (`services/gateways/openclaw.py`) — LLM routing via persistent WebSocket
- **Frontend** (`src/app.js`, `src/core/`, `src/adapters/`) — Voice UI, STT, display, tag parsing

---

## Request Flow

1. User speaks → **WebSpeechSTT** transcribes to text
2. Frontend sends `POST /api/conversation?stream=1` with message + UI context
3. Server builds **context prefix** (canvas state, music state, track list, page list)
4. Server loads **system prompt** from `prompts/voice-system-prompt.md` (hot-reload)
5. Full message sent to **OpenClaw Gateway** via WebSocket
6. Gateway routes to LLM, streams response back as NDJSON events
7. Server processes stream: `delta` → `text_done` → TTS generation → `audio` events
8. Frontend parses stream: displays text, executes tags, plays audio
9. TTS plays → STT muted → TTS ends → 600ms delay → STT unmuted

---

## Agent Tag System

Tags are inline commands embedded in the LLM's text response. They control the UI without tool calls.

### Complete Tag Reference

| Tag | Format | Action | Parser Locations |
|-----|--------|--------|-----------------|
| CANVAS | `[CANVAS:page-id]` | Open canvas page | VoiceSession.js, app.js |
| CANVAS_MENU | `[CANVAS_MENU]` | Open page picker | VoiceSession.js, app.js |
| HTML Creation | `` ```html...``` `` | Save & show new page | app.js only |
| MUSIC_PLAY | `[MUSIC_PLAY]` or `[MUSIC_PLAY:name]` | Play track | VoiceSession.js, app.js |
| MUSIC_STOP | `[MUSIC_STOP]` | Stop music | VoiceSession.js, app.js |
| MUSIC_NEXT | `[MUSIC_NEXT]` | Skip track | VoiceSession.js, app.js |
| SUNO_GENERATE | `[SUNO_GENERATE:description]` | Generate AI song | VoiceSession.js, app.js |
| SPOTIFY | `[SPOTIFY:track\|artist]` | Play from Spotify | VoiceSession.js, app.js |
| SOUND | `[SOUND:name]` | DJ soundboard effect | VoiceSession.js, app.js |
| SLEEP | `[SLEEP]` | Return to wake-word mode | VoiceSession.js, app.js |
| REGISTER_FACE | `[REGISTER_FACE:name]` | Save face from camera | VoiceSession.js, app.js |
| SESSION_RESET | `[SESSION_RESET]` | Clear conversation | conversation.py (server-side) |

### Three-Layer Processing

Tags are processed at three layers — **all three must be kept in sync**:

1. **Backend strip** (`routes/conversation.py: clean_for_tts()`) — Removes tags before TTS generation so they're not spoken
2. **Frontend parse** (`src/app.js: checkCanvasInStream()` and `src/core/VoiceSession.js: _checkCmdsInStream()`) — Detects tags in stream, fires UI actions
3. **Frontend strip** (`src/app.js: stripCanvasTags()` and `src/core/VoiceSession.js: _stripCmdTags()`) — Removes tags from display text

### Tag Injection Points

The agent learns about tags from three sources:

1. **System prompt** (`prompts/voice-system-prompt.md`) — Tag format and usage rules. Hot-reload, no restart needed.
2. **Context prefix** (`routes/conversation.py` lines 482-548) — Dynamic state: canvas open/closed, music playing, available pages, available tracks, available sounds
3. **Generated tracks** (`services/gateways/openclaw.py: _load_generated_tracks()`) — Appended to system prompt

### Critical Rule

> **Any change to tag names, formats, or parsing MUST be updated in ALL files simultaneously:**
> - `prompts/voice-system-prompt.md` (agent instructions)
> - `routes/conversation.py` (clean_for_tts + context prefix)
> - `src/app.js` (stripCanvasTags + checkCanvasInStream)
> - `src/core/VoiceSession.js` (_stripCmdTags + _checkCmdsInStream)

---

## Display Pipeline

**SECURITY CRITICAL** — This is where XSS protection lives.

```
LLM text response
    |
    v
stripCanvasTags()     — Remove [CANVAS:], [MUSIC_PLAY], etc.
    |
    v
escapeHtml()          — Escape <, >, &, " to prevent XSS
    |
    v
.replace(/\n/g, '<br>')  — Convert newlines
    |
    v
element.innerHTML     — Safe to set because HTML is escaped
```

### Rules

1. **NEVER set innerHTML without calling escapeHtml() first**
2. The `displayMessage()` function in app.js has its own escapeHtml — but streaming updates bypass it
3. Both the streaming update path AND text_done path must use escapeHtml
4. `stripCanvasTags()` does NOT escape HTML — it only removes known tags
5. If you add a new display path, it MUST go through escapeHtml

### Files

- `src/app.js` — `escapeHtml()` (module-scope helper), `stripCanvasTags()`, `displayMessage()`
- `src/core/VoiceSession.js` — `_stripCmdTags()` (emits events, no DOM)

---

## Frontend Architecture

### Core Modules (src/app.js — ~5900 lines)

| Module | Purpose |
|--------|---------|
| `CONFIG` | Server URL, settings |
| `DJSoundboard` | Sound effects (trigger words + [SOUND:] tags) |
| `CanvasControl` | Canvas iframe management |
| `CanvasMenu` | Page picker UI, manifest loading |
| `MusicPlayerUI` | Music player panel |
| `FaceModule` | Animated face (mood, amplitude) |
| `StatusModule` | Status bar (LISTENING, SPEAKING, THINKING) |
| `TranscriptPanel` | Conversation transcript display |
| `ActionConsole` | Debug/action log |
| `ClawdBotMode` | Main voice conversation mode (HTTP streaming) |
| `VoiceConversation` | Legacy non-streaming voice mode |
| `ModeManager` | Switches between modes |

### Core Modules (src/core/)

| File | Purpose |
|------|---------|
| `EventBus.js` | Pub/sub event system (ADR-009) |
| `VoiceSession.js` | Slim voice orchestrator (STT + TTS + tags) |
| `EmotionEngine.js` | Emotion inference from text → face mood |
| `EventBridge.js` | Multi-agent event bridge (AgentEvents) |

### Providers (src/providers/)

| File | Purpose |
|------|---------|
| `WebSpeechSTT.js` | Browser speech recognition + wake word |
| `TTSPlayer.js` | Audio playback with queue + waveform |

### Adapters (src/adapters/)

| File | Purpose |
|------|---------|
| `ClawdBotAdapter.js` | OpenClaw gateway adapter |
| `ElevenLabsHybridAdapter.js` | ElevenLabs voice + OpenClaw brain |
| `elevenlabs-classic.js` | ElevenLabs standalone |
| `hume-evi.js` | Hume EVI standalone |
| `_template.js` | Boilerplate for new adapters |

### EventBus Events

**Session:** `session:start`, `session:stop`, `session:reset`, `session:listening`, `session:error`
**Voice:** `tts:start`, `tts:stop`, `stt:start`, `stt:stop`
**Conversation:** `session:message`, `session:streaming`, `session:thinking`, `session:tool`, `session:emotion`
**Commands:** `cmd:canvas_menu`, `cmd:canvas_page`, `cmd:music_play`, `cmd:music_stop`, `cmd:music_next`, `cmd:suno_generate`, `cmd:spotify`, `cmd:sound`, `cmd:register_face`, `cmd:sleep`
**UI:** `config:loaded`, `profile:switched`, `face:mood`, `music:play`, `music:stop`

---

## Backend Architecture

### Routes (routes/*.py)

| File | Key Endpoints | Purpose |
|------|--------------|---------|
| `conversation.py` | `POST /api/conversation` | Main voice endpoint (streaming) |
| `music.py` | `GET /api/music?action=...` | Music player API (list, play, stop, skip) |
| `canvas.py` | `GET/POST /api/canvas/*` | Canvas pages, manifest, proxy |
| `admin.py` | `GET /api/server-stats`, `POST /api/admin/gateway/rpc` | Admin dashboard |
| `profiles.py` | `GET/POST/PUT/DELETE /api/profiles` | Profile CRUD |
| `instructions.py` | `GET/PUT /api/instructions/<name>` | Hot-reload prompt editor |
| `suno.py` | `GET/POST /api/suno` | AI song generation |
| `vision.py` | `POST /api/vision`, `/api/identify`, `/api/faces` | Camera, face recognition |
| `transcripts.py` | `GET/POST /api/transcripts` | Conversation transcripts |
| `greetings.py` | `GET /api/greetings` | Greeting templates |
| `theme.py` | `GET/POST /api/theme` | UI theme colors |
| `static_files.py` | `GET /sounds/*`, `/music/*`, `/src/*` | Static file serving |

### Services (services/*.py)

| File | Purpose |
|------|---------|
| `gateway_manager.py` | Gateway registry, routing, plugin discovery |
| `gateways/openclaw.py` | Persistent WebSocket to OpenClaw |
| `tts.py` | TTS generation wrapper |
| `auth.py` | Clerk JWT verification |
| `health.py` | Liveness/readiness probes |
| `db_pool.py` | SQLite connection pool |
| `paths.py` | Central path definitions |
| `speech_normalizer.py` | Text normalization for TTS |

### TTS Providers (tts_providers/*.py)

| Provider | Model | Latency | Cost | Requires |
|----------|-------|---------|------|----------|
| Groq Orpheus | canopylabs/orpheus-v1-english | 130-200ms | ~$0.05/1K chars | `GROQ_API_KEY` |
| Supertonic | Local ONNX | Very fast | Free | `SUPERTONIC_API_URL` or local models |
| Qwen3 | fal.ai Qwen3-TTS 1.7B | 500ms-1s | ~$0.003/min | `FAL_KEY` |
| Hume | (inactive stub) | — | — | `HUME_API_KEY` |

---

## Canvas System

### Components

- **Manifest** (`canvas-manifest.json`) — Registry of all pages with categories
- **Pages directory** (`CANVAS_PAGES_DIR`, default: `runtime/canvas-pages/`) — HTML files
- **Sync** (`POST /api/canvas/manifest/sync`) — Scans directory, updates manifest

### How Pages Open

1. Agent includes `[CANVAS:page-id]` in response
2. Frontend `checkCanvasInStream()` detects tag
3. Syncs manifest (`/api/canvas/manifest/sync`)
4. Calls `CanvasControl.showPage(pageId)` → loads in iframe

### How Pages Are Created

1. Agent outputs `` ```html<!DOCTYPE html>...</html>``` `` code block
2. Frontend detects `</html>` during streaming
3. Calls `_saveAndShowHtml()` → `POST /api/canvas/pages` with HTML
4. Server saves file, registers in manifest, returns URL
5. Canvas iframe loads the new page

### Context Injection

Every message includes: `[Canvas pages: page-id-1, page-id-2, ...]`

---

## Music System

### Playlists

| Playlist | Directory | Metadata File | Served Via |
|----------|-----------|--------------|-----------|
| Library | `runtime/music/` | `music_metadata.json` | `GET /music/<file>` |
| Generated | `runtime/generated_music/` | `generated_metadata.json` | `GET /generated_music/<file>` |
| Spotify | (virtual — no local files) | — | Frontend Spotify SDK |

### Track Flow

1. `GET /api/music?action=list&playlist=library` → returns track objects
2. Agent uses `[MUSIC_PLAY:Track Name]` → frontend matches by title
3. Frontend calls `musicPlayer.play(trackName)` → plays audio
4. Server tracks state in `current_music_state` dict (thread-safe)

### Suno AI Song Generation

1. Agent includes `[SUNO_GENERATE:description]` in response
2. Frontend calls `window.sunoModule.generate(prompt)`
3. Frontend hits `POST /api/suno` with action=generate
4. Suno API processes (~45 seconds), webhook callback
5. Track saved to `runtime/generated_music/`

### DJ Soundboard

Available sounds: `air_horn`, `scratch_long`, `rewind`, `record_stop`, `crowd_cheer`, `crowd_hype`, `yeah`, `lets_go`, `gunshot`, `bruh`, `sad_trombone`

Two trigger methods:
1. **Tags** — `[SOUND:air_horn]` (explicit, DJ mode only)
2. **Trigger words** — `DJSoundboard.checkTriggers(text)` matches phrases like "air horn", "rewind"

### Context Injection

Every message includes:
- `[Available tracks: Track 1, Track 2, ...]`
- `[DJ sounds: air_horn, scratch_long, ...]`
- `[Music PLAYING: Track Name]` or no music context if stopped

---

## Voice Pipeline

### Speech-to-Text (STT)

- **Provider:** Web Speech API (browser-native, requires Chrome + internet)
- **File:** `src/providers/WebSpeechSTT.js`
- **Silence timeout:** Configurable (default 1800ms) via profile `stt.silence_timeout_ms`
- **Wake word:** `WakeWordDetector` class, configurable words (default: "wake up")
- **Mute cycle:** Muted during TTS playback to prevent echo capture

### Text-to-Speech (TTS)

- **Generation:** Server-side via `services/tts.py` → provider plugin
- **Delivery:** Base64-encoded audio in NDJSON stream (`type: audio`)
- **Playback:** `src/providers/TTSPlayer.js` with audio queue
- **Mid-stream TTS:** Server generates TTS per-sentence as they complete (parallel streaming)

### Mute/Unmute Cycle

```
TTS audio starts
    → stt.mute() — blocks STT results
    → eventBus.emit('tts:start') — MusicPlayer ducks volume

TTS audio ends
    → 600ms settling delay (prevents tail-echo)
    → stt.resume() — re-enables STT
    → eventBus.emit('tts:stop') — MusicPlayer restores volume
```

---

## Profile System

### Schema (`profiles/schema.json`)

Profiles control every aspect of behavior:

| Section | Controls |
|---------|----------|
| `llm` | Provider, model, temperature, max_tokens |
| `voice` | TTS provider, voice ID, speed, parallel sentences |
| `stt` | Language, silence timeout, wake words, PTT mode |
| `features` | Canvas, vision, music, tools, DJ soundboard |
| `ui` | Theme, face, transcript panel, thought bubbles |
| `conversation` | Greeting, auto-hangup, interruption, max response length |
| `adapter` | Which frontend adapter (clawdbot, hume-evi, elevenlabs) |

### API

- `GET /api/profiles` — List all
- `GET /api/profiles/active` — Current active profile
- `POST /api/profiles` — Create new
- `PUT /api/profiles/<id>` — Update
- `POST /api/profiles/activate` — Switch active profile
- `DELETE /api/profiles/<id>` — Delete (default protected)

### Storage

- Profile JSONs: `profiles/*.json`
- Active profile ID: `.active-profile` file
- Applied on page load and profile switch

---

## Security Model

### XSS Protection

- **escapeHtml()** applied before ALL innerHTML assignments
- Display pipeline: `stripTags → escapeHtml → innerHTML`
- **DO NOT** add new innerHTML paths without escapeHtml

### Content Security Policy

```
default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; ...
```

Set in `app.py` after_request handler.

### Rate Limiting

- Default: 200 requests/minute per IP
- Configurable via `RATELIMIT_DEFAULT` env var
- In-memory storage (single-worker)

### Input Validation

- Message length: max 4000 characters (`routes/conversation.py`)
- Upload size: max 25 MB (`app.py`)
- Face name: brackets stripped to prevent tag injection
- Canvas page ID: sanitized filename

### Prompt Injection

- **Status:** Partially mitigated (issue #23)
- **Current defense:** Prompt armor delimiter in system prompt
- **Gap:** System prompt and user input concatenated in same message field
- **Needs:** OpenClaw protocol change to separate system/user roles

### Error Handling

- Generic error messages to HTTP responses (no stack traces)
- Detailed errors logged server-side only

### What NOT to Change Without Security Review

1. Any `innerHTML` assignment path
2. Tag stripping functions (stripCanvasTags, _stripCmdTags, clean_for_tts)
3. CSP headers in app.py
4. Input validation guards
5. Error response formatting

---

## Configuration

### Required Environment Variables

| Variable | Purpose |
|----------|---------|
| `CLAWDBOT_AUTH_TOKEN` | OpenClaw gateway authentication |

### Optional Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAWDBOT_GATEWAY_URL` | `ws://127.0.0.1:18791` | Gateway WebSocket URL |
| `GROQ_API_KEY` | — | Groq Orpheus TTS |
| `FAL_KEY` | — | Qwen3-TTS via fal.ai |
| `SUNO_API_KEY` | — | AI song generation |
| `CANVAS_PAGES_DIR` | `runtime/canvas-pages/` | Canvas HTML storage |
| `CANVAS_REQUIRE_AUTH` | `false` | Clerk auth on canvas |
| `RATELIMIT_DEFAULT` | `200 per minute` | Rate limit |
| `CORS_ORIGINS` | — | Extra CORS origins (comma-separated) |
| `SECRET_KEY` | auto-generated | Flask session key |

### Runtime Directories (all gitignored)

```
runtime/
  canvas-pages/          — Canvas HTML pages
  music/                 — Library playlist
  generated_music/       — AI-generated tracks
  known_faces/           — Face recognition data
  faces/                 — User photos
  transcripts/           — Listen-mode transcriptions
  uploads/               — User-uploaded files
usage.db                 — SQLite database
```

### Hot-Reload Files

| File | What It Controls | Reload Method |
|------|-----------------|---------------|
| `prompts/voice-system-prompt.md` | Agent personality + tag instructions | Reloaded every request |
| `profiles/*.json` | All behavior settings | Reloaded on profile switch |
| `canvas-manifest.json` | Available canvas pages | Reloaded on manifest sync |

---

## Test Suite

- **Location:** `tests/`
- **Runner:** `venv/bin/python3 -m pytest tests/ -q`
- **Current state:** 457 passed, 2 skipped, 0 failed
- **Coverage:** Routes, services, TTS providers, profiles, speech normalization
