<p align="center">
  <img src="docs/banner.jpg" alt="OpenVoiceUI Banner" width="100%" />
</p>

<h1 align="center">OpenVoiceUI</h1>
<p align="center"><strong>The open-source voice AI that actually does work.</strong></p>

<p align="center">
  <a href="https://www.npmjs.com/package/openvoiceui"><img src="https://img.shields.io/npm/v/openvoiceui?style=flat-square&color=3b82f6" alt="npm version" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT License" /></a>
  <a href="https://github.com/MCERQUA/OpenVoiceUI/stargazers"><img src="https://img.shields.io/github/stars/MCERQUA/OpenVoiceUI?style=flat-square&color=06b6d4" alt="GitHub Stars" /></a>
  <a href="https://openvoiceui.com"><img src="https://img.shields.io/badge/website-openvoiceui.com-0f172a?style=flat-square" alt="Website" /></a>
</p>

<p align="center">
  Talk to any LLM. Watch it build live web pages. Automate with 35+ skills.<br/>
  Self-host with full privacy. MIT licensed, forever free.
</p>

---

<!-- TODO: Add 15-30s demo GIF showing voice prompt → canvas page rendering live -->

> **[Watch the demo](https://openvoiceui.com)** -- see voice-to-canvas in action

---

## Install

Pick one:

```bash
# npm (quickest)
npx openvoiceui setup
npx openvoiceui start

# Docker
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
cp .env.example .env        # edit with your API keys
docker compose up

# Pinokio (one-click)
# Search "OpenVoiceUI" in the Pinokio app store and click Install
```

Open **localhost:5001**, say *"build me a dashboard"*, and watch it render live.

---

## What is OpenVoiceUI?

Most voice AI platforms sell you a voice API. OpenVoiceUI gives you an **entire AI workspace** — voice, canvas, skills, agents, media generation — open source and self-hosted.

It's a voice-first AI assistant that doesn't just talk back — it builds live HTML pages mid-conversation, runs 35+ built-in skills, delegates work to parallel sub-agents, and remembers everything across sessions. Works with any LLM. Runs on your hardware. MIT licensed.

## Core Features

- **Hands-Free Voice Control** — Wake word, push-to-talk, or continuous mode. Works with any LLM provider.
- **Canvas UI** — AI builds live HTML pages mid-conversation: dashboards, reports, galleries, tools. Real web apps, not text responses.
- **Skill System** — 35+ built-in skills for social media, SEO, email, business briefings, marketing. Build your own without touching core code.
- **Sub-Agents** — Parallel AI workers. Delegate multiple tasks simultaneously and get results back.
- **Long-Term Memory** — ByteRover context engine curates knowledge every turn. Persists across sessions in human-readable markdown.
- **Admin Dashboard** — Mobile-responsive. Agent profiles, provider config, workspace file browser, plugin management, system health. Everything editable and saving.
- **Plugin System** — Install face packs, gateway adapters, and extensions. Drop-in, no core changes.
- **Self-Hosted** — Your hardware, your data. Docker, npm, or one-click Pinokio. No vendor lock-in, no monthly fees.

## And More

- Desktop OS interface with themes (Windows XP, macOS, Ubuntu, Win95, Win 3.1)
- Image generation (FLUX.1, Stable Diffusion 3.5)
- AI music generation and player (Suno)
- Video creation (Remotion Studio)
- Voice cloning (Qwen3-TTS via fal.ai)
- Cron jobs for scheduled automation
- File explorer with drag-and-drop
- Animated face modes (eye-face avatar, halo smoke orb)
- Agent profiles — switch personas, voices, and LLM providers from the admin panel

---

## Install Details

### Option 1: npm (recommended for local use)

Requires **Node.js 20+** and **Python 3.10+**.

```bash
npx openvoiceui setup     # interactive wizard — configures LLM, TTS, API keys
npx openvoiceui start     # starts OpenClaw gateway + Supertonic TTS + voice UI
```

The setup wizard walks you through choosing an LLM provider, TTS provider, and entering API keys. Configuration is saved to `.env` and `openclaw-data/`.

```bash
npx openvoiceui stop      # stop all services
npx openvoiceui status    # check what's running
npx openvoiceui logs      # tail service logs
```

### Option 2: Docker

Requires **Docker** and **Docker Compose**.

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
cp .env.example .env
```

Edit `.env` with your API keys (at minimum: an LLM provider key and optionally a TTS key). Then:

```bash
docker compose up -d
```

This starts three containers:

| Container | Port | Purpose |
|-----------|------|---------|
| `openclaw` | 18791 | LLM gateway — routes to your chosen LLM provider |
| `supertonic` | (internal) | Free local TTS — no API key needed |
| `openvoiceui` | 5001 | Voice UI + Canvas + Admin dashboard |

Open **http://localhost:5001** to use the voice interface, or **http://localhost:5001/admin** for the admin dashboard.

To stop: `docker compose down`

### Option 3: Pinokio (one-click)

1. Install [Pinokio](https://pinokio.computer) if you don't have it
2. Search **"OpenVoiceUI"** in the Pinokio app store
3. Click **Install**, then **Start**

Pinokio handles Docker, dependencies, and configuration automatically.

### Option 4: VPS / Production

For running on an Ubuntu server with nginx and systemd:

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
cp .env.example .env               # edit with your API keys
sudo bash deploy/setup-sudo.sh     # creates dirs, installs systemd service
bash deploy/setup-nginx.sh         # generates nginx config (edit domain)
```

See [`deploy/`](deploy/) for the full production setup including SSL, nginx reverse proxy, and systemd service files.

---

## Configuration

All configuration is in `.env`. Copy `.env.example` to `.env` and fill in your values.

**Required:**
- An LLM provider API key (OpenAI, Anthropic, Groq, Z.AI, or any OpenClaw-compatible provider)
- `CLAWDBOT_AUTH_TOKEN` — set during `npx openvoiceui setup` or in OpenClaw's setup wizard

**Optional but recommended:**
- `GROQ_API_KEY` — enables Groq Orpheus TTS (fast, high quality, free tier)
- `SUNO_API_KEY` — enables AI music generation
- `CLERK_PUBLISHABLE_KEY` — enables login/auth (for multi-user or public deployments)

See [`.env.example`](.env.example) for all available options with descriptions.

---

## Works With Any Provider

**LLM**

| Provider | Status |
|----------|--------|
| OpenClaw Gateway | Built-in — routes to OpenAI, Anthropic, Groq, Z.AI, and more |
| Z.AI (GLM-5-turbo) | Built-in |
| Groq (Llama, Qwen) | Via OpenClaw |
| Google Gemini | Via OpenClaw |
| MiniMax | Via OpenClaw |
| Ollama (local) | Via adapter |
| Any LLM | Drop-in gateway plugin |

**Text-to-Speech**

| Provider | Status |
|----------|--------|
| Supertonic (local) | Free, ships with Docker setup |
| Groq Orpheus | Fast cloud TTS, free tier |
| Resemble AI | Premium cloned voices |
| Qwen3-TTS (fal.ai) | Voice cloning |
| Hume EVI | Emotion-aware |
| ElevenLabs | High quality, many voices |

**Speech-to-Text**

| Provider | Status |
|----------|--------|
| Web Speech API | Free, browser-native (default) |
| Deepgram | Streaming, accurate |
| Groq Whisper | Fast cloud transcription |

---

## Admin Dashboard

Access at **localhost:5001/admin**. Mobile-responsive.

- **Profiles** — View and activate agent personas
- **Agent Editor** — Edit name, voice, LLM provider, system prompt, features, and agent workspace files. 4 tabs: Profile, System Prompt, Features, Agent Files
- **Plugins** — Install and manage face packs, gateways, and extensions
- **Canvas Pages** — Toggle public/private, lock pages, delete with archive
- **Workspace Files** — Browse and edit agent workspace. Audio playback, image preview built in.
- **Music (Suno)** — View all generated songs, play inline, archive tracks
- **Provider Config** — Select LLM, TTS, STT providers. Saves to active profile.
- **Health and Stats** — CPU, RAM, disk, gateway status, session reset
- **Connector Tests** — 12 automated endpoint diagnostics

---

## Use Cases

**Small Business** — AI receptionist, appointment scheduler, report builder. Talk to your AI and get a live dashboard of today's leads, reviews, and tasks.

**Digital Agencies** — Deploy custom AI assistants per client. Multi-tenant ready. Each client gets their own voice-powered workspace.

**Developers** — Fork it, extend it, deploy it anywhere. MIT licensed. Build custom skills, gateway plugins, and adapters on top of a voice-first platform.

---

## How It's Different

| | OpenVoiceUI | Typical Voice AI |
|---|---|---|
| **Source** | Open source (MIT) | Closed source |
| **Canvas UI** | Live HTML rendering | Text/audio only |
| **Skills** | 35+ built-in, extensible | API endpoints |
| **Memory** | ByteRover long-term context | Session only |
| **Admin** | Full dashboard, mobile-ready | Config files |
| **Hosting** | Self-hosted, your data | Vendor cloud only |
| **Pricing** | Free forever | Per-minute billing |

---

## Extend It

- **Build a skill** — Add capabilities without touching core code. See [`docs/`](docs/)
- **Build a gateway plugin** — Connect any LLM provider. See [`plugins/README.md`](plugins/README.md)
- **Build an adapter** — Add new STT/TTS providers. See [`src/adapters/_template.js`](src/adapters/_template.js)
- **Build a face plugin** — Custom animated avatars. See the [BHB Animated Characters](https://github.com/MCERQUA/openvoiceui-plugins) plugin as a reference.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python / Flask |
| Frontend | Vanilla JS (ES modules, no framework) |
| Canvas | Fullscreen iframe + SSE |
| STT | Web Speech API, Deepgram, Groq Whisper |
| TTS | Supertonic, Groq Orpheus, Resemble, Qwen3-TTS |
| LLM | Any provider via OpenClaw gateway |
| Memory | ByteRover context engine (markdown knowledge base) |
| Auth | Clerk (optional) |
| Deploy | npm, Docker, Pinokio, VPS/systemd |

---

## Documentation

- [Architecture Overview](docs/ARCHITECTURE.md)
- [TTS Provider Guide](docs/tts-providers.md)
- [Supertonic Setup](docs/supertonic-setup.md)
- [Environment Variables](.env.example)
- [PR Review Checklist](docs/PR-REVIEW-CHECKLIST.md)
- [Website](https://openvoiceui.com)

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. This project is MIT licensed — fork it, build on it, make it yours.

## License

[MIT](LICENSE)

---

<p align="center">
  <a href="https://openvoiceui.com">Website</a> &nbsp;&middot;&nbsp;
  <a href="https://github.com/MCERQUA/OpenVoiceUI">GitHub</a> &nbsp;&middot;&nbsp;
  <a href="https://www.npmjs.com/package/openvoiceui">npm</a> &nbsp;&middot;&nbsp;
  <a href="mailto:hello@openvoiceui.com">hello@openvoiceui.com</a>
</p>
