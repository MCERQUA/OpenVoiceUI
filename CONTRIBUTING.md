# Contributing to OpenVoiceUI

Thanks for your interest in contributing. OpenVoiceUI is a voice-first front end for an AI agent:
contributions that improve voice quality, add TTS/STT providers, improve the frontend, add canvas
pages or plugins, or make setup easier are all welcome.

Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before taking part.

---

## Before you start

The voice pipeline needs a running agent gateway. The default is [OpenClaw](https://openclaw.ai),
but any gateway plugin works, including the `example-gateway` echo stub for offline development.
Without a gateway you can still work on:

- the frontend (face, settings, music player, desktop, canvas pages)
- TTS/STT provider implementations (unit-testable in isolation)
- documentation, installers and tests

Useful orientation:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the system is built, subsystem by subsystem
- [docs/VISION.md](docs/VISION.md): positioning and explicit non-goals
- [AGENTS.md](AGENTS.md): orientation for AI coding agents working in this repo
- [docs/REPO-ORGANIZATION.md](docs/REPO-ORGANIZATION.md): where things live and why

---

## Branch workflow

The repository is **trunk-based**. There is one long-lived branch, `main`.

- Branch off the latest `main` with a scope prefix: `feat/`, `fix/`, `refactor/`, `chore/`, `docs/`.
- Open your pull request against `main`. Nobody pushes to `main` directly, and the `test` CI check
  must pass.
- Pull requests are **squash-merged**, and the branch is deleted on merge.
- Releases are CalVer tags (`vYYYY.M.D`) cut from `main`. See [docs/RELEASING.md](docs/RELEASING.md).

---

## Development setup

```bash
git clone https://github.com/MCERQUA/OpenVoiceUI
cd OpenVoiceUI
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env: point CLAWDBOT_GATEWAY_URL / CLAWDBOT_AUTH_TOKEN at your gateway
venv/bin/python3 server.py
```

Open `http://localhost:5001`. The admin dashboard is at `http://localhost:5001/admin`.

CI runs Python 3.11; the Docker image uses Python 3.12.

### Running the tests

```bash
venv/bin/pip install pytest
venv/bin/python3 -m pytest tests/ -q
```

Run the suite in a clean environment. An exported `CLERK_PUBLISHABLE_KEY` switches the app into
authenticated mode and makes the route tests fail with 401:

```bash
env -i PATH="$PATH" HOME="$HOME" venv/bin/python3 -m pytest tests/ -q
```

---

## Project structure

The short version is below; [docs/REPO-ORGANIZATION.md](docs/REPO-ORGANIZATION.md) has the full map.

```
server.py, app.py   Flask entry point and app factory
routes/             Flask blueprints, one file per feature area
services/           Backend services (gateways, auth, paths, TTS, plugins, vault, ...)
  gateways/         Gateway implementations (OpenClaw) + compatibility shims
providers/          LLM/STT/TTS provider registry and base classes
tts_providers/      TTS provider implementations
src/                Frontend: vanilla JS ES modules, no build step
  adapters/         Voice/agent adapters (+ _template.js)
  core/             EventBus, VoiceSession, EmotionEngine
  shell/            Bridges between the agent's tags and the UI
  ui/, face/        App shell, settings, themes, face renderers
default-pages/      Canvas pages shipped with the app (desktop, file explorer, ...)
plugins/            Plugin catalog stubs + the example gateway
skills/             Agent skills bundled for OpenClaw (installed by install-skills.js; docs/SKILLS.md)
profiles/           Agent profile schema + default profile
prompts/            Voice system prompt (hot-reloaded)
config/             Server config YAML/JSON
deploy/             Container builds for OpenClaw, Supertonic and optional services
tests/              pytest suite (tests/js holds standalone JS checks)
docs/               Documentation (the Docusaurus site builds from here)
```

---

## Adding a TTS provider

1. Copy `tts_providers/base_provider.py` as a starting point.
2. Implement `generate(text, voice, speed) -> bytes` (raw PCM or MP3).
3. Register it in `tts_providers/providers_config.json`.
4. Add any required env vars to `.env.example` with a comment.
5. Update the TTS provider table in `README.md`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#how-to-add-a-new-provider) for the full recipe.

## Adding a frontend adapter

1. Copy `src/adapters/_template.js`.
2. Implement the four lifecycle methods: `init`, `start`, `stop`, `destroy`.
3. Register it in `src/shell/adapter-registry.js`.
4. Document required config keys in the adapter's comments.

## Adding a gateway plugin

Gateway plugins connect the server to any LLM backend and run alongside OpenClaw.

1. Copy `plugins/example-gateway/` to `plugins/my-gateway/`.
2. Edit `plugin.json`: set your `id` and any required env vars in `requires_env`.
3. Implement `gateway.py`: subclass `GatewayBase` and implement `stream_to_queue()`.
4. Restart the server. Plugins are auto-discovered on startup.
5. Set `"gateway_id": "my-gateway"` in a profile's `adapter_config` to route traffic to it.

See [plugins/README.md](plugins/README.md) for the event protocol and `gateway_manager.ask()`.

---

## Code style

`.editorconfig` sets the basics (UTF-8, LF, final newline, indent width per file type).

**Python**
- Follow existing patterns: Flask blueprints, `logger = logging.getLogger(__name__)`.
- No bare `print()`; use `logger.debug()` / `logger.info()`.
- Add new env vars to `.env.example` with a comment explaining them.

**JavaScript**
- Vanilla ES modules, no build step.
- Use the EventBus for cross-component communication.
- Keep adapters self-contained: no direct DOM manipulation outside `ui/`.
- Never persist app state in `localStorage`/`sessionStorage`; state lives on the server.

---

## Submitting a pull request

1. Fork the repo and create a branch: `git checkout -b feat/my-thing`.
2. Make your change, keeping it to one feature or fix.
3. Run the test suite.
4. Test end-to-end locally (voice in, TTS audio out, if relevant).
5. Open the PR against `main` and fill out the PR template.

Before submitting a security-related PR, read:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the systems connect
- [docs/contributing/pr-checklist.md](docs/contributing/pr-checklist.md): what reviewers check
- [SECURITY.md](SECURITY.md): vulnerability reporting policy

## How pull requests are reviewed

Every PR is reviewed against the [PR review checklist](docs/contributing/pr-checklist.md).
The maintainer will:

1. **Review the code** against the checklist (security, tag system, display pipeline, tests).
2. **Test locally** on a `devtest/pr-<number>` branch: full test suite plus a manual voice test.
3. **Merge or request changes.** If it passes, the original PR is squash-merged and you keep the
   credit. If it fails, you get specific feedback.

Security fixes get extra care because they often touch tightly coupled systems (for example, the
tag system spans several files; see the architecture doc).

---

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include logs from
`journalctl -u openvoiceui -f` (systemd install), `docker compose logs -f` (Docker) or your
terminal (development). Gateway connection errors and TTS provider errors are usually the most
useful.

Security vulnerabilities go to the private channel in [SECURITY.md](SECURITY.md), never to a
public issue.

## Questions

Open a [GitHub Discussion](https://github.com/MCERQUA/OpenVoiceUI/discussions) or file an issue
with the `question` label.
