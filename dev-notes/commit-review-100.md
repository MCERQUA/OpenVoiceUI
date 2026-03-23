# OpenVoiceUI — Last 100 Commits Review

**Generated:** 2026-03-17
**Range:** 2026-03-06 (PR #110) → 2026-03-17 (npm CLI)
**Total:** 100 commits in 12 days

---

## Raw Commit Log

```
c5bcf29 2026-03-17 feat: add npm package and CLI for easy installation
208c746 2026-03-17 feat: steer mode — voice interrupts redirect instead of killing tasks
bd1fdcb 2026-03-17 fix: allow external HTTPS images in canvas page CSP
037e0c1 2026-03-17 fix: resolve Resemble voice names to UUIDs
db18a4b 2026-03-17 fix: use module-level global cache for Resemble voices
8075f15 2026-03-17 fix: don't fetch Resemble voices on provider listing
05a3140 2026-03-17 feat: add Resemble AI (Chatterbox) TTS provider
61252f0 2026-03-17 feat: add Deepgram WebSocket streaming STT with WebSpeech fallback
96e54f4 2026-03-16 fix: change default TTS provider from supertonic to groq everywhere
d3c0054 2026-03-16 fix: remove openclaw restart from inject — breaks network_mode dependency
197602c 2026-03-16 fix: Windows compatibility for device identity injection
3ad3f9f 2026-03-16 fix: sync device identity from Docker volume instead of always injecting
b9e7e6e 2026-03-16 fix: pre-pair device identity at install, inject via docker exec at start
32d53a3 2026-03-16 fix: add role and scopes to auto-approved device entries
534d6d1 2026-03-16 fix: remove gateway-level dangerouslyDisableDeviceAuth — unrecognized key crashes OpenClaw
ab41bad 2026-03-16 fix: add device auto-approval on startup — dangerouslyDisableDeviceAuth only affects control UI
82ac00a 2026-03-16 fix: auto-detect default model from provided API keys — prevents 401 on wrong provider
f1b6828 2026-03-16 fix: move token declaration before openclaw config — fixes ReferenceError on install
77cf4ea 2026-03-16 fix: match gateway auth token between .env and openclaw.json, dynamic port in start.js
46df516 2026-03-15 fix: restore Pinokio v3.7 menu format — v2.5 declarative menu causes black screen
7385b59 2026-03-15 fix: rewrite Pinokio installer — reliable key passing, simplified lifecycle, proper auth-profiles
c78ac82 2026-03-15 fix: add auth token to canvas upload requests + fix revokeObjectURL error
724e7de 2026-03-15 fix: auto-approve device pairing on startup instead of pre-pairing
f1e82cc 2026-03-15 fix: clear devices/pending.json during install to prevent stale repair blocks
dc0df3f 2026-03-15 fix: correct json.set syntax + single step to preserve input variable
1ab0b41 2026-03-15 fix: setup-config.js prompts in terminal when Pinokio templates fail
3fe7701 2026-03-15 fix: simplify install — use fs.write with JSON.stringify for input persistence
6a8118e 2026-03-15 fix: pre-pair device identity to bypass OpenClaw NOT_PAIRED errors
b34efba 2026-03-15 fix: use json.set instead of shell.run env for Pinokio template resolution
fbcd054 2026-03-15 feat: add Deepgram STT key to install, validate required keys, reorder form
bdbd59e 2026-03-15 fix: Pinokio install — generate auth-profiles, fix gateway connection, dynamic WS port
06642ad 2026-03-15 fix: pass CLAWDBOT_AUTH_TOKEN to openvoiceui in Pinokio override
c201475 2026-03-15 feat: add Cerebras API key to Pinokio installer
da75f66 2026-03-15 feat: install form matches all OpenClaw onboarding providers
b8e005e 2026-03-15 feat: add Z.AI (GLM) API key to Pinokio installer
1cee564 2026-03-15 fix: Clerk auth failure falls back to local mode instead of blocking forever
78ec56b 2026-03-15 feat: local-mode menu — settings and admin access without Clerk auth
eaf870c 2026-03-15 feat: Pinokio install asks for AI provider keys
594ecaf 2026-03-15 fix: Pinokio install — supertonic model cache, gateway auth, healthcheck timing
ac99eba 2026-03-15 fix: remove S&S Activewear proxy — client-specific, not core product
f79575a 2026-03-15 docs: update OpenClaw tested version to 2026.3.13
5af015f 2026-03-15 chore: remove game pages from core defaults
ca302a8 2026-03-15 feat: core default pages + canvas auth token bridge
1085468 2026-03-15 fix: stop button mutes TTS audio instead of killing session
55e3dd0 2026-03-15 feat: OpenClaw protocol compatibility layer for upgrade resilience
bf99cbd 2026-03-15 feat: pre-existing local changes (app.js, static_files, desktop)
5e855e4 2026-03-14 fix: start.js event regex matched too early — supertonic fired before model loaded
2b09920 2026-03-14 fix: canvas asset auth, PTT mute clearing, fallback messages, icon gen model
a362f9d 2026-03-14 fix: Pinokio install Docker check — extract to standalone script
005cf38 2026-03-14 fix: rewrite Pinokio scripts — native fs.write, correct event capture, foreground docker compose
5126893 2026-03-14 fix: Pinokio installer — correct openclaw config schema, Windows Docker PATH fallback
5c5b24a 2026-03-14 fix: openclaw.json config keys for v2026.3.2 schema
41dde21 2026-03-14 fix: restore missing closing braces in MUSIC_NEXT blocks after matchAll patch
3c14164 2026-03-14 fix: Pinokio start — false-match sentinel + 10min timeout for first launch
38c4b47 2026-03-14 fix: start.js reads port from .env instead of kernel.port()
f5565e7 2026-03-14 feat: suno proactive agent notify + canvas custom icons
e48affb 2026-03-14 fix: Pinokio install marker + Docker daemon check
3a27951 2026-03-14 fix: stop install loop when Docker Desktop is not running
b66f989 2026-03-14 fix: gracefully skip env var overrides with invalid types
b040a81 2026-03-13 fix: replace icon with correct requested icon (fc.png, 239KB)
b3bc685 2026-03-13 feat: add custom OpenVoiceUI icon (512x512 AI chip character)
2422b3d 2026-03-13 fix: restore Docker-based Pinokio scripts with v3.7 schema fixes
cb08a63 2026-03-13 fix: rewrite Pinokio scripts — no Docker required, use Python venv + npm
ba12639 2026-03-13 fix: reduce canvas polling 3s→10s, pause all polling when tab hidden
425ecfc 2026-03-13 fix: Suno generates 1 song per request instead of 3 duplicates
083ca5b 2026-03-13 docs: update README with all dev branch features
2ce513e 2026-03-13 feat: desktop page persistence, canvas protection, STT error reporting, task-stopped UX
c724b58 2026-03-12 Merge pull request #122 from MCERQUA/feat/pinokio-install
a8873b1 2026-03-12 feat: Pinokio one-click installer + VS Code dev container
24b5b89 2026-03-12 docs: note issue reporter button is temporary (dev phase only)
01a7065 2026-03-12 feat: HuggingFace image gen — FLUX.1, SD3.5 with quality and aspect ratio presets
d75c895 2026-03-12 feat: issue reporter — in-app bug/feedback reporting modal with session context
f72f230 2026-03-11 feat: connection resilience, S&S Activewear proxy, TTS improvements
dba90e0 2026-03-09 remove agent workspace files from repo
6db9936 2026-03-09 Merge pull request #119 from MCERQUA/dev
40bc411 2026-03-09 feat: Deepgram Nova-2 STT provider — reliable paid STT replacing Chrome Web Speech
4546c2f 2026-03-09 feat: uploads list API, voice English enforcement, CANVAS_URL TTS fix, chip style
d7ea639 2026-03-09 feat: agent activity ticker, openclaw UI proxy, client name branding
af51aea 2026-03-08 Merge pull request #118 from MCERQUA/dev
1e48bce 2026-03-09 feat: session auto-recovery, Chrome STT, stop button drain timer
15675a0 2026-03-09 feat: workspace API, icon generation, emulator support, STT + vision improvements
d24c8d3 2026-03-09 fix: context menu click reliability — addEventListener + try-catch + max z-index modal
e993cec 2026-03-09 fix: flatten context menu — remove submenus that require hover to open
33c6ba6 2026-03-08 feat: desktop OS — all menu actions working, new folder UX, shortcuts
0e0266b 2026-03-08 feat: desktop OS — trash drag-drop, delete key, right-click recycle bin
603a589 2026-03-08 feat: add file-explorer + update desktop OS to default-pages
7928bee 2026-03-08 feat: agent activity chip + live action display in UI
7d4d987 2026-03-08 Merge pull request #112 from MCERQUA/dev
55a6857 2026-03-08 feat: OpenClaw integration fixes — STT splitting, context caps, ActionConsole, tag-only fallback
a8345bb 2026-03-08 feat: pin OpenClaw to tested version, add compatibility checking and requirements doc
d680749 2026-03-08 feat: admin lock/URL columns, canvas padded mode, silence detection fix, Clerk v5 pin
bde9a64 2026-03-08 feat: desktop OS — wallpaper upload + fix New Folder (iframe prompt blocked)
ca557cf 2026-03-07 fix: exempt init-time read-only APIs from Clerk auth requirement
6d96faf 2026-03-07 fix: sync Clerk token to __session cookie for iframe canvas auth
3d3fab1 2026-03-07 fix: skip auth for default pages (desktop menu blocked by signin in iframe)
4dae172 2026-03-07 feat: ship desktop OS menu as default page, auto-seed on startup
bfe4689 2026-03-07 Merge pull request #111 from MCERQUA/dev
db72c80 2026-03-07 fix: Z.AI direct fallback on double-empty + auto-restart openclaw
4143749 2026-03-07 feat: desktop OS globe menu + STT hallucination filter + canvas nav helpers
c7ca7b8 2026-03-06 Merge pull request #110 from MCERQUA/dev
```

---

## Commit Breakdown by Category

### By Type
| Type | Count | % |
|------|-------|---|
| fix: | 53 | 53% |
| feat: | 38 | 38% |
| docs: | 4 | 4% |
| chore/merge/other | 5 | 5% |

### By Area
| Area | Commits | Notes |
|------|---------|-------|
| **Pinokio installer** | ~30 | Biggest single area. Multiple rewrites. |
| **Device pairing / auth** | ~12 | Overlaps with Pinokio — NOT_PAIRED, auto-approve, pre-pair |
| **Desktop OS / canvas** | ~12 | wallpaper, context menu, file explorer, default pages |
| **TTS providers** | ~6 | Resemble added, supertonic→groq default, voice cloning |
| **STT providers** | ~4 | Deepgram Nova-2, Deepgram WebSocket streaming, hallucination filter |
| **OpenClaw integration** | ~6 | compat layer, config schema, version pinning, empty response handling |
| **Voice conversation flow** | ~5 | steer mode, stop button, session recovery, silence detection |
| **Canvas auth / security** | ~5 | Clerk token bridge, CSP, canvas asset auth |
| **Image generation** | ~2 | HuggingFace FLUX/SD3.5, icon generation |
| **Music** | ~2 | Suno duplicate fix, proactive notify |
| **Other** | ~6 | S&S removal, polling perf, issue reporter, README |

---

## Patterns Observed

### 1. THE PINOKIO SPIRAL — 30 commits for one installer

This is the most striking pattern. The Pinokio installer was written, then rewritten, then rewritten again across 4 days:

```
Mar 12: feat: Pinokio one-click installer + VS Code dev container    ← Initial
Mar 13: fix: rewrite Pinokio scripts — no Docker, use Python venv    ← Rewrite #1 (abandoned Docker)
Mar 13: fix: restore Docker-based Pinokio scripts with v3.7 fixes    ← Rewrite #2 (back to Docker)
Mar 14: fix: Pinokio installer — correct openclaw config schema...   ← Fix cascade begins
Mar 14: fix: rewrite Pinokio scripts — native fs.write...            ← Rewrite #3
Mar 14: 5 more fix commits for Pinokio                               ← Whack-a-mole
Mar 15: fix: rewrite Pinokio installer — reliable key passing...     ← Rewrite #4
Mar 15: 12 more fix commits for Pinokio                              ← More whack-a-mole
Mar 16: 8 more fix commits for device pairing/injection              ← Still going
```

**What happened:** The installer was shipped without being tested end-to-end on a clean machine. Each commit fixed one thing that broke something else. The device pairing problem alone took 10+ commits across 3 different approaches (dangerouslyDisableDeviceAuth → auto-approve → pre-pair → inject via docker exec → sync from volume).

**The circle:** Docker → no Docker → back to Docker. Pre-pair → auto-approve → pre-pair again → inject. json.set → shell.run env → fs.write → setup-config.js.

### 2. DEVICE PAIRING — 10+ commits to solve one auth problem

```
Mar 15: fix: pre-pair device identity to bypass NOT_PAIRED errors
Mar 15: fix: auto-approve device pairing on startup instead of pre-pairing
Mar 15: fix: clear devices/pending.json during install
Mar 16: fix: add device auto-approval on startup — dangerouslyDisableDeviceAuth only affects control UI
Mar 16: fix: remove gateway-level dangerouslyDisableDeviceAuth — unrecognized key crashes OpenClaw
Mar 16: fix: add role and scopes to auto-approved device entries
Mar 16: fix: pre-pair device identity at install, inject via docker exec at start
Mar 16: fix: sync device identity from Docker volume instead of always injecting
Mar 16: fix: remove openclaw restart from inject — breaks network_mode dependency
Mar 16: fix: Windows compatibility for device identity injection
```

This is the same problem being approached from 4 different angles, each partially working then failing in a different way. The root cause (OpenClaw's device pairing protocol) wasn't fully understood before coding started.

### 3. CANVAS AUTH — 4 commits in 1 day for iframe auth

```
Mar 07: fix: skip auth for default pages (desktop menu blocked by signin in iframe)
Mar 07: fix: sync Clerk token to __session cookie for iframe canvas auth
Mar 07: fix: exempt init-time read-only APIs from Clerk auth requirement
Mar 15: fix: add auth token to canvas upload requests + fix revokeObjectURL error
```

Auth in iframes is inherently tricky (cookie partitioning, CSP, cross-origin). Each fix uncovered the next layer of the problem.

### 4. FEATURE-BEFORE-FOUNDATION pattern

New features land before the underlying systems are stable:

- Desktop OS (5 commits, Mar 7-8) shipped while canvas auth was still breaking
- HuggingFace image gen (Mar 12) shipped same day as Pinokio installer that didn't work yet
- Resemble TTS provider (4 commits, Mar 17) added while TTS config options are still unwired (#115)
- Deepgram WebSocket STT (Mar 17) added while Chrome STT issues (#78, #79) are still open

This isn't necessarily wrong — features show value. But the fix commits pile up because each new feature inherits unresolved issues from the layers below.

### 5. MEGA-COMMITS mixing unrelated changes

Several commits bundle 3-5 unrelated changes:

```
55a6857: feat: OpenClaw integration fixes — STT splitting, context caps, ActionConsole, tag-only fallback
d680749: feat: admin lock/URL columns, canvas padded mode, silence detection fix, Clerk v5 pin
f72f230: feat: connection resilience, S&S Activewear proxy, TTS improvements
2b09920: fix: canvas asset auth, PTT mute clearing, fallback messages, icon gen model
```

Makes it hard to bisect issues, cherry-pick fixes, or understand what changed when.

### 6. FIX RATIO: 53% of all commits are fixes

More than half of the 100 commits are fixing things that were just shipped. The Pinokio area is the worst (roughly 5:1 fix-to-feature ratio), but it's a pattern everywhere. Many fixes are for things that could have been caught by:
- Testing on a clean environment before committing
- Understanding the dependency (OpenClaw device pairing protocol) before coding against it
- Smaller, tested commits instead of big-bang features

### 7. NO TESTS in any commit

Zero commits add or run automated tests. Everything is manual testing, which explains the high fix rate — issues are discovered after commit, not before.

---

## Velocity Summary

| Week | Dates | Theme | Commits |
|------|-------|-------|---------|
| Week 1 | Mar 6-8 | Desktop OS, OpenClaw integration, canvas auth | ~25 |
| Week 2 | Mar 9-12 | STT providers, Deepgram, Pinokio v1, image gen | ~20 |
| Week 3 | Mar 13-15 | Pinokio fix spiral, local mode, core cleanup | ~35 |
| Week 4 | Mar 16-17 | Device pairing fixes, Resemble TTS, steer mode | ~20 |

The project is moving fast — 100 commits in 12 days is high velocity. But roughly half that velocity is spent fixing things that just went in.

---

## Takeaways for Discussion

1. **Pinokio needs an end-to-end test script** — something that runs a clean install in a fresh Docker environment and verifies the app starts. Would have prevented 20+ fix commits.

2. **Device pairing should be a documented protocol** — the 10-commit spiral happened because the OpenClaw pairing protocol wasn't understood up front. A one-page doc on "how device auth works" would save future pain.

3. **Smaller commits, one thing each** — the mega-commits make it impossible to track what broke when. If STT splitting and context caps are separate changes, they should be separate commits.

4. **Feature gates** — new features (Resemble, Deepgram WS) going in while foundation issues (#115 TTS config, #168 subagent delivery) are open means more surface area to debug.

5. **A project ledger** — this review is basically what a living ledger would give you automatically. "What are we working on, what's stable, what's still circling."
