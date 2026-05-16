# Hermes Agent Plugin for OpenVoiceUI

Gateway plugin that adds [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT, Nous Research) as an alternative agent framework. Full voice support — STT, text processing, and TTS work identically to the default OpenClaw gateway.

**Tested with:** Hermes Agent v0.13.0 (`v2026.5.7`) | OpenVoiceUI >= 2026.5.4

## What It Adds

- **Standalone Hermes gateway** — routes voice/text conversations to Hermes REST API
- **Self-improving skills** — agent automatically creates reusable skills from successful tasks (auto-Curator, 7-day cycle, opt-out)
- **Deep memory search** — FTS5 full-text search across all past sessions
- **Autonomous tasks** — delegate long-running research, content generation, data processing; multi-agent Kanban board (v0.13)
- **Goal-locking** — Hermes "finishes what it starts" via `/goal` slash command (v0.13)
- **Agent Skills canvas page** — dashboard showing learned skills, memory, tasks, schedules
- **89+ built-in tools** — terminal, browser, file ops, code execution, image gen, video analysis (v0.13), voice cloning via xAI Custom Voices (v0.13), delegation

## Requirements

- OpenVoiceUI >= 2026.5.4 (running in Docker or standalone)
- Docker (for the Hermes container)
- At least one LLM API key (OpenRouter recommended for getting started; Z.AI Coding Plan subscription supported via Anthropic-protocol routing — see below)

## Install

### Admin UI (recommended)

1. Open the OVU admin panel → **Plugins**.
2. Click **Install** on the Hermes Agent card.
3. Fill in API keys for whichever providers you want — at least one required. Pick a **Default Provider** (others become fallbacks).
4. Click **Install** — the plugin lifecycle hook provisions the Hermes container automatically.
5. Restart OpenVoiceUI to register the gateway. The Hermes Agent profile then appears under Admin → Agents.

### Manual (advanced — for hand-rolled deployments)

If your OVU host doesn't have the provisioning service (single-tenant self-hosters), run the Hermes container yourself:

```yaml
# docker-compose.yml
hermes:
  image: nousresearch/hermes-agent:v2026.5.7
  hostname: hermes
  mem_limit: 2g
  cpus: 1.0
  volumes:
    - ./hermes-data:/opt/data
  environment:
    - API_SERVER_ENABLED=true
    - API_SERVER_PORT=18790
    - API_SERVER_HOST=0.0.0.0
    - GATEWAY_ALLOW_ALL_USERS=true
    - API_SERVER_KEY=${API_SERVER_KEY}        # required when binding non-loopback (v0.10+)
    # Add at least one LLM key:
    - OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
    - MINIMAX_API_KEY=${MINIMAX_API_KEY:-}
  restart: unless-stopped
```

Generate the API server key once and persist it: `openssl rand -hex 32 > .env-api-server-key`. Add `HERMES_HOST=hermes` + `HERMES_API_KEY=$(cat .env-api-server-key)` to OpenVoiceUI's `.env`.

The Hermes container must be on the same Docker network as OpenVoiceUI and have `hostname: hermes` set.

## Activating

After install + restart:

1. Admin → Agents.
2. Select **Hermes Agent** profile.
3. Start a conversation — it now routes through Hermes.

To switch back, select any other profile (e.g. **Assistant** for OpenClaw).

## Configuration

### LLM Providers

Hermes supports many providers. Set keys as environment variables on the Hermes container:

| Variable | Provider | Notes |
|----------|----------|-------|
| `OPENROUTER_API_KEY` | OpenRouter | Access 200+ models. Best for getting started. |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | Direct Anthropic API. Also used for Z.AI Coding Plan — see below. |
| `MINIMAX_API_KEY` | MiniMax | MiniMax M2.7-highspeed |
| `GLM_API_KEY` | Z.AI / ZhipuAI GLM | Pay-per-use endpoint only (see Z.AI section below for subscription routing) |
| `HF_TOKEN` | Hugging Face | HF Inference API |
| `GITHUB_TOKEN` | GitHub Copilot | Copilot models |

### Z.AI Coding Plan subscription — important

The same Z.AI key works for both pay-per-use and Coding Plan subscription, but they bill on different endpoints. Calling the wrong endpoint with a subscription-only key returns `HTTP 429: Insufficient balance` even though the subscription has credits available.

**For Coding Plan subscription:** route through Hermes's `anthropic` provider pointed at Z.AI's Anthropic-messages facade:

```yaml
# hermes-data/config.yaml
model:
  provider: anthropic
  default: glm-5-turbo
  base_url: https://api.z.ai/api/anthropic
```

```bash
# .env
ANTHROPIC_API_KEY=<your-zai-key>
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
API_TIMEOUT_MS=3000000
```

**For pay-per-use:** the default `zai` provider (OpenAI-wire on `/api/paas/v4`) works as documented.

Hermes's `zai` provider is hardcoded to the OpenAI-wire endpoint and cannot be redirected — the subscription path requires the `anthropic` provider with the base_url override.

### Default Model

Edit `hermes-data/config.yaml` (or `hermes config set` from inside the container):

```yaml
model:
  provider: openrouter
  default: anthropic/claude-sonnet-4
```

### Disable the Curator (optional)

v0.13's Curator wakes on a 7-day cycle and consolidates per-tenant skill files. Defense-in-depth gates protect bundled/hub skills, but for first rollouts you may want to disable it:

```yaml
# hermes-data/config.yaml
curator:
  enabled: false
```

### SOUL.md (Personality)

Edit `hermes-data/SOUL.md` to customize the agent's personality. Changes take effect on the next message — no restart needed.

## How Tool Calls Work

v0.13 emits structured `event: hermes.tool.progress` SSE events with `{tool, emoji, label, toolCallId, status: running | completed}` per tool invocation. The gateway parses these events, emits structured action events for the OpenVoiceUI actions panel, and strips them from the text sent to TTS so the user only hears the clean response.

Legacy inline backtick markers (still emitted on some platforms) are also recognized:

```
`💻 ls -la`          → terminal command
`🔎 search query`    → file/web search
`📖 /path/to/file`   → reading a file
`✏️ content`         → writing a file
`🧠 +memory: fact`   → saving to memory
`🌐 https://url`     → browser navigation
`👥 delegate task`   → spawning a sub-agent
```

## Canvas Page

Once installed, an **Agent Skills** page appears in your canvas with:
- Learned skills list with metadata
- Memory search across all sessions
- Active and completed autonomous tasks
- Scheduled recurring tasks
- Hermes container health status

## Plugin Structure

```
hermes-agent/
  plugin.json                  Manifest (gateway type, container spec, routes)
  gateway.py                   HermesGateway + HermesBridgeGateway classes
  routes/hermes.py             /api/hermes/* proxy endpoints + mid-flight pipeline
  pages/hermes.html            Agent Skills dashboard
  profiles/hermes-agent.json   Agent profile (gateway_id: hermes)
  README.md                    This file
```

## Environment Variables

Set these on the **OpenVoiceUI** container (not Hermes):

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_HOST` | `hermes` | Hostname of the Hermes container |
| `HERMES_PORT` | `18790` | API port on the Hermes container |
| `HERMES_API_KEY` | (none) | Bearer key for the Hermes API server. Required when Hermes binds non-loopback (v0.10+). Mint once with `openssl rand -hex 32` and persist. |
| `HERMES_TIMEOUT` | `300` | Request timeout in seconds |

## Upgrading from older Hermes Plugin versions

If you're moving from a Hermes Plugin version that targeted Hermes <= v0.9:

- **`API_SERVER_KEY` is now mandatory** when Hermes binds on `0.0.0.0` (v0.10+ enforces this). The container will refuse to start without it. Generate one with `openssl rand -hex 32` and pass it on both sides (`API_SERVER_KEY` on Hermes, `HERMES_API_KEY` on OpenVoiceUI).
- **`GATEWAY_ALLOW_ALL_USERS=true` is required in v0.13** when you're not using Hermes's per-platform user allowlists (Telegram, Discord, etc.). Without it Hermes denies all unauthorized users by default.
- **v0.13 emits `event: hermes.tool.progress` SSE events** in addition to the legacy backtick markers. The gateway already handles both.
- **Two session headers in v0.13:** `X-Hermes-Session-Id` (per-conversation, since v0.7) and `X-Hermes-Session-Key` (per-tenant long-term memory, new in v0.13).
- **`flush_memories` removed in v0.7.** If your tenant skills referenced it, migrate to the current memory tool API.
- **`BOOT.md` auto-hook removed in v0.12.** If you had a `BOOT.md` in your Hermes home, rewire it as a regular shell hook.

## Troubleshooting

**"Gateway 'hermes' not registered"** — The plugin isn't loaded. Check that `hermes-agent/` is in your `plugins/` directory and restart OpenVoiceUI.

**"Cannot connect to Hermes Agent"** — The Hermes container isn't reachable. Check:
- Container is running: `docker ps | grep hermes`
- Same Docker network as OpenVoiceUI
- Hostname is set: `--hostname hermes` or `hostname: hermes` in compose
- `HERMES_API_KEY` matches `API_SERVER_KEY` on the Hermes container (v0.10+)

**`HTTP 429: Insufficient balance or no resource package`** — If you have a Z.AI Coding Plan subscription, see the Z.AI section above. You're hitting the pay-per-use endpoint with a subscription key. Switch to `provider: anthropic` + `base_url: https://api.z.ai/api/anthropic`.

**`Refusing to start: binding to 0.0.0.0 requires API_SERVER_KEY`** — Mint and pass `API_SERVER_KEY` (v0.10+).

**Tools not showing in actions panel** — Make sure you're using the **Hermes Agent** profile (Admin → Agents), not the default OpenClaw profile.

**Slow first response** — Cold start takes 30-60s while Hermes initializes. Subsequent responses are 2-10s depending on the model and task complexity.

## Version Compatibility

| Plugin Version | Hermes Version | Status |
|---------------|---------------|--------|
| current | v0.13.0 (`v2026.5.7`) | Tested, stable |
| current | v0.12.0 (`v2026.4.30`) | Compatible — `X-Hermes-Session-Key` not sent, otherwise fine |
| current | <= v0.9.x | Not compatible — `API_SERVER_KEY` gate didn't exist; SSE event format predates `hermes.tool.progress` |

## License

MIT — same as Hermes Agent and OpenVoiceUI.
