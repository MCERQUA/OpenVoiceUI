---
sidebar_position: 1
title: Installation
---

# Installation

OpenVoiceUI runs as a set of Docker containers. **[Docker](https://docs.docker.com/get-docker/) must be installed and running for all install methods.**

There are four ways to install, from simplest to most customizable:

| Method | Best For | Requirements |
|--------|----------|--------------|
| [Pinokio](#pinokio-one-click) | Trying it out, non-technical users | Pinokio app |
| [npm CLI](#npm-cli) | Standard install with interactive setup | Node.js 20+, Python 3.10+, Docker |
| [Docker Compose](#docker-compose) | Manual control, custom configuration | Docker, Docker Compose |
| [VPS / Production](#vps--production) | Public-facing deployments with SSL | Ubuntu server, Docker, nginx |

## Pinokio (One-Click)

The fastest way to try OpenVoiceUI.

1. Install [Pinokio](https://pinokio.co) if you don't have it
2. Search **"OpenVoiceUI"** in the Pinokio app store
3. Click **Install**, then **Start**

Pinokio handles Docker, dependencies, API key configuration, and image building automatically. Open the URL shown in Pinokio when it finishes.

## npm CLI

The CLI provides an interactive setup wizard that walks you through API key configuration, builds Docker images, and manages the container lifecycle.

### Setup

```bash
npx openvoiceui setup
```

The wizard prompts for:

1. **Groq API key** (required) -- for Orpheus TTS. [Get a free key](https://console.groq.com).
2. **Deepgram API key** (optional) -- for streaming STT. Falls back to browser-native Web Speech if skipped.
3. **AI provider key** -- at least one of: Z.AI, Anthropic, or OpenAI.
4. **Optional keys** -- Gemini (vision), OpenRouter, Suno (music).
5. **Port** -- defaults to 5001.

The wizard generates a `.env` file and builds the Docker images.

### Start

```bash
npx openvoiceui start
```

This starts all three containers and injects device identity if available. Open **http://localhost:5001** and start talking.

### Other Commands

```bash
npx openvoiceui stop       # stop all containers
npx openvoiceui restart    # stop + start
npx openvoiceui status     # show container status
npx openvoiceui logs       # tail container logs (Ctrl+C to stop)
npx openvoiceui update     # pull latest source and rebuild images
npx openvoiceui config     # print the OpenClaw control panel URL
```

## Docker Compose

For full manual control over configuration.

### 1. Clone and configure

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
cp .env.example .env
```

Edit `.env` with your API keys. At minimum:

```bash
CLAWDBOT_AUTH_TOKEN=your-openclaw-gateway-token
GROQ_API_KEY=your-groq-api-key
SECRET_KEY=any-random-string-here
```

See [Configuration](/getting-started/configuration) for the full list of environment variables.

### 2. Optional: enable coding agent

The coding-agent skill lets the AI write code, create files, and run commands autonomously. Set `CODING_CLI` in `.env` before building:

```bash
# Choose one (or leave unset to skip):
CODING_CLI=codex      # OpenAI Codex -- also needs OPENAI_API_KEY
CODING_CLI=claude     # Anthropic Claude Code -- also needs ANTHROPIC_API_KEY
CODING_CLI=opencode   # OpenCode
CODING_CLI=pi         # Pi coding agent
```

### 3. Build and start

```bash
docker compose up --build
```

Open **http://localhost:5001** to use the voice interface, or **http://localhost:5001/admin** for the admin dashboard.

To stop:

```bash
docker compose down
```

### Connecting to an existing OpenClaw gateway

If you already have an OpenClaw gateway running outside of Docker Compose, you can point OpenVoiceUI at it instead of starting the bundled one.

1. Make sure your existing OpenClaw gateway has `bind: "lan"` and the required auth settings:

   ```json
   {
     "gateway": {
       "bind": "lan",
       "auth": { "mode": "token" },
       "controlUi": {
         "dangerouslyDisableDeviceAuth": true,
         "dangerouslyAllowHostHeaderOriginFallback": true
       }
     }
   }
   ```

2. Share the canvas-pages directory between OpenClaw and OpenVoiceUI (both need read/write access):

   ```bash
   mkdir -p canvas-pages
   echo '{"pages":{},"categories":{},"order":[]}' > canvas-manifest.json
   ```

3. Set `CLAWDBOT_GATEWAY_URL` in `.env`:

   ```bash
   CLAWDBOT_GATEWAY_URL=ws://192.168.1.10:18791
   ```

4. Start only the OpenVoiceUI and Supertonic services:

   ```bash
   docker compose up --build openvoiceui supertonic
   ```

## VPS / Production

For running on an Ubuntu server with nginx, SSL, and systemd.

### 1. Clone and configure

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
cp .env.example .env
```

Edit `.env`:

```bash
PORT=5001
DOMAIN=your-domain.com
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
CLAWDBOT_AUTH_TOKEN=your-openclaw-gateway-token
CLAWDBOT_GATEWAY_URL=ws://127.0.0.1:18791
GROQ_API_KEY=your-groq-api-key
```

### 2. Create virtual environment

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. Test the server

```bash
set -a && source .env && set +a
venv/bin/python3 server.py
```

Open `http://your-server-ip:5001` to verify, then press Ctrl+C.

### 4. Deploy with systemd and nginx

Edit the top of `deploy/setup-sudo.sh` to set your domain and email, then run:

```bash
sudo bash deploy/setup-sudo.sh
bash deploy/setup-nginx.sh
```

This creates:
- systemd service (`openvoiceui.service`)
- nginx reverse proxy with SSL (Let's Encrypt)
- Canvas pages directory at `/var/www/openvoiceui/canvas-pages`

### 5. Verify

```bash
sudo systemctl status openvoiceui
sudo journalctl -u openvoiceui -f
```

Open `https://your-domain.com` in your browser.

See the [`deploy/`](https://github.com/MCERQUA/OpenVoiceUI/tree/main/deploy) directory in the repository for full production deployment scripts.

## Container Architecture

Docker Compose starts three containers that work together:

| Container | Default Port | Purpose |
|-----------|-------------|---------|
| `openclaw` | 18791 | LLM gateway. Routes voice input to your chosen LLM provider (OpenAI, Anthropic, Groq, Z.AI, etc.). Manages agent skills, tools, sub-agents, and memory. |
| `supertonic` | Internal only | Local TTS engine. Free, no API key needed. Generates speech audio from text using an ONNX model. |
| `openvoiceui` | 5001 | Voice UI, canvas system, admin dashboard, and all API endpoints. Connects to OpenClaw via WebSocket and to Supertonic via HTTP. |

The `openvoiceui` container uses `network_mode: "service:openclaw"` -- it shares the OpenClaw container's network stack. This means `ws://127.0.0.1:18791` works for the gateway connection without any cross-container networking.

Data is stored in Docker named volumes:
- `openclaw-data` -- OpenClaw workspace, agent files, memory
- `openvoiceui-runtime` -- uploads, transcripts, music, faces
- `canvas-pages` -- shared between OpenClaw and OpenVoiceUI for live page rendering

## CLI Reference

All commands are available via `npx openvoiceui <command>` or, if installed globally, `openvoiceui <command>`.

| Command | Description |
|---------|-------------|
| `setup` | Interactive wizard. Configures API keys, generates `.env`, builds Docker images. |
| `start` | Start all containers (`docker compose up -d`). Injects device identity. |
| `stop` | Stop all containers (`docker compose down`). |
| `restart` | Stop and start all containers. |
| `status` | Show container status (`docker compose ps`). |
| `logs` | Stream container logs in real time. Ctrl+C to stop. |
| `update` | Pull latest source (if git clone) and rebuild Docker images. Restarts containers if they were running. |
| `config` | Print the OpenClaw control panel URL (`http://localhost:18791`). |

## Upgrading from Pre-2.0

If you have an existing installation, runtime data directories moved under `runtime/`:

| Old Location | New Location |
|---|---|
| `uploads/` | `runtime/uploads/` |
| `canvas-pages/` | `runtime/canvas-pages/` |
| `known_faces/` | `runtime/known_faces/` |
| `music/` | `runtime/music/` |
| `generated_music/` | `runtime/generated_music/` |
| `faces/` | `runtime/faces/` |
| `transcripts/` | `runtime/transcripts/` |
| `usage.db` | `runtime/usage.db` |

To migrate:

```bash
mkdir -p runtime
for dir in uploads canvas-pages known_faces music generated_music faces transcripts; do
  [ -d "$dir" ] && mv "$dir" "runtime/$dir"
done
[ -f usage.db ] && mv usage.db runtime/usage.db
```

Docker users: `docker compose down`, pull the latest code, then `docker compose up --build`. Volume mounts already point to `runtime/`.

## Next Steps

- [Configuration](/getting-started/configuration) -- environment variables and API keys
- [OpenClaw Requirements](/getting-started/openclaw-requirements) -- gateway version and settings
- [Admin Dashboard](/features/admin-dashboard) -- manage profiles, providers, and plugins from the browser
