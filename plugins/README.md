# OpenVoiceUI Plugins

Plugins let contributors add alternative LLM backends, TTS providers, and tools
without touching the core repo. Drop a folder here, restart — it's live.

---

## Gateway Plugins

A gateway is the LLM backend. The built-in gateway is OpenClaw.
You can add Claude API, LangChain, AutoGen, Ollama, or anything else as a plugin.

### Quick start

```bash
# Clone any community gateway plugin into this directory
git clone https://github.com/someone/openvoiceui-claude-gateway plugins/claude-gateway

# Or copy the reference implementation and build from it
cp -r plugins/example-gateway plugins/my-gateway
```

Restart the server — it scans `plugins/*/plugin.json` on startup.

---

### Plugin structure

```
plugins/
  my-gateway/
    plugin.json     ← required manifest
    gateway.py      ← required: class Gateway(GatewayBase)
    requirements.txt  ← optional: pip install -r plugins/my-gateway/requirements.txt
    README.md         ← optional but recommended
```

### plugin.json

```json
{
  "id": "my-gateway",
  "name": "My LLM Gateway",
  "version": "1.0.0",
  "provides": "gateway",
  "gateway_class": "Gateway",
  "requires_env": ["MY_API_KEY"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Unique slug used as the routing key |
| `provides` | yes | Must be `"gateway"` for gateway plugins |
| `gateway_class` | no | Class name in gateway.py (default: `"Gateway"`) |
| `requires_env` | no | Env vars that must be set; plugin skipped if any are missing |

### gateway.py

```python
import os, queue
from services.gateways.base import GatewayBase

class Gateway(GatewayBase):

    gateway_id = "my-gateway"   # must match plugin.json "id"
    persistent = False          # True = you maintain a live connection

    def is_configured(self) -> bool:
        return bool(os.getenv("MY_API_KEY"))

    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs) -> None:
        if captured_actions is None:
            captured_actions = []
        try:
            # --- call your LLM here ---
            response_text = call_my_llm(message)

            # stream tokens (optional but recommended for low latency TTS)
            for token in response_text.split():
                event_queue.put({'type': 'delta', 'text': token + ' '})

            # final event — always required
            event_queue.put({
                'type': 'text_done',
                'response': response_text,
                'actions': captured_actions,
            })
        except Exception as exc:
            event_queue.put({'type': 'error', 'error': str(exc)})
```

### Event protocol

`stream_to_queue()` must put these events onto the queue in order:

| Event | When | Fields |
|-------|------|--------|
| `handshake` | optional, before first delta | `ms: int` (connection latency) |
| `delta` | one or more, during streaming | `text: str` |
| `action` | optional, on tool calls | `action: dict` |
| `text_done` | **required**, once at the end | `response: str\|None`, `actions: list` |
| `error` | instead of text_done on failure | `error: str` |

**`deferred` (optional, on `text_done`).** Send `{'type': 'text_done', 'response': '', 'deferred': True, 'actions': [...]}`
when your gateway ends the turn **on purpose** with nothing to say yet — the agent still has the words and
will deliver them later through its own proactive push channel. OpenVoiceUI then ends the turn silently:
no empty-response retry, no fallback LLM call, no substituted filler line, no TTS, and no empty assistant
turn saved; the browser gets `{'type': 'no_audio', 'deferred': True}` and re-enables the mic. Do **not**
use it to report a failure — an empty `text_done` *without* `deferred` still runs the empty-response
recovery path, which is what a failure should get. `deferred` is ignored when `response` is non-empty or
`error` is set.

### Routing via profile

To use your gateway, add `gateway_id` to a profile's `adapter_config`:

```json
{
  "id": "my-agent",
  "name": "My Agent",
  "adapter": "clawdbot",
  "adapter_config": {
    "gateway_id": "my-gateway",
    "sessionKey": "my-session-1"
  },
  ...
}
```

Or pass `gateway_id` directly in the API request body:
```json
{"message": "hello", "gateway_id": "my-gateway"}
```

### Inter-agent communication

One gateway can call another using `gateway_manager.ask()`:

```python
from services.gateway_manager import gateway_manager

# Inside your gateway's stream_to_queue():
summary = gateway_manager.ask("openclaw", "Summarise this: " + long_text, session_key)
```

---

## Installing plugin dependencies

```bash
pip install -r plugins/my-gateway/requirements.txt
# or
venv/bin/pip install -r plugins/my-gateway/requirements.txt
```

Dependencies are not auto-installed — add them to your setup instructions.

---

## Community plugins

| Plugin | Provides | Repo |
|--------|----------|------|
| *(add yours here via PR to the main repo)* | | |

---

## This directory is gitignored

`plugins/*/` is gitignored so your installed plugins stay local and don't
pollute the base repo. The `example-gateway/` and this README are tracked
as references.

To share a plugin, publish it as its own GitHub repo. Others install it with:
```bash
git clone https://github.com/you/openvoiceui-my-gateway plugins/my-gateway
```
