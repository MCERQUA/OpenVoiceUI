# OpenVoiceUI Architecture

This document describes how OpenVoiceUI is put together, so you can find the file you need
and extend the system without reverse-engineering it first.

It is written against the code in this repository. Where a subsystem has a deeper reference
doc, it is linked. For the conceptual positioning of the project, see [VISION.md](VISION.md).
For AI-coding-agent orientation, see [AGENTS.md](AGENTS.md). For contribution mechanics, see
[CONTRIBUTING.md](CONTRIBUTING.md).

---

## Table of Contents

1. [The one-paragraph version](#the-one-paragraph-version)
2. [Runtime topology](#runtime-topology)
3. [Repository layout](#repository-layout)
4. [Backend: Flask](#backend-flask)
5. [Frontend: vanilla JS ES modules](#frontend-vanilla-js-es-modules)
6. [The gateway layer (agent brain)](#the-gateway-layer-agent-brain)
7. [The provider system (LLM / STT / TTS)](#the-provider-system-llm--stt--tts)
8. [How to add a new provider](#how-to-add-a-new-provider)
9. [The plugin system](#the-plugin-system)
10. [The canvas page system](#the-canvas-page-system)
11. [Faces](#faces)
12. [Profiles](#profiles)
13. [Configuration](#configuration)
14. [Tests](#tests)

---

## The one-paragraph version

OpenVoiceUI is a Python **Flask** server plus a **vanilla-JavaScript ES-module** frontend
(no build step, no framework). The Flask server owns HTTP/WebSocket endpoints, TTS
synthesis, canvas page storage, profiles, plugins, and the connection to the **agent
gateway**. The frontend owns the microphone, speech recognition, the animated face, the
canvas iframe, the music player, and the settings/admin UI. The agent itself does *not*
live in this repo — it lives behind a gateway (OpenClaw by default), and OpenVoiceUI is the
voice-and-display shell in front of it.

---

## Runtime topology

```
  Browser
   |  microphone -> STT (Web Speech API in-browser, or a server STT provider)
   |
   |  POST /api/conversation            (SSE stream of tokens + events)
   |  POST /api/tts/generate            (audio bytes back)
   |  GET  /pages/<page>.html           (canvas pages, rendered in an iframe)
   v
  Flask server (default port 5001)
   |    server.py  -> app.py create_app() -> routes/*.py blueprints
   |
   |  GatewayManager.stream_to_queue(...)
   v
  Gateway  (services/gateways/openclaw.py by default, persistent WebSocket)
   |
   v
  Agent runtime (OpenClaw / Hermes / your own plugin gateway)
   |
   v
  Whatever LLM that runtime is configured to use
```

Two things to internalise:

- **The LLM choice does not live in OpenVoiceUI.** It lives in the agent runtime behind the
  gateway. See [The gateway layer](#the-gateway-layer-agent-brain).
- **Speech in and speech out are separate paths.** STT usually happens in the browser;
  TTS always happens on the server (so API keys stay server-side).

---

## Repository layout

```
OpenVoiceUI/
  server.py               Entry point: builds the app, registers every blueprint,
                          hosts the WebSocket routes and a few standalone endpoints
  app.py                  create_app() Flask application factory (CORS, Limiter,
                          Sock, ProxyFix, upload limits). Returns (app, sock).
  index.html              Thin shell. Loads CSS + a few global scripts, then
                          <script type="module" src="src/app.js">

  routes/                 Flask blueprints, one file per feature area
  services/               Backend service layer (gateways, TTS, auth, plugins, paths...)
  providers/              Provider ABCs + registry (LLM / STT / TTS)
  tts_providers/          Canonical TTS provider implementations
  plugins/                Drop-in plugins (gateways, pages, faces, routes)
  config/                 YAML/JSON configuration + loader with env overrides
  profiles/               Agent profile JSON files + manager.py + schema.json
  prompts/                System prompt files (hot-reloaded)
  default-pages/          Canvas pages shipped with the product
  default-faces/          Custom-face HTML template
  default-styles/         Canvas design-system presets
  src/                    Frontend (vanilla ES modules; no bundler)
  static/                 Static assets (icons, favicon, service worker assets)
  sounds/                 Soundboard audio
  runtime/                Gitignored runtime data (canvas-pages, uploads, music,
                          transcripts, usage.db, canvas-manifest.json, ...)
  tests/                  pytest suite
  docs/                   Long-form documentation site sources
  deploy/                 Deployment assets
```

---

## Backend: Flask

### Entry point and factory

`server.py` is the process entry point. It loads `.env`, then:

```python
from app import create_app
app, sock = create_app()
```

`app.py::create_app()` builds the Flask app and initialises CORS, `flask_limiter`,
`flask_sock` (WebSockets), `ProxyFix`, and the 100 MB upload limit. It returns the
`(app, sock)` tuple so `server.py` can keep using module-level `@app.route` and
`@sock.route` decorators.

`server.py` then imports and registers every blueprint, runs the provider registry
autodiscovery, and finally loads plugins:

```python
from providers.registry import registry as _provider_registry
_provider_registry.autodiscover()

from services.plugins import load_plugins
load_plugins(app)
```

Order matters: plugins register their own blueprints, so they load last.

### `routes/` — blueprints

One module per feature area. Each defines a `*_bp` blueprint that `server.py` registers.

| File | Responsibility |
|---|---|
| `admin.py` | Admin API: agent config, server stats, panel data |
| `airadio_bridge.py` | Bridge to an external AI-radio service |
| `browse.py` | Proxy to the co-browsing service |
| `canvas.py` | Canvas pages, canvas manifest, canvas context, page versions, page/iframe proxies |
| `canvas_styles.py` | Canvas design-system (style) picker API |
| `chat.py` | Lightweight text completion endpoint used by canvas pages |
| `chatgpt_import.py` | Import a ChatGPT data export |
| `conversation.py` | The main `/api/conversation` turn endpoint plus the whole `/api/tts/*` surface |
| `custom_faces.py` | Custom face HTML pages stored in `runtime/faces/` |
| `elevenlabs_hybrid.py` | ElevenLabs Conversational + gateway hybrid bridge |
| `fal.py` | fal.ai image-generation proxy (keeps the key server-side) |
| `greetings.py` | `greetings.json` read/write |
| `icons.py` | Icon library + AI icon generation |
| `identity.py` | `/api/identity/whoami` — resolved identity for the logged-in user |
| `image_gen.py` | Image-generation proxy (Gemini / Imagen / HuggingFace) |
| `instructions.py` | Live editor for agent instruction files |
| `maps.py` | Maps config + server-side directions proxy |
| `message_classifier.py` | Classifies an incoming message into a routing lane before dispatch |
| `music.py` | Music library, playlists, playback state |
| `onboarding.py` | Onboarding state read/write |
| `pi.py` | Raspberry-Pi-optimised page serving |
| `plugins.py` | Plugin management API (list / install / uninstall / assets / config) |
| `profiles.py` | Agent profile CRUD + activation |
| `registry.py` | Pinokio registry check-in endpoint |
| `report_issue.py` | User-submitted bug/feedback reports |
| `services.py` | Admin-only Service Catalog aggregating gateway/STT/TTS/provider config |
| `static_files.py` | Static asset serving |
| `story.py` | Story engine scene generation |
| `suno.py` | Suno song generation |
| `theme.py` | Server-side theme persistence |
| `transcripts.py` | Listen-mode transcript storage |
| `vault.py` | Credential vault: keys, OAuth flows, sync, connection tests |
| `vision.py` | Camera / vision / face recognition |
| `workspace.py` | Read-only browser over the agent workspace directory |

### `services/` — the service layer

| File | Responsibility |
|---|---|
| `gateway_manager.py` | `GatewayManager` singleton: registers built-in gateways, discovers plugin gateways, routes `stream_to_queue()`, `ask()`, `send_steer()`, `list_gateways()` |
| `gateways/base.py` | `GatewayBase` — the abstract contract every gateway implements |
| `gateways/openclaw.py` | The default gateway: persistent WebSocket to OpenClaw with reconnect/backoff |
| `gateways/compat.py` | Version/protocol compatibility helpers and event matchers for the OpenClaw protocol |
| `gateway.py` | Backwards-compatibility shim re-exporting from the above |
| `tts.py` | Unified TTS service — the single entry point for speech generation |
| `tts_fallback.py` | Loads the TTS fallback chain + voice-gender map from `config/tts-fallback.json` |
| `speech_normalizer.py` | Cleans LLM text before TTS, rules from `config/speech_normalization.yaml` |
| `ai_providers.py` | Loads the LLM provider *catalog* from `config/ai-providers.json` (what the admin panel renders) |
| `plugins.py` | Plugin discovery, install/uninstall/update, and `load_plugins(app)` |
| `canvas_styles.py` | Per-install canvas design-system store + renderer |
| `canvas_versioning.py` | Automatic version history for canvas pages (`.versions/` subdir) |
| `paths.py` | Canonical path constants — **always import paths from here** |
| `auth.py` | Clerk JWT verification (Bearer header or `__session` cookie); optional |
| `identity.py` | Maps an authenticated user to an identity the conversation route injects as context |
| `vault.py` | Credential vault — single source of truth for API keys and OAuth tokens |
| `health.py` | `/health/live` and `/health/ready` probes |
| `db_pool.py` | SQLite connection pool with WAL enabled |
| `ui_state.py` | Writes agent-facing UI state to a durable workspace markdown file |
| `memory_client.py` | Read access to the agent's memory/search store |
| `update_manager.py` | Update checking and controlled self-update |
| `chatgpt_parser.py` | Parses the tree-structured ChatGPT export format |
| `jambot_books_hook.py` | Optional hook that records provider calls for usage accounting |

### Path constants

Every runtime path is defined once in `services/paths.py`. Do not hardcode paths.

```python
from services.paths import (
    APP_ROOT, RUNTIME_DIR, UPLOADS_DIR, CANVAS_PAGES_DIR,
    CANVAS_MANIFEST_PATH, MUSIC_DIR, TRANSCRIPTS_DIR, DB_PATH,
    WORKSPACE_DIR, SOUNDS_DIR, STATIC_DIR, DEFAULT_PAGES_DIR,
)
```

Several are env-overridable (`CANVAS_PAGES_DIR`, `OVUI_DB_PATH`, `WORKSPACE_DIR`) so a
container deployment can bind-mount them.

---

## Frontend: vanilla JS ES modules

There is **no build step and no framework**. `index.html` is a thin shell that loads a few
global scripts and then a single module entry point:

```html
<script type="module" src="src/app.js?v=29"></script>
```

`src/app.js` imports `AppShell.inject()` to build the DOM, starts the update checker, wires
bridges, and then contains the bulk of the live application wiring (voice loop, streaming
response handling, agent tag dispatch). It is large by design — it is the shell's runtime.

### `src/` layout

```
src/
  app.js              Application entry point and main runtime wiring
  core/               Framework-free primitives
    EventBus.js         Pub/sub: on/off/once/emit. The cross-component contract.
    EventBridge.js      The adapter <-> shell boundary (AgentEvents / AgentActions)
    VoiceSession.js     Voice session state machine
    EmotionEngine.js    Maps agent signals to face moods
    Config.js           Frontend config access
    constants.js        Shared constants
  shell/              Bridges that connect an active adapter's capabilities to UI subsystems
    orchestrator.js     AgentOrchestrator: register adapters, switch modes, show/hide UI
                        based on the adapter's declared capabilities
    adapter-registry.js Adapter ID -> module path map + cached dynamic import
    canvas-bridge.js    Canvas show/update commands
    face-bridge.js      Face mood/lifecycle
    music-bridge.js     Music playback commands
    sounds-bridge.js    Soundboard
    transcript-bridge.js Transcript + action console
    waveform-bridge.js  Audio visualiser feed
    camera-bridge.js    Camera/vision
    caller-bridge.js    Phone-filter audio effect
    commercial-bridge.js Commercial break triggers
    airadio-bridge.js   AI-radio command dispatch
    profile-discovery.js Profile listing/selection
  adapters/           One module per agent/voice framework
    ClawdBotAdapter.js  Default adapter (gateway-backed)
    hume-evi.js, xai-realtime.js, elevenlabs-classic.js, elevenlabs-hybrid.js
    _template.js        Copy this to write a new adapter
  providers/          Browser-side STT and TTS playback
    WebSpeechSTT.js     Web Speech API STT + WakeWordDetector
    GroqSTT.js, DeepgramSTT.js, DeepgramStreamingSTT.js, ExternalSTT.js
    TTSPlayer.js        Audio playback of server-generated speech
    tts/                Browser-side TTS provider wrappers (BaseTTSProvider.js,
                        SupertonicProvider.js, HumeProvider.js, index.js)
  face/               Face implementations
    BaseFace.js         Abstract base (init / setMood / destroy), VALID_MOODS
    EyeFace.js          Animated eyes
    HaloSmokeFace.js    Reactive halo/smoke orb
    manifest.json       Face registry: id, name, module path, preview, moods, features
    previews/           SVG previews
  features/           Self-contained feature modules
    MusicPlayer.js, Soundboard.js
  ui/                 UI surfaces
    AppShell.js         inject() — builds the application DOM
    SessionControl.js, ProfileSwitcher.js, UpdateBanner.js
    face/               FaceRenderer.js, FacePicker.js, CustomFaceLoader.js
    settings/           SettingsPanel.js/.css, TTSVoicePreview.js, PlaylistEditor.js
    themes/             ThemeManager.js
    visualizers/        BaseVisualizer.js, PartyFXVisualizer.js/.css
  styles/             base.css, theme-dark.css, face.css, pi-overrides.css
  admin.html          Admin panel page
```

### The two frontend contracts

**EventBus** (`src/core/EventBus.js`) is the intra-frontend pub/sub:

```js
import { eventBus } from './core/EventBus.js';
const unsub = eventBus.on('tts:start', (data) => { /* ... */ });
eventBus.emit('face:mood', { mood: 'thinking' });
```

**EventBridge** (`src/core/EventBridge.js`) is the boundary between an *adapter* and the
shell. Adapters talk to the outside world **only** through the bridge — they never touch
the DOM directly. The rules are stated at the top of `src/adapters/_template.js`:

1. Communicate with the outside world only through the bridge.
2. Manage your own audio, WebSocket, and SDK internally.
3. Release all resources in `destroy()`.
4. Never import from another adapter.

### Adapters and capabilities

An adapter declares a `capabilities` array. `src/shell/orchestrator.js` maps capabilities
to UI element IDs (`CAPABILITY_UI_MAP`) and shows/hides controls accordingly, then connects
only the bridges that adapter supports. Adding an adapter:

1. Copy `src/adapters/_template.js` to `src/adapters/my-adapter.js`.
2. Implement `init`, `start`, `stop`, `destroy` and declare `capabilities`.
3. Add an entry to `ADAPTER_PATHS` in `src/shell/adapter-registry.js`.
4. Create a profile in `profiles/` with `"adapter": "my-adapter"`.

---

## The gateway layer (agent brain)

A **gateway** is the connection to whatever actually runs the agent. `GatewayBase`
(`services/gateways/base.py`) is the contract:

```python
class GatewayBase:
    gateway_id: str = "unnamed"
    persistent: bool = False          # True = you hold a live connection

    def is_configured(self) -> bool: ...
    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs) -> None: ...
    def is_healthy(self) -> bool: ...
    def check_health(self) -> tuple: ...
    def get_config_schema(self) -> dict: ...
    def configure(self, partial: dict) -> dict: ...
    def restart_scope(self) -> str: ...
    def shutdown(self) -> None: ...
```

`stream_to_queue()` is the hot path: it pushes event dicts (streaming text chunks,
`text_done`, `error`, tool/action events) into a `queue.Queue` that the conversation route
drains into the browser's SSE stream.

`services/gateway_manager.py` holds the singleton:

```python
from services.gateway_manager import gateway_manager

gateway_manager.stream_to_queue(event_queue, message, session_key,
                                captured_actions, gateway_id='openclaw')
response = gateway_manager.ask('my-gateway', 'Summarise this: ...', session_key)
```

At import time it runs `_load_builtin_gateways()` (registers OpenClaw when
`CLAWDBOT_AUTH_TOKEN` is set) and `_load_plugins()` (scans `plugins/*/plugin.json` for
`"provides": "gateway"`). Unknown or unregistered `gateway_id` values fall back to
`openclaw`.

Profiles pick the gateway via `adapter_config.gateway_id`. Multiple gateways can be
registered at once, and `ask()` lets one agent delegate to another.

**Because the gateway owns the agent, the LLM choice is not OpenVoiceUI's concern.** See
`providers/llm/DEPRECATED.md` — the `providers/llm/` package has no production call sites
and exists only for registry stability. The provider *catalog* the admin panel renders is
`config/ai-providers.json` via `services/ai_providers.py`.

---

## The provider system (LLM / STT / TTS)

Two related mechanisms exist. Know which one you are touching.

### 1. `providers/` — the ABC + registry pattern

`providers/base.py` defines the common contract:

```python
class BaseProvider(ABC):
    def __init__(self, config: Dict[str, Any] = None): ...
    @abstractmethod
    def is_available(self) -> bool: ...
    @abstractmethod
    def get_info(self) -> Dict[str, Any]: ...   # must include 'name' and 'status'
    def get_config(self, key, default=None): ...
```

plus `ProviderError`, `ProviderUnavailableError`, `ProviderGenerationError`.

Per-type abstract subclasses add the real work method:

| Type | Base class | Required method | Result type |
|---|---|---|---|
| LLM | `providers/llm/base.py::LLMProvider` | `generate()`, `generate_stream()` | `LLMResponse` |
| STT | `providers/stt/base.py::STTProvider` | `transcribe(audio_data, language=None, **kw)` | `TranscriptionResult` |
| TTS | `providers/tts/base.py::TTSProvider` | `generate_speech(text, **kw)`, `list_voices()` | `bytes` / `list[str]` |

`providers/registry.py` is the singleton registry:

```python
from providers.registry import registry, ProviderType

registry.register(ProviderType.STT, 'whisper', WhisperProvider)
stt = registry.get_provider(ProviderType.STT)            # configured default
stt = registry.get_provider(ProviderType.STT, 'whisper') # explicit
registry.list_providers(ProviderType.STT)                # metadata, sorted by priority
```

Convenience wrappers exist: `get_llm_provider()`, `get_tts_provider()`, `get_stt_provider()`.

**Auto-discovery.** `registry.autodiscover()` reads `config/providers.yaml`, imports every
module listed under each type's `modules:` key (so their `register()` calls fire), and
caches the YAML so `get_provider()` can merge config. `${ENV_VAR}` placeholders in the YAML
are resolved from the environment at build time via `_resolve_env_vars()`. Config merge
order is: static config passed to `register()`, then YAML config on top.

**What is actually load-bearing today:** the `stt:` section. `server.py` calls
`autodiscover()` at startup so the Service Catalog reads STT ids from the same registry
`/api/stt/*` uses. The `llm:` section is deprecated (see above) and the `tts:` section is
informational — live TTS runs through `tts_providers/`. This is documented in the header of
`config/providers.yaml` itself; trust that header over any other summary.

### 2. `tts_providers/` — the canonical TTS implementations

Live speech synthesis uses this package, not `providers/tts/`. (`providers/tts/*` are thin
adapter wrappers that exist so the registry and its tests have TTS entries.)

```python
from tts_providers import get_provider, list_providers, invalidate

provider = get_provider()            # configured default_provider
provider = get_provider('supertonic')
audio = provider.generate_speech("Hello world", voice='M1')   # -> bytes
```

Structure:

- `tts_providers/base_provider.py` — `TTSProvider` ABC with three abstract methods
  (`generate_speech`, `list_voices`, `get_info`) plus concrete helpers
  (`is_available`, `validate_text`, `validate_voice`, `get_default_voice`), the
  `TTSVoice` / `TTSProviderInfo` dataclasses, and the error hierarchy
  (`TTSProviderError`, `TTSGenerationError`, `TTSConfigurationError`,
  `TTSVoiceNotFoundError`).
- `tts_providers/__init__.py` — the `_PROVIDERS` id-to-class dict, plus a
  process-lifetime instance cache and an mtime-checked config cache. `invalidate()`
  drops both when config is rewritten.
- `tts_providers/providers_config.json` — the catalog: display name, cost, quality,
  latency, features, `requires_api_key`, languages, notes, `status`, and the
  `default_provider` key.
- One `*_provider.py` per backend.

`list_providers(probe=False)` builds metadata from the config catalog **without**
instantiating anything or calling a vendor API — that is the health/readiness path.
`probe=True` instantiates and calls `get_info()` for live detail; use it only for
admin/UI endpoints. Never make a health probe hit a third-party API.

Above this sits `services/tts.py` (unified entry point) and `services/tts_fallback.py`
(fallback chain and voice-gender map from `config/tts-fallback.json`), with
`services/speech_normalizer.py` cleaning text before synthesis.

---

## How to add a new provider

### Adding a TTS provider (most common)

1. **Implement it** in `tts_providers/myprovider_provider.py`:

   ```python
   from .base_provider import TTSProvider, TTSGenerationError

   class MyProvider(TTSProvider):
       def __init__(self):
           self._api_key = os.getenv("MYPROVIDER_API_KEY")
           self._status = "active" if self._api_key else "inactive"

       def generate_speech(self, text: str, **kwargs) -> bytes:
           self.validate_text(text)
           voice = kwargs.get("voice") or self.get_default_voice()
           try:
               return call_my_api(text, voice)     # -> raw WAV/MP3 bytes
           except Exception as e:
               raise TTSGenerationError("myprovider", str(e))

       def list_voices(self) -> list[str]:
           return ["voice-a", "voice-b"]

       def get_info(self) -> dict:
           return {
               "name": "My Provider",
               "status": self._status,
               "description": "One-line description",
               "capabilities": {
                   "streaming": False, "ssml": False,
                   "custom_voices": False, "languages": ["en"],
               },
           }
   ```

   Keep construction cheap. The instance is cached for the process lifetime and
   `get_info()` is called on catalog paths — do not open sockets in `__init__`.

2. **Register the class** in `tts_providers/__init__.py`: import it and add it to the
   `_PROVIDERS` dict (`'myprovider': MyProvider`) and to `__all__`.

3. **Add catalog metadata** to `tts_providers/providers_config.json` under `providers`:
   `name`, `provider_id`, `cost_per_minute`, `quality`, `latency`, `features`,
   `requires_api_key`, `status`, `description`, `languages`, `notes`.

4. **Declare env vars** in `.env.example` with a comment.

5. **Test it** in isolation — TTS providers are unit-testable without a gateway. See
   `tests/test_tts_providers_extended.py`.

6. Update the TTS provider table in `README.md`.

### Adding an STT provider

1. Create `providers/stt/myprovider_provider.py`, subclass
   `providers/stt/base.py::STTProvider`, implement `transcribe()` returning a
   `TranscriptionResult`, and override `get_info()`.
2. At the bottom of the module, register it:

   ```python
   from providers.registry import registry, ProviderType
   registry.register(ProviderType.STT, 'myprovider', MyProviderSTT)
   ```

3. Import the module from `providers/stt/__init__.py` (with a `# noqa: F401` comment)
   so a plain package import also registers it.
4. Add the module path under `stt.modules` in `config/providers.yaml`, and a
   `stt.providers.myprovider` block with `name`, `priority`, and any
   `${ENV_VAR}` placeholders you need.
5. Add env vars to `.env.example`.

If the recognition runs in the browser instead of on the server, add a module under
`src/providers/` following the shape of `src/providers/WebSpeechSTT.js` (which exports both
the STT class and a matching `WakeWordDetector`).

### Adding an LLM

Do **not** add it under `providers/llm/`. LLM routing belongs to the agent runtime behind
the gateway. Either:

- add the model to `config/ai-providers.json` so it appears in the admin catalog and let
  the active gateway's `configure()` route it, or
- write a [gateway plugin](#gateway-plugins) that talks to your backend directly.

---

## The plugin system

There are two plugin flavours and they are loaded by different code. Both are discovered
from `plugins/*/plugin.json` at startup, and neither requires a rebuild — drop the folder
in and restart.

### Gateway plugins

Loaded by `services/gateway_manager.py::_load_plugins()`. Selected by
`"provides": "gateway"`.

```
plugins/my-gateway/
  plugin.json          {"id","name","version","provides":"gateway",
                        "gateway_class":"Gateway","requires_env":[...]}
  gateway.py           class Gateway(GatewayBase)
  requirements.txt     optional
  README.md            optional
```

The manager skips the plugin if any `requires_env` var is missing, imports `gateway.py`,
looks up `gateway_class` (default `"Gateway"`), verifies it subclasses `GatewayBase`,
instantiates it, and registers it under its `gateway_id`. Route traffic to it by setting
`"gateway_id": "my-gateway"` in a profile's `adapter_config`.

`plugins/example-gateway/` is a working echo reference implementation, and
`plugins/README.md` documents the full event protocol.

### Feature plugins (pages, routes, faces, profiles, lore)

Loaded by `services/plugins.py::load_plugins(app)`, called from `server.py` after all
built-in blueprints are registered. It scans `PLUGIN_DIR`, reads each `plugin.json`, and:

1. **Routes** — for each entry in `routes[]` (`{module, blueprint, prefix}`), it imports
   the module by file path and calls `app.register_blueprint(bp)` using the named
   blueprint object.
2. **Pages** — for each entry in `pages[]` (`{file, name, title, icon}`), it copies the
   HTML into `CANVAS_PAGES_DIR` **only if the destination does not already exist** (never
   overwrites a user's edited page), then calls
   `routes.canvas.add_page_to_manifest(page_name, title, content=...)` so the page joins
   the canvas manifest with an icon and category.
3. **Faces** — if `faces[]` is present, it registers a static blueprint serving the whole
   plugin directory at `/plugins/<id>/`. The frontend fetches `/api/plugins/assets` on
   page load and injects the returned scripts and stylesheets.
4. **Profiles** — copies each `profiles[]` file into the runtime profiles dir if absent.
5. **Lore** — copies knowledge files declared under `lore` into the agent workspace (if
   writable) so the agent can read them, namespaced by plugin id.
6. Stores the manifest in the in-memory `_registry` that `routes/plugins.py` serves.

Manifest fields observed in the shipped plugins: `id`, `name`, `version`, `description`,
`author`, `type`, `license`, `requires`, `repository`, `install`, `pages`, `routes`,
`faces`, `profiles`, `lore`, `requires_env`, `credentials`, `lifecycle`.

`services/plugins.py` also handles install/uninstall/update from a local catalog directory
or a remote GitHub registry (`PLUGIN_REGISTRY_URL`), and can run lifecycle hooks for
plugins that need host-side provisioning.

**A plugin page becomes a canvas page** by the copy-plus-manifest step in (2) — after that
it is an ordinary canvas page, served at `/pages/<name>.html`, listed in the manifest, and
addressable by the agent.

---

## The canvas page system

Canvas pages are plain HTML files rendered in an iframe inside the shell. They are the
"show" half of OpenVoiceUI.

- **Storage:** `CANVAS_PAGES_DIR` (default `runtime/canvas-pages/`, env-overridable).
  Pages shipped with the product live in `default-pages/`.
- **Serving:** `GET /pages/<path>` (`routes/canvas.py::canvas_pages_proxy`). Path
  traversal is blocked by `_safe_canvas_path()`. Related routes serve
  `/images/<path>`, `/canvas-data/<filename>`, and proxy an external URL or dev server.
- **Creation:** `POST /api/canvas/pages`; `POST /api/canvas/update`;
  `POST /api/canvas/show` to display one.
- **Versioning:** `services/canvas_versioning.py` snapshots previous content into a
  `.versions/` subdirectory on every write regardless of source, exposed via
  `/api/canvas/versions/<page_id>` (list, preview, restore).
- **Page data:** `GET/POST /api/canvas/data/<filename>` gives a page server-side JSON
  persistence, with a merge that preserves existing values.
- **Styles:** `services/canvas_styles.py` + `routes/canvas_styles.py` implement a
  design-system picker; presets live in `default-styles/`, and a page records the style it
  was authored under.

### `canvas-manifest.json`

`CANVAS_MANIFEST_PATH` (`runtime/canvas-manifest.json`) is the index of every page. Shape:

```json
{
  "version": 1,
  "last_updated": "...",
  "categories": { "<category>": { "name": "...", "icon": "...", "color": "...", "pages": ["id"] } },
  "pages": {
    "<page-id>": {
      "filename": "page-id.html",
      "display_name": "Page Id",
      "description": "",
      "category": "tools",
      "tags": [],
      "created": "...", "modified": "...",
      "starred": false,
      "is_public": false,
      "is_locked": false,
      "voice_aliases": ["..."],
      "access_count": 0,
      "icon": "...", "style": "..."
    }
  },
  "uncategorized": [],
  "recently_viewed": [],
  "user_custom_order": null
}
```

Key functions in `routes/canvas.py`:

| Function | Purpose |
|---|---|
| `load_canvas_manifest()` | mtime-cached load; returns a deep copy so callers can mutate freely |
| `save_canvas_manifest()` | direct write (bind-mounted files do not support atomic rename) |
| `add_page_to_manifest()` | register or refresh one page; preserves user-customised fields |
| `sync_canvas_manifest()` | reconcile the manifest against files on disk |
| `suggest_category()` | keyword-based category inference |
| `generate_voice_aliases()` | spoken names the agent can use to open the page |
| `extract_page_icon()` | reads the canonical icon from a meta tag in the page HTML |
| `track_page_access()` | bumps `access_count` / recently-viewed |
| `get_canvas_context()` | builds the canvas description injected into the agent's context |

The manifest is protected by `_manifest_lock`; hold it when a load-modify-save must be
atomic. The page HTML is the source of truth for the icon.

### Agent tags

The agent triggers UI behaviour by emitting bracket tags in its text
(`[CANVAS:page-id]`, `[MOOD:happy]`, `[MUSIC_PLAY]`, `[SOUND:name]`, ...). The frontend
extracts and strips them before display, then dispatches the action. There is also a
structured `[CANVAS_ACTION:{...}]` form for driving forms inside a canvas page, extracted
by brace-walking rather than regex because the payload nests. Full list:
[`docs/reference/agent-tags.md`](docs/reference/agent-tags.md).

---

## Faces

A face is the animated presence in the shell.

- `src/face/manifest.json` lists built-in faces: `id`, `name`, `description`, `module`
  path, `preview`, `moods`, `features`, `configurable`.
- `src/face/BaseFace.js` is the abstract base. Extend it and implement `init(container)`,
  `setMood(mood)`, and `destroy()`. `VALID_MOODS` is
  `neutral, happy, sad, angry, thinking, surprised, listening`. Faces emit `face:mood`,
  `face:ready`, and `face:changed` on the EventBus.
- Built-ins: `EyeFace.js` (animated eyes) and `HaloSmokeFace.js` (audio-reactive orb).
- `src/ui/face/` holds `FaceRenderer.js`, `FacePicker.js`, and `CustomFaceLoader.js`.
- Plugins can ship faces via the manifest `faces[]` array; their scripts and CSS are served
  from `/plugins/<id>/` and injected via `/api/plugins/assets`.
- Custom HTML faces live in `runtime/faces/` and are managed by `routes/custom_faces.py`
  (`default-faces/template.html` is the starting point).

---

## Profiles

A profile is a JSON file describing one agent persona end to end: prompt, LLM settings,
voice, STT behaviour, context options, and which adapter/gateway to use.

- Files: `profiles/*.json`, validated against `profiles/schema.json`, managed by
  `profiles/manager.py`, exposed by `routes/profiles.py`.
- The active profile is recorded at `runtime/profiles/.active-profile`.
- Notable sections in `profiles/default.json`: `system_prompt`, `llm` (provider, model,
  parameters, queue mode), `voice` (`tts_provider`, `voice_id`, speed, sentence chunking),
  `stt` (provider, language, silence timeout, wake words, PTT), `context`.
- `adapter` selects the frontend adapter; `adapter_config.gateway_id` selects the backend
  gateway.

`prompts/` holds system prompt files that are hot-reloaded — no restart needed.

---

## Configuration

Three layers, in increasing priority:

1. **`config/default.yaml`** — the defaults: `server.{host,port,debug,threaded}`,
   `gateway.{url,auth_token,session_key}`, `models.*`, `tts.provider`,
   `conversation.{max_history_messages,active_profile}`, `logging.level`, `features.*`.
2. **`config/flags.yaml`** — feature flags, readable via `config.flag('name')`.
3. **Environment variables** — loaded from `.env` by `server.py` at startup.

`config/loader.py` implements the override rules:

```python
from config.loader import config

config.get('server.port')      # 5001
config['tts.provider']
config.flag('use_blueprints')
```

- **Named overrides** (highest priority) via `_ENV_MAP`: `PORT`, `CLAWDBOT_GATEWAY_URL`,
  `CLAWDBOT_AUTH_TOKEN`, `GATEWAY_SESSION_KEY`, `GEMINI_MODEL`, `TTS_PROVIDER`,
  `USE_GROQ_TTS`, `MAX_HISTORY_MESSAGES`, `LOG_LEVEL`, `ENABLE_FTS`, `ENABLE_BRIEFING`,
  `ENABLE_HISTORY_RELOAD`, `OPENCLAW_WORKSPACE`.
- **Generic double-underscore override**: `SERVER__PORT=5002` sets `server.port`.
- **Feature flags**: `FEATURE_<FLAG_NAME_UPPER>=true|false`.

Other config files:

| File | Purpose |
|---|---|
| `config/providers.yaml` | Provider registry autodiscovery + per-provider config (STT is the load-bearing section) |
| `config/ai-providers.json` | LLM provider catalog rendered by the admin panel |
| `config/tts-fallback.json` | TTS fallback chain + voice-gender map |
| `config/speech_normalization.yaml` | Text normalisation rules applied before TTS |
| `config/theme.json` | Theme defaults |
| `tts_providers/providers_config.json` | TTS provider catalog + `default_provider` |

Every new environment variable must be added to `.env.example` with a comment.

---

## Tests

```bash
venv/bin/python3 -m pytest tests/ -q
```

`pytest.ini` sets `testpaths = tests`. `tests/conftest.py` provides fixtures. Coverage
spans the app factory, config loader, routes (admin, canvas, conversation, music, static),
the provider base and registry, TTS providers, profiles, health, the db pool, the speech
normalizer, adapter discovery, and UI state.

Most subsystems are testable without a running gateway — TTS providers, config, the
registry, and route handlers in particular. Use `plugins/example-gateway/` when you need a
gateway that answers without any external service.

---

## Further reading

- [`docs/reference/architecture.md`](docs/reference/architecture.md) — request-flow-level
  reference including the agent tag system and the display pipeline
- [`docs/reference/agent-tags.md`](docs/reference/agent-tags.md) — full tag list
- [`docs/reference/api.md`](docs/reference/api.md) — HTTP API reference
- [`docs/extending/plugin-system.md`](docs/extending/plugin-system.md),
  [`building-canvas-pages.md`](docs/extending/building-canvas-pages.md),
  [`building-face-plugins.md`](docs/extending/building-face-plugins.md)
- [`docs/customization/tts-providers.md`](docs/customization/tts-providers.md),
  [`profiles.md`](docs/customization/profiles.md), [`themes.md`](docs/customization/themes.md)
- [`plugins/README.md`](plugins/README.md) — gateway plugin authoring guide
- [`SECURITY.md`](SECURITY.md) — vulnerability reporting
