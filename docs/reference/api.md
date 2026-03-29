---
sidebar_position: 1
title: API Reference
---

# API Reference

OpenVoiceUI exposes a REST API from its Flask server. All endpoints accept and return JSON unless otherwise noted. The default base URL is `http://localhost:5001`.

## Error Format

All error responses use a consistent JSON shape:

```json
{ "error": "Human-readable error message" }
```

HTTP status codes follow standard conventions: `400` for bad requests, `404` for not found, `413` for payload too large, `415` for unsupported media type, `500` for internal errors, and `502` for upstream failures.

## Quick Reference

| Module | Base Path | Description |
|--------|-----------|-------------|
| [Conversation](#conversation) | `/api/conversation` | Voice conversation, TTS generation |
| [TTS](#tts-text-to-speech) | `/api/tts/*` | Text-to-speech providers, voices, cloning |
| [Canvas](#canvas) | `/api/canvas/*` | Canvas page CRUD, versioning, manifest |
| [Music](#music) | `/api/music` | Music player control, uploads, playlists |
| [Suno](#suno-ai-song-generation) | `/api/suno` | AI music generation |
| [Image Generation](#image-generation) | `/api/image-gen` | Image creation, saved designs |
| [Profiles](#profiles) | `/api/profiles` | Agent profile CRUD, activation |
| [Plugins](#plugins) | `/api/plugins` | Plugin install, uninstall, listing |
| [Icons](#icons) | `/api/icons/*` | Icon library, AI icon generation |
| [Vision](#vision) | `/api/vision` | Camera analysis, face recognition |
| [Chat](#chat) | `/api/chat` | Lightweight text completion |
| [Uploads](#uploads) | `/api/upload` | File upload and serving |
| [Greetings](#greetings) | `/api/greetings` | Greeting phrases management |
| [Instructions](#instructions) | `/api/instructions` | Agent instruction file editor |
| [Theme](#theme) | `/api/theme` | Color theme persistence |
| [Transcripts](#transcripts) | `/api/transcripts` | Conversation transcript storage |
| [Onboarding](#onboarding) | `/api/onboarding/state` | Onboarding wizard state |
| [Workspace](#workspace) | `/api/workspace/*` | File browser for agent workspace |
| [ChatGPT Import](#chatgpt-import) | `/api/import/chatgpt` | Import ChatGPT conversation history |
| [ElevenLabs Hybrid](#elevenlabs-hybrid) | `/api/elevenlabs-llm` | ElevenLabs voice bridge |
| [Admin](#admin) | `/api/admin/*` | Gateway RPC, system stats, client list |
| [Refactor Monitoring](#refactor-monitoring) | `/api/refactor/*` | Refactor automation state |
| [Report Issue](#report-issue) | `/api/report-issue` | Bug and feedback reports |
| [Registry](#registry-pinokio) | `/registry/*` | Pinokio check-in and checkpoints |

---

## Conversation

The core voice conversation loop -- speech-to-text transcription arrives from the browser, is forwarded to the OpenClaw Gateway for LLM inference, and the response is synthesized to audio via TTS.

### POST /api/conversation

Main voice conversation endpoint. Sends a transcribed user message through the OpenClaw Gateway and returns the AI response with optional TTS audio.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | yes | Transcribed user speech |
| `tts_provider` | string | no | `supertonic` or `groq` (default from env) |
| `voice` | string | no | Voice ID, e.g. `M1` (default: `M1`) |
| `session_id` | string | no | Session identifier (default: `default`) |
| `ui_context` | object | no | Canvas/music state from the frontend |

**Response body:**

```json
{
  "response": "AI text response",
  "audio": "base64-encoded WAV audio",
  "timing": {
    "handshake_ms": 120,
    "llm_ms": 2400,
    "tts_ms": 800,
    "total_ms": 3320
  },
  "actions": []
}
```

### POST /api/conversation/abort

Abort the active agent run for the current voice session. Fire-and-forget -- used by push-to-talk interrupt and message interrupt to stop generation.

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Label for logging (e.g. `stopVoiceInput`) |
| `text` | string | User text that triggered the abort |

**Response:** `{ "ok": true, "aborted": true }`

### POST /api/conversation/steer

Inject a user message into the active agent run without aborting it. OpenClaw inserts the message at the next tool boundary so the agent pivots immediately.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | yes | User text to inject (max 4000 chars) |
| `source` | string | no | Label for logging |

**Response:** `{ "ok": true, "steered": true }`

### POST /api/conversation/reset

Clear in-process conversation history for a session.

**Request body:** `{ "session_id": "default" }`

**Response:** `{ "status": "ok", "message": "Conversation history cleared" }`

### POST /api/session/reset

Hard reset of the OpenClaw session state. Clears the session JSONL file and bumps the voice session key so the next request starts completely fresh. Used by the UI Reset button.

**Response:** `{ "status": "ok", "old": "voice-main-6", "new": "voice-main-7" }`

### POST /api/stt-events

Receive STT error/status events from the browser for session monitoring. Only real errors are sent (no-speech and aborted are filtered client-side).

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `error` | string | Error code |
| `message` | string | Human-readable message |
| `provider` | string | STT provider (default: `webspeech`) |
| `source` | string | `stt` or `wake_word` |

---

## TTS (Text-to-Speech)

### GET /api/tts/providers

List all available TTS providers with metadata.

**Response:**

```json
{
  "providers": [
    {
      "provider_id": "supertonic",
      "name": "Supertonic",
      "voices": ["M1", "M2", "F1"],
      "active": true
    }
  ],
  "default_provider": "supertonic"
}
```

### POST /api/tts/generate

Generate speech audio from text using any configured TTS provider.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | yes | Text to synthesize (max 2000 chars) |
| `provider` | string | no | Provider ID (default: `supertonic`) |
| `voice` | string | no | Voice ID |
| `lang` | string | no | Language code: `en`, `ko`, `es`, `pt`, `fr`, `zh`, `ja`, `de` |
| `speed` | float | no | Speech speed (0.5 - 2.0) |
| `options` | object | no | Provider-specific options |

**Response:** WAV audio file (`Content-Type: audio/wav`).

### POST /api/tts/clone

Clone a voice from an audio sample. Accepts JSON (with `audio_url`) or multipart form (with audio file upload).

**JSON request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio_url` | string | yes | URL of audio sample |
| `name` | string | yes | Name for the cloned voice |
| `provider` | string | no | `qwen3`, `elevenlabs`, or `resemble` |
| `reference_text` | string | no | Transcript of the audio sample |

**Response:**

```json
{
  "status": "ok",
  "provider": "qwen3",
  "voice_id": "clone_abc123",
  "name": "My Voice",
  "clone_time_ms": 37000,
  "usage": "Use voice_id \"clone_abc123\" in /api/tts/generate with provider=qwen3"
}
```

### GET /api/tts/voices

List all available voices across all providers, including cloned voices.

**Response:**

```json
{
  "voices": {
    "supertonic": {
      "builtin": ["M1", "M2", "F1"],
      "cloned": []
    },
    "qwen3": {
      "builtin": [],
      "cloned": [{"voice_id": "clone_abc123", "name": "My Voice"}]
    }
  }
}
```

### DELETE /api/tts/voices/\{voice_id\}

Retire a cloned voice embedding. Only voices with the `clone_` prefix can be retired. The voice directory is renamed (not deleted).

**Response:** `{ "status": "ok", "voice_id": "clone_abc123", "action": "retired" }`

### POST /api/tts/preview

Generate a short audio preview for a given TTS voice.

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | TTS provider ID (default: `supertonic`) |
| `voice` | string | Voice ID |
| `text` | string | Custom preview text (max 200 chars) |

**Response:**

```json
{
  "audio_b64": "base64-encoded WAV",
  "provider": "supertonic",
  "voice": "M1"
}
```

### POST /api/supertonic-tts

**Deprecated** -- use `/api/tts/generate` instead. Legacy endpoint for Supertonic TTS generation. Returns WAV audio.

---

## Canvas

The canvas system manages HTML pages displayed in the application's iframe panel. Pages are stored on disk and tracked through a manifest that provides metadata, categories, and version history.

### POST /api/canvas/pages

Create or overwrite a canvas page from HTML content.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `html` | string | yes | Full HTML content |
| `filename` | string | no | Target filename (derived from title if omitted) |
| `title` | string | no | Page title (default: `Canvas Page`) |

**Response:**

```json
{
  "filename": "my-dashboard.html",
  "page_id": "my-dashboard",
  "url": "/pages/my-dashboard.html",
  "title": "My Dashboard",
  "category": "dashboards"
}
```

Protected system pages (`desktop.html`, `file-explorer.html`) cannot be overwritten via this endpoint (returns 403).

### POST /api/canvas/update

Forward a display command to the canvas SSE server. Updates the canvas context and sends the command to connected displays.

**Request body:**

```json
{
  "displayOutput": {
    "type": "page",
    "path": "/pages/my-dashboard.html",
    "title": "My Dashboard"
  }
}
```

### POST /api/canvas/show

Quick helper to display a page on the canvas. Delegates to `/api/canvas/update`.

**Request body:** `{ "type": "page", "path": "/pages/my-dashboard.html", "title": "My Page" }`

### POST /api/canvas/context

Update the canvas context (what page is currently displayed).

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `page` | string | Page path (e.g. `/pages/dashboard.html`) |
| `title` | string | Display title |
| `content_summary` | string | Brief content summary for the agent |

### GET /api/canvas/context

Return the current canvas context state.

**Response:**

```json
{
  "current_page": "/pages/dashboard.html",
  "current_title": "Dashboard",
  "page_content": "Brief summary...",
  "updated_at": "2026-03-29T12:00:00",
  "all_pages": [{"name": "dashboard.html", "title": "dashboard", "mtime": 1711700000}]
}
```

### GET /api/canvas/manifest

Return the full canvas manifest with all pages and categories. Auto-syncs with the filesystem (throttled to once per 60s). Pass `?sync=1` to force immediate sync.

**Response:** The manifest object containing `pages`, `categories`, `uncategorized`, and `recently_viewed` arrays.

### POST /api/canvas/manifest/sync

Force-sync the manifest with the pages directory. Adds new pages and removes entries for deleted files.

**Response:** `{ "status": "ok", "pages_count": 15, "categories_count": 4 }`

### GET/PATCH/DELETE /api/canvas/manifest/page/\{page_id\}

Manage individual page metadata.

- **GET** -- Return full metadata for a page.
- **PATCH** -- Update metadata fields: `display_name`, `description`, `category`, `tags`, `starred`, `is_public`, `is_locked`, `icon`. Locked pages cannot have `is_public` changed by agents (returns 403). New pages cannot be made public within 30 seconds of creation (returns 429).
- **DELETE** -- Archive the page (renames file to `.bak`, removes from manifest).

### GET/POST/PATCH /api/canvas/manifest/category

Manage canvas categories.

- **GET** -- List all categories.
- **POST** -- Create a category. Body: `{ "id": "finance", "name": "Finance", "icon": "...", "color": "#2ecc71" }`
- **PATCH** -- Update a category's `name`, `icon`, or `color`.

### POST /api/canvas/manifest/access/\{page_id\}

Track page access for the recently-viewed list and access count.

### GET /api/canvas/data/\{filename\}

Read a JSON data file from the `canvas-pages/_data/` directory. Returns `{}` if the file does not exist. Only `.json` files are allowed.

### POST /api/canvas/data/\{filename\}

Write a JSON data file to `canvas-pages/_data/`. Used by system pages for shared data (e.g. approval queues, stats).

### GET /api/canvas/mtime/\{filename\}

Return the last-modified time of a canvas page. Used by the frontend to detect changes.

**Response:** `{ "mtime": 1711700000.0, "filename": "dashboard.html" }`

### GET /api/canvas/versions/\{page_id\}

List all saved versions of a canvas page.

**Response:**

```json
{
  "page_id": "my-dashboard",
  "versions": [{"timestamp": 1711700000, "size": 4096}],
  "count": 3
}
```

### GET /api/canvas/versions/\{page_id\}/\{timestamp\}

Preview a specific version's HTML content. Returns raw HTML (`Content-Type: text/html`).

### POST /api/canvas/versions/\{page_id\}/\{timestamp\}/restore

Restore a canvas page to a previous version. The current version is saved before restoring.

### GET /api/canvas/build-log/\{project\}

Return parsed z-code JSONL session activity as human-readable console lines. Used by website build monitor pages.

**Query params:** `?lines=300&since=<iso-timestamp>`

### GET /pages/\{path\}

Serve a canvas HTML page from the pages directory. Handles authentication (when `CANVAS_REQUIRE_AUTH` is enabled), injects base styles and error bridge scripts, and sets no-cache headers.

### GET /images/\{path\}

Serve image assets from the canvas images directory.

---

## Music

Server-side music player state and library management. The server tracks playback state, playlists, and track metadata.

### GET /api/music

All-in-one music endpoint controlled by the `action` query parameter.

**Query params:**

| Param | Values | Description |
|-------|--------|-------------|
| `action` | `list`, `play`, `pause`, `resume`, `stop`, `skip`, `next`, `next_up`, `volume`, `status`, `shuffle`, `sync`, `confirm`, `spotify` | Action to perform |
| `track` | string | Track name or filename (for `play`) |
| `volume` | 0-100 | Volume level (for `volume` action) |
| `playlist` | `library`, `generated`, `spotify` | Active playlist |

**Response for `action=play`:**

```json
{
  "action": "play",
  "track": {"filename": "track.mp3", "title": "My Song", "artist": "AI DJ"},
  "url": "/music/track.mp3",
  "playlist": "library",
  "duration_seconds": 180,
  "dj_hints": "Title: My Song. Duration: 3:00.",
  "reservation_id": "abc123",
  "response": "Now playing 'My Song'!"
}
```

### POST /api/music/transition

Signal that the current song is ending and pre-queue the next track (DJ transition).

**Request body:** `{ "remaining_seconds": 10 }`

### GET /api/music/transition

Check if a DJ transition is pending.

### POST /api/music/upload

Upload a music file to the library playlist. Accepts multipart form with a `file` field. Allowed formats: `.mp3`, `.wav`, `.ogg`, `.m4a`, `.webm`.

### GET /api/music/playlists

List all available playlists with track counts and total sizes.

**Response:**

```json
{
  "playlists": [
    {"name": "library", "track_count": 5, "total_size_bytes": 25000000, "active": true},
    {"name": "generated", "track_count": 3, "total_size_bytes": 12000000, "active": false},
    {"name": "spotify", "track_count": null, "source": "spotify", "active": false}
  ]
}
```

### DELETE /api/music/track/\{playlist\}/\{filename\}

Delete a track from a playlist.

### PUT /api/music/track/\{playlist\}/\{filename\}/metadata

Update track metadata. Allowed fields: `title`, `artist`, `description`, `duration_seconds`, `phone_number`, `ad_copy`, `fun_facts`, `genre`, `energy`, `dj_intro_hints`.

### GET/POST /api/music/playlist/\{playlist\}/order

- **GET** -- Return the saved track order for the playlist.
- **POST** -- Save a new track order. Body: `{ "order": ["file1.mp3", "file2.mp3"] }`

### GET /music/\{filename\}

Serve a library music file.

### GET /generated_music/\{filename\}

Serve an AI-generated music file.

---

## Suno (AI Song Generation)

Generate songs using the Suno API. Generated songs are saved to the `generated_music/` directory.

### GET/POST /api/suno

Unified Suno endpoint. Action is specified via query param or POST body.

**Actions:**

| Action | Description |
|--------|-------------|
| `generate` | Submit a song generation job |
| `status` | Poll job status (auto-downloads when ready) |
| `list` | List all generated songs |
| `credits` | Check remaining API credits |

**Generate request body:**

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | string | Song description or lyrics |
| `style` | string | Musical style |
| `title` | string | Song title |
| `lyrics` | string | Explicit lyrics (enables custom mode) |
| `instrumental` | bool | Instrumental only (default: false) |
| `vocal_gender` | string | `m` or `f` |

**Generate response:**

```json
{
  "action": "generating",
  "job_id": "uuid",
  "task_id": "suno-task-id",
  "response": "Cooking! 'your track' is being generated...",
  "estimated_seconds": 45
}
```

### POST /api/suno/callback

Webhook endpoint for sunoapi.org. Called when song generation completes. Verifies HMAC signature if `SUNO_WEBHOOK_SECRET` is configured.

### GET/POST /api/suno/completed

- **GET** -- Returns completed songs waiting for frontend notification.
- **POST** -- Clear specific song or all from the completed queue. Body: `{ "song_id": "..." }`

### DELETE /api/suno/song/\{filename\}

Archive a generated song file (renames to `.deleted`, never truly removes).

---

## Image Generation

Proxy for image generation APIs (Google Gemini, HuggingFace, Imagen). Every generated image is saved to the server immediately.

### POST /api/image-gen

Generate an image from a text prompt, optionally with reference images.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | Image description |
| `images` | array | no | Reference images `[{mime_type, data (base64)}]` |
| `model` | string | no | Model name (default: `nano-banana-pro-preview`). Prefix `hf:` for HuggingFace models. |
| `quality` | string | no | `standard`, `high`, or `ultra` |
| `aspect` | string | no | `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3` |

**Response:**

```json
{
  "images": [
    {
      "mime_type": "image/png",
      "data": "base64...",
      "url": "/uploads/ai-gen-1711700000.png"
    }
  ],
  "text": ""
}
```

### POST /api/image-gen/enhance

Enhance a rough idea into a detailed image generation prompt using Gemini.

**Request body:** `{ "idea": "spray foam mascot", "quality": "high", "style": "vintage" }`

**Response:** `{ "prompt": "Detailed enhanced prompt text..." }`

### GET /api/image-gen/saved

Return the server-side AI designs manifest (persisted across devices).

### POST /api/image-gen/saved

Add a design entry to the manifest.

**Request body:** `{ "url": "/uploads/ai-gen-123.png", "name": "My Design", "ts": 1711700000 }`

### DELETE /api/image-gen/saved

Remove a design entry from the manifest by URL.

**Request body:** `{ "url": "/uploads/ai-gen-123.png" }`

---

## Profiles

Agent profile management. Profiles control LLM provider, TTS voice, system prompt, and other configuration. Stored as JSON files on the server.

### GET /api/profiles

List all profiles (summary) with the active profile ID.

**Response:**

```json
{
  "profiles": [{"id": "default", "name": "Default", "description": "..."}],
  "active": "default"
}
```

### GET /api/profiles/active

Return the full currently active profile object.

### GET /api/profiles/\{profile_id\}

Return a single profile by ID (full object).

### POST /api/profiles

Create a new profile.

**Required fields:** `id`, `name`, `system_prompt`, `llm.provider`, `voice.tts_provider`

Returns 201 on success, 400 on validation error, 409 if the ID already exists.

### POST /api/profiles/activate

Activate a profile by ID. Persists across service restarts.

**Request body:** `{ "profile_id": "default" }`

**Response:**

```json
{
  "ok": true,
  "active": "default",
  "profile": { "...full profile object..." }
}
```

### PUT /api/profiles/\{profile_id\}

Partial update of an existing profile. Only supplied fields are changed. Sub-objects (`llm`, `voice`, etc.) are merged one level deep.

### DELETE /api/profiles/\{profile_id\}

Delete a profile. The `default` profile is protected and cannot be deleted. Returns 204 on success.

---

## Plugins

Plugin management for face plugins, canvas page plugins, and other extensions.

### GET /api/plugins

List all installed plugins with status.

**Response:**

```json
{
  "plugins": [
    {
      "id": "bighead-avatar",
      "name": "BigHead Avatar",
      "version": "1.0.0",
      "type": "face",
      "status": "active",
      "faces": ["bighead"],
      "pages": []
    }
  ]
}
```

### GET /api/plugins/available

List catalog plugins that are not yet installed.

### GET /api/plugins/assets

Return script and CSS URLs for installed face plugins. Used by the frontend to dynamically inject plugin scripts.

**Response:**

```json
{
  "scripts": ["/plugins/bighead-avatar/bighead.js"],
  "styles": ["/plugins/bighead-avatar/bighead.css"]
}
```

### POST /api/plugins/\{plugin_id\}/install

Install a plugin from the catalog. Returns 201 on success, 404 if not found, 409 if already installed.

### DELETE /api/plugins/\{plugin_id\}

Uninstall a plugin. Returns 404 if not installed.

---

## Icons

Static icon library (Lucide SVGs) and AI-generated icon creation via Gemini.

### GET /api/icons/library

List all available Lucide icon names.

**Response:** `{ "count": 1500, "icons": ["activity", "airplay", ...] }`

### GET /api/icons/library/search

Search icons by name. Query params: `?q=folder&limit=20`

### GET /api/icons/library/\{name\}.svg

Serve a Lucide SVG icon by name. Cached for 1 day.

### POST /api/icons/generate

Generate a custom icon via Gemini image generation. Background is automatically removed.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | Description of the icon |
| `name` | string | no | Filename slug |
| `style` | string | no | Style override |

**Response:**

```json
{
  "url": "/api/icons/generated/my-icon.png",
  "name": "my-icon",
  "filename": "my-icon.png",
  "prompt": "folder with documents",
  "size": 24576
}
```

### GET /api/icons/generated

List all user-generated icons with metadata.

### GET /api/icons/generated/\{filename\}

Serve a generated icon file.

---

## Vision

Camera analysis and facial recognition.

### POST /api/vision

Analyze a camera frame with a vision LLM (Groq Llama 4 Scout).

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | string | yes | Base64 image or data URI |
| `prompt` | string | no | Analysis prompt (default: "Describe what you see") |
| `model` | string | no | Model override |

**Response:** `{ "description": "I see a person holding...", "model": "glm-4.6v" }`

### POST /api/frame

Store the latest camera frame in memory for use by other endpoints.

**Request body:** `{ "image": "base64-or-data-uri" }` (max 5 MB)

### POST /api/identify

Identify a person from a camera frame using DeepFace (local, no API calls). Uses the SFace model.

**Request body:** `{ "image": "base64-or-data-uri" }` (uses latest frame if omitted)

**Response:** `{ "name": "Mike", "confidence": 87.5 }`

### GET /api/faces

List all registered face directories with photo counts.

**Response:** `{ "faces": [{"name": "Mike", "photo_count": 3}] }`

### POST /api/faces/\{name\}

Register a face photo for a named person.

**Request body:** `{ "image": "base64-or-data-uri" }`

### DELETE /api/faces/\{name\}

Remove all photos for a registered face.

### GET /api/vision/models

List available vision models with the currently active one.

---

## Chat

Lightweight text completion for canvas pages using Groq.

### POST /api/chat

Simple text completion (not voice conversation). Uses Groq `llama-3.3-70b-versatile`.

**Request body:** `{ "message": "Rewrite this text: ..." }`

**Response:** `{ "response": "Rewritten text..." }`

---

## Uploads

File upload and serving.

### POST /api/upload

Upload a file. Accepts multipart form with a `file` field. Files are saved with UUID filenames. Supports text extraction from PDF, DOCX, XLSX, and PPTX files. Max 100 MB.

**Response:**

```json
{
  "original_name": "report.pdf",
  "filename": "a1b2c3d4.pdf",
  "url": "/uploads/a1b2c3d4.pdf",
  "type": "file",
  "content_preview": "Extracted text content...",
  "extracted_type": "pdf"
}
```

### GET /uploads/\{filename\}

Serve an uploaded file. Path traversal is guarded.

---

## Greetings

Manage agent greeting messages for session starts.

### GET /api/greetings

Return the full `greetings.json` file.

### GET /api/greetings/random

Return a single random greeting. Pass `?user=mike` for user-specific categories. Queued greetings (set via `/api/greetings/add`) take priority.

**Response:** `{ "greeting": "What can I help with?", "category": "random", "user": "mike" }`

### POST /api/greetings/add

Queue a contextual greeting for the next session start. Max 300 characters. Keeps the last 20 contextual greetings.

**Request body:** `{ "greeting": "Welcome back! I finished that report." }`

---

## Instructions

Live editor for agent instruction files (system prompts, identity, memory, etc.). Changes take effect on the next conversation request -- no restart needed.

### GET /api/instructions

List all registered instruction files with metadata, size, and existence status.

### GET /api/instructions/\{name\}

Read a single instruction file's content.

**Known file names:** `voice-system-prompt`, `soul`, `identity`, `agent`, `client`, `agents`, `user`, `tools`, `memory`, `claude`, `heartbeat`.

### PUT /api/instructions/\{name\}

Write new content to an instruction file.

**Request body:** `{ "content": "New file content..." }`

---

## Theme

Server-side persistence for user theme preferences.

### GET /api/theme

Return current saved theme colors.

**Response:** `{ "primary": "#0088ff", "accent": "#00ffff" }`

### POST /api/theme

Save theme colors.

**Request body:** `{ "primary": "#0088ff", "accent": "#00ffff" }` (hex `#rrggbb` format required)

### POST /api/theme/reset

Reset to default theme colors (`#0088ff` / `#00ffff`).

---

## Transcripts

Save and browse conversation transcripts.

### POST /api/transcripts/save

Save a listen-mode transcript to disk.

**Request body:** `{ "title": "Meeting Notes", "text": "Full transcript text..." }`

**Response:**

```json
{
  "saved": true,
  "path": "transcripts/2026-03-29/14-30-00_meeting-notes.txt",
  "date": "2026-03-29",
  "filename": "14-30-00_meeting-notes.txt",
  "words": 450
}
```

### GET /api/transcripts

List saved transcripts, newest first.

### GET /api/transcripts/\{date\}/\{filename\}

Read one transcript file. Returns plain text (`Content-Type: text/plain`).

---

## Onboarding

Persist onboarding wizard state on the server.

### GET /api/onboarding/state

Read onboarding state from the runtime directory. Returns `null` if no state exists.

### POST /api/onboarding/state

Save onboarding state. Body is the full state object (any JSON shape).

---

## Workspace

File browser for the OpenClaw agent workspace directory.

### GET /api/workspace/browse

List directory contents. Query param: `?path=Agent/memory`

**Response:**

```json
{
  "path": "Agent/memory",
  "entries": [
    {"name": "MEMORY.md", "type": "file", "size": 2048, "modified": 1711700000, "ext": ".md"}
  ]
}
```

### GET /api/workspace/tree

Return directories only (one level deep) for sidebar navigation. Query param: `?path=`

### GET /api/workspace/file

Read a text file's content (max 500 KB). Query param: `?path=Agent/MEMORY.md`

### PUT /api/workspace/file

Write content to a file in a writable workspace area.

**Query param:** `?path=Uploads/notes.txt`

**Request body:** `{ "content": "File content..." }`

### DELETE /api/workspace/file

Archive a file in a writable workspace area (renames to `.deleted`).

**Query param:** `?path=Uploads/old-file.txt`

### POST /api/workspace/mkdir

Create a new folder in a writable workspace area.

**Request body:** `{ "path": "Uploads/new-folder" }`

### GET /api/workspace/raw

Serve a raw file (images, audio, video, PDF) with correct content-type. Max 20 MB. Query param: `?path=Uploads/photo.jpg`

### GET /api/workspace/writable

Check if a workspace path is in a writable area. Returns the list of writable top-level areas: `Agent`, `Uploads`, `Canvas`, `Music`, `AI-Music`, `Transcripts`, `Voice-Clones`, `Icons`.

---

## ChatGPT Import

Import conversation history from ChatGPT data exports.

### POST /api/import/chatgpt

Upload and parse a ChatGPT export ZIP file. Conversations are stored as markdown files in the workspace.

**Request:** Multipart form with a `file` field (ZIP only, max 500 MB).

**Response:**

```json
{
  "status": "complete",
  "total": 150,
  "imported": 148,
  "skipped": 2,
  "errors": [],
  "conversations": [...]
}
```

### GET /api/import/chatgpt/conversations

List all imported conversations. Supports `?q=search` text filter and `?topic=python` topic filter.

### GET /api/import/chatgpt/conversation/\{filename\}

Read the markdown content of a single imported conversation.

### GET /api/import/chatgpt/status

Get import status and aggregate statistics (total conversations, word counts, topic distribution).

---

## ElevenLabs Hybrid

Bridge between ElevenLabs Conversational AI (voice layer) and OpenClaw (brain layer). Used when ElevenLabs handles voice I/O and OpenVoiceUI proxies the LLM.

### POST /api/elevenlabs-llm

Custom LLM endpoint for ElevenLabs. Receives OpenAI chat format, extracts the latest user message, streams it through the OpenClaw Gateway, strips canvas markers from the response, and returns clean text as OpenAI-compatible SSE for ElevenLabs TTS.

**Request body (OpenAI format):**

```json
{
  "messages": [
    {"role": "user", "content": "What's the weather?"}
  ]
}
```

**Response:** Server-Sent Events (SSE) stream in OpenAI delta format.

### GET /api/canvas-pending

Return and clear pending canvas commands extracted from OpenClaw responses. Polled by the ElevenLabs hybrid adapter during conversations.

**Response:** `{ "commands": [{"action": "present", "url": "/pages/stats.html"}] }`

---

## Admin

Administrative endpoints for gateway management, monitoring, and server diagnostics.

### GET /api/auth/check

Check if the current Clerk session is on the allowed user list.

**Response (200):** `{ "allowed": true, "user_id": "user_abc" }`
**Response (401):** `{ "allowed": false, "user_id": null, "reason": "not_signed_in" }`
**Response (403):** `{ "allowed": false, "user_id": null, "reason": "not_on_allowlist" }`

### GET /api/admin/gateway/status

Ping the OpenClaw Gateway (connect, handshake, disconnect).

**Response:** `{ "connected": true, "gateway_url": "ws://127.0.0.1:18791" }`

### POST /api/admin/gateway/rpc

Proxy an RPC call to the OpenClaw Gateway. Restricted to an allowlist of methods: `sessions.list`, `sessions.history`, `sessions.abort`, `chat.abort`, `chat.send`, `ping`, `status`, `agent.status`.

**Request body:** `{ "method": "sessions.list", "params": {}, "timeout": 10 }`

**Response:** `{ "ok": true, "result": {...} }` or `{ "ok": false, "error": "reason" }`

### POST /api/admin/install/start

Trigger agent-driven framework installation via the Gateway. Returns an SSE stream of installation progress.

**Request body:** `{ "url": "https://github.com/org/framework" }`

### GET /api/admin/clients

List all client directories under `/mnt/clients/` with container status information (JamBot multi-tenant only).

### GET /api/server-stats

VPS resource snapshot -- CPU, RAM, disk, uptime, top processes, gateway status.

**Response:**

```json
{
  "cpu_percent": 15.2,
  "memory": {"used_gb": 8.5, "total_gb": 16.0, "percent": 53.1},
  "disk": {"used_gb": 25.0, "free_gb": 25.0, "total_gb": 50.0, "percent": 50.0},
  "uptime": "5d 3h 20m",
  "top_processes": [{"pid": 1234, "name": "node", "cpu": 5.2, "mem": 3.1}],
  "gateways": [...]
}
```

---

## Refactor Monitoring

Read-only views of refactor automation state files. Used by the refactor-dashboard canvas page.

### GET /api/refactor/status

Return the full `playbook-state.json` (all task statuses, phase gates).

### GET /api/refactor/activity

Return the last 50 entries from `activity-log.jsonl`, newest first.

### GET /api/refactor/metrics

Return `metrics.json` (line counts, test coverage, etc.).

### POST /api/refactor/control

Control the refactor automation: pause, resume, or skip a task.

**Request body:**

```json
{"action": "pause"}
{"action": "resume"}
{"action": "skip", "task_id": "P2-T6"}
```

---

## Report Issue

User-submitted bug and feedback reports. Reports are saved locally and optionally forwarded to the public feedback service.

### POST /api/report-issue

Save an issue report.

**Request body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | no | Issue type (max 50 chars, default: `other`) |
| `description` | string | yes | Description (max 2000 chars) |
| `context` | object | no | Additional context |

**Response:** `{ "ok": true, "saved": "2026-03-29_14-30-00_bug.json" }`

### GET /api/report-issues

List the last 50 issue reports, newest first.

---

## Registry (Pinokio)

Pinokio app registry check-in and checkpoint management.

### GET /registry/checkin

Pinokio check-in page. Renders an HTML page that reads the one-time token from the URL hash, posts a checkpoint snapshot to `api.pinokio.co`, and redirects back to Pinokio.

### POST /checkpoints/snapshot

Create a git snapshot and optionally publish it to the Pinokio registry. Called by the check-in page JavaScript. Requires `X-Registry-Token` header.

### GET /registry/debug

Debug endpoint showing the computed git SHA, canonical URL, and checkpoint hash for the registry.
