---
sidebar_position: 2
title: Configuration
---

# Configuration

All configuration is managed through a single `.env` file in the project root. Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Docker Compose, the CLI wizard, and the VPS setup all read from this file.

## Required Keys

You need two things to get a working installation:

### 1. LLM Provider API Key

OpenVoiceUI needs at least one LLM provider to power conversations. The AI gateway (OpenClaw) routes requests to whichever provider you configure. Pick one:

| Provider | Env Var | Where to Get a Key |
|----------|---------|-------------------|
| Groq (Llama, Qwen) | `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| Z.AI (GLM-5-turbo) | Set via OpenClaw config | [z.ai](https://z.ai) |
| OpenAI (GPT-4o) | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

The provider key is configured in OpenClaw's auth profiles, either through the `openclaw onboard` wizard or the OpenClaw control panel at `http://localhost:18791`.

### 2. Gateway Auth Token

```bash
CLAWDBOT_AUTH_TOKEN=your-openclaw-gateway-token
```

This token authenticates OpenVoiceUI with the OpenClaw gateway. If you use `npx openvoiceui setup`, the wizard generates this for you. For manual Docker installs, get it from OpenClaw's setup wizard (`openclaw onboard`) or the gateway config file.

## Optional Keys by Category

### TTS (Text-to-Speech)

| Variable | Provider | Notes |
|----------|----------|-------|
| `GROQ_API_KEY` | Groq Orpheus | Recommended. Fast, high quality, free tier. Also used for Whisper STT. |
| `USE_GROQ` | -- | Set to `true` to enable Groq integration. |
| `USE_GROQ_TTS` | -- | Set to `true` to enable Groq Orpheus as a TTS option. |
| `FAL_KEY` | Qwen3-TTS (fal.ai) | Voice cloning via fal.ai. |
| `ELEVENLABS_API_KEY` | ElevenLabs | Premium TTS with instant voice cloning and 29 languages. |
| `ELEVENLABS_VOICE_ID` | ElevenLabs | Default voice ID. Browse voices at [elevenlabs.io/app/voice-library](https://elevenlabs.io/app/voice-library). |
| `HUME_API_KEY` | Hume EVI | Emotion-aware, expressive TTS. |
| `HUME_SECRET_KEY` | Hume EVI | Required alongside `HUME_API_KEY`. |
| `SUPERTONIC_API_URL` | Supertonic (local) | Override only if running Supertonic elsewhere. Ships with Docker Compose -- no config needed. |

Supertonic (local TTS) is included in the Docker Compose stack and works without any API key. It is the default fallback when no cloud TTS is configured.

### Music Generation

| Variable | Notes |
|----------|-------|
| `SUNO_API_KEY` | Enables AI music generation via Suno. |
| `SUNO_CALLBACK_URL` | Webhook URL for async generation results (e.g. `https://your-domain.com/api/suno/callback`). |
| `SUNO_WEBHOOK_SECRET` | Optional HMAC secret for webhook verification. |

### Authentication (Clerk)

| Variable | Notes |
|----------|-------|
| `CLERK_PUBLISHABLE_KEY` | Enables Clerk login. Leave unset for open access (single-user installs). |
| `CANVAS_REQUIRE_AUTH` | Set to `true` to require login for canvas pages. |
| `ALLOWED_USER_IDS` | Comma-separated Clerk user IDs. Restricts access to specific users. Find your user ID in server logs after first login. |

Without Clerk keys, the app runs open -- no login required. This is fine for single-user and local installs.

### Vision

| Variable | Notes |
|----------|-------|
| `GEMINI_API_KEY` | Enables screenshot and image analysis via Google Gemini. [Get key](https://aistudio.google.com/app/apikey). |

### Search

| Variable | Notes |
|----------|-------|
| `BRAVE_API_KEY` | Enables web search tool in the OpenClaw agent. Free tier: 2,000 queries/month. [Get key](https://brave.com/search/api/). Must also be set in the OpenClaw gateway service environment. |

### Coding Agent

| Variable | Notes |
|----------|-------|
| `CODING_CLI` | Which coding CLI to install in the OpenClaw container. Options: `codex`, `claude`, `opencode`, `pi`, `none` (default). Requires the corresponding provider API key. |

## Environment Variable Reference

Complete list of all recognized environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PORT` | No | `5001` | Server port. |
| `DOMAIN` | No | -- | Domain name (VPS/production installs). |
| `SECRET_KEY` | Recommended | Random per restart | Flask session secret. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`. |
| `CLAWDBOT_GATEWAY_URL` | No | `ws://127.0.0.1:18791` | OpenClaw gateway WebSocket URL. |
| `CLAWDBOT_AUTH_TOKEN` | **Yes** | -- | Gateway authentication token. |
| `GATEWAY_SESSION_KEY` | No | `voice-main-1` | Session key prefix. Change if running multiple instances on the same gateway. |
| `OPENCLAW_VERSION` | No | `2026.3.13` | Docker build arg: OpenClaw version to install. Only change if you have verified compatibility. |
| `CANVAS_PAGES_DIR` | No | `runtime/canvas-pages/` | Where canvas HTML pages are stored. Docker: leave unset (uses volume mount). VPS: set to the path created by the deploy script. |
| `GROQ_API_KEY` | Recommended | -- | Groq API key for Orpheus TTS and Whisper STT. |
| `USE_GROQ` | No | -- | Set `true` to enable Groq integration. |
| `USE_GROQ_TTS` | No | -- | Set `true` to enable Groq Orpheus TTS. |
| `FAL_KEY` | No | -- | fal.ai API key for Qwen3-TTS voice cloning. |
| `ELEVENLABS_API_KEY` | No | -- | ElevenLabs TTS API key. |
| `ELEVENLABS_VOICE_ID` | No | -- | Default ElevenLabs voice ID. |
| `HUME_API_KEY` | No | -- | Hume EVI API key. |
| `HUME_SECRET_KEY` | No | -- | Hume EVI secret key. |
| `SUPERTONIC_API_URL` | No | `http://supertonic:8765` | Supertonic TTS service URL. |
| `GEMINI_API_KEY` | No | -- | Google Gemini API key for vision/image analysis. |
| `SUNO_API_KEY` | No | -- | Suno API key for AI music generation. |
| `SUNO_CALLBACK_URL` | No | -- | Suno webhook callback URL. |
| `SUNO_WEBHOOK_SECRET` | No | -- | Suno webhook HMAC secret. |
| `BRAVE_API_KEY` | No | -- | Brave Search API key. |
| `CLERK_PUBLISHABLE_KEY` | No | -- | Clerk publishable key for authentication. |
| `CANVAS_REQUIRE_AUTH` | No | -- | Set `true` to require Clerk auth for canvas pages. |
| `ALLOWED_USER_IDS` | No | -- | Comma-separated Clerk user IDs for access control. |
| `CODING_CLI` | No | `none` | Coding CLI to install in OpenClaw container. |
| `RATELIMIT_DEFAULT` | No | -- | Rate limit override. Format: `"200 per day;50 per hour"`. |

## First Run Walkthrough

After installing via any method:

1. **Open the app** at `http://localhost:5001`. You should see the voice interface with an animated face or orb.

2. **Allow microphone access** when your browser prompts. HTTPS is required in production; HTTP localhost works for local development. Chrome or Edge is recommended -- Firefox has limited Web Speech API support.

3. **Check the admin dashboard** at `http://localhost:5001/admin`. This shows the active profile, connected providers, system health, and all configuration options.

4. **Test the voice connection.** Click the call button (or say the wake word if configured). The agent should greet you. If you see connection errors, verify:
   - `CLAWDBOT_AUTH_TOKEN` in `.env` matches your OpenClaw gateway token
   - The OpenClaw container is healthy: `docker compose ps`
   - Gateway logs show no errors: `docker compose logs openclaw`

5. **Configure your LLM provider.** Open the OpenClaw control panel at `http://localhost:18791` to set your preferred LLM provider and API key if you haven't already.

6. **Explore profiles.** Go to the admin dashboard and try switching between agent profiles, or create your own. See [Profiles & Personas](/customization/profiles) for the full schema.

## Next Steps

- [Profiles & Personas](/customization/profiles) -- customize the agent's voice, personality, and features
- [Canvas System](/features/canvas-system) -- how the AI builds live pages
- [Admin Dashboard](/features/admin-dashboard) -- manage everything from the browser
- [TTS Providers](/customization/tts-providers) -- compare voice providers
