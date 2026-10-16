# Repository organization

Audit of the repository layout on 2026-10-16, the target structure, and the moves that get us
there. Each move has a risk level. Only the **safe** set (plus documentation-only path updates)
was done in the organization PR; everything marked **proposed, not done** needs its own reviewed
change.

Risk levels:

- **safe**: nothing imports, serves, installs or copies the file at its old path.
- **needs path updates**: the move is fine once listed references are updated. When those
  references are documentation only, the move was done; when they are code, it is proposed.
- **risky**: Python packages, installer entry points, or paths that external tooling calls.
  Proposed only.

---

## 1. Audit

### 1.1 Top-level layout

Tracked files by top-level directory before the pass (588 files on `main` at `1f8c496`):

| Directory | Files | | Directory | Files |
|---|---|---|---|---|
| `default-styles/` | 87 | | `deploy/` | 15 |
| `src/` | 68 | | `tts_providers/` | 13 |
| `static/` | 55 | | `sounds/` | 11 |
| `tests/` | 54 | | `config/` | 11 |
| `routes/` | 42 | | `.github/` | 8 |
| `services/` | 35 | | `runtime/` | 7 (`.gitkeep` only) |
| `docs/` | 31 | | `dev-tasks/` | 5 |
| `default-pages/` | 27 | | `profiles/` | 3 |
| `plugins/` | 20 | | `future-dev-plans/`, `scripts/`, `data/`, `prompts/`, `cli/`, `canvas-shared/`, `default-faces/`, `.claude/` | 1 each |
| `providers/` | 18 | | `.devcontainer/` | 2 |
| `website/` | 17 | | repository root | 51 loose files |

Findings:

- **51 loose files at the root**, mixing project docs, Python modules, Pinokio/npm launcher
  scripts, native installers, Docker files and stray artifacts.
- **A dated backup was committed:** `app.py.pre-csp-leaflet-20260727T192343Z` (the existing
  `*.bak` ignore rules did not match its name).
- **A superseded icon:** `icon.old.png`, referenced nowhere.
- **Stale installer duplicates:** `deploy/setup-sudo.sh`, `deploy/setup-nginx.sh` and
  `deploy/openvoiceui.service` were March 2026 copies of the root files. The root copies are the
  maintained ones (`bump-openclaw-version.sh` updates the root `setup-sudo.sh`). The `deploy/`
  copy of `setup-sudo.sh` resolves `INSTALL_DIR` to `deploy/` and then looks for `deploy/.env`,
  so the README's documented VPS command (`sudo bash deploy/setup-sudo.sh`) exited with
  "No .env file found" on a normal checkout.
- **Duplicate Python modules:** `supertonic_tts.py` is byte-identical to
  `tts_providers/supertonic_tts.py`; `memory_client.py` is byte-identical to
  `services/memory_client.py`, and neither copy is imported anywhere.
- **Duplicate data:** `greetings.json` is byte-identical to `data/greetings.json`; only the root
  copy is read (`routes/greetings.py`).
- **Planning docs at the root:** `dev-tasks/` (5 files) and `future-dev-plans/` (1 file).
- **A deployment-specific helper at the root:** `kyle-clip-gen.py` generates clips for one
  hosted deployment's character voice.
- **A tracked file inside an ignored directory:** `scripts/` is in `.gitignore`, but
  `scripts/jambot-capture-migrate.py` is tracked (force-added). The ignore rule also blocks a
  conventional `scripts/` directory for repository tooling.
- **A test page inside the shipped frontend:** `src/test-tts.html`.
- **JS tests not run in CI:** `tests/js/*.test.js` (5 files) are not executed by
  `.github/workflows/tests.yml`, which only runs pytest.
- **Large binaries:** `static/voice-samples/` holds 45 WAV files, 8.9 MB of the repository's
  20.4 MB of tracked bytes. `docs/banner.png` (1.0 MB) is unused; the README uses
  `docs/banner.jpg`.
- **Ambiguous name:** `requirements-docs.txt` holds the optional document-extraction packages
  for `/api/upload` (PDF/DOCX/XLSX/PPTX), not documentation-site dependencies.
- **Docusaurus template leftovers in `website/static/img/`** (`undraw_*.svg`, `docusaurus.png`),
  and `themeConfig.image` points at `img/social-card.jpg`, which does not exist.

### 1.2 README accuracy (before this pass)

| README claim | What the code shows |
|---|---|
| VPS install: `sudo bash deploy/setup-sudo.sh` | Fails (see above). The maintained script is the root `setup-sudo.sh`. |
| Pinokio "handles Docker" | `install.js` only checks that Docker is installed and running; it does not install it. |
| npm path needs Node 20+ | `package.json` `engines` says `>=18`. |
| Docker: `cp .env.example .env && docker compose up` is enough | The base `docker-compose.yml` gives the `openclaw` service neither `.env` nor a generated config. The npm wizard and Pinokio generate `openclaw-data/` and layer `docker-compose.local.yml`. Not verified end to end; the README now says so. |
| "Long-term memory: optional context engine plugin" | No such plugin is tracked. |
| "Video creation (Remotion Studio)" | No Remotion code is tracked. |
| TTS list (6 providers, "Hume EVI") | 9 providers in `tts_providers/providers_config.json`: Pocket TTS, xAI Grok and local Qwen3 were missing; the Hume TTS provider is Octave (Hume EVI is a separate realtime adapter). |
| "Ollama via adapter" | No Ollama adapter exists. |
| Hermes plugin "v0.13.0 / v2026.5.7" | `plugins/hermes-agent/plugin.json` pins `hermes_version` 0.15.2; the plugin's own README is also stale. |
| Plugin table (4 plugins, all linked to the plugins repo) | 7 catalog entries are tracked here; Booking, MidiViz and Example Gateway were missing. |
| Admin dashboard sections | The real navigation has about 20 sections (connectors, voice TTS/STT, sessions, updates, tools and logs, ...). |
| Many shipped features unlisted | Vault and one-click connectors, canvas styles, terminal, camera capture and job albums, image tools, icon library, maps, sing-along, ChatGPT import, audio clips. |
| Screenshots | None are tracked. |

All external links in the old README returned HTTP 200 (npmjs.com blocks automated checks; the
registry shows `openvoiceui@2026.9.29`, matching `package.json`).

### 1.3 Project basics

| Item | Before | After this pass |
|---|---|---|
| `LICENSE` (MIT) | present | unchanged |
| `CONTRIBUTING.md` | present, but described a `dev` branch that does not exist and linked two missing files | rewritten for the trunk-based flow; links fixed |
| `SECURITY.md` | present, linked two missing files and a missing issue | links fixed, supported-versions section added |
| `CODE_OF_CONDUCT.md` | missing | added (Contributor Covenant 2.1) |
| `CHANGELOG.md` | missing (notes only in GitHub Releases) | added, one entry per release tag, linking to the full notes |
| Issue templates | bug + feature | `config.yml` added (security, discussions, plugin links) |
| PR template | told contributors to target `dev` | targets `main`; test/docs checklist items added |
| `.editorconfig` | missing | added, matching the conventions already in the tree |
| `CODEOWNERS` | missing | not added (single maintainer) |
| GitHub private vulnerability reporting | disabled | not changeable from the repo; recommend enabling it in Settings |

### 1.4 Documentation site

- The **Deploy Docs** workflow had failed on every push to `main` since 2026-09-29 (three
  consecutive runs), so the published site was stale. Cause: MDX rejects `<https://...>`
  autolinks, and `docs/features/one-click-connectors.md` used three of them. Fixed in this pass;
  the page is also added to the sidebar.
- Docusaurus compiles every Markdown file under `docs/`, so repository-only documents (internal
  deployment notes in `docs/jambot/` and planning docs) were being published as orphan pages.
  `website/docusaurus.config.ts` now excludes them, and the root docs moved into `docs/` in this
  pass are excluded too (they are read on GitHub).
- Remaining warnings: a duplicate route at `/` (`docs/intro.md` and
  `website/src/pages/index.tsx`) and the deprecated `onBrokenMarkdownLinks` option.

### 1.5 Branch-workflow drift

`CONTRIBUTING.md`, the PR template, `docs/contributing/development-setup.md`,
`docs/contributing/pr-checklist.md`, `AGENTS.md`, `llms.txt` and `.claude/CLAUDE.md` described a
`dev` → `main` flow. The repository is trunk-based: no `dev` branch exists, merge commits and
rebase merges are disabled, PRs are squash-merged, and `docs/RELEASING.md` already said so. All
seven now describe the real flow.

### 1.6 Public-repository hygiene (security)

No secrets were found in the tracked tree or in history: key-shaped patterns, private keys and
`.env` files all returned zero hits, and only `.env.example` was ever committed. The scan did find
**deployment-specific content** that a public repository should not carry. The specific locations
are being handled in a separate, privately tracked clean-up rather than listed here:

| Category | Scope |
|---|---|
| Hosted-deployment customer identifiers in code comments and tests | about 10 source and test files |
| A hard-coded administrator account identifier used for authorization | 2 source files; should come from configuration |
| An infrastructure IP address and private-network addresses in docs/tests | 2 files |
| Server filesystem paths and internal hostnames of the hosted deployment | about 25 files |
| Deployment-specific documents and helpers | `docs/jambot/`, `kyle-clip-gen.py`, two character voice samples in `static/voice-samples/` |
| Internal host names in plugin/provider descriptions | `plugins/booking/plugin.json`, `tts_providers/providers_config.json` |

These are identifiers and context, not credentials, so nothing needs rotating. Removing them from
the current tree is a normal PR; removing them from history would need a history rewrite and a
force-push, which is a separate decision.

---

## 2. Target structure

```
OpenVoiceUI/
├── README.md  LICENSE  CHANGELOG.md  CONTRIBUTING.md  CODE_OF_CONDUCT.md
├── SECURITY.md  CONTRIBUTORS.md
├── AGENTS.md  llms.txt                  # AI-agent conventions; must stay at the root
├── server.py  app.py                    # entry point + app factory
├── routes/  services/  providers/  tts_providers/
├── profiles/  config/  prompts/
├── src/  static/  index.html  sounds/
├── default-pages/  default-faces/  default-styles/  canvas-shared/
├── plugins/                             # catalog stubs + example gateway
├── skills/                              # PROPOSED: bundled OpenVoiceUI agent skills (section 5)
├── deploy/
│   ├── openclaw/  supertonic/  parakeet-stt/  skill-runner/  browse-service/
│   └── native/                          # PROPOSED: setup-sudo.sh, setup-nginx.sh, openvoiceui.service
├── cli/                                 # npm CLI
├── pinokio.js  install.js  start.js  stop.js  update.js  icon.png   # Pinokio requires these at the root
├── installer/                           # PROPOSED: setup-config.js, inject-device-identity.js,
│                                        #   auto-approve-devices.js, check-docker.js, diagnose.js
├── Dockerfile  docker-compose.yml  docker-compose.local.yml  entrypoint.sh
├── requirements.txt  requirements-docs.txt  package.json
├── tests/                               # pytest (+ tests/js, to be wired into CI)
├── docs/                                # site docs + repository docs
│   ├── ARCHITECTURE.md  VISION.md  SETUP.md  RELEASING.md  REPO-ORGANIZATION.md
│   └── plans/                           # design notes, not published on the site
├── website/                             # Docusaurus site
└── archive/                             # superseded files kept for reference, not shipped
```

Reorganizing the Python packages into a single `app/` or `src/openvoiceui/` package is **not**
recommended now. The flat layout is referenced by the Dockerfile `CMD`, every import, the test
suite, `package.json` `files`, coverage config and external deployment tooling, and the gain is
cosmetic.

---

## 3. Moves done in this pass

| From | To | Risk | References updated |
|---|---|---|---|
| `ARCHITECTURE.md` | `docs/ARCHITECTURE.md` | needs path updates (docs only) | `AGENTS.md`, `llms.txt`, `docs/VISION.md`, internal links. `CONTRIBUTING.md`, `SECURITY.md` and two `docs/contributing/` pages already linked to `docs/ARCHITECTURE.md`, which was a broken link until now. |
| `VISION.md` | `docs/VISION.md` | needs path updates (docs only) | `AGENTS.md`, `llms.txt`, `docs/ARCHITECTURE.md` |
| `SETUP.md` | `docs/SETUP.md` | needs path updates (docs + npm manifest) | `llms.txt`, `package.json` `files` (`docs/SETUP.md`), two broken links inside it fixed |
| `RELEASING.md` | `docs/RELEASING.md` | safe | none needed (added a CHANGELOG step) |
| `dev-tasks/*` (5 files) | `docs/plans/` | safe | indexed in `docs/plans/README.md` |
| `docs/camera-capture-build-plan.md` | `docs/plans/` | safe | none |
| `app.py.pre-csp-leaflet-20260727T192343Z` | `archive/` | safe | none |
| `icon.old.png` | `archive/` | safe | none |
| `deploy/setup-sudo.sh`, `deploy/setup-nginx.sh`, `deploy/openvoiceui.service` | `archive/deploy-legacy/` | needs path updates (docs only) | `README.md`, `docs/SETUP.md`, `docs/getting-started/installation.md`, `.env.example` now point at the maintained root files |

Also in this pass: `.gitignore` gains standard Python tool caches, editor and patch leftovers,
`/version.json` (written by the Docker build) and a rule for dated `*.pre-*-YYYYMMDD*` backups.
`.dockerignore` excludes `archive/` and `docs/plans/` from the image. No tracked file matches the
new ignore rules. `archive/README.md` indexes every archived file and how to restore it.

---

## 4. Proposed, not done

| Proposal | Risk | What it needs |
|---|---|---|
| `future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md` → `docs/plans/` | needs path updates (code comments) | Update citations in `src/adapters/_template.js`, `src/adapters/xai-realtime.js`, `src/shell/adapter-registry.js`, `src/shell/orchestrator.js`, `config/tools.yaml` and `docs/jambot/voice-agent-tool-bus.md`. Several `providers/` docstrings cite `future-dev-plans/02-PROVIDER-SYSTEMS.md`, which was never committed; fix them at the same time. |
| Native installer (`setup-sudo.sh`, `setup-nginx.sh`, `openvoiceui.service`) → `deploy/native/` | risky | `setup-sudo.sh` must resolve the repo root (`INSTALL_DIR` is currently its own directory); update `bump-openclaw-version.sh` (it edits `setup-sudo.sh` by path) and every doc that names the script; external maintainer runbooks name the root path. |
| `bump-openclaw-version.sh` → `scripts/` or `deploy/` | needs path updates | Un-ignore `scripts/` first (see below); update `docs/RELEASING.md` and maintainer runbooks. Also extend it to the commented version in `.env.example` and the version text in `docs/SETUP.md`, which still say `2026.3.13`. |
| Pinokio/npm helpers → `installer/` | risky | `install.js`, `start.js`, `cli/index.js`, `package.json` `files`, `.npmignore`, `diagnose.js` messages. `pinokio.js`, `install.js`, `start.js`, `stop.js`, `update.js` and `icon.png` must stay at the root. |
| `check-docker.js` | needs decision | Nothing calls it. Either have `install.js` run it (as its header intends) or archive it. |
| Root `supertonic_tts.py` (duplicate) | needs path updates (code) | Change `tts_providers/supertonic_provider.py` to import `tts_providers.supertonic_tts`, update `.coveragerc`, then archive the root copy. |
| `memory_client.py` + `services/memory_client.py` (identical, unimported) | needs decision (code) | Confirm no plugin imports `memory_client`, then archive one or both and drop the `config/default.yaml` flag. |
| `data/greetings.json` vs root `greetings.json` | needs path updates (code) | Pick one location (`data/` is tidier), update `routes/greetings.py` and `package.json` `files`, archive the other. |
| `kyle-clip-gen.py` | risky (external caller) | Deployment-specific: move it to the hosted deployment's private tooling, and update the external script that copies it from the source checkout in the same change. |
| `scripts/` ignore rule and `scripts/jambot-capture-migrate.py` | needs decision | Replace the blanket `scripts/` ignore with a narrower one so repository tooling can live in `scripts/`; keep or archive the one-time migration (it is cited by `docs/plans/camera-capture-build-plan.md` and run in-container from `/app/scripts/`). |
| `docs/jambot/` | needs decision | Internal deployment notes. Excluded from the site in this pass; move them to private docs. External tooling cites the folder path. |
| Rename `requirements-docs.txt` (e.g. `requirements-documents.txt`) | needs path updates | The file's own install hint and any docs that name it; confirm Dependabot's pip scan of `/` still picks up the renamed file. |
| `src/test-tts.html` → `tests/` or archive | needs decision | Confirm nothing links to it in production. |
| Run `tests/js` in CI | safe | Add a Node job to `tests.yml`. |
| `static/voice-samples/` size | needs decision | 8.9 MB of WAV previews; convert to Opus/MP3 or serve from release assets. |
| `docs/banner.png` (unused) | safe | Archive it and drop its `.npmignore` line. |
| Website clean-up | safe | Remove Docusaurus template images, add the missing social card, resolve the duplicate `/` route, move `onBrokenMarkdownLinks` to `markdown.hooks`. |
| Consolidate `docs/ARCHITECTURE.md` and `docs/reference/architecture.md` | needs decision | Two architecture docs at different depths; link one from the other or merge. |
| Screenshots | safe | Add `docs/screenshots/` (already in `.npmignore`) and reference them from the README. |
| Enable GitHub private vulnerability reporting | settings | Repository Settings → Security. |

---

## 5. Bundled agent skills (proposed, not done)

**The gap.** The repository ships no agent skills (no `SKILL.md`, no `skills/` directory), and no
installer (Docker Compose, the npm wizard, Pinokio or `setup-sudo.sh`) installs any. The README
only says the agent can use "any Claude Code or OpenClaw skill". The voice system prompt and the
[agent tags](reference/agent-tags.md) teach the basic UI tags, but a public install's agent has no
skill that explains how to drive OpenVoiceUI's own features in depth (building canvas pages,
custom faces, music). On the maintainers' hosted deployment those skills come from a shared
skills directory mounted into every gateway container, and several source files assume that
mount exists: `routes/conversation.py`, `routes/custom_faces.py`, `services/host_broker.py` and
`plugins/booking/routes/booking.py` reference `/mnt/shared-skills/...` paths that do not exist
on a public install.

### 5.1 Candidates

| Skill | Class | Public-repo scrub needed |
|---|---|---|
| `voice-flow` | OpenVoiceUI core | None found. Smallest and cleanest; ship first. |
| `custom-faces` | OpenVoiceUI core | Essentially clean; every endpoint it uses exists in `routes/custom_faces.py`. |
| `threejs-html-surfaces` | OpenVoiceUI core | Server filesystem paths (3) and example page names to genericize. |
| `threejs-canvas-integration` | OpenVoiceUI core | Hosted-deployment hostnames (2) to genericize; ship `SKILL.md` only (not the `.old` copy). |
| `canvas-page-visibility` | OpenVoiceUI core | Hostnames (2), hosted-auth wording (Clerk, 4 lines) to make conditional on `CANVAS_REQUIRE_AUTH`, one customer reference. |
| `canvas-web-design` | OpenVoiceUI core | Product naming of the hosted service and shared-skill paths (5) that point at skills that would not ship. |
| `canvas-pages` | mixed | Needs a rewrite, not a scrub: it embeds incident stories from hosted customers, a hosted-only sign-in checker script, and hostnames. |
| `suno-music` | mixed | Needs a fresh, trimmed public version: the hosted skill carries licensing and business sections, host-specific paths and examples, and its directory holds private configuration that must never be copied. Never copy this directory wholesale. |
| `canvas-design` | not OpenVoiceUI | A general poster/art skill whose name collides with a common skill; exclude. |

Further candidates found while auditing: `notify-tags` and `dj-soundboard` (strong; they document
OpenVoiceUI tags), `icon-generation`, `chart-builder`, `audio-clips`, `song-tagger`. Skills tied
to the hosted business (CRM, SEO, social, mesh/notification tooling) stay out.

Rules for whoever does the copy: copy `SKILL.md` and reviewed assets only, never whole
directories; grep every file for hostnames, server paths, customer names, account identifiers
and key-shaped strings before committing; prefer relative API paths (`/api/...`) and
`<your-domain>` placeholders.

### 5.2 Where OpenClaw reads skills

OpenClaw loads skills from, highest precedence first: `<workspace>/skills`,
`~/.openclaw/skills`, its bundled skills, then any directories listed in the
`skills.load.extraDirs` config key. Putting OpenVoiceUI's skills in an `extraDirs` entry gives
them the lowest precedence, so a user's own skill with the same name always wins, and does not
touch the user's `~/.openclaw/skills`.

### 5.3 How each installer would install them

| Installer | Proposed change |
|---|---|
| Docker Compose | Mount the repo's `skills/` read-only into the `openclaw` service (for example `./skills:/opt/openvoiceui/skills:ro`) in both compose files, and add `/opt/openvoiceui/skills` to `skills.load.extraDirs`. |
| npm wizard / Pinokio | Same compose mount; `setup-config.js` writes `skills.load.extraDirs` into the `openclaw.json` it generates. |
| Native (`setup-sudo.sh`) | Run `openclaw config set skills.load.extraDirs '["<repo>/skills"]' --json` (use the gateway's own config command rather than editing the file). |
| OpenVoiceUI server | Replace the hard-coded `/mnt/shared-skills/...` references with a configurable skills path (an env var whose default matches the mount above), so the prompts that tell the agent to read a skill point at a file that exists. |

The copy into `skills/` should be its own reviewed pull request, after the list above is
approved.

---

## 6. External references

Tooling outside this repository was searched for every moved path. No build, image or deploy
script references any of them. One internal task-list entry points at the old
`dev-tasks/voice-latency-deferred-fixes.md` path and should be updated when this change merges.
Paths that external tooling does depend on (`deploy/supertonic`, `deploy/parakeet-stt`,
`deploy/skill-runner`, `deploy/browse-service`, `default-pages/`, `default-styles/`,
`canvas-shared/`, `profiles/`, `static/`, `src/`, `routes/`, `services/`, `Dockerfile`,
`kyle-clip-gen.py`, `docs/jambot/`, `setup-sudo.sh`) were deliberately left in place.
