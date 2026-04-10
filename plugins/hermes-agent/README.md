# Hermes Agent Plugin for OpenVoiceUI

Gateway plugin that adds [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT, Nous Research) as an alternative agent framework. Full voice support — STT, text processing, and TTS work identically to the default OpenClaw gateway.

**Tested with:** Hermes Agent v0.6.0 | OpenVoiceUI >= 1.0

## What It Adds

- **Standalone Hermes gateway** — routes voice/text conversations to Hermes REST API
- **Self-improving skills** — agent automatically creates reusable skills from successful tasks
- **Deep memory search** — FTS5 full-text search across all past sessions
- **Autonomous tasks** — delegate long-running research, content generation, data processing
- **Agent Skills canvas page** — dashboard showing learned skills, memory, tasks, schedules
- **50+ built-in tools** — terminal, browser, file ops, code execution, image gen, delegation

## Requirements

- OpenVoiceUI running (Docker or standalone)
- At least one LLM API key (OpenRouter recommended for getting started)
- Docker (for running the Hermes container)

## Install

### From GitHub (manual)

```bash
# 1. Copy plugin into your plugins directory
cp -r hermes-agent /path/to/openvoiceui/plugins/

# 2. Add Hermes to your docker-compose.yml (see below)

# 3. Start Hermes container
docker compose up -d hermes

# 4. Restart OpenVoiceUI to load the plugin
docker compose restart openvoiceui
```

### From Pinokio

Install via the OpenVoiceUI Plugins panel in Settings.

### From npm (planned)

```bash
openvoiceui plugin install hermes-agent
```

## Docker Compose

Add this service to your `docker-compose.yml`:

```yaml
hermes:
  image: nousresearch/hermes-agent:v0.6.0
  container_name: hermes
  hostname: hermes
  mem_limit: 2g
  cpus: 1.0
  volumes:
    - ./hermes-data:/opt/data
  environment:
    - API_SERVER_ENABLED=true
    - API_SERVER_PORT=18790
    - API_SERVER_HOST=0.0.0.0
    # Add your API key(s) — at least one required:
    - OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
    - MINIMAX_API_KEY=${MINIMAX_API_KEY:-}
  restart: unless-stopped
```

Then add `HERMES_HOST=hermes` to your OpenVoiceUI `.env` file so the plugin knows where to find it.

**Important:** The Hermes container must be on the same Docker network as OpenVoiceUI and must have `hostname: hermes` set. If using a custom network, make sure both services are on it.

## Activating

After install and restart:

1. Go to Admin > Agents
2. Select the "Hermes Agent" profile
3. Start a conversation — it now routes through Hermes

To switch back, select any other profile (e.g. "Assistant" for OpenClaw).

## Configuration

### API Keys

Hermes needs at least one LLM provider key. Set them as environment variables on the Hermes container:

| Variable | Provider | Notes |
|----------|----------|-------|
| `OPENROUTER_API_KEY` | OpenRouter | Access 200+ models. Best for getting started. |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | Direct Anthropic API |
| `MINIMAX_API_KEY` | MiniMax | MiniMax M2.7-highspeed |
| `DEEPSEEK_API_KEY` | DeepSeek | DeepSeek models |
| `HF_TOKEN` | Hugging Face | HF Inference API |
| `GITHUB_TOKEN` | GitHub Copilot | Copilot models |

### Default Model

Edit `hermes-data/config.yaml` to set the default model and provider:

```yaml
model:
  provider: openrouter
  default: anthropic/claude-sonnet-4
```

### SOUL.md (Personality)

Edit `hermes-data/SOUL.md` to customize the agent's personality. Changes take effect on the next message — no restart needed.

## How Tool Calls Work

Hermes emits inline markers in its response stream when using tools:

```
`💻 ls -la`          → terminal command
`🔎 search query`    → file/web search
`📖 /path/to/file`   → reading a file
`✏️ content`          → writing a file
`🧠 +memory: fact`   → saving to memory
`🌐 https://url`     → browser navigation
`👥 delegate task`   → spawning a sub-agent
```

The gateway parses these markers from the SSE stream, emits structured action events for the OpenVoiceUI actions panel, and strips them from the text sent to TTS so the user only hears the clean response.

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
  plugin.json              Manifest (gateway type, container spec, routes)
  gateway.py               HermesGateway + HermesBridgeGateway classes
  pages/hermes.html        Agent Skills dashboard
  routes/hermes.py         /api/hermes/* proxy endpoints
  profiles/hermes-agent.json   Agent profile (gateway_id: hermes)
  README.md                This file
```

## Environment Variables

Set these on the **OpenVoiceUI** container (not Hermes):

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_HOST` | `hermes` | Hostname of the Hermes container |
| `HERMES_PORT` | `18790` | API port on the Hermes container |
| `HERMES_TIMEOUT` | `300` | Request timeout in seconds |

## Troubleshooting

**"Gateway 'hermes' not registered"** — The plugin isn't loaded. Check that `hermes-agent/` is in your `plugins/` directory and restart OpenVoiceUI.

**"Cannot connect to Hermes Agent"** — The Hermes container isn't reachable. Check:
- Container is running: `docker ps | grep hermes`
- Same Docker network as OpenVoiceUI
- Hostname is set: `--hostname hermes` or `hostname: hermes` in compose

**Tools not showing in actions panel** — Make sure you're using the "Hermes Agent" profile (Admin > Agents), not the default OpenClaw profile.

**Slow first response** — Cold start takes 30-60s while Hermes initializes. Subsequent responses are 2-10s depending on the model and task complexity.

## Version Compatibility

| Plugin Version | Hermes Version | Status |
|---------------|---------------|--------|
| 1.0.0 | v0.6.0 | Tested, stable |
| 1.0.0 | v0.8.0 | Untested — may work |

## License

MIT — same as Hermes Agent and OpenVoiceUI.
