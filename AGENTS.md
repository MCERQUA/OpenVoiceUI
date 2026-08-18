# AGENTS.md

Instructions for AI coding agents working in the OpenVoiceUI repository.

This file is the vendor-neutral orientation doc. It does not replace
[CONTRIBUTING.md](CONTRIBUTING.md) (process, branches, PR rules) or
[ARCHITECTURE.md](ARCHITECTURE.md) (how the system is built) — read those too. If anything
here conflicts with CONTRIBUTING.md, CONTRIBUTING.md wins.

---

## 1. What this project is

OpenVoiceUI is the **voice and canvas shell** in front of an agent. It is a Flask backend
plus a vanilla-JavaScript ES-module frontend. The agent itself is not in this repo — it
runs behind a gateway (OpenClaw by default, or any plugin gateway). See
[VISION.md](VISION.md) for positioning and non-goals.

Practical consequence: if a task sounds like "make the model smarter", it is almost
certainly out of scope here. If it sounds like "make the shell hear, speak, show, or
extend better", it belongs here.

---

## 2. Orient yourself first

Before writing code, read in this order:

1. `ARCHITECTURE.md` — the section covering the subsystem you are touching.
2. The module docstring of the file you are about to edit. Almost every Python module in
   this repo opens with a docstring stating its responsibility, its usage pattern, and
   often the reason a design decision was made. Those docstrings are authoritative.
3. The nearest reference doc under `docs/` (`docs/reference/`, `docs/extending/`,
   `docs/customization/`).

Do not infer structure from filenames. `providers/tts/` and `tts_providers/` sound
interchangeable and are not — one is an adapter shim, the other is the live
implementation. That distinction is documented in `providers/tts/__init__.py`.

---

## 3. Repository map (short form)

| Path | What lives there |
|---|---|
| `server.py` | Entry point: registers blueprints, WebSocket routes, startup wiring |
| `app.py` | `create_app()` Flask application factory, returns `(app, sock)` |
| `index.html` | Thin shell that loads `src/app.js` as a module |
| `routes/` | Flask blueprints, one file per feature area |
| `services/` | Backend services (gateways, TTS, plugins, auth, paths, vault, health) |
| `services/gateways/` | `GatewayBase` + the OpenClaw implementation |
| `providers/` | Provider ABCs (`base.py`) + the registry (`registry.py`) for LLM/STT/TTS |
| `tts_providers/` | Canonical TTS implementations — add new TTS backends here |
| `plugins/` | Drop-in plugins: gateways, pages, routes, faces |
| `config/` | YAML/JSON config + `loader.py` with env overrides |
| `profiles/` | Agent profile JSON, schema, and manager |
| `src/` | Frontend: `core/`, `shell/`, `adapters/`, `providers/`, `face/`, `features/`, `ui/`, `styles/` |
| `default-pages/`, `default-faces/`, `default-styles/` | Shipped canvas pages, face template, style presets |
| `runtime/` | Gitignored runtime data. Never commit anything from here |
| `tests/` | pytest suite |

---

## 4. Running and testing

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env          # set CLAWDBOT_AUTH_TOKEN and GROQ_API_KEY at minimum
venv/bin/python3 server.py    # serves on http://localhost:5001 by default
```

Tests:

```bash
venv/bin/python3 -m pytest tests/ -q
```

Run the full suite before proposing a change. If your change touches TTS, provider
registration, canvas manifest handling, or the config loader, there is already a test file
for it — extend it rather than creating a parallel one.

Docker (`docker compose up`) is the alternative path; see `README.md`.

**You do not need a gateway to work on most of this repo.** Frontend UI, TTS providers,
canvas pages, config, docs, and tests are all reachable without one. If you need a gateway
that responds, use `plugins/example-gateway/` — it echoes.

Port defaults to 5001 (`config/default.yaml`, override with `PORT`).

---

## 5. Extension points

These are the places designed for new code. Prefer them over editing core files.

| You want to add | Go here | Wiring step |
|---|---|---|
| A TTS backend | `tts_providers/myprovider_provider.py` | add to `_PROVIDERS` in `tts_providers/__init__.py` + a block in `providers_config.json` |
| An STT backend (server-side) | `providers/stt/myprovider_provider.py` | `registry.register(ProviderType.STT, 'id', Class)` + import in `providers/stt/__init__.py` + `stt.modules` in `config/providers.yaml` |
| An STT backend (browser-side) | `src/providers/MySTT.js` | follow `WebSpeechSTT.js` (export STT class + `WakeWordDetector`) |
| A different agent backend | `plugins/my-gateway/gateway.py` | `plugin.json` with `"provides": "gateway"`, class subclasses `GatewayBase` |
| A frontend agent/voice framework | `src/adapters/my-adapter.js` | copy `_template.js`, add to `ADAPTER_PATHS` in `src/shell/adapter-registry.js`, add a profile |
| A face/avatar | `src/face/MyFace.js` or a plugin `faces[]` entry | extend `BaseFace`, add to `src/face/manifest.json` |
| A canvas page | `default-pages/*.html` or a plugin `pages[]` entry | plugin pages are auto-copied and registered in the canvas manifest at load |
| A new API surface | `routes/my_feature.py` | define `my_feature_bp`, register it in `server.py` |
| An agent persona | `profiles/my-profile.json` | validate against `profiles/schema.json` |

Step-by-step provider recipes with code live in
[ARCHITECTURE.md — How to add a new provider](ARCHITECTURE.md#how-to-add-a-new-provider).
Gateway plugin protocol details live in [`plugins/README.md`](plugins/README.md).

---

## 6. Code conventions

### Python

- Flask **blueprints**, one module per feature area under `routes/`. Name the blueprint
  `<feature>_bp` and register it in `server.py`.
- `logger = logging.getLogger(__name__)` at module top. **No bare `print()`** in server
  code — use `logger.debug/info/warning/error`.
- **ABC + registry** is the extension pattern. Abstract base defines the contract,
  concrete classes register themselves under a string id, callers resolve by id with a
  configured default.
- Import paths from `services/paths.py`. Do not hardcode directory paths.
- Type hints on new function signatures; dataclasses for structured returns
  (`LLMResponse`, `TranscriptionResult`, `TTSVoice` are the existing examples).
- Errors use the provider error hierarchy (`ProviderError` /
  `TTSProviderError` and their subclasses) rather than bare `Exception`.
- Module docstrings explain **why**, not just what. Follow suit — several existing
  docstrings encode hard-won constraints, and stripping them loses the reason.
- Every new environment variable goes in `.env.example` with an explanatory comment.

### JavaScript

- **Vanilla ES modules. No framework, no bundler, no build step.** Do not introduce
  React/Vue/Svelte, TypeScript compilation, webpack/vite, or a package-install step for
  frontend code. The browser loads `src/` directly.
- Cross-component communication goes through the **EventBus**
  (`src/core/EventBus.js`), not direct imports between unrelated modules.
- Adapters talk to the shell **only** through the **EventBridge**
  (`src/core/EventBridge.js`). The rules are listed at the top of
  `src/adapters/_template.js`: no DOM access outside `ui/`, own your audio/WebSocket/SDK,
  release everything in `destroy()`, never import another adapter.
- Adapters declare a `capabilities` array; `src/shell/orchestrator.js` shows/hides UI from
  it. Add a capability there rather than special-casing UI in the adapter.
- Cache-busting query strings (`?v=N`) on script/style tags in `index.html` are
  intentional. Bump them when you change the referenced file.

### General

- Keep changes focused. One feature or fix per PR (CONTRIBUTING.md).
- Match the surrounding style of the file you are editing over any global preference.

---

## 7. Constraints — read before changing anything

These are the traps that cause regressions in this codebase.

**Do not delete or "clean up" files you were not asked to touch.** Deprecated code here is
deliberately kept (see `providers/llm/DEPRECATED.md`) because other machinery still imports
it. Deprecate with a note; do not remove.

**Do not add an LLM under `providers/llm/`.** That package has no production call sites.
LLM routing belongs to the gateway/agent runtime. Add catalog entries to
`config/ai-providers.json` or write a gateway plugin.

**Never let a health or readiness path call a third-party API.** `tts_providers.list_providers()`
defaults to `probe=False` for exactly this reason. Probing is for admin/UI endpoints only.

**Respect the provider instance cache.** `tts_providers.get_provider()` returns a
process-lifetime singleton. Do not store per-request state on a provider instance, and do
not do expensive or network work in `__init__`. Call `invalidate()` after rewriting
`providers_config.json`.

**Canvas manifest writes must be serialised.** `routes/canvas.py` guards load-modify-save
with `_manifest_lock`, and `load_canvas_manifest()` returns a deep copy. Use
`add_page_to_manifest()` rather than hand-editing the manifest, and preserve
user-customised fields (`description`, `starred`, `is_public`, `is_locked`, ordering) on
re-registration.

**Plugin page installation must never overwrite an existing page.** `load_plugins()` copies
a page only when the destination does not exist. A user's edits to a canvas page are
theirs.

**Do not overwrite user data in `runtime/`.** It holds canvas pages, transcripts, uploads,
music, profiles state, and the database. It is gitignored — nothing from it belongs in a
commit.

**Keep API keys server-side.** Several routes (`fal.py`, `image_gen.py`, `suno.py`) exist
specifically so keys are never sent to the browser. Do not add a frontend path that carries
a secret.

**Treat agent output as untrusted input.** The gateway prepends prompt armor to user
messages, and the frontend strips agent tags before display. Anything you render from agent
or user-controlled content must go through the existing sanitising path — see the display
pipeline section of `docs/reference/architecture.md` before touching it.

**Path traversal.** Canvas file serving goes through `_safe_canvas_path()`. Any new
filesystem-serving route needs equivalent containment.

**Do not change the OpenClaw version by hand.** Three installer paths pin it and must stay
in sync; use `bump-openclaw-version.sh` and commit the changed files together.

**Security-touching PRs** additionally require reading
`docs/reference/architecture.md`, `docs/contributing/pr-checklist.md`, and `SECURITY.md`
before submission.

---

## 8. Definition of done

Before you report a task complete:

- [ ] `venv/bin/python3 -m pytest tests/ -q` passes.
- [ ] New/changed env vars are in `.env.example` with comments.
- [ ] New provider/plugin/adapter is registered in **every** place its type requires
      (see the table in section 5) — a class that is written but not registered does
      nothing.
- [ ] No `print()` added to server code; logging used instead.
- [ ] No new frontend build step, framework, or bundler.
- [ ] Nothing deleted that you were not explicitly asked to delete.
- [ ] Relevant docs updated: `README.md` provider tables, `docs/` pages, and
      `ARCHITECTURE.md` if you changed structure.
- [ ] PR targets `dev`, not `main`, and is scoped to one change.

State plainly what you verified and what you did not. Do not describe an untested change
as working.
