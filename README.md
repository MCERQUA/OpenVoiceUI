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

## Quickstart

```bash
npx openvoiceui setup
npx openvoiceui start
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
- **Memory System** — Remembers across sessions. Gets smarter over time.
- **Self-Hosted** — Your hardware, your data. Docker, npm, or one-click Pinokio. No vendor lock-in, no monthly fees.

## And More

- Desktop OS interface with themes (Windows XP, macOS, Ubuntu, Win95, Win 3.1)
- Image generation (FLUX.1, Stable Diffusion 3.5)
- AI music generation & player (Suno)
- Video creation (Remotion Studio)
- Voice cloning (Qwen3-TTS via fal.ai)
- Cron jobs for scheduled automation
- File explorer with drag-and-drop
- Animated face modes (eye-face avatar, halo smoke orb)
- Agent profiles — switch personas via JSON

---

## Install Options

| Method | Command / Steps | Best For |
|--------|----------------|----------|
| **npm** | `npx openvoiceui setup && npx openvoiceui start` | Quickest start |
| **Pinokio** | One-click install from Pinokio app store | Non-technical users |
| **VPS** | Run the setup script on any Ubuntu server | Production hosting |
| **Docker** | `docker compose up` | Containerized deployment |
| **Dev Container** | Open in VS Code Dev Container | Contributing / development |

---

## Works With Any Provider

**LLM**

| Provider | Status |
|----------|--------|
| OpenClaw | Built-in — routes to OpenAI, Anthropic, Groq, and more |
| Z.AI (GLM) | Built-in |
| Ollama (local) | Via adapter |
| Any LLM | Drop-in gateway plugin |

**Text-to-Speech**

| Provider | Status |
|----------|--------|
| Supertonic (local) | Free, default |
| Groq Orpheus | Supported |
| Qwen3-TTS (fal.ai) | Supported |
| Hume EVI | Supported |
| ElevenLabs | Supported |

**Speech-to-Text**

| Provider | Status |
|----------|--------|
| Web Speech API | Free, default |
| Deepgram | Supported |
| Groq Whisper | Supported |
| Hume | Supported |

---

## Use Cases

**Small Business** — AI receptionist, appointment scheduler, report builder. Talk to your AI and get a live dashboard of today's leads, reviews, and tasks.

**Digital Agencies** — Deploy custom AI assistants per client. Multi-tenant, white-label ready. Each client gets their own voice-powered workspace.

**Developers** — Fork it, extend it, deploy it anywhere. MIT licensed. Build custom skills, gateway plugins, and adapters on top of a voice-first platform.

---

## How It's Different

| | OpenVoiceUI | Typical Voice AI |
|---|---|---|
| **Source** | Open source (MIT) | Closed source |
| **Canvas UI** | Live HTML rendering | Text/audio only |
| **Skills** | 35+ built-in, extensible | API endpoints |
| **Hosting** | Self-hosted, your data | Vendor cloud only |
| **Pricing** | Free forever | Per-minute billing |

---

## Extend It

- **Build a skill** — Add capabilities without touching core code. See [`docs/`](docs/)
- **Build a gateway plugin** — Connect any LLM provider. See [`plugins/README.md`](plugins/README.md)
- **Build an adapter** — Add new STT/TTS providers. See [`src/adapters/_template.js`](src/adapters/_template.js)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python / Flask |
| Frontend | Vanilla JS (ES modules, no framework) |
| Canvas | Fullscreen iframe + SSE |
| STT | Web Speech API, Deepgram, Groq Whisper |
| TTS | Supertonic, Groq Orpheus, Qwen3-TTS |
| LLM | Any provider via gateway adapter |
| Auth | Clerk (optional) |
| Deploy | npm, Docker, Pinokio, VPS |

---

## Documentation

- [Full Docs](docs/) — architecture, provider guides, configuration
- [Architecture Overview](docs/ARCHITECTURE.md)
- [Website](https://openvoiceui.com)
- [Environment Variables](.env.example)

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. This project is MIT licensed — fork it, build on it, make it yours.

## License

[MIT](LICENSE)

---

<p align="center">
  <a href="https://openvoiceui.com">Website</a> &nbsp;·&nbsp;
  <a href="https://github.com/MCERQUA/OpenVoiceUI">GitHub</a> &nbsp;·&nbsp;
  <a href="https://www.npmjs.com/package/openvoiceui">npm</a> &nbsp;·&nbsp;
  <a href="mailto:hello@openvoiceui.com">hello@openvoiceui.com</a>
</p>
