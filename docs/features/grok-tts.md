# Grok / xAI TTS

Cloud text-to-speech via xAI (`POST https://api.x.ai/v1/tts`).

**Single source of truth:** implement and fix TTS logic in `tts_providers/grok_provider.py` (`GrokProvider`).
`providers/tts/grok_provider.py` (`GrokTTSProvider`) is a thin registry adapter only — keep it in sync by delegating, do not fork HTTP client code.

Higher-level: [VISION.md](../../VISION.md) · [ARCHITECTURE.md](../../ARCHITECTURE.md) · [SECURITY.md](../../SECURITY.md) (env-only keys).

## Enable (5-line quick start)

```bash
# 1 — key from console.x.ai (never commit real keys)
export XAI_API_KEY=your-xai-api-key
# 2 — optional presence check (exit 0 even without keys; values redacted)
bash scripts/check-runtime.sh
# 3 — unit tests (mocked HTTP; no live xAI)
pytest -q tests/test_grok_tts_provider.py
```

1. Get an API key from [console.x.ai](https://console.x.ai/team/default/api-keys).
2. Set in `.env` (placeholder only in git):

```bash
XAI_API_KEY=your-xai-api-key
```

3. Select provider id **`grok`** in admin Provider Config, or call:

```python
from tts_providers import get_provider

provider = get_provider("grok")
audio_mp3 = provider.generate_speech(
    "Hello from OpenVoiceUI.",
    voice="eve",
    language="en",
)
```

## Request shape

| Field | Required | Notes |
|-------|----------|--------|
| `text` | yes | Max 15,000 chars; [speech tags](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech#speech-tags) supported |
| `voice_id` | no | Default **`eve`** |
| `language` | yes in API; we default **`en`** | BCP-47 or `auto` |

Optional: `speed` (0.7–1.5), `output_format`, `text_normalization`.

## Voices (agents)

Documented public roster shipped in-repo (non-exhaustive; live list via `GET /v1/tts/voices`):

- **`eve`** — default for agents and UI
- **`ara`**, **`rex`**, **`sal`**, **`leo`**

Do **not** commit private Team custom voice IDs into the repository. Operators may pass clone IDs at runtime after creating them in console / Custom Voices API.

## Registry adapter

- Canonical: `tts_providers/grok_provider.py` (`GrokProvider`)
- ADR registry: `providers/tts/grok_provider.py` (`GrokTTSProvider`, id `grok`)
- Config metadata: `tts_providers/providers_config.json` → `providers.grok`

## Ops notes

- Response body is **raw audio** (default MP3 24 kHz / 128 kbps) unless `with_timestamps` is used (JSON — not used by the default provider path).
- Health signal: list voices endpoint when the key is set.
- Official docs: [Text to Speech](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech)

## Tests

```bash
pytest -q tests/test_grok_tts_provider.py
XAI_API_KEY= pytest -q tests/test_grok_tts_provider.py  # missing key paths must still pass (mocked)
```

Mocks only — no live xAI network in CI. Never print real secrets in test output.

## Voice tech radar (manual, no cron required)

```bash
python3 scripts/voice_tech_radar.py --help
python3 scripts/voice_tech_radar.py          # writes docs/radar/YYYY-MM-DD.md
python3 scripts/voice_tech_radar.py --stdout  # dry-run to terminal
```

Exit code is always 0 (ops-friendly).
