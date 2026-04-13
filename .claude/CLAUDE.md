# OpenVoiceUI — Project Context

This file loads automatically when you open the repo with Claude Code. It
gives any contributor — human or AI — the baseline knowledge needed to
make changes without breaking things. Read it before you touch code.

## What OpenVoiceUI is

OpenVoiceUI is a **self-hosted, voice-first AI interface**. You run it on
your own machine or server, open it in a browser, and talk to it. It is
designed for a **single user connecting to their own backend** — not a
SaaS product, not a multi-user web app.

The important consequence: **the server is always reachable from the
browser**, because the browser is only ever used by the one person who
owns that server. There is no CDN, no distributed client, no edge cache.
Design accordingly.

## Stack

- **Backend**: Python 3 / Flask. Entry point is `app.py`. Routes are
  Flask blueprints under `routes/`. Business logic is in `services/`.
- **Frontend**: Vanilla JavaScript (no build step for the main app).
  Entry point is `src/app.js`. Subsystems live under `src/core/`,
  `src/providers/`, `src/features/`, `src/ui/`, `src/adapters/`,
  `src/shell/`, `src/face/`.
- **Templates**: Jinja HTML in `templates/`, canvas pages in `pages/`.
- **Styles**: CSS in `src/styles/`.
- **Package manager**: `pnpm`. Lockfile is `pnpm-lock.yaml`.
- **Plugins**: self-contained modules under `plugins/<plugin-id>/` with
  a `plugin.json` manifest (routes, credentials, container config,
  pages, etc.). The plugin loader reads these at startup.
- **Agent backend**: the conversation API talks to a "gateway" — a
  process that handles the actual LLM interaction. Multiple gateway
  implementations exist (see `services/gateways/` and gateway plugins).
  The UI picks one per profile.

## Directory map

```
app.py                  Flask entry point, app factory, startup wiring
routes/                 HTTP blueprints (conversation, canvas, plugins,
                        vault, profiles, admin, music, image_gen, ...)
services/               Business logic (gateway manager, TTS, auth,
                        vault, plugins, health, paths)
services/gateways/      Gateway backend implementations
plugins/                Plugin directory — each has plugin.json +
                        routes/pages/credentials/container config
src/app.js              Main frontend app (voice, transcript, action
                        console, canvas iframe, profile switching)
src/providers/          STT, TTS, wake-word, provider adapters
src/core/               Core UI primitives
src/features/           Feature modules
src/ui/                 UI components
src/shell/              App shell / layout
src/face/               Animated face rendering
src/styles/             CSS
templates/              Jinja templates (main UI, admin)
pages/                  Canvas pages (HTML dashboards shown in iframe)
static/                 Static assets
data/                   Runtime data (writable at runtime)
default-pages/          Shipped canvas page defaults
default-faces/          Shipped face defaults
docs/                   Project documentation
.claude/                Claude Code project context (this file)
```

## Key concepts

**Profile** — a named configuration that bundles a gateway, an agent ID,
a TTS provider, a voice, a face, etc. The user can switch profiles from
the UI. Profiles are stored server-side.

**Gateway** — the backend process that actually runs the agent. The
gateway manager (`services/gateway_manager.py`) dispatches conversation
requests to the right gateway based on the active profile. Adding a new
agent backend means writing a new gateway implementation.

**Canvas page** — an HTML dashboard at `/pages/<name>.html` that loads
inside an iframe on the main UI. Canvas pages are how tools (image
generation, music, CRM, SEO, etc.) surface their interfaces. They run
under a restrictive Content Security Policy (see Canvas page CSP below).

**Action Console** — the verbose log of tool calls that the agent makes.
Every tool invocation, its input, and its result land here. The
transcript is the *clean* view (what the user said / what the agent
said); the Action Console is the *verbose* view (what the agent did).

**Plugin** — a self-contained module under `plugins/<plugin-id>/` with
its own routes, credentials, config UI, canvas pages, and optionally a
containerized backend. Plugins can register a new gateway, add canvas
pages, register API routes, declare credentials for the vault, etc.
See `plugins/example-gateway/` for the reference shape.

**Credential vault** — per-user encrypted store for API keys. Plugins
declare which credentials they consume in `plugin.json`. The vault
writes values into agent/plugin `.env` files at runtime. Contributors
adding a new external service should declare it as a vault credential,
not a hardcoded key.

---

# Git workflow

## Branch model

```
feature/xyz ──PR──► dev ──PR──► main
                      │          │
                      │          └── production
                      └── staging / integration
```

- **`main`** — production. Protected. Only ever updated via PR from `dev`.
- **`dev`** — integration branch. Features land here first and soak.
- **feature branches** — your work. Always branched off `dev`, never off `main`.

## Branch naming

Use a scope prefix so the branch name tells you what kind of change it is:

| Prefix        | Use for                                         |
|---------------|-------------------------------------------------|
| `feat/*`      | new feature                                     |
| `fix/*`       | bug fix                                         |
| `refactor/*`  | code restructure, no behavior change            |
| `chore/*`     | deps, tooling, build, CI                        |
| `docs/*`      | documentation only                              |

Example: `feat/action-console-verbose`, `fix/suno-tag-normalization`.

## Commit messages

Conventional commits. First line: `<type>(<scope>): <summary>`, under ~72 chars.

```
feat(action-console): show verbose tool detail (raise 120→2000 char cap)

Action Console is the verbose surface for tool calls. The 120-char cap was
burying real shell commands and paths. Raise to 2000 chars bounded, keep
transcript clean via gateway-side truncation.
```

- Imperative mood ("add", not "added")
- Body explains **why**, not what (the diff shows what)
- Reference issues/PRs when relevant (`fixes #123`)

## Workflow

1. **Start from a clean `dev`**
   ```bash
   git checkout dev && git pull origin dev
   git checkout -b feat/my-feature
   ```

2. **Commit atomically**
   Each commit should be one logical change. If your working tree has
   unrelated edits from another task, **isolate the hunks first** — don't
   bundle them into one commit. The pattern:
   ```bash
   cp src/file.js /tmp/file.mixed          # save mixed state
   git checkout HEAD -- src/file.js        # reset to clean
   # re-apply only your hunks in editor
   git add src/file.js && git commit
   cp /tmp/file.mixed src/file.js          # restore other work as dirty
   ```

3. **Push the feature branch**
   ```bash
   git push -u origin feat/my-feature
   ```

4. **Open PR → `dev`** (not main)
   ```bash
   gh pr create --base dev --head feat/my-feature --title "..." --body "..."
   ```
   PRs into `dev` should be **squash-merged** so `dev` stays one-commit-per-
   feature.

5. **Release `dev` → `main` as a batch PR**
   When `dev` has accumulated enough work to cut a release, open a PR from
   `dev` → `main`. This one should be a **merge commit** (not squash) so the
   per-feature commits are preserved in `main` history.

## Rules

- **Never commit with a mixed working tree.** If other changes are sitting
  dirty, isolate yours first. Mixed commits are the #1 source of regressions.
- **Never push without confirming that specific push.** Approval for the work
  is not approval for the push.
- **Never force-push `main` or `dev`.** Force-push is only safe on your own
  feature branches (e.g. after a rebase).
- **Rebase, don't merge, when updating your feature branch.** Keeps history
  linear. `git fetch origin dev && git rebase origin/dev`.

## PR format

- **Title**: conventional-commit style (`feat(scope): ...`, `fix(scope): ...`).
- **Body**: a short `## Summary` bullet list, then anything reviewers need to
  know. That's it.
- **Do NOT** include a "Test plan" section with an empty checklist.
- **Do NOT** include a "Generated with Claude Code" footer or similar tool
  attribution in the PR body. Commit trailers (`Co-Authored-By:`) are fine.
- Link related issues: `fixes #123`, `refs #456`.

## Before opening a PR

- [ ] Branch is off latest `dev`
- [ ] Commits are atomic and conventionally-named
- [ ] No unrelated files in the diff
- [ ] Tested the change in a browser (for UI work) or ran the affected code
- [ ] No secrets, `.env` files, or credentials in the diff
- [ ] Commit messages explain **why**

---

# Code rules

The git workflow above gets your change merged cleanly. These rules get it
*accepted*. Violate them and reviewers will ask for a rewrite.

## Architectural invariants

OpenVoiceUI is a browser UI for a **single user connecting to their own
server**. The browser is a terminal; the server is the source of truth.
Features must respect this.

**Persistence — never in the browser:**
- ❌ Never use `localStorage`, `sessionStorage`, or `IndexedDB` for app
  state (selections, preferences, lists, manifests, histories, settings)
- ✅ All state → save to the server via an API endpoint
- ✅ All data loading → `fetch()` from the server on page init
- ✅ JSON files on the server filesystem are the database — use them

When a canvas page or feature needs to persist anything, create or use a
server endpoint (`GET/POST /api/<thing>`) and write JSON files into the
uploads/config directories. The browser should never be the authoritative
store for anything.

**AI-generated content is permanent the moment it exists:**

Every generated image, audio clip, or video costs real compute / API
money. Treat every generation as permanent from the instant the provider
returns it.

- ❌ Never store generated content only in browser memory (data URLs,
  ArrayBuffers, canvas elements)
- ❌ Never wait for a user action ("Save", "Add to list") before writing
  the file to disk
- ❌ Never let a UI action (dismiss, clear, remove from view) delete the
  underlying server file
- ✅ API returns generation → **immediately POST to `/api/upload`** → file
  saved to the server's uploads directory
- ✅ UI displays using the server URL (`/uploads/ai-gen-xxx.png`), not a
  data URL as the source of truth
- ✅ Manifests and lists store URLs, not raw bytes

**Additive data, never replace:**

Every paid API response is valuable. Tool views should show full result
history, not just the latest. Never replace, never discard previous
results without an explicit user delete action against the server.

## Never delete

When something looks broken or obsolete, the instinct to `rm` it is
usually wrong. Files that look like dead code often have a reason.

- ❌ Don't `rm` files as cleanup
- ❌ Don't delete "old" or "broken" scripts, configs, or executors
- ❌ Don't delete logs, research files, prompts, or outputs
- ✅ Add new files, edit existing ones, or rename with a `.old` suffix
- ✅ If you're genuinely certain something is unused, flag it in your PR
  and let a reviewer confirm before deletion

## Fix root causes, never regress

When something is broken, find *why* and fix it. Disabling, bypassing, or
removing a feature to make an error go away is regression, not repair.

- ❌ "Let me disable this so it stops erroring"
- ❌ "Easiest fix is to turn off the cron job / feature flag"
- ❌ "Just remove this check"
- ✅ Find the root cause (missing file? bad config? race condition?
  wrong permissions?) and fix it directly
- ✅ Verify the system now works as originally intended

If a fix ends up removing code, the PR body must explain *why* the code
was doing the wrong thing — not just that it was easier to delete it.

## No hardcoded voice responses

Everything the agent says out loud must come from the AI model, not from
canned strings in JS. If you're tempted to add
`TranscriptPanel.addMessage('assistant', 'Your song is ready!')`, stop —
route it through the conversation API or emit it as a system-prefixed
event that the agent can respond to on the next turn.

System *status* messages (`ActionConsole.addEntry('system', ...)`) are
fine because they go to the Actions panel, not TTS. The rule is about
anything the user hears spoken.

## UI design rules

- **Mobile first.** Most users interact with this app on a phone. Start
  with a mobile layout and enhance for desktop. Touch targets ≥ 44px,
  inputs ≥ 16px font, single-column cards, bottom-sheet modals where
  appropriate.
- **No purple.** Default accent is `#3b82f6` (blue). No purple gradients,
  no purple backgrounds, no purple anything. Use neutral / steel-blue
  palettes for professional dark themes.
- **No emoji as UI chrome.** No emoji in labels, buttons, headers, or
  card titles. Emoji inside transcript text or system log lines is fine;
  emoji as permanent interface decoration is not.
- **Test in a real browser before claiming a UI change is done.** Type
  checkers and unit tests verify code correctness, not feature
  correctness. If you can't test the UI, say so explicitly in the PR.

## Canvas page CSP

Canvas pages (`pages/*.html`, served at `/pages/<name>.html`) run under a
restrictive Content Security Policy enforced by `routes/canvas.py`. By
default the CSP blocks:
- External scripts (`script-src`)
- External API calls (`connect-src`)

If your canvas page needs an external resource, **allowlist it in
`routes/canvas.py`** — don't try to inline-relax the CSP per page.

- **Tailwind CDN is allowed** (`cdn.tailwindcss.com`). You can use it
  directly in canvas pages.
- **Dual layout pattern**: separate `mobile-view` and `desktop-view` HTML
  blocks in the same page, rather than trying to make one layout
  responsive via class juggling.
- **No caching.** Canvas pages are live dashboards. The canvas route
  serves them with `Cache-Control: no-store, no-cache`. Don't add
  caching at any layer.

## Voice / STT code (`src/providers/WebSpeechSTT.js`, `src/app.js`)

Before touching speech recognition code, read this section. Four previous
attempts at "fixing" the STT flow ended up making it worse because
contributors didn't know the constraints.

- Chrome allows **only one** `SpeechRecognition` instance to `.start()`
  at a time, but two can **exist** simultaneously. This is how
  `WebSpeechSTT.recognition` (conversation STT) and
  `WakeWordDetector.recognition` (wake-word listener) coexist.
- **Both instances must be created eagerly in the constructor and never
  destroyed.** `src/app.js` monkey-patches them for PTT support, and
  lazy-init / destroy-and-recreate patterns break those patches.
- **The abort loop in `WakeWordDetector` is normal.** Chrome periodically
  drops the SpeechRecognition connection; the `onend` handler restarts
  after a short delay. Speech *is* captured between abort cycles. Do not
  try to eliminate it.
- Toggle via `.start()` / `.stop()`, not by creating and nulling
  instances.
- If you change PTT, wake word, or call lifecycle, test the full flow in
  a real browser: wake word → call start → greeting TTS → STT listening
  → user speech → AI reply → sleep tag → call end → wake word resume.

## Package manager

Use **`pnpm`**, not `npm`. The lockfile is `pnpm-lock.yaml`. Running
`npm install` will create `package-lock.json`, desync dependencies, and
break CI.

```bash
pnpm install          # install deps
pnpm add <pkg>        # add a dep
pnpm remove <pkg>     # remove a dep
```

## Secrets

Never commit:
- `.env` files
- API keys, tokens, passwords
- Private SSH keys
- Database dumps
- Anything in `uploads/` or generated content directories

External service credentials should be declared in a plugin's
`plugin.json` as a vault credential, not hardcoded into JS or Python.
The credential vault writes them into agent/plugin `.env` files at
runtime.

If a secret lands in a commit, rotate it immediately and amend/rebase
the commit out of history before pushing.

## Parked / quarantined plugins

Sometimes a plugin has known issues but we don't want to lose the code
— we just want it out of `main`/`dev` until someone stabilizes it. The
pattern we use:

1. **`parked/<plugin-name>`** branch — a long-lived branch off `dev` (or
   `main` for the plugin catalog repo) that keeps the plugin files
   intact.
2. **GitHub issue** on the repo documenting what breaks, what was tried,
   and the acceptance criteria for un-parking.
3. **Removal PR** — a small `chore/remove-<plugin>` PR deletes the
   plugin from `dev`/`main` so the loader no longer sees it.
4. **Draft PR** from `parked/<plugin>` back to `dev`/`main`, titled
   `[PARKED] Re-add <plugin> when stable`, linking the issue. GitHub
   blocks merge on draft PRs, so this can't be shipped by accident.
5. Optional `do-not-merge` label on the draft PR as a second signal.

**Rules for parked branches:**
- Branch prefix **must** be `parked/*` — anything else is stale/WIP.
- Do not delete `parked/*` branches when "cleaning up" the branch list.
  They are intentional quarantine.
- Fix work happens ON the parked branch. When the issue's acceptance
  criteria are met, mark the draft PR ready, review, merge.
- If a parked plugin is abandoned for good, close the draft PR with a
  note and tag the branch with `archived/<plugin>-<date>` before
  deletion.

## Anti-patterns

- Pushing straight to `dev` without a feature branch — no review, no
  revert unit, history gets messy.
- Giant PRs mixing 5 unrelated things — impossible to review, impossible
  to revert one piece.
- "Fix" commits that disable the broken thing instead of fixing it.
- Committing secrets, `.env` files, or large binaries.
- Rewriting shared history (`git push --force` on `main` or `dev`).
- Bundling "drive-by cleanup" into an unrelated fix — if you see
  something else that needs fixing, open a separate PR.
- Adding `localStorage` "just for this one thing".
- Inlining a fallback for an AI provider that silently drops generated
  content when the real provider is down.
