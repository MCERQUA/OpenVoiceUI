<p align="center">
<img src="docs/banner.png" alt="OpenVoiceUI Banner" />
</p>

# OpenVoiceUI

A plug-and-play browser-based voice agent platform. Connect any LLM, any TTS provider, and any AI framework — with a built-in music player, AI music generation, and a live web canvas display system.

> **Hosting notice:** OpenVoiceUI is designed to run on a dedicated VPS (see [Hetzner setup](#hosting-multiple-users-hetzner-vps) below). Running it on a local machine is possible but not recommended — microphone access, SSL, and persistent uptime all work significantly better on a hosted server. For the best experience, deploy to a VPS before using it seriously.

---

## What It Is

OpenVoiceUI is a modular voice UI shell. You bring the intelligence (LLM + TTS), it handles everything else:

- **Voice I/O** — browser-based STT with push-to-talk, wake words, or continuous mode
- **Animated Faces** — multiple face modes (eye-face avatar, halo smoke orb) with mood states, thinking animations, and audio-reactive waveform mouth
- **Web Canvas** — fullscreen iframe display system for AI-generated HTML pages, dashboards, and reports with interactive links, page versioning, and external URL display
- **Desktop OS Interface** — full desktop-like canvas experience with right-click context menus, wallpaper upload, trash, shortcuts, and folder creation (auto-seeded as default pages)
- **Music Player** — background music with crossfade, AI ducking, and AI trigger commands
- **Music Generation** — AI-generated track support via Suno or fal.ai integrations
- **AI Image Generation** — HuggingFace-powered image generation with FLUX.1 and SD3.5 models, quality presets, and aspect ratio control
- **Voice Cloning** — clone and generate speech with custom voice embeddings via fal.ai Qwen3-TTS
- **Soundboard** — configurable sound effects with text-trigger detection
- **Agent Profiles** — switch personas/providers without restart via JSON config
- **Agent Activity Chip** — live action ticker showing what the agent is doing in real-time
- **Live Instruction Editor** — hot-reload system prompt from the admin panel
- **Admin Dashboard** — session control, playlist editor, face picker, theme editor
- **Issue Reporter** — in-app bug/feedback reporting modal with session context (development tool)
- **Server-Side Settings** — voice, face, and TTS preferences persist across devices via server (no localStorage)
- **Document Upload Extraction** — PDF and document text extraction from uploads
- **Empty Response Auto-Recovery** — auto-retry on empty LLM responses with Z.AI direct fallback and session auto-recovery

---

## Open Framework Philosophy

OpenVoiceUI is built as an **open voice UI shell** — it doesn't lock you into any specific LLM, TTS engine, STT provider, or AI framework. Every layer is a pluggable slot. Drop in a gateway plugin, a TTS provider, or a custom adapter and it just works. The built-in providers are defaults, not requirements.

### LLM / Gateway Providers
Connect to any LLM via a gateway plugin — OpenClaw is built-in, others are drop-in:

| Provider | Status |
|----------|--------|
| Any OpenClaw-compatible gateway | Built-in |
| Z.AI (GLM models) | Built-in |
| OpenAI-compatible APIs | Via adapter |
| Ollama (local) | Via adapter |
| Hume EVI | Built-in adapter |
| LangChain, AutoGen, custom agent framework | Via gateway plugin |
| **Any LLM or framework you build a plugin for** | Drop a folder in `plugins/` |

### TTS Providers
| Provider | Type | Cost |
|----------|------|------|
| **Supertonic** | Local ONNX | Free |
| **Groq Orpheus** | Cloud, fast | ~$0.05/min |
| **Qwen3-TTS** | Cloud, expressive | ~$0.003/min |
| **Hume EVI** | Cloud, emotion-aware | ~$0.032/min |
| **Any TTS engine you implement** | Local or cloud | Your choice |

### STT Providers
| Provider | Type | Cost | Notes |
|----------|------|------|-------|
| **Web Speech API** | Browser-native | Free | No API key needed, Chrome/Edge only |
| **Deepgram Nova-2** | Cloud streaming | Pay-per-use | Reliable paid alternative, real-time WebSocket streaming |
| **Groq Whisper** | Cloud batch | Free tier available | Fast batch transcription via Groq API |
| **Whisper** | Local | Free | Self-hosted Whisper model |
| **Hume EVI** | Cloud, full-duplex | ~$0.032/min | Emotion-aware, bidirectional |
| **Any STT provider** | Via custom adapter | Your choice | Implement the STT adapter interface |

---

## Features

### Voice Modes
- **Continuous** — always listening, silence timeout triggers send
- **Push-to-Talk** — hold button or configurable hotkey (keyboard/mouse)
- **Listen** — passive monitoring mode
- **Sleep** — goodbye detection pauses the agent, wake word reactivates
- **Agent-to-Agent** — A2A communication panel

### Canvas System
- AI can open and display any HTML page in a fullscreen overlay
- Manifest-based page discovery with search, categories, and starred pages
- Triggered via `[CANVAS:page-id]` tags in AI responses
- Real-time SSE updates from server
- **Interactive links** — canvas pages communicate with the app via postMessage bridge (navigate, speak, open URLs)
- **Page versioning** — automatic `.versions/` backup on every change with restore API
- **External URL display** — load any URL in the canvas iframe via `[CANVAS_URL:https://...]`
- **Default pages** — desktop OS and file explorer pages auto-seeded on startup
- **Admin lock/URL columns** — admin panel shows lock state and copyable URLs for each page
- **Padded mode** — configurable edge padding on canvas pages
- **Error auto-injection** — canvas pages get an error bridge for debugging in the ActionConsole
- **Content Security Policy** — restrictive CSP on canvas pages to prevent XSS

### STT Improvements
- **Hallucination filter** — rejects ghost transcripts from silence
- **Noise rejection** — sustained speech detection prevents spurious triggers
- **VAD tuning** — configurable voice activity detection thresholds

### Music Player
- Background playlist with crossfade (1.5s smooth transitions)
- Auto-ducking during TTS (volume drops, restores after)
- AI voice commands: play, stop, skip, volume up/down
- Generated tracks (AI-composed) + custom playlists
- Track history (back button, 20-track buffer)

### Profile System
Define agents in JSON — each profile configures:
- LLM provider, model, parameters
- TTS voice, speed, parallel sentence mode
- STT silence timeout, PTT mode, wake words
- UI theme, face mood, enabled features
- Session key strategy

### Security
- **Content Security Policy** — restrictive CSP headers on canvas pages to prevent XSS
- **SSRF protection** — all external fetch endpoints validate and block internal network requests
- **Path traversal protection** — file access endpoints sanitize paths
- **WebSocket authentication** — gateway WebSocket connections require valid auth tokens

---

## Project Structure

```
├── server.py                   Entry point
├── app.py                      Flask app factory
├── docker-compose.yml          Multi-service Docker setup
├── docker-compose.pinokio.yml  Pinokio one-click installer compose
├── pinokio.js                  Pinokio app manifest
├── install.js                  Pinokio install script
├── start.js                    Pinokio start script
├── stop.js                     Pinokio stop script
├── update.js                   Pinokio update script
├── .devcontainer/
│   ├── devcontainer.json       VS Code dev container config
│   └── docker-compose.devcontainer.yml
├── routes/
│   ├── conversation.py         Voice + parallel TTS streaming (with abort + heartbeats)
│   ├── canvas.py               Canvas display system + CDN stripping
│   ├── instructions.py         Live system prompt editor
│   ├── music.py                Music control
│   ├── suno.py                 Suno AI music generation + webhooks
│   ├── profiles.py             Agent profile management
│   ├── admin.py                Admin + server stats
│   ├── transcripts.py          Conversation transcript auto-save
│   ├── vision.py               Screenshot / image analysis (Gemini)
│   ├── greetings.py            Greeting management
│   ├── theme.py                Theme management
│   ├── elevenlabs_hybrid.py    ElevenLabs TTS adapter
│   ├── pi.py                   Pi coding agent
│   ├── static_files.py         Static asset serving
│   ├── image_gen.py            HuggingFace image generation (FLUX.1, SD3.5)
│   ├── workspace.py            Agent workspace file management
│   ├── ssactivewear.py         S&S Activewear wholesale API proxy
│   ├── report_issue.py         In-app issue reporter
│   ├── icons.py                Icon generation
│   └── onboarding.py           Onboarding flow
├── services/
│   ├── auth.py                 Clerk JWT authentication middleware
│   ├── canvas_versioning.py    Automatic page version history + restore
│   ├── db_pool.py              SQLite WAL connection pool
│   ├── health.py               Liveness + readiness health probes
│   ├── paths.py                Canonical path constants (all dirs)
│   ├── speech_normalizer.py    Speech text normalization
│   ├── gateway_manager.py      Gateway registry + plugin loader + router
│   ├── gateways/
│   │   ├── base.py             GatewayBase abstract class
│   │   └── openclaw.py         OpenClaw gateway implementation
│   └── tts.py                  TTS service wrapper (retry + provider fallback)
├── tts_providers/              TTS provider implementations
│   ├── groq_provider.py        Groq Orpheus
│   ├── supertonic_provider.py  Supertonic (local ONNX)
│   ├── qwen3_provider.py       Qwen3-TTS via fal.ai
│   └── hume_provider.py        Hume EVI
├── providers/                  LLM/STT provider implementations
├── plugins/                    Gateway plugins (gitignored, drop-in)
│   ├── README.md               Plugin authoring guide
│   └── example-gateway/        Reference implementation
├── profiles/                   Agent profile JSON files
│   └── default.json            Base agent (edit to personalize)
├── prompts/
│   └── voice-system-prompt.md  Hot-reload system prompt
├── config/
│   ├── default.yaml            Server configuration
│   └── speech_normalization.yaml
├── deploy/
│   ├── openclaw/Dockerfile     OpenClaw container build
│   ├── supertonic/             Supertonic TTS container (Dockerfile + server.py)
│   ├── skill-runner/           Shared skill execution service (Dockerfile + server.py)
│   ├── setup-sudo.sh           VPS setup (nginx, SSL, systemd)
│   └── openvoiceui.service     Systemd unit file
├── default-pages/              Auto-seeded default canvas pages
│   ├── desktop.html            Desktop OS interface
│   └── file-explorer.html      File explorer page
├── src/
│   ├── app.js                  Frontend core
│   ├── adapters/               Adapter implementations
│   │   ├── ClawdBotAdapter.js
│   │   ├── hume-evi.js
│   │   ├── elevenlabs-classic.js
│   │   ├── elevenlabs-hybrid.js
│   │   └── _template.js        Build your own adapter
│   ├── core/                   EventBus, VoiceSession, EmotionEngine, Config
│   ├── face/                   Animated face implementations
│   │   ├── EyeFace.js          Classic eye-face avatar
│   │   ├── HaloSmokeFace.js    Halo smoke orb with thinking mode
│   │   ├── BaseFace.js         Base class for face types
│   │   └── manifest.json       Face registry + previews
│   ├── features/               MusicPlayer, Soundboard
│   ├── shell/                  Orchestrator, bridges, profile discovery
│   ├── ui/
│   │   ├── AppShell.js         Main app layout
│   │   ├── face/               FacePicker, FaceRenderer
│   │   ├── settings/           SettingsPanel, PlaylistEditor, TTSVoicePreview
│   │   ├── themes/             ThemeManager
│   │   └── visualizers/        PartyFXVisualizer, BaseVisualizer
│   └── providers/
│       ├── WebSpeechSTT.js     Browser speech recognition + wake word detection
│       ├── DeepgramSTT.js      Deepgram Nova-2 streaming STT
│       ├── GroqSTT.js          Groq Whisper batch STT
│       ├── TTSPlayer.js        TTS audio playback
│       └── tts/                TTS provider JS modules
├── sounds/                     Soundboard audio files
└── runtime/                    Runtime data (gitignored, docker-mounted)
    ├── uploads/                User-uploaded files
    ├── canvas-pages/           Canvas HTML pages
    │   └── .versions/          Automatic page version backups
    ├── known_faces/            Face recognition photos
    ├── music/                  Music playlist folder
    ├── generated_music/        AI-generated tracks
    ├── transcripts/            Conversation transcripts (auto-saved)
    └── canvas-manifest.json    Canvas page registry
```

---

## Prerequisites

- **OpenClaw gateway `2026.3.2`** — [openclaw.ai](https://openclaw.ai) · [version requirements](docs/openclaw-requirements.md)
- **Groq API key** for TTS — [console.groq.com](https://console.groq.com) (free tier available)
- Optional: Suno API key (music generation), Clerk (auth for multi-user deployments)

> OpenVoiceUI is tested with **openclaw@2026.3.2**. The Docker setup installs this version automatically. If you're using an existing OpenClaw install, see [OpenClaw Requirements](docs/openclaw-requirements.md) — other versions may have breaking changes that prevent voice conversations from working.

---

## Installation

### Option 1: Pinokio One-Click Install

The easiest way to get started. [Pinokio](https://pinokio.computer) is a free app manager that handles installation, startup, and updates automatically.

1. Install [Pinokio](https://pinokio.computer) if you don't have it
2. Search for "OpenVoiceUI" in the Pinokio app store, or add this repo URL directly
3. Click **Install** — Pinokio will clone the repo, build Docker images, and run onboarding
4. Click **Start** to launch all services
5. Open the URL shown in Pinokio to access the UI

Pinokio handles Docker Compose orchestration, environment configuration, and service lifecycle. Use the **Stop** button to shut down, and **Update** to pull the latest changes.

### Option 2: Deployment (Recommended: VPS)

The recommended way to run OpenVoiceUI is on a dedicated VPS — microphone access, SSL, and always-on uptime all work significantly better hosted than on a local machine.

A setup script handles nginx, Let's Encrypt SSL, and systemd automatically:

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI
cd OpenVoiceUI
cp .env.example .env
# Edit .env — set CLAWDBOT_AUTH_TOKEN and GROQ_API_KEY at minimum
# Edit deploy/setup-sudo.sh — set DOMAIN, PORT, EMAIL, INSTALL_DIR at the top
sudo bash deploy/setup-sudo.sh
```

The script is idempotent — safe to re-run. Skips SSL if cert already exists.

```bash
sudo systemctl status openvoiceui
sudo journalctl -u openvoiceui -f
```

### Option 3: Local Install (Docker)

Docker is the easiest path for local development — it runs OpenClaw, Supertonic TTS, and OpenVoiceUI together. Note that browser microphone access requires HTTPS — on localhost Chrome/Edge will still allow it, but other devices on your network won't work without a cert.

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI
cd OpenVoiceUI
cp .env.example .env
```

#### Step 1: Onboard OpenClaw (one-time)

Run the interactive onboarding wizard to configure your LLM provider and generate an auth token:

```bash
docker compose build openclaw
docker compose run --rm openclaw openclaw onboard
```

This will prompt you to choose an LLM provider (Anthropic, OpenAI, etc.), enter your API key, and generate a gateway auth token.

#### Step 2: Configure `.env`

Set the auth token from onboarding:

```bash
PORT=5001
CLAWDBOT_AUTH_TOKEN=<token-from-onboarding>
```

> `CLAWDBOT_GATEWAY_URL` does not need to be set — Docker Compose automatically routes to the OpenClaw container via loopback networking. TTS works out of the box with Supertonic (local, free). Optionally add `GROQ_API_KEY` for Groq Orpheus TTS.

#### Step 3: Start

```bash
docker compose up --build
```

Open `http://localhost:5001` in your browser.

#### How it works

The `docker-compose.yml` runs three services:

| Service | Description |
|---------|-------------|
| `openclaw` | OpenClaw gateway (Node.js) — handles LLM routing, tool use, and agent sessions on port 18791 |
| `supertonic` | Local TTS engine (ONNX) — provides free text-to-speech without external API keys |
| `openvoiceui` | OpenVoiceUI server (Python/Flask) — serves the frontend and connects to OpenClaw and Supertonic |

OpenClaw config is persisted in a Docker volume (`openclaw-data`), so onboarding only needs to run once.

### Option 4: VS Code Dev Container

For contributors and developers, OpenVoiceUI includes a VS Code dev container configuration that sets up the full development environment automatically.

1. Install the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) in VS Code
2. Open the repo folder in VS Code
3. When prompted, click **Reopen in Container** (or run the "Dev Containers: Reopen in Container" command)
4. VS Code will build and start all services using `.devcontainer/docker-compose.devcontainer.yml`
5. The development server starts automatically with hot-reload

The dev container includes all dependencies pre-installed and is configured for the full Docker Compose stack.

---

### TTS setup

Supertonic (local, free) is included and works out of the box — select "supertonic" as the TTS provider in the Settings panel.

To use **Groq Orpheus TTS** instead, you must first accept the model terms at [console.groq.com/playground?model=canopylabs%2Forpheus-v1-english](https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english), then set `GROQ_API_KEY` in `.env`.

---

## Authentication

Auth is **opt-in**. By default, OpenVoiceUI runs with no authentication — all endpoints are accessible. This is the right setting for self-hosted single-user deployments.

To **enable Clerk JWT auth** (for multi-user or public-facing deployments):
1. Create a Clerk app at [clerk.com](https://clerk.com)
2. Add `CLERK_PUBLISHABLE_KEY=pk_live_...` to `.env`
3. Set `CANVAS_REQUIRE_AUTH=true` in `.env`
4. Set `ALLOWED_USER_IDS=user_yourclerkid` — find your user ID in server logs after first login

---

## OpenClaw Integration

OpenVoiceUI connects to an [OpenClaw](https://openclaw.ai) gateway via persistent WebSocket. OpenClaw handles LLM routing, tool use, and agent sessions.

**OpenClaw >= 2026.2.24**: Requires Ed25519 device identity signing. OpenVoiceUI handles this automatically — a `.device-identity.json` file is generated on first run (never committed to git). The gateway auto-approves local loopback clients on first connect.

**Without a configured gateway**: The frontend will load but `/api/conversation` calls will fail. OpenClaw is the default — or drop in any gateway plugin as a replacement.

**Version compatibility**: OpenVoiceUI is tested against openclaw@2026.3.2 and performs a compatibility check on startup. See [OpenClaw Requirements](docs/openclaw-requirements.md) for details on supported versions and known breaking changes.

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | Yes | Server port (default: 5001) |
| `DOMAIN` | Yes | Your domain (used for callbacks) |
| `SECRET_KEY` | Recommended | Flask session secret — random per restart if unset |
| `CLAWDBOT_GATEWAY_URL` | Yes | OpenClaw WebSocket URL (default: `ws://127.0.0.1:18791`) |
| `CLAWDBOT_AUTH_TOKEN` | Yes | OpenClaw gateway auth token |
| `GATEWAY_SESSION_KEY` | No | Session key prefix (default: `voice-main-1`) |
| `GROQ_API_KEY` | No | Groq Orpheus TTS and Groq Whisper STT ([console.groq.com](https://console.groq.com)) |
| `FAL_KEY` | No | Qwen3-TTS and voice cloning via fal.ai ([fal.ai](https://fal.ai/dashboard)) |
| `SUPERTONIC_API_URL` | No | Override Supertonic TTS URL (Docker sets this automatically) |
| `HUME_API_KEY` | No | Hume EVI — emotion-aware voice ([platform.hume.ai](https://platform.hume.ai)) |
| `HUME_SECRET_KEY` | No | Hume EVI secret key |
| `CLERK_PUBLISHABLE_KEY` | No | Clerk auth — enables login ([clerk.com](https://clerk.com)) |
| `CANVAS_REQUIRE_AUTH` | No | Set `true` to require auth for canvas endpoints |
| `ALLOWED_USER_IDS` | No | Comma-separated Clerk user IDs for access control |
| `GEMINI_API_KEY` | No | Vision/image analysis ([aistudio.google.com](https://aistudio.google.com)) |
| `SUNO_API_KEY` | No | Suno AI music generation |
| `SUNO_CALLBACK_URL` | No | Auto-derived from `DOMAIN` if unset |
| `SUNO_WEBHOOK_SECRET` | No | Optional HMAC verification for Suno webhooks |
| `BRAVE_API_KEY` | No | Brave Search for agent web_search tool ([brave.com/search/api](https://brave.com/search/api)) |
| `CANVAS_PAGES_DIR` | No | Override canvas pages path (VPS installs) |
| `CODING_CLI` | No | Coding agent in openclaw: `codex`, `claude`, `opencode`, `pi`, or `none` |
| `RATELIMIT_DEFAULT` | No | Custom rate limit (e.g. `"200 per day;50 per hour"`) |
| `HUGGINGFACE_API_KEY` | No | HuggingFace image generation — FLUX.1, SD3.5 models ([huggingface.co](https://huggingface.co/settings/tokens)) |
| `DEEPGRAM_API_KEY` | No | Deepgram Nova-2 streaming STT ([deepgram.com](https://console.deepgram.com)) |
| `AGENT_API_KEY` | No | Internal agent-to-Flask API authentication token |

See `.env.example` for full documentation and comments.

### Personalizing Your Agent

Edit `profiles/default.json` to configure your agent:

```json
{
  "name": "My Assistant",
  "system_prompt": "You are a helpful voice assistant...",
  "llm": { "provider": "gateway", "model": "glm-4.7" },
  "voice": { "tts_provider": "groq", "voice_id": "tara" },
  "features": { "canvas": true, "music": true, "tools": true }
}
```

Edit `prompts/voice-system-prompt.md` to change the system prompt — changes are hot-reloaded with no restart.

---

## API Reference

```bash
# Health
GET  /health/live
GET  /health/ready

# Voice (streaming NDJSON with heartbeats)
POST /api/conversation?stream=1
     {"message": "Hello", "tts_provider": "groq", "voice": "tara"}
POST /api/conversation/abort              # Cancel in-progress response

# Profiles
GET  /api/profiles
POST /api/profiles/activate  {"profile_id": "default"}

# Canvas
GET  /api/canvas/manifest
GET  /api/canvas/versions/<page_id>       # List page version history
POST /api/canvas/versions/<page_id>/restore  {"timestamp": "..."}

# Transcripts
GET  /api/transcripts                     # List saved transcripts
GET  /api/transcripts/<session_id>        # Get transcript by session

# Upload
POST /api/upload                          # File upload (multipart)

# Session
POST /api/session/reset  {"type": "hard"}

# TTS
GET  /api/tts/providers
POST /api/tts/generate  {"text": "Hello", "provider": "groq", "voice": "tara"}

# Voice Cloning (fal.ai Qwen3-TTS)
POST /api/tts/clone                       # Clone voice from audio sample
POST /api/tts/generate                    # Generate speech with cloned voice
     {"text": "Hello", "provider": "qwen3", "voice_id": "clone-xxx"}

# Vision
POST /api/vision/analyze                  # Image/screenshot analysis

# Image Generation (HuggingFace)
POST /api/image-gen/generate              # Generate image (FLUX.1, SD3.5)
     {"prompt": "...", "model": "flux", "quality": "high", "aspect_ratio": "16:9"}

# AI Image Enhancement
POST /api/image-gen/enhance               # Server-side image editing with aspect ratio

# Workspace
GET  /api/workspace/files                 # List workspace files
GET  /api/workspace/files/<path>          # Read workspace file
POST /api/workspace/files/<path>          # Write workspace file

# Settings (server-side persistence)
GET  /api/settings                        # Get all persisted settings
POST /api/settings                        # Save settings to server

# Suno Music Generation
POST /api/suno/generate                   # Generate AI music
POST /api/suno/callback                   # Webhook callback endpoint

# Issue Reporter
POST /api/report-issue                    # Submit bug report with session context

# S&S Activewear
GET  /api/ssactivewear/*                  # Wholesale apparel API proxy
POST /api/ssactivewear/*

# Icons
POST /api/icons/generate                  # Generate icons

# Onboarding
GET  /api/onboarding/status               # Onboarding flow status
POST /api/onboarding/complete             # Mark onboarding step complete
```

---

## Building an Adapter

To connect a new LLM or voice framework, use `src/adapters/_template.js` as a starting point. Built-in adapters include ClawdBot (OpenClaw), Hume EVI, ElevenLabs Classic, and ElevenLabs Hybrid. Adapters implement a simple interface:

```js
export class MyAdapter {
  async init(bridge, config) { ... }
  async start() { ... }
  async stop() { ... }
  async destroy() { ... }
}
```

Register it in `src/shell/adapter-registry.js` and reference it in your profile JSON.

---

## Gateway Plugins

The backend uses a plugin system for LLM gateways. Drop a folder into `plugins/`, restart — it's live.

```
plugins/
  my-gateway/
    plugin.json   <- manifest (id, provides, requires_env)
    gateway.py    <- class Gateway(GatewayBase)
```

**plugin.json:**
```json
{
  "id": "my-gateway",
  "provides": "gateway",
  "gateway_class": "Gateway",
  "requires_env": ["MY_API_KEY"]
}
```

**gateway.py** subclasses `services.gateways.base.GatewayBase` and implements `stream_to_queue()`.

To route a profile to your gateway, add `gateway_id` to its `adapter_config`:
```json
"adapter_config": { "gateway_id": "my-gateway", "sessionKey": "my-1" }
```

Gateways can also call each other for inter-agent delegation:
```python
from services.gateway_manager import gateway_manager
result = gateway_manager.ask("openclaw", "Summarise this: " + text, session_key)
```

Full guide: [`plugins/README.md`](plugins/README.md)

---

## Skill Runner Service

The `deploy/skill-runner/` directory contains a shared skill execution service. This is a lightweight Python server that can execute agent skills in an isolated environment, providing a common runtime for skill definitions that need server-side execution (file I/O, API calls, data processing).

Build and run alongside the main stack:

```bash
docker compose build skill-runner
docker compose up -d skill-runner
```

---

## Hosting Multiple Users (Hetzner VPS)

OpenVoiceUI is designed so you can host a single VPS and serve multiple clients, each with their own voice agent instance.

**Recommended workflow:**

1. **Set up your base account** — install OpenVoiceUI on a Hetzner VPS under a base Linux user. Configure all API keys in `.env`. Verify everything works.

2. **For each new client**, create a new Linux user on the same VPS:
   ```bash
   adduser clientname
   cp -r /home/base/OpenVoiceUI /home/clientname/OpenVoiceUI
   chown -R clientname:clientname /home/clientname/OpenVoiceUI
   ```

3. **Edit their `.env`** with their API keys and a unique port:
   ```bash
   PORT=15004          # different port per user
   CLAWDBOT_AUTH_TOKEN=their-openclaw-token
   GROQ_API_KEY=their-groq-key
   ```

4. **Run `setup-sudo.sh`** for their domain — creates systemd service, nginx vhost, and SSL cert automatically.

5. **Each client** gets their own domain, their own agent session, and their own canvas/music library.

**Quick server requirements:**
- Ubuntu 22.04+
- Nginx + Certbot (Let's Encrypt)
- Python 3.10+, `venv` per user

---

## Development Notes

> **Issue Reporter (temporary):** The in-app issue reporting button in the toolbar is a temporary development tool included during the active development phase to help capture bugs with session context. It will be removed or made optional before a stable release.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python / Flask (blueprint architecture) |
| Frontend | Vanilla JS ES modules (no framework) |
| STT | Web Speech API / Deepgram Nova-2 / Groq Whisper / Whisper / Hume |
| TTS | Supertonic / Groq Orpheus / Qwen3 / Hume EVI |
| LLM | Any via gateway adapter |
| Image Gen | HuggingFace (FLUX.1, SD3.5) |
| Canvas | Fullscreen iframe + SSE manifest system |
| Music Gen | Suno API / fal.ai |
| Auth | Clerk (optional) |
| Installer | Pinokio / Docker Compose / VPS deploy script |

---

## License

MIT
