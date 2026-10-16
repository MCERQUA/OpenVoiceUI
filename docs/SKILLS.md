---
title: Bundled agent skills
---

# Bundled agent skills

OpenVoiceUI ships a set of OpenClaw **skills**: `SKILL.md` instruction files that teach the
agent how to use OpenVoiceUI's own features in depth. The voice system prompt covers the basic
tags; the skills cover the full workflows (building and verifying canvas pages, custom faces,
music generation, 3D pages). They live in the repository's `skills/` folder and every installer
puts them where OpenClaw loads them, so a fresh install's agent has them from the first
conversation.

## What is bundled

| Skill | What it teaches the agent |
|-------|---------------------------|
| `canvas-pages` | The canvas page workflow: write the file, open it with `[CANVAS:page-id]`, verify with `/api/canvas/context`, lint, page icons, categories, the postMessage bridge, page data storage (`/api/canvas/data`), uploads, privacy. |
| `canvas-page-visibility` | Making a page public or private again through the canvas manifest API, and the rules around it (only on an explicit request, cooldown, admin locks). |
| `canvas-web-design` | The classic design system for canvas pages when no Canvas Style is active: layout, motion, glass, KPI and dashboard patterns. Ships `canvas-template.html`. |
| `custom-faces` | Turning a page into the assistant's face: the `face:*` postMessage contract, the full-load performance gate, promotion and activation. |
| `suno-music` | Songs, jingles and sound effects through OpenVoiceUI's `/api/suno` and the `[SUNO_GENERATE:]` / `[JINGLE_GENERATE:]` tags, status, delivery links. Needs `SUNO_API_KEY`. |
| `threejs-canvas-integration` | Three.js games and 3D scenes as canvas pages: import map, CSP, same-origin assets, server-side save state. |
| `threejs-html-surfaces` | Experimental: painting live HTML onto named meshes with `/pages/_shared/html-surfaces.js` (Chrome flag required). |
| `voice-flow` | Voice conversation rules: acknowledge before tool calls, speakable replies, interruptions, tags always inside spoken words. |

All examples in the skills assume a stock install: the OpenVoiceUI API at
`http://localhost:5001` (the Docker setup shares one network namespace between the gateway and
the app) and an optional `X-Agent-Key: $AGENT_API_KEY` header, which is only needed when login is
required (Clerk with `CANVAS_REQUIRE_AUTH=true`).

## Where they install

OpenClaw scans its managed skills directory (`~/.openclaw/skills`, or `$OPENCLAW_STATE_DIR/skills`)
by default, including one level of grouping folders. OpenVoiceUI uses a grouping folder it owns:

```text
~/.openclaw/skills/openvoiceui/<skill>/SKILL.md
```

No `openclaw.json` change is needed.

| Installer | How the skills get there | Updating |
|-----------|--------------------------|----------|
| Docker Compose (`docker compose up`) | `docker-compose.yml` mounts `./skills` read-only at `/root/.openclaw/skills/openvoiceui` in the `openclaw` service. | `git pull`. The mount is live: OpenClaw's skills watcher picks up changes, or restart the `openclaw` container. |
| Pinokio and the npm CLI (`openvoiceui setup`) | Same mount, via `docker-compose.local.yml`. `setup-config.js` pre-creates `openclaw-data/skills/openvoiceui` so Docker does not create it root-owned. Pinokio's install and update steps run `node install-skills.js --check` to validate the bundle. | Pinokio **Update** (git pull + rebuild), or `git pull` / reinstall the npm package. |
| Native (`setup-sudo.sh`) | Copies the bundle into `~/.openclaw/skills/openvoiceui/` of the service user with `install-skills.js`. Copied, not symlinked: OpenClaw skips skill paths that resolve outside their skills root. | After `git pull`, run `node install-skills.js` as the service user. |

### `install-skills.js`

```text
node install-skills.js [--target DIR] [--dry-run] [--force] [--quiet]
node install-skills.js --check [--mountpoint DIR]
```

- The default target follows OpenClaw's own resolution: `$OPENCLAW_STATE_DIR`, else the folder of
  `$OPENCLAW_CONFIG_PATH`, else `$OPENCLAW_HOME/.openclaw` or `~/.openclaw`, plus
  `/skills/openvoiceui`.
- Safe to re-run. New files are added; files unchanged since the last install are updated; files
  you edited are **kept** with a warning (`--force` replaces them and saves your copy as
  `<file>.local-<timestamp>`). Nothing is ever deleted: a skill dropped from the bundle stays in
  place until you remove it. The install record is `.openvoiceui-skills.json` in the target.
- `--dry-run` prints what would change. `--check` only validates the bundle (frontmatter, names,
  sizes, no symlinks) and exits non-zero if it is broken.

## Adding your own skills

Never put your own skills inside the `openvoiceui/` folder: in Docker it is a read-only mount of
the repository, and on native installs it belongs to the installer. Use either:

- **Workspace skills** (highest precedence, one agent): `<workspace>/skills/<name>/SKILL.md`. In the
  Docker install the workspace is `/root/.openclaw/workspace`, which is `openclaw-data/workspace/`
  on the host for Pinokio and npm installs.
- **Managed skills** (every agent on the gateway): `~/.openclaw/skills/<name>/SKILL.md`, next to the
  `openvoiceui/` folder (`openclaw-data/skills/<name>/` on the host for Pinokio and npm installs).

A skill is a folder with a `SKILL.md` that starts with YAML frontmatter (`name` and
`description`). See the OpenClaw skills documentation for gating, allowlists and per-skill
configuration.

### Overriding a bundled skill

Copy the bundled skill into your **workspace** skills folder under the same name and edit it there.
Workspace skills outrank managed ones, so your copy wins and updates to the bundled copy never
touch it. (A same-named skill elsewhere in the managed folder is not a reliable override, because
it sits in the same precedence tier as the bundled one.)

### Hiding bundled skills from an agent

Skill visibility is separate from location. Use OpenClaw's agent allowlists
(`agents.defaults.skills` or `agents.list[].skills`) to control which skills an agent sees.

## Precedence, for reference

OpenClaw resolves same-named skills in this order, highest first: workspace `skills/`, workspace
`.agents/skills`, `~/.agents/skills`, managed `~/.openclaw/skills` (where the bundle lives),
OpenClaw's own bundled skills, then `skills.load.extraDirs` and plugin skills. The OpenVoiceUI
skills therefore take priority over OpenClaw's generic bundled skills and yield to anything you
put in your workspace.

## Contributing a skill to the bundle

A skill belongs in `skills/` only if it documents a feature that ships in this repository.
Requirements, enforced by `tests/test_bundled_skills.py`:

- `skills/<name>/SKILL.md` with frontmatter whose `name` equals the folder name and a
  `description` of at most 400 characters (all skill descriptions share a small prompt budget).
- Real files only, no symlinks; `SKILL.md` under 256 KB.
- Deployment-neutral: no server paths, hosted domains, account identifiers, keys, or
  customer-specific content. Write for a stock install (`http://localhost:5001`, `<your-domain>`).
- Every endpoint, tag and field the skill mentions must exist in the code.
