# Releasing OpenVoiceUI

## Version format — Calendar Versioning (CalVer), `YYYY.M.D`

OpenVoiceUI versions are **date-based**. The version number *is* the release date.

- **Format:** `YYYY.M.D` — year.month.day, **no zero-padding** (e.g. `2026.5.4`, `2026.5.30`).
- **`package.json` `"version"`:** bare, **no** `v` prefix → `"2026.5.30"`.
- **Git tag:** **with** `v` prefix → `v2026.5.30`.
- **Second (or later) release on the same day:** append `-N` → `v2026.5.30-1`, `-2`, …

This has been the scheme for **every** release (`2026.3.27` → `2026.5.4`, ~15 tags).
**Do not** switch to semver (`1.2.3`) or any other format. This is the one format.

### Do NOT confuse the app version with bundled component versions
The OpenVoiceUI **app** version (CalVer, above) is independent of the versions of things it
bundles:
- **OpenClaw** has its own version scheme (e.g. `2026.5.7`) — pinned separately (see below).
- The **Hermes** plugin has its own tag (e.g. `nousresearch/hermes-agent:v2026.5.7`).

When documenting, state them **separately** — e.g.
"OpenVoiceUI `2026.5.30` (bundles OpenClaw `2026.5.7`)". Never set the app version to match a
component version.

## Release steps
1. On `dev`: bump `package.json` `"version"` to today's date `YYYY.M.D` (`-N` if it's a repeat
   release the same day).
2. Merge `dev → main` (PR, CI green).
3. Tag the release commit on `main`:
   ```bash
   git tag vYYYY.M.D
   git push origin vYYYY.M.D
   ```
4. **Publish a GitHub Release** from that tag (this is the artifact people see — always
   create it, not just the tag). Use the **exact** title + body format below.
5. Rebuild images from `main`, verify, then point the live source dir at `main`.

## GitHub Release format — DO NOT improvise, every release looks identical

Create with `gh release create vYYYY.M.D --repo MCERQUA/OpenVoiceUI --title "..." --notes "..."`.

- **Title:** `OpenVoiceUI YYYY.M.D` — the word `OpenVoiceUI` + a space + the bare CalVer
  (no `v`, no "Release", no extra words). e.g. `OpenVoiceUI 2026.5.30`.
- **First line of body:** `Release YYYY.M.D — bundles all work since <PREV_VERSION> (<one-line summary>).`
- **Then these `## ` sections, in this order. Include a section ONLY if it has entries; omit empty ones.**
  Compile entries from `git log --oneline v<PREV>..main` (drop merge commits). One bullet per change,
  past tense, append the PR number `(#NNN)` and `— @author` for community PRs.

  ```markdown
  Release YYYY.M.D — bundles all work since <PREV> (<summary>).

  ## Voice / conversation
  - <STT / wake-word / TTS / call-lifecycle changes>

  ## Features
  - <new endpoints / engines / capabilities>

  ## Fixes
  - <bug fixes not covered above>

  ## Community contributions
  - <external-contributor PRs> (#NNN) — @author

  ## Dependencies
  - <one bullet listing the bumped packages + versions; note security fixes>

  ## Ops
  - <OpenClaw/Hermes pins, RELEASING/CI/infra changes>
  ```

  Reference exemplar: the `v2026.5.30` release body. Match its tone and density exactly.

## OpenClaw pin — separate from the app version
The bundled OpenClaw version is pinned in three installer paths + `services/gateways/compat.py`.
Bump it **only** via `bash bump-openclaw-version.sh <version>` — never hand-edit (it keeps the
three paths in sync). This is a *component* bump, not an app release; the app version stays CalVer.
