---
sidebar_position: 3
title: OpenClaw Requirements
---

# OpenClaw Requirements

OpenVoiceUI uses [OpenClaw](https://openclaw.ai) as its AI gateway. This document
lists the version and configuration requirements for voice conversations to work
correctly.

**If you're using Docker (`docker compose up`), you don't need this doc** -- the
included Docker image is pre-configured with the correct version and settings.

This doc is for users who want to connect OpenVoiceUI to an **existing** OpenClaw
installation.

---

## Tested Version

| | |
|---|---|
| **Tested** | `openclaw@2026.3.13` |
| **Minimum** | `openclaw@2026.3.1` |

Check your version:
```bash
openclaw --version
```

Install the tested version:
```bash
npm i -g openclaw@2026.3.13
```

> **Version compatibility:** OpenVoiceUI includes a protocol compatibility layer
> (`services/gateways/compat.py`) that handles differences between OpenClaw versions
> automatically. Earlier versions (2026.3.1-2026.3.6) and newer versions (2026.3.7+)
> have different config key names and authentication requirements -- the compat layer
> translates between them so you don't have to worry about it.

---

## Required Gateway Settings

These are **global settings** in `openclaw.json` that affect ALL agents on this
OpenClaw instance. Review carefully before applying -- they may impact other agents
you're running.

```json
{
  "gateway": {
    "auth": {
      "mode": "token",
      "token": "your-gateway-token"
    },
    "controlUi": {
      "dangerouslyDisableDeviceAuth": true
    }
  }
}
```

### Why `dangerouslyDisableDeviceAuth`?

Without this, every new browser/device that connects gets added to a
`devices/pending.json` queue and the session receives `NOT_PAIRED` status forever.
Voice users connect from phones, laptops, different browsers -- requiring manual
device approval for each one makes voice conversations unusable.

If you're uncomfortable with this setting, you can instead manually approve
devices via the OpenClaw control UI, but you'll need to do it for every new
device/browser your users connect from.

---

## Required Agent Settings

These can be scoped to the OpenVoiceUI agent specifically (in `agents.list[]`) so
they don't affect your other agents. Or set them in `agents.defaults` to apply
globally.

```json
{
  "agents": {
    "defaults": {
      "thinkingDefault": "off",

      "blockStreamingDefault": "on",
      "blockStreamingBreak": "text_end",

      "timeoutSeconds": 300,

      "compaction": {
        "reserveTokens": 120000,
        "keepRecentTokens": 8000,
        "reserveTokensFloor": 120000,
        "memoryFlush": {
          "enabled": true,
          "softThresholdTokens": 6000
        }
      },

      "contextPruning": {
        "mode": "cache-ttl",
        "ttl": "30m",
        "keepLastAssistants": 3,
        "softTrimRatio": 0.3,
        "hardClearRatio": 0.5
      }
    }
  }
}
```

### Settings Explained

| Setting | Why |
|---|---|
| `thinkingDefault: "off"` | Some models (GLM-4.7, etc.) return thinking blocks with no user-visible text. Without this, the voice agent appears to respond with silence. |
| `blockStreamingDefault: "on"` | Voice TTS needs the complete response to synthesize speech. Streaming partial chunks causes choppy/repeated audio. |
| `timeoutSeconds: 300` | Large canvas pages, image generation, and cold-cache first calls can take 60-90 seconds. Default timeout is too short. |
| `compaction` | Without compaction, conversation history grows unbounded. At ~60-80K tokens most models start timing out. The recommended settings trigger compaction at ~85K tokens. |
| `contextPruning` | Trims old tool results (NOT user messages) to keep the context window manageable. |

---

## Agent Workspace

OpenVoiceUI includes agent workspace templates in `setup/openvoiceui-agent/`:

| File | Purpose |
|---|---|
| `SOUL.md` | Agent personality and voice behavior |
| `TOOLS.md` | Available tools and action tags |
| `AGENTS.md` | Sub-agent delegation rules |
| `IDENTITY.md` | Agent identity |
| `MEMORY.md` | Persistent memory |
| `USER.md` | User context |

Copy these into your OpenClaw agent workspace:
```bash
cp -r setup/openvoiceui-agent/* ~/.openclaw/agents/openvoiceui/
```

Then register the agent in your `openclaw.json`:
```json
{
  "agents": {
    "list": [
      {
        "id": "openvoiceui",
        "default": true,
        "workspace": "/home/youruser/.openclaw/agents/openvoiceui"
      }
    ]
  }
}
```

---

## Checklist

Before connecting OpenVoiceUI to your existing OpenClaw:

- [ ] OpenClaw version is `2026.3.1` or newer
- [ ] `gateway.auth.mode` is set to `"token"`
- [ ] `gateway.auth.token` is set (same value as `CLAWDBOT_AUTH_TOKEN` in `.env`)
- [ ] `gateway.controlUi.dangerouslyDisableDeviceAuth` is `true`
- [ ] `agents.defaults.thinkingDefault` is `"off"`
- [ ] `agents.defaults.blockStreamingDefault` is `"on"`
- [ ] Agent workspace is created with OpenVoiceUI templates
- [ ] `CLAWDBOT_AUTH_TOKEN` in `.env` matches your gateway token
- [ ] `CLAWDBOT_GATEWAY_URL` in `.env` points to your gateway (default: `ws://127.0.0.1:18791`)

---

## Docker Alternative

If you don't want to modify your existing OpenClaw config, the Docker setup
includes its own isolated OpenClaw instance with all settings pre-configured:

```bash
docker compose up --build
```

This runs a separate OpenClaw container that doesn't interfere with your
existing installation.
