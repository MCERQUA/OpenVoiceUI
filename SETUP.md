# OpenVoiceUI Setup Guide

This guide covers three install paths:
- [Docker](#docker-quick-start) — easiest, works on any OS
- [VPS / Linux server](#vps-setup) — for production self-hosting
- [Local development](#local-development) — for contributors

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **OpenClaw** | The AI gateway that powers conversations. Required. [Download here](https://openclaw.ai) |
| **Groq API key** | For Orpheus TTS (fast, high quality). Free tier available. [Get key](https://console.groq.com) |
| Python 3.10+ | For local / VPS installs |
| Docker + Compose | For Docker install only |

> **OpenClaw is the most important dependency.** Without it the server starts but cannot respond to any voice input. Install and start OpenClaw before running OpenVoiceUI.

---

## Upgrading from Pre-2.0

If you have an existing installation, runtime data directories have moved under `runtime/`:

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

To migrate, move your existing data into the new paths:
```bash
mkdir -p runtime
for dir in uploads canvas-pages known_faces music generated_music faces transcripts; do
  [ -d "$dir" ] && mv "$dir" "runtime/$dir"
done
[ -f usage.db ] && mv usage.db runtime/usage.db
```

Docker users: `docker compose down`, pull the latest code, then `docker compose up --build`. Volume mounts in `docker-compose.yml` already point to `runtime/`.

---

## OpenClaw Setup

1. Download and install OpenClaw from [https://openclaw.ai](https://openclaw.ai)
2. Start the OpenClaw service — it listens on `ws://127.0.0.1:18791` by default
3. Create an agent workspace and configure it with the voice system prompt from `prompts/voice-system-prompt.md`
4. Copy your auth token — you'll need it for `CLAWDBOT_AUTH_TOKEN` in `.env`

---

## Docker Quick Start

**Fastest path. Recommended for trying OpenVoiceUI.**

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
cp .env.example .env
```

---

### Do you already have OpenClaw running?

#### No — start everything fresh (recommended)

The compose stack starts three containers for you:
- **openclaw** — AI gateway on port 18791
- **openvoiceui** — the UI/API server on port 5001
- **supertonic** — local TTS engine

Edit `.env` and set at minimum:
```bash
CLAWDBOT_AUTH_TOKEN=your-openclaw-token   # from openclaw gateway config
GROQ_API_KEY=your-groq-key
SECRET_KEY=any-random-string-here
```

**Optional: enable the coding-agent skill**

The coding-agent skill lets the AI write code, create files, and run commands
autonomously. It requires a coding CLI installed in the openclaw container.
Set `CODING_CLI` in your `.env` before building — same options as openclaw's
setup wizard:

```bash
# Choose one (or leave unset to skip):
CODING_CLI=codex      # OpenAI Codex — also needs OPENAI_API_KEY
CODING_CLI=claude     # Anthropic Claude Code — also needs ANTHROPIC_API_KEY
CODING_CLI=opencode   # OpenCode — bring your own provider key
CODING_CLI=pi         # Pi coding agent — bring your own provider key
```

> If you already ran openclaw's interactive setup wizard, it asked you this
> question — you don't need to set it here.

```bash
docker compose up --build
```

#### Yes — connect to your existing OpenClaw

Point openvoiceui at your running OpenClaw gateway instead of starting a new one.

1. Make sure your existing openclaw gateway has `bind: "lan"` (not `"loopback"`) so it
   accepts connections from other containers, and `controlUi.dangerouslyAllowHostHeaderOriginFallback: true`:
   ```json
   "gateway": {
     "bind": "lan",
     "controlUi": { "dangerouslyAllowHostHeaderOriginFallback": true }
   }
   ```

2. Share the canvas-pages directory between your existing openclaw container and openvoiceui
   (both need to read/write the same pages). Add a bind mount to **both** containers:
   ```yaml
   # your existing openclaw container (add to its volumes):
   - ./canvas-pages:/path/to/openclaw/workspace/canvas-pages

   # openvoiceui (already in docker-compose.yml):
   - ./canvas-pages:/app/runtime/canvas-pages
   ```
   Pre-create the canvas manifest file before starting (Docker would otherwise create it as a directory):
   ```bash
   mkdir -p canvas-pages
   echo '{"pages":{},"categories":{},"order":[]}' > canvas-manifest.json
   ```

3. Edit `.env`:
   ```bash
   CLAWDBOT_GATEWAY_URL=ws://<your-openclaw-host>:<port>   # e.g. ws://192.168.1.10:18791
   CLAWDBOT_AUTH_TOKEN=your-openclaw-token
   GROQ_API_KEY=your-groq-key
   SECRET_KEY=any-random-string-here
   ```

4. Start only the openvoiceui and supertonic services (skip the built-in openclaw):
   ```bash
   docker compose up --build openvoiceui supertonic
   ```

---

> Leave `CANVAS_PAGES_DIR` unset for Docker — it defaults correctly to the mounted volume.

Open [http://localhost:5001](http://localhost:5001) in your browser. Allow microphone access and speak.

**To stop:**
```bash
docker compose down
```

**Persistent data** (canvas pages, music, uploads, transcripts) lives in Docker named volumes and survives container restarts.

---

## VPS Setup

For a production install on a Linux VPS with nginx + SSL.

### 1. Clone and configure

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
cp .env.example .env
nano .env   # or your preferred editor
```

Set these in `.env`:
```bash
PORT=5001
DOMAIN=your-domain.com
SECRET_KEY=<run: python3 -c "import secrets; print(secrets.token_hex(32))">
CLAWDBOT_AUTH_TOKEN=your-openclaw-token
CLAWDBOT_GATEWAY_URL=ws://127.0.0.1:18791
GROQ_API_KEY=your-groq-key
CANVAS_PAGES_DIR=/var/www/openvoiceui/canvas-pages
```

### 2. Create Python virtual environment

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. Test the server runs

```bash
set -a && source .env && set +a
venv/bin/python3 server.py
```

Open `http://your-server-ip:5001` to verify. Press Ctrl+C when done.

### 4. Run the setup script (nginx + SSL + systemd)

Edit the top of `deploy/setup-sudo.sh` to set your domain and email, then:

```bash
sudo bash deploy/setup-sudo.sh
```

This creates:
- `/etc/nginx/sites-available/your-domain.com` — nginx reverse proxy config
- `/etc/systemd/system/openvoiceui.service` — systemd service
- `/var/www/openvoiceui/canvas-pages` — canvas page storage directory
- Let's Encrypt SSL certificate

### 5. Verify

```bash
sudo systemctl status openvoiceui
sudo journalctl -u openvoiceui -f
```

Open `https://your-domain.com` in your browser.

---

## Local Development

For contributors running without Docker or a VPS.

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI.git
cd OpenVoiceUI
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env — set CLAWDBOT_AUTH_TOKEN and GROQ_API_KEY at minimum
venv/bin/python3 server.py
```

Open [http://localhost:5001](http://localhost:5001).

The system prompt (`prompts/voice-system-prompt.md`) hot-reloads — edit it without restarting the server.

---

## Configuration Reference

All configuration is via `.env`. Key variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLAWDBOT_AUTH_TOKEN` | **Yes** | — | OpenClaw gateway auth token |
| `CLAWDBOT_GATEWAY_URL` | No | `ws://127.0.0.1:18791` | OpenClaw WebSocket URL |
| `GROQ_API_KEY` | Recommended | — | Groq Orpheus TTS |
| `SECRET_KEY` | Recommended | random | Flask session key |
| `PORT` | No | `5001` | Server port |
| `CANVAS_PAGES_DIR` | No | `canvas-pages/` in app dir | Where canvas HTML pages are stored |
| `GATEWAY_SESSION_KEY` | No | `voice-main-1` | Session prefix (change for multiple instances) |
| `SUPERTONIC_MODEL_PATH` | No | — | Path to local ONNX TTS model |
| `FAL_KEY` | No | — | fal.ai key for Qwen3-TTS |
| `HUME_API_KEY` | No | — | Hume EVI TTS |
| `HUME_SECRET_KEY` | No | — | Hume EVI TTS secret |
| `GEMINI_API_KEY` | No | — | Vision / screenshot analysis |
| `SUNO_API_KEY` | No | — | AI music generation |
| `CLERK_PUBLISHABLE_KEY` | No | — | Auth (leave unset for open access) |

---

## Useful Commands

```bash
# VPS: view live logs
sudo journalctl -u openvoiceui -f

# VPS: restart
sudo systemctl restart openvoiceui

# VPS: status
systemctl status openvoiceui

# Docker: view logs
docker compose logs -f

# Docker: restart
docker compose restart

# Run tests
venv/bin/python -m pytest tests/
```

---

## Troubleshooting

**Voice input not working**
- Allow microphone in browser (HTTPS required in production, HTTP localhost is fine for dev)
- Check browser console for WebSpeech API errors
- Chrome/Edge recommended; Firefox has limited WebSpeech support

**Agent not responding**
- Check OpenClaw is running: `ss -tlnp | grep 18791`
- Check `CLAWDBOT_AUTH_TOKEN` is set in `.env` and matches your OpenClaw token
- Check logs: `sudo journalctl -u openvoiceui -f` or `docker compose logs -f`
- Look for `### Persistent WS connected` in logs — if missing, gateway connection failed

**TTS audio not playing**
- Check `GROQ_API_KEY` is set and valid
- Try a different TTS provider in the Settings panel
- Check logs for `tts_error` events

**502 Bad Gateway (nginx)**
- Verify the server is running: `systemctl status openvoiceui`
- Verify PORT in `.env` matches nginx proxy port (default 5001)
- Check nginx error log: `sudo tail -f /var/log/nginx/error.log`

**Canvas pages not loading / black screen**
- Verify `CANVAS_PAGES_DIR` path exists and is writable by the server user
- Docker: leave `CANVAS_PAGES_DIR` unset so it uses the mounted volume
- Docker: both `openclaw` and `openvoiceui` share the `canvas-pages` named volume — if you
  customised the compose file make sure both services mount it at the same paths as the
  default `docker-compose.yml`
- Check logs for canvas route errors

**Permission errors on VPS**
- Canvas dir and uploads must be owned by the service user: `sudo chown -R $USER /var/www/openvoiceui`

**Separate openclaw container (not using docker-compose)**
- If you run openclaw outside of this compose stack (e.g. an existing installation), make sure
  openclaw's gateway `bind` is set to `"lan"` (not `"loopback"`) so openvoiceui can reach it:
  ```json
  "gateway": {
    "bind": "lan",
    "controlUi": { "dangerouslyAllowHostHeaderOriginFallback": true }
  }
  ```
- Share the canvas-pages directory between the two containers via a bind mount so openclaw
  can write pages that openvoiceui serves.
