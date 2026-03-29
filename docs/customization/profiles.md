---
sidebar_position: 1
title: Profiles & Personas
---

# Profiles & Personas

Profiles are the single source of truth for agent behavior in OpenVoiceUI. A profile controls everything: which LLM provider answers questions, what voice the agent speaks with, how speech recognition works, which UI features are enabled, what the agent says on startup, and how sessions are managed. One profile is active at a time, and switching profiles changes the entire agent personality instantly.

Profiles are stored as JSON files in the `profiles/` directory (or `runtime/profiles/` in Docker). The built-in `default` profile ships with every install and cannot be deleted.

## How Profiles Work

1. On startup, `ProfileManager` loads every `.json` file in the profiles directory (except `schema.json`).
2. The active profile ID is read from a `.active-profile` file on disk, defaulting to `default`.
3. When a profile is activated via the API, the selection is persisted to disk so it survives container restarts.
4. The active profile feeds configuration to every subsystem: LLM routing, TTS, STT, canvas, vision, conversation flow, and the UI.

## Profile Schema Reference

Every profile requires three fields: `id`, `name`, and `system_prompt`. Everything else has sensible defaults. Below is a full walkthrough of every section.

### Core Identity

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier. Lowercase letters, numbers, and hyphens only (`^[a-z0-9-]+$`). |
| `name` | string | Yes | Display name shown in the admin panel. Max 50 characters. |
| `description` | string | No | Short description. Max 200 characters. |
| `version` | string | No | Semantic version for tracking changes. Default `"1.0"`. |
| `author` | string | No | Who created this profile. |
| `created` | string | No | ISO date string (e.g. `"2026-03-15"`). |
| `icon` | string | No | Emoji icon for UI display. |
| `system_prompt` | string | Yes | The agent personality and instructions. Minimum 10 characters. |
| `adapter` | string | No | Transport adapter: `clawdbot`, `hume-evi`, or `elevenlabs-classic`. Default `"clawdbot"`. |
| `adapter_config` | object | No | Passed verbatim to the adapter. For `clawdbot`: `sessionKey` and `agentId`. |

### LLM (`llm`)

Controls which language model powers the agent and how responses are streamed.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | **Required** | One of: `zai`, `clawdbot`, `gateway`, `openai`, `ollama`, `anthropic`, `hume`, `elevenlabs`. |
| `model` | string | -- | Model ID (e.g. `"glm-5-turbo"`, `"gpt-4o"`, `"claude-sonnet-4-20250514"`). |
| `config_id` | string | -- | Optional named config reference. |
| `parameters.max_tokens` | integer | -- | Maximum response length in tokens. |
| `parameters.temperature` | number | -- | Sampling temperature (0.0 -- 2.0). |

#### Queue (`llm.queue`)

Controls how the OpenClaw gateway streams responses to TTS.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string or null | `null` | `"collect"` = wait for full response then TTS. `"steer"` = user can interrupt mid-tool-chain, gateway pivots immediately. `"steer-backlog"` = steer but original message preserved. `null` = gateway default. |
| `block_streaming_chunk` | integer or null | `null` | Minimum characters in a streaming delta before firing a TTS sentence (10--2000). `null` = platform default (40). |
| `block_streaming_coalesce` | boolean or null | `null` | Coalesce streaming tokens into larger chunks before TTS. |

### Voice (`voice`)

Controls text-to-speech output.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tts_provider` | string | **Required** | One of: `supertonic`, `groq`, `elevenlabs`, `hume`, `qwen3`. |
| `voice_id` | string | **Required** | Voice identifier for the provider (e.g. `"autumn"`, `"troy"`, `"rachel"`). |
| `speed` | number | `1.0` | Playback speed (0.5 -- 2.0). |
| `parameters` | object | `{}` | Provider-specific parameters passed through. |
| `parallel_sentences` | boolean or null | `null` | Fire TTS for all sentences simultaneously (`true`) or sequentially (`false`). `null` = platform default (`true`). Set `false` if the provider rate-limits parallel requests. |
| `min_sentence_chars` | integer or null | `null` | Minimum characters before dispatching a chunk to TTS (5--500). Prevents TTS on single words. `null` = platform default (40). |
| `inter_sentence_gap_ms` | integer or null | `null` | Silence in ms between TTS audio chunks (0--2000). `null` = no gap. |

### STT (`stt`)

Controls speech-to-text input.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | **Required** | One of: `deepgram`, `deepgram-streaming`, `deepgram-batch`, `groq`, `webspeech`, `whisper`, `hume`, `elevenlabs`. |
| `language` | string | `"en-US"` | BCP-47 language code. |
| `silence_timeout_ms` | integer or null | `null` | Milliseconds of silence after final result before dispatching to AI (500--10000). `null` = platform default (3000). |
| `vad_threshold` | integer or null | `null` | FFT average amplitude threshold for voice activity detection (10--80). Lower = more sensitive. `null` = platform default (35). |
| `max_recording_s` | integer or null | `null` | Maximum recording duration in seconds before auto-chunking (10--120). `null` = platform default (45). |
| `continuous` | boolean or null | `null` | `true` = continuous listening. `false` = one-shot per utterance (PTT-only agents). `null` = provider default. |
| `wake_words` | array or null | `null` | Override the wake word phrase list. `null` = platform defaults. `[]` (empty array) = disable wake word entirely. |
| `wake_word_required` | boolean or null | `null` | Start session in passive wake-word mode. `null` = not required (active from start). |
| `ptt_default` | boolean or null | `null` | Default the UI to push-to-talk mode on session start. `null` = continuous listening. |
| `identify_on_wake` | boolean or null | `null` | Run face identification when wake word fires so the agent can greet by name. `null`/`true` = enabled when camera is on. |
| `require_camera_auth` | boolean or null | `null` | Block wake word activation unless a registered face is recognized (min 50% confidence). `null`/`false` = disabled. |

### Context (`context`)

Controls what context is injected before each LLM call.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_fts` | boolean | `true` | Include full-text search results from the knowledge base. |
| `enable_briefing` | boolean | `true` | Include the daily briefing context. |
| `enable_history` | boolean | `true` | Include recent conversation history. |
| `max_history_messages` | integer | `12` | How many recent messages to include (1--100). |

### Vision (`vision`)

Controls the camera/image analysis model.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | `"glm-4.6v"` | Vision model ID. |
| `provider` | string | `"zai"` | Vision provider. |

### Features (`features`)

Feature toggles. Each boolean enables or disables a UI capability. Unknown keys are allowed for forward compatibility.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `canvas` | boolean | `true` | Enable the canvas system (live HTML pages). |
| `vision` | boolean | `true` | Enable camera/screenshot analysis. |
| `music` | boolean | `false` | Enable AI music generation (Suno). |
| `tools` | boolean | `false` | Enable tool execution UI. |
| `emotion_detection` | boolean | `false` | Enable emotion detection from voice. |
| `dj_soundboard` | boolean | `false` | Enable the DJ soundboard panel. |

### UI (`ui`)

Controls the visual interface.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `theme` | string | `"dark"` | `"dark"` or `"light"`. |
| `theme_preset` | string | -- | Color preset: `"blue"`, `"purple"`, `"green"`, `"red"`, `"orange"`. |
| `face_enabled` | boolean | `true` | Show the animated face/avatar. |
| `face_mood` | string | `"neutral"` | Starting mood for the face animation. |
| `transcript_panel` | boolean | `true` | Show the conversation transcript panel. |
| `thought_bubbles` | boolean | `true` | Show agent thought bubbles during processing. |
| `show_mode_badge` | boolean | `false` | Show a mode badge in the UI. |
| `mode_badge_text` | string | -- | Custom text for the mode badge. |

### Speech Normalization (`speech_normalization`)

Controls how AI text output is cleaned before being sent to TTS.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `strip_markdown` | boolean | `true` | Remove markdown formatting (`**bold**`, `# headings`, etc.). |
| `strip_urls` | boolean | `true` | Remove URLs from speech output. |
| `strip_emoji` | boolean | `true` | Remove emoji characters. |
| `max_length` | integer | `800` | Maximum characters sent to TTS per chunk (50--5000). |
| `abbreviations` | object | `{}` | Custom abbreviation expansions (e.g. `{"API": "A P I"}`). |

### Conversation (`conversation`)

Controls the conversation flow.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `greeting` | string or null | `null` | Text spoken on session start. `null` = random from greeting pool. `""` (empty string) = silent connect. |
| `auto_hangup_silence_ms` | integer or null | `null` | Auto-hangup after N ms of total silence (5000--600000). `null` = never. |
| `interruption_enabled` | boolean or null | `null` | Allow user to barge in during TTS playback. Requires `llm.queue.mode` set to `"steer"`. `null` = platform default (`false`). |
| `max_response_chars` | integer or null | `null` | Hard cap on AI response length before TTS. Truncates at sentence boundary (50--16000). `null` = no cap. |

### Modes (`modes`)

Controls which input mode buttons are available in the UI.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `normal` | boolean | `true` | Standard continuous voice mode. |
| `listen` | boolean | `false` | Transcribe-only mode -- no AI sends. |
| `ptt` | boolean | `true` | Push-to-talk button available. |
| `a2a` | boolean | `false` | Agent-to-Agent programmatic input mode. |

### Session (`session`)

Controls how the OpenClaw gateway session key is managed.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key_strategy` | string or null | `null` | `"persistent"` = warm shared key (best for voice). `"per-call"` = new key per session start. `"per-message"` = fresh key per message (stateless). `null` = defer to `adapter_config.sessionKey` or platform default. |
| `key_prefix` | string or null | `null` | Prefix for auto-generated keys (e.g. `"voice-dj"` becomes `"voice-dj-1"`). `null` = env var or `"voice-main"`. |

### Auth (`auth`)

Per-profile authentication override. Supplements the global Clerk auth gate.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `required` | boolean or null | `null` | `true` = always require Clerk login. `false` = public even when global auth is on. `null` = inherit global setting. |
| `allowed_roles` | array or null | `null` | Clerk roles allowed to activate this profile. `null` = any authenticated user. `[]` = disabled. `["admin"]` = admin only. |

## Default Profile

The `default.json` profile ships with every install and serves as the base configuration. Here is what it sets:

- **LLM:** `gateway` provider, `glm-5-turbo` model, steer queue mode (supports interruption), 256 max tokens, 0.8 temperature
- **Voice:** Groq Orpheus TTS, `autumn` voice, 1.1x speed, parallel sentence dispatch
- **STT:** Deepgram, 3500ms silence timeout, continuous listening, wake word `"wake up"`
- **Context:** Full-text search on, briefing off, 8 history messages
- **Features:** Canvas and vision on, tools on, music off
- **UI:** Dark theme, blue preset, face enabled (halo-smoke mode), transcript panel on
- **Conversation:** Random greeting, interruption enabled, 1500 character response cap
- **Modes:** Normal, listen, and PTT all available
- **Session:** Persistent key strategy with `"voice-main"` prefix

## Common Recipes

### DJ Agent

A music-focused agent with soundboard and music generation enabled:

```json
{
  "id": "dj-agent",
  "name": "DJ Bot",
  "system_prompt": "You are a DJ assistant. Help users discover and create music. Use the Suno music generation tool when asked to make songs. Keep responses short and energetic.",
  "llm": {
    "provider": "gateway",
    "model": "glm-5-turbo",
    "parameters": { "max_tokens": 150, "temperature": 1.0 }
  },
  "voice": {
    "tts_provider": "groq",
    "voice_id": "troy",
    "speed": 1.2
  },
  "stt": { "provider": "webspeech" },
  "features": {
    "canvas": true,
    "music": true,
    "dj_soundboard": true,
    "vision": false,
    "tools": false
  },
  "conversation": {
    "greeting": "What's up! I'm your DJ. Want me to make you a track?",
    "max_response_chars": 300
  },
  "ui": {
    "theme": "dark",
    "theme_preset": "orange",
    "show_mode_badge": true,
    "mode_badge_text": "DJ Mode"
  }
}
```

### Silent Builder

A text-only agent that never speaks -- canvas output only. Good for kiosk displays or coding sessions where TTS is distracting:

```json
{
  "id": "silent-builder",
  "name": "Silent Builder",
  "system_prompt": "You are a coding assistant. Build canvas pages when asked. Never speak out loud -- use [CANVAS_URL:...] tags to show your work visually. Keep text responses minimal.",
  "llm": {
    "provider": "gateway",
    "model": "glm-5-turbo",
    "parameters": { "max_tokens": 512, "temperature": 0.5 },
    "queue": { "mode": "collect" }
  },
  "voice": {
    "tts_provider": "supertonic",
    "voice_id": "default",
    "speed": 1.0
  },
  "stt": {
    "provider": "webspeech",
    "ptt_default": true,
    "continuous": false
  },
  "features": {
    "canvas": true,
    "vision": false,
    "music": false,
    "tools": true
  },
  "conversation": {
    "greeting": "",
    "max_response_chars": 200
  },
  "ui": {
    "face_enabled": false,
    "transcript_panel": false,
    "thought_bubbles": true
  },
  "modes": {
    "normal": false,
    "ptt": true,
    "listen": false
  }
}
```

### Kiosk / Receptionist

A public-facing agent with wake word required and camera-based face recognition:

```json
{
  "id": "receptionist",
  "name": "Receptionist",
  "system_prompt": "You are a friendly receptionist for a business lobby. Greet visitors by name when recognized. Keep responses brief and helpful. Direct people to the right department.",
  "llm": {
    "provider": "gateway",
    "model": "glm-5-turbo",
    "parameters": { "max_tokens": 128, "temperature": 0.7 }
  },
  "voice": {
    "tts_provider": "groq",
    "voice_id": "diana",
    "speed": 1.0
  },
  "stt": {
    "provider": "deepgram",
    "wake_word_required": true,
    "wake_words": ["hey there", "hello", "excuse me"],
    "identify_on_wake": true,
    "silence_timeout_ms": 5000
  },
  "features": {
    "canvas": false,
    "vision": true,
    "music": false
  },
  "conversation": {
    "greeting": "Welcome! How can I help you today?",
    "auto_hangup_silence_ms": 30000,
    "max_response_chars": 500
  },
  "ui": {
    "face_enabled": true,
    "transcript_panel": false
  },
  "auth": {
    "required": false
  }
}
```

## API Endpoints

All profile endpoints are under `/api/profiles`.

### List All Profiles

```
GET /api/profiles
```

Returns a summary list of all profiles and the currently active profile ID.

**Response:**
```json
{
  "profiles": [
    { "id": "default", "name": "Assistant", "description": "...", "version": "1.0" },
    { "id": "dj-agent", "name": "DJ Bot", "description": "...", "version": "1.0" }
  ],
  "active": "default"
}
```

### Get Active Profile

```
GET /api/profiles/active
```

Returns the full configuration of the currently active profile.

### Get Profile by ID

```
GET /api/profiles/<profile_id>
```

Returns the full configuration of a specific profile. Returns `404` if not found.

### Create Profile

```
POST /api/profiles
Content-Type: application/json
```

**Required fields:** `id`, `name`, `system_prompt`, `llm.provider`, `voice.tts_provider`.

Returns `201` on success, `400` on validation error, `409` if a profile with the same `id` already exists.

### Activate Profile

```
POST /api/profiles/activate
Content-Type: application/json

{ "profile_id": "dj-agent" }
```

Sets the active profile. The selection is persisted to disk and survives restarts.

**Response:**
```json
{
  "ok": true,
  "active": "dj-agent",
  "profile": { ... }
}
```

### Update Profile (Partial)

```
PUT /api/profiles/<profile_id>
Content-Type: application/json
```

Partially updates an existing profile. Only the supplied fields are changed. Sub-objects (`llm`, `voice`, `stt`, etc.) are merged one level deep -- you can update `voice.speed` without re-specifying `voice.tts_provider`.

The `id` field cannot be changed via update.

Returns the updated profile on success, `404` if not found.

### Delete Profile

```
DELETE /api/profiles/<profile_id>
```

Deletes a profile. The `default` profile is protected and cannot be deleted. Returns `204` on success, `400` if protected, `404` if not found.

## Storage and Hot-Reload

- **Storage format:** Each profile is a single JSON file named `<id>.json` in the profiles directory.
- **Storage location:** `runtime/profiles/` (Docker) or `profiles/` (local dev). Docker uses a named volume so profiles persist across container rebuilds.
- **Schema file:** `profiles/schema.json` defines the full JSON Schema (draft-07) and is excluded from profile loading.
- **Startup loading:** All `.json` files in the profiles directory are loaded when the server starts. Invalid files are logged as errors and skipped.
- **Write-through:** Creating, updating, or deleting a profile writes to disk immediately and updates the in-memory cache.
- **Active profile persistence:** The active profile ID is written to `.active-profile` on disk. On restart, the server reads this file and re-activates the last selected profile.
- **Singleton pattern:** `ProfileManager` is a process-wide singleton. All API routes share the same in-memory profile cache.
- **Validation:** Profile creation validates required fields (`id`, `name`, `system_prompt`, `llm.provider`, `voice.tts_provider`) and checks that the `id` uses only alphanumeric characters, hyphens, and underscores.
