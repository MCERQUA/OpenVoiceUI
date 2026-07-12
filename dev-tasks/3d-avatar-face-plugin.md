# Plan: 3D Avatar Face Plugin for OpenVoiceUI

**Created:** 2026-06-01  
**Requested by:** Mike via SMS  
**Stack:** TalkingHead.js + VRM (open standard, no vendor lock-in)  
**Why TalkingHead.js:** Three.js + three-vrm, designed for AI chatbot faces, bring-your-own TTS, active 2024-2025 development. VRM is an open standard — swap avatars by swapping the .vrm file.

---

## Goal

Add a `talkinghead-3d` face option to OpenVoiceUI that renders a fully animated 3D avatar driven by the existing postMessage face API (amplitude, mood, state). Works as a drop-in face plugin — no changes to core OVU required.

---

## Architecture

```
OpenVoiceUI face-box (iframe ~380×380px)
  └── talkinghead-3d.html
        ├── Three.js scene (transparent bg)
        ├── TalkingHead.js instance
        │     ├── VRM avatar file (loaded from /api/faces/assets/)
        │     ├── Idle animation loop
        │     └── Lip sync via morph targets
        └── postMessage bridge
              ├── face:amplitude  → drive jaw/lip morph targets directly
              ├── face:state      → switch animation (idle / thinking / listening)
              ├── face:mood       → facial expression morph (happy, sad, surprised...)
              └── face:speaking   → start/stop talking animation
```

---

## Phase 1 — Static face prototype (1–2 hours)

**Files:**
- `/mnt/system/base/OpenVoiceUI/default-faces/talkinghead-3d.html`

**Tasks:**
1. Set up HTML shell — transparent canvas, no scroll, fills iframe
2. Load TalkingHead.js from CDN (`https://cdn.jsdelivr.net/...`) or copy to `/app/static/talkinghead/`
3. Load a free test VRM avatar (e.g. from VRoid Hub CDN or bundled test asset)
4. Initialize Three.js scene + camera + lighting inside iframe
5. Confirm avatar renders and idles
6. Wire postMessage bridge:
   - `face:amplitude` → `talkingHead.setMorphTarget('jawOpen', value * 0.6)` style driving
   - `face:state=thinking` → tilt head / thinking animation
   - `face:state=listening` → subtle idle with attentive expression
   - `face:mood` → expression preset (happy/sad/surprised/neutral)
   - `face:speaking=true` → enable talking anim; `false` → return to idle

**Deliverable:** A face HTML file that renders and animates in the face-box. No server changes needed for Phase 1.

---

## Phase 2 — Avatar configurability (1–2 hours)

**Goal:** Each tenant can have their own avatar file; fallback to a default.

**Approach:**
- Face reads `?avatar=<filename>` query param from its src URL
- OVU loads face with: `faces/talkinghead-3d.html?avatar=danielle.vrm`
- VRM files served from `/api/faces/assets/<filename>` (new static endpoint) or existing `/uploads/`
- Per-tenant face config in `openclaw/workspace/face-config.json`:
  ```json
  { "face": "talkinghead-3d", "avatar": "danielle.vrm", "scale": 1.2, "cameraY": 0.1 }
  ```
- Server reads face-config.json and passes params when building the face iframe src

**Files:**
- Add `/api/faces/assets/` static route in `app.py` (or symlink to a shared dir)
- `face-config.json` per tenant workspace
- Update face iframe src builder to append query params

---

## Phase 3 — Lip sync quality (2–3 hours)

**Current postMessage data:** `face:amplitude` gives a 0.0–1.0 RMS value from TTS audio.

**Option A — Amplitude-driven (simple, works now):**
- Map amplitude → jawOpen morph target
- Add slight lip curl, brow raise at peaks
- Smoothed with lerp to avoid jitter

**Option B — Phoneme/viseme data (better quality, needs TTS change):**
- Requires TTS to emit viseme events (Azure TTS, ElevenLabs streaming visemes)
- OVU sends `face:viseme { viseme: 'aa', duration: 80 }` events alongside audio
- TalkingHead.js has native viseme support if you drive it this way
- Deferred — Phase 3b once TTS pipeline supports it

**Phase 3a deliverable:** Smooth, natural-looking amplitude-driven lip sync.

---

## Phase 4 — Production polish (1 hour)

- Loading spinner while VRM downloads
- Fallback to 2D face if WebGL unavailable (mobile/low-end)
- Performance: target 60fps, reduce shadow quality on mobile
- Transparent background so OVU theme bleeds through
- Avatar framing presets (full body / bust / face-only via camera Y offset)
- Face manifest entry so it appears in the face picker UI

---

## File summary

| File | Action |
|------|--------|
| `default-faces/talkinghead-3d.html` | New — the face plugin |
| `app/static/talkinghead/` | New — TalkingHead.js lib + default VRM |
| `app.py` | Minor — add `/api/faces/assets/` static route |
| `openclaw/workspace/face-config.json` | New per tenant — avatar selection |

---

## Avatar sourcing (VRM)

- **Default test avatar:** VRoid Hub free avatars (CC0/CC-BY)
- **Custom per-tenant:** VRoid Studio (free, Windows/Mac app) → export .vrm
- **High quality:** Avaturn (free tier, GLB with ARKit blendshapes → convert to VRM)
- No dependency on ReadyPlayerMe (Netflix-acquired, uncertain future)

---

## Open questions for Mike

1. Start with Phase 1 POC only, or build all 4 phases?
2. Pick a default avatar for the POC — or use a placeholder cube for now?
3. Should the 3D face be the new default for new tenants, or opt-in only?
