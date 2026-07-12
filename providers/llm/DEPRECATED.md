# DEPRECATED — providers/llm/

**Status:** Deprecated as of 2026-07-11 (WO-1.3). **Not deleted** (house rule:
never delete). Do **not** build new features on this package.

## Why

LLM routing in OpenVoiceUI belongs to the **gateway / framework layer**, not to
an OVU-side LLM provider registry:

- The real LLM choice lives in `openclaw.json` (`agents.defaults.model.primary /
  fallbacks`), managed by the gateway `config.patch` RPC
  (`routes/admin.py` → `PUT /api/admin/ai-config`, and now
  `OpenClawGateway.configure()`).
- Alternate frameworks (Hermes, future gateways) each own their own model
  selection through `GatewayBase.get_config_schema()` / `configure()`.
- The provider **catalog** (what the admin panel renders) is
  `config/ai-providers.json` (loaded by `services/ai_providers.py`), surfaced
  via `GET /api/services/catalog`.

`providers/llm/` (`zai_provider.py`, `clawdbot_provider.py`, `base.py`) has
**zero production call sites**. It is still imported by
`providers.registry.autodiscover()` (driven by `config/providers.yaml`), which
only registers the classes — it routes nothing.

## What IS wired

Only the **STT** side of `providers/` is load-bearing:
- `providers/stt/*` registers webspeech / whisper / external into the registry.
- `autodiscover()` runs at startup (`server.py`) so the STT Service Catalog
  reads ids from the same registry that `/api/stt/*` uses.

## If you need to add an LLM

Add it to `config/ai-providers.json` (catalog) and wire the actual routing
through the active framework's `configure()` — not here.
