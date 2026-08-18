# Camera Capture — Flagship Feature Build Plan
*Researched by Opus 2026-07-29. Full CompanyCam competitive analysis + phased implementation.*

## Bugs found in current code (fix before Phase 1)

1. **DATA-LOSS RACE (ship-blocker):** The current page keeps all photos in one flat array and does a whole-file replace via `POST /api/canvas/data/capture-manifest.json`. Two devices uploading simultaneously = last-write-wins, loser's photos vanish from manifest silently (JPEGs orphan in /uploads/). Must fix in Phase 1.

2. **iOS CAMERA-CONFLICT:** `src/app.js:955` opens a front-facing stream for the vision feature. A second `getUserMedia()` while a stream is active sets the earlier track's `muted=true` with no way to unmute. Opening Camera Capture kills the vision feed until app reload. Fix: camera-arbitration postMessage bridge.

3. **`routes/albums.py` already exists and is unused** — 271 lines of CompanyCam-style album API the page ignores. Supersede with new `/api/capture/` blueprint instead of extending.

**De-risking (all good):**
- Canvas iframe already has `allow="camera; geolocation"` — no change needed
- `camera=(self), geolocation=(self)` in Permissions-Policy — already set
- ffmpeg 7.1.5 is inside the OVU container — Phase 4 video is server-side ffmpeg, no Remotion container
- Pillow 12.3 + pillow-heif already there for thumbnails
- `/api/tts/generate` and `/api/suno` already exist for voiceover and music

---

## Strategic positioning: where we beat CompanyCam

| Gap | Evidence | Our counter |
|---|---|---|
| **3-user minimum / per-seat tax** | Core $63/mo, Crew $129/mo — all with 3-seat floor. Solo contractors pay ~$2k/yr in phantom seats. Loudest complaint across all reviews. | Camera Capture included in flat tenant rate. Solo operators are our best segment. |
| **Marketing Suite is $99/mo bolt-on** | CompanyCam AI video/marketing on top of base plan. G2: "AI doesn't always work super well." | Photo → reel is native and free in our stack (ffmpeg + Suno + TTS all installed). CompanyCam validated demand then priced it as upsell. |
| **Subscription stacking** | Contractors pay CompanyCam PLUS their FSM (Jobber/HousecallPro). 10-person team = $19k–30k/yr. | OVU already has CRM, booking, website builder, voice agent, SEO. Capture plugs into an existing platform. |
| **Reliability + search** | "takes forever to upload then says upload failed"; "search doesn't work reliably"; offline batches "upload out of order" | Server-authoritative append (no client-side manifest rewrite), ordered offline queue with monotonic seq numbers, AI captions indexed for semantic search. |

**What we MUST match to be credible:**
- GPS + timestamp locked at capture moment (not at upload) — the insurance/dispute evidence that makes contractors say "it paid for itself"
- **Before/After ghost-overlay capture** (translucent "before" over live viewfinder to match framing) — their single most-loved differentiator
- Photo-required checklists — the accountability gate that changes crew behavior
- Live shareable project timeline link, revocable

---

## Data model

### Storage layout (NEW mount needed)
```
/mnt/clients/<tenant>/openvoiceui/captures/    → /app/runtime/captures
  index.json                    # lightweight album summaries
  albums/
    <album_id>/
      album.json                # full record incl. photos[]
      media/
        <photo_id>.jpg          # original (EXIF-stripped except orientation)
        <photo_id>.mp4          # video clips
        <photo_id>.annotated.jpg
      thumbs/
        <photo_id>.jpg          # 400px, generated on ingest
      exports/
        report-<ts>.html
        reel-<ts>.mp4
  shares/
    <token>.json                # {album_id, scope, created_at, revoked}
  queue-receipts/
    <client_uuid>.json          # offline idempotency ledger
```

**Security note:** `/uploads/` is in `_PUBLIC_PREFIXES` (unauthenticated). Job addresses + client names + photos of homes must NOT sit behind guessable paths. Add bind mount for captures dir, serve only through gated `/api/capture/*` routes and share-token route.

### Album record (key fields)
```json
{
  "id": "a1b2c3d4e5f6",
  "name": "Johnson Residence — Attic Foam",
  "client_name": "Dave Johnson",
  "address": "1420 W Palm Ln, Phoenix AZ",
  "lat": 33.4712, "lng": -112.0891, "geofence_m": 120,
  "status": "in_progress",
  "folders": ["Before", "During", "After", "Damage", "Completion"],
  "share": { "token": null, "enabled": false, "scope": "all" },
  "photos": []
}
```

### Photo record (key fields)
```json
{
  "id": "p7h8j9k0l1m2",
  "type": "photo",
  "folder": "Before",
  "captured_at": "2026-07-29T16:12:04Z",   // CLIENT clock at shutter — legal timestamp
  "uploaded_at": "2026-07-29T16:12:09Z",
  "lat": 33.4712, "lng": -112.0891, "gps_accuracy_m": 8,
  "note": "", "tags": [], "ai_caption": "",
  "annotated": false, "annotated_url": null,
  "pair_before_id": null,                    // before/after pairing
  "hidden_from_share": false,
  "client_uuid": "...",                      // offline idempotency key
  "seq": 41                                  // monotonic per-device ordering
}
```

`captured_at` + GPS locked CLIENT-SIDE at shutter press. NEVER derived from upload arrival time.

---

## API: /api/capture/* (new blueprint)

### Albums
```
GET    /api/capture/albums                    # list with filter/search
POST   /api/capture/albums                    # create
GET    /api/capture/albums/<aid>              # full record + photos
PATCH  /api/capture/albums/<aid>              # update (merge semantics, never blank)
POST   /api/capture/albums/<aid>/archive      # soft-delete (never hard delete)
GET    /api/capture/albums/nearby             # ?lat=&lng=&radius=200 — GPS auto-suggest
POST   /api/capture/albums/<aid>/folders      # add sub-folder
```

### Photos
```
POST   /api/capture/albums/<aid>/photos                # upload (multipart: file, captured_at, lat, lng, folder, client_uuid, seq)
                                                        # Idempotent on client_uuid — retried offline uploads return existing record
GET    /api/capture/albums/<aid>/media/<pid>           # gated binary, Accept-Ranges for video
GET    /api/capture/albums/<aid>/thumb/<pid>           # 400px, Cache-Control: private max-age=86400
PATCH  /api/capture/albums/<aid>/photos/<pid>          # note, tags, folder, hidden_from_share, pair_before_id
POST   /api/capture/albums/<aid>/photos/<pid>/annotate # flattened PNG → .annotated.jpg (original untouched)
POST   /api/capture/albums/<aid>/photos/<pid>/move     # copy-then-relink, source file retained
DELETE /api/capture/albums/<aid>/photos/<pid>          # SOFT DELETE ONLY — sets deleted:true, file stays on disk
```

### AI
```
POST   /api/capture/albums/<aid>/photos/<pid>/describe    # server-side vision → ai_caption + ai_tags
POST   /api/capture/albums/<aid>/ai/batch-describe        # bulk, haiku tier (model discipline)
POST   /api/capture/albums/<aid>/ai/damage-report         # tagged photo set → findings JSON
POST   /api/capture/albums/<aid>/ai/marketing-copy        # before/after set → caption variants
GET    /api/capture/search                                 # ?q= semantic across captions/tags/notes/albums
```

### Sharing (Phase 3)
```
POST   /api/capture/albums/<aid>/share             # {scope, folders?, expires_at?} → {token, url}
DELETE /api/capture/albums/<aid>/share             # revoke
GET    /share/capture/<token>                      # PUBLIC — client portal HTML
GET    /api/capture/public/<token>                 # PUBLIC — sanitized album JSON
GET    /api/capture/public/<token>/media/<pid>     # PUBLIC — token-gated binary
POST   /api/capture/public/<token>/approve         # {milestone_id, name, signature_png?}
```

### Video / reports (Phase 4)
```
POST   /api/capture/albums/<aid>/report    # → exports/report-<ts>.html (print-to-PDF via browser)
POST   /api/capture/albums/<aid>/reel      # {photo_ids, aspect, music_task_id?, voiceover_text?, transition}
GET    /api/capture/jobs/<job_id>          # render progress
```

---

## Camera button UI spec (live-preview shutter)

### Layout
```
        ┌─────────────────────────────────┐
        │  [⚡flash]  [⊞grid]  [⏱timer]   │   ← 44px tap targets
        │                                 │
        │            ╭─────────╮          │
        │           │  LIVE FEED│         │   ← 92px circle, real rear-camera feed
        │            ╰─────────╯          │      inside, object-fit:cover
        │      ◜───────────────────◝      │   ← 104px progress ring (SVG stroke-dashoffset)
        │                                 │
        │  [⟲flip]              [🖼 last] │
        └─────────────────────────────────┘
```

- **Circle:** 92px diameter mobile. `border-radius:50%; overflow:hidden`.
- **Inside:** a SECOND `<video>` element sharing the SAME MediaStream as the full viewfinder — `srcObject` assigned to both. One `getUserMedia` call, two sinks. `object-fit:cover` crops to fill.
- **Tap (still):** ring flashes, circle scales 0.92 for 90ms, white overlay fades 0→0.85→0 (shutter flash), `navigator.vibrate(15)`
- **Hold ≥400ms (video):** ring turns record-red #ff3b30, `stroke-dashoffset` countdown, MM:SS counter
- **Uploading:** ring switches to indeterminate arc sweep in amber, pending-queue count badge

### Stream acquisition (critical iOS notes)
```js
const CAM = { stream:null, track:null, facing:'environment', torch:false };

async function startCamera(facing = CAM.facing) {
  // Always stop old tracks first — never call getUserMedia twice without stopping
  if (CAM.stream) { CAM.stream.getTracks().forEach(t => t.stop()); CAM.stream = null; }

  CAM.stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } }
  });
  CAM.track = CAM.stream.getVideoTracks()[0];
  CAM.facing = facing;

  // Two sinks, one stream
  for (const id of ['viewfinder', 'shutterVideo']) {
    const v = document.getElementById(id);
    v.srcObject = CAM.stream;
    v.muted = true;              // REQUIRED for iOS autoplay
    v.playsInline = true;        // REQUIRED — property AND attribute
    v.setAttribute('playsinline', '');
    v.setAttribute('webkit-playsinline', '');
    await v.play().catch(() => {});
  }
}
```

**iOS non-negotiables:** `muted` + `playsinline` + `autoplay` all three, both as property and attribute.

### Capturing stills (Safari has no ImageCapture)
```js
async function captureStill() {
  const t0 = new Date().toISOString();   // legal timestamp — shutter moment
  const gps = gpsSnapshot();             // last good fix, taken NOW not at upload

  let blob;
  // Fast path (Chrome/Android)
  if (window.ImageCapture && CAM.track.readyState === 'live') {
    try { blob = await new ImageCapture(CAM.track).takePhoto({ imageWidth: 4096 }); }
    catch { /* fall through */ }
  }
  // Universal path — required for all iOS/Safari
  if (!blob) {
    const v = document.getElementById('viewfinder');
    const c = document.createElement('canvas');
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext('2d', { alpha:false }).drawImage(v, 0, 0, c.width, c.height);
    blob = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.92));
  }

  flashShutter();
  navigator.vibrate?.(15);
  renderOptimisticTile(blob, t0);   // instant grid tile while upload happens
  await enqueueUpload({ blob, capturedAt: t0, gps, kind: 'photo' });
}
```

### Video recording
- Hold ≥400ms to record. On hold, acquire audio separately (`getUserMedia({audio:true})`) then compose with existing video track. NEVER re-request video — that trips the iOS mute bug on your own preview.
- MIME probe: `['video/mp4;codecs=h264,aac', 'video/mp4', 'video/webm;codecs=vp9,opus', 'video/webm']` — first supported wins (iOS = mp4, Chrome = webm).

### Camera arbitration with parent app
```js
// On open:
window.parent.postMessage({ type:'canvas-action', action:'camera-acquire' }, '*');
// On close/hide:
window.parent.postMessage({ type:'canvas-action', action:'camera-release' }, '*');
```
Parent (`src/app.js`): on `camera-acquire`, stop vision stream tracks, set `cameraYielded` flag. On `camera-release`, re-acquire. Prevents vision feed dying when capture page opens.

### Overlays to add
- **Grid:** pure CSS 3×3 rule-of-thirds, 1px rgba(255,255,255,.28), pointer-events:none
- **Level/horizon:** `deviceorientation` → thin line turns green within ±2° of level (roofers love this)
- **Timer:** 3s/10s countdown numeral centered
- **Before/after ghost:** when pair_before_id set, the "before" photo renders at opacity:.35 over viewfinder with 0–70% slider. **THIS IS COMPANYCAM'S MOST-LOVED FEATURE — DO NOT SKIP.**

---

## Phased build plan

### Phase 1 — MVP "wow" (5–7 days)
Goal: looks and feels better than CompanyCam on first open. Can't lose data.

- Fix data-loss race (new server-authoritative append with fcntl.flock)
- Add `captures/` bind mount + gated media/thumb routes + server-generated thumbnails
- Live-preview shutter button (full spec above), flip, torch-when-available, grid, timer, level
- Real album entities: create/edit name, client, address, status, labels, cover photo
- Full-screen viewer: swipe/keyboard nav, pinch-zoom, metadata panel
- AI caption on demand via server-side describe (no more blob→base64 round trip)
- Camera arbitration bridge in src/app.js
- Migration script: copy existing flat-manifest photos into album records (copy, never move)

**Files:** `routes/capture.py` (new, ~600 lines), `server.py` (+1 line), `services/paths.py` (+2 lines), compose template (new bind mount), `scripts/jambot-capture-migrate.py`, `camera-capture.html` (substantial rewrite of capture + album layer), `src/app.js` (~25 lines)

---

### Phase 2 — Contractor power (7–10 days)
- Sub-folders (Before/During/After/Damage/Completion) with drag-between, folder filter chips
- **Before/after ghost-overlay capture** + paired slider in viewer
- GPS auto-suggest: `GET /albums/nearby` on open → "You're at Johnson Residence. Add photos here?"
- Reverse geocode for auto-fill address on album create (new `/api/maps/geocode` proxy)
- **Offline queue** — IndexedDB (`captureQueue` store: blob + metadata + client_uuid + seq), `navigator.onLine` + `online` event, serial drain in seq order, server dedupes on client_uuid. Explicitly ordered — CompanyCam's reviewed weakness.
- Photo-required checklists per album (yes/no, rating, text, choice; reusable templates)
- Report builder: select photos → template → print-CSS HTML → browser Print-to-PDF. Zero new deps.
- Batch: multi-select, bulk tag, bulk move, bulk describe
- Real search across captions/tags/notes/album fields

---

### Phase 3 — Client portal (5–6 days)
- Revocable share tokens (scoped to whole album or selected folders)
- Public portal page: branded header, phase-grouped gallery, before/after sliders, lightbox
- Milestone approvals: client checks off phases, optional typed/drawn signature, timestamped + IP-logged
- Notify tenant on approval via InkBox SMS/email
- QR code per album for on-site "scan to see progress"

---

### Phase 4 — Content creation (8–12 days) — THE DIFFERENTIATOR
- **Reel builder:** photos → aspect (9:16/1:1/16:9) → transition → Suno track → AI voiceover → render
- **Time-lapse:** all album photos in `captured_at` order at 4–12 fps, date-stamp burn-in
- **Before/after reveal:** animated wipe — highest-engagement social format
- **AI damage report:** tagged photos → structured findings → feeds Phase 2 report
- **Estimate helper:** count visible damage over tagged subset
- **Marketing copy:** before/after set → caption variants per platform + hashtags
- Export to social-dashboard bridges

All video via **ffmpeg 7.1.5 already in OVU container**. No Remotion needed.

---

## Cross-cutting requirements

**Auth bridge:** `window.authFetch` token arrives async via postMessage. Must `await awaitAuth()` before first `/api/capture/*` call or first load intermittently 401s.

**Never-delete:** Deletes are soft everywhere. Photo delete flips flag. Album delete is archive. Annotation writes `.annotated.jpg` sibling, never touches original. Photo move copies-then-relinks.

**Model discipline:** Bulk captioning → haiku. Damage reports/marketing → sonnet. Never account-default.

**No localStorage (except the queue):** IndexedDB offline queue is the sanctioned exception — stores pending binary assets, entries deleted only after server receipt confirmed.

**Money-safety:** Server file written first, UI tile is optimistic from in-memory blob. AI renders written to `exports/` before UI shows them.

---

*Sources: CompanyCam Features, Pricing, AI Features, Before/After, Reports, Marketing Suite docs; Capterra/G2/SoftwareAdvice reviews; WebKit MediaRecorder blog; webrtchacks Safari guide; MDN ImageCapture (Safari: unsupported).*


---
---

# AMENDMENT A — 2026-07-30: scope expansion + status reconciliation

*Origin: host-clone session with Mike, 2026-07-30. The original plan (07-29) is sound and stays
authoritative for Phases 1–4. This amendment (a) records what is actually BUILT vs DARK, and
(b) widens scope on six axes the original plan did not cover. Nothing above is retracted.*

**Standing constraint from Mike (2026-07-30):** everything on this platform is new and untested;
all clients are beta testers. Do NOT make decisions about any client workspace situation or client
tasks based on current state — nothing is operating the way we want yet. Build on `test-dev` with
synthetic data only.

**Positioning restated (Mike, 2026-07-30):** we target small, new companies that have nothing and
need everything. No big team — but they get, from day one, the systems a big team would need. This
reframes several features: the checklist, the report builder, and the watermark are not
conveniences, they are **substitutes for the project manager and the office admin the client does
not have.** Only the geofence is genuinely a multi-crew feature.

---

## A.0 — Status at time of amendment

Phase 1 is **fully coded and completely dark.** Nothing a user can reach has changed.

| Phase 1 deliverable | State |
|---|---|
| `routes/capture.py` | ✅ ~980 lines (plan estimated ~600) |
| Data-loss race fix | ✅ `flock` at 6 call sites |
| `services/paths.py` → `CAPTURES_DIR` | ✅ |
| Compose bind mount (template) | ✅ 3 mounts incl. openclaw workspace |
| Compose bind mount (test-dev live) | ⚠️ only 2 of 3 — see A.9.4 |
| `scripts/jambot-capture-migrate.py` | ✅ exists |
| Camera arbitration in `src/app.js` | ✅ |
| Offline idempotency, server side | ✅ `client_uuid` ×13, `seq` ×4, `queue-receipts/` |
| **Present in the running image** | ❌ image built from `c0ad7eb`, which predates all of it |
| **Seeded to any tenant `canvas-pages/`** | ❌ tenants still serve the gen-1 61KB page |

**Three generations of this app exist simultaneously.** Anyone judging the UI must confirm which
one they are looking at first:

| Generation | Page | Backend | Where |
|---|---|---|---|
| gen-1 | 61KB, client-side manifest | `routes/albums.py` | **live on tenants + in the image** |
| gen-1b | 15.7KB | — | `/app/default-pages/` inside the image |
| gen-2 | 88.9KB, `/api/capture/*` | `routes/capture.py` | `main` source only — DARK |

Phase 2: folders ✅ · batch ops ✅ · search ✅ · `GET /albums/nearby` ✅ **but no caller** ·
offline queue client-side ❌ (no IndexedDB) · geocode ❌ · checklists ❌ · report ❌ ·
ghost-overlay ❌ (3 stub refs only).
Phase 3: share-token mint ✅ · portal route ❌ (see A.9.2).
Phase 4: nothing.

**Open SMS action items** (`MIKE-AI/docs/reflections/ACTIONS.jsonl`, opened 2026-07-29, still open,
owner `host`):
- `sms-ea5afc95` — "Make the capture screen canvas"
- `sms-b5d65f11` — "Images taken with the camera should automatically go into an album like company cam"

Both are satisfied by deploying Phase 1. They are currently blocked on an image rebuild, not on code.

---

## A.1 — Reframe: this is the company computer's visual memory, not a camera app

OpenVoiceUI already has ears and a mouth (voice, Telnyx), a screen (canvas, desktop), a brain (the
agent), memory of people (Office `people/` `companies/` `matters/`), memory of deals (Twenty CRM), a
nervous system (agent mesh), a voice to the outside world (InkBox SMS/email), and hands (the content
factory). **What it lacks is eyes and a visual memory.** That is what this subsystem is. The camera
is one input among many (A.3).

The governing analogy: on a phone, the camera is a **system service** and the photo library is a
**system store** that Messages, Notes, Mail and every third-party app reads. Nobody ships "a camera
app." **The failure mode for this project is building a better camera app instead of building the
library.**

Today media lands in six disconnected stores:

```
runtime/captures/          gen-2 albums          (dark)
runtime/uploads/albums/    gen-1 albums          (live)
runtime/uploads/           web + drag-drop + iOS raw-body camera blobs
runtime/canvas-pages/      AI-generated images land here
runtime/music/
runtime/generated_music/
```

Consequence: the agent **cannot** answer *"show me everything we have about the Johnson job."* The
homeowner's texted photo is in an SMS log, the crew's photo is in `captures/`, the AI-generated hero
image is in `canvas-pages/`, the walkthrough video is nowhere. This is the same defect class as
`platform-mailbox-had-no-reader` — media arrives and nothing reads it.

**Naming:** "Camera Capture" names one input. The subsystem is the record; the camera is a view onto
it. Rename before the wrong name accretes (`capture` is already the route prefix and works).

---

## A.2 — Generalize collections: five orthogonal axes

The original plan is contractor-framed throughout. Many clients will be contractors, but not all,
and captured media can be of anything. **Do not use one big `type` enum** — a single list forces
every client into one box. Five orthogonal axes compose, so a cannabis grower's batch log and a
roofer's job site run on identical machinery.

### Axis 1 — collection type (was implicitly 1; now 16)

| Group | Type | Default folders |
|---|---|---|
| Field/ops | `job_site` | Before · During · After · Damage · Completion |
| | `property` | Exterior · Rooms · Systems · Defects |
| | `recurring_visit` | per-visit, same site, compared over time |
| | `asset_vehicle` | Intake · Condition · Damage · Return |
| | `incident_claim` | Scene · Damage · Cause · Resolution |
| | `compliance_inspection` | Checklist-driven |
| | `location_venue` | Per-audit |
| Commerce | `product_sku` | Hero · Detail · Scale · In-use |
| | `menu_item` | Plated · Ingredients · Process |
| | `batch_lot` | Stage-driven (cannabis, food, craft) |
| People/service | `client_matter` | Intake · Progress · Outcome |
| | `personnel_credential` | Licenses · Certs · Training |
| | `portfolio_case_study` | Curated — the published artifact |
| Content | `content_shoot` | Raw · Selects · Approved |
| | `campaign` | Grouped by intent, pulls from any collection |
| | `event_shoot` | Setup · Event · Teardown |

### Axis 2 — asset kind
`photo` · `video` · `audio_note` · `document_scan` · `screen_capture` · `model_3d` ·
`ai_generated` · `derived` · `timelapse`

### Axis 3 — purpose tags (the routing layer)
`evidence` · `hero` · `before` · `after` · `detail` · `wide` · `problem` · `resolution` ·
`b_roll` · `testimonial` · `internal_only` · `client_facing` · `publish_approved` · `needs_release`

These decide **where an asset is allowed to go.** CompanyCam has no equivalent concept.

### Axis 4 — destinations (the output arm)
job report PDF · client share portal · website gallery · blog illustration · social post · GMB post ·
reel/short · case study page · estimate/invoice attachment · insurance packet · 3D model source ·
review request · email campaign · ad creative

### Axis 5 — lifecycle
`captured → described → curated → derived → approved → published → attributed`

The final stage is the one nobody does: **did this asset produce a lead?** See A.8.

### Schema impact (additive — existing records stay valid)
```json
{
  "collection_type": "job_site",   // default job_site; existing albums migrate to it
  "purpose": [],                   // Axis 3, on the ASSET
  "kind": "photo"                  // Axis 2, replaces/extends the existing "type"
}
```

---

## A.3 — Capture is fourteen doors, not one

The original plan has exactly one input: the crew's phone camera. Every channel below either already
delivers media with nowhere to put it, or dumps it into a private store.

| # | Channel | Status |
|---|---|---|
| 1 | Crew phone camera | Phase 1, coded |
| 2 | **Customer MMS** | ❌ **unbuilt — highest leverage** |
| 3 | **Customer email attachment** | ⚠️ partial (`pickup-platform-agentmail.py`) |
| 4 | Web / drag-drop upload | exists, wrong store |
| 5 | Voice call audio → `transcribe` | unbuilt as asset |
| 6 | Desktop screen capture (ubuntu-os, `browser-automation`) | exists, wrong store |
| 7 | Agent-generated (`image_gen`/`fal`/`stitch`/`gemini-image`/`meshy`) | exists, wrong store |
| 8 | Website form upload (lead arrives with photos) | unbuilt |
| 9 | Social inbound — DMs, mentions, tagged media | unbuilt |
| 10 | Review platforms — customer-posted photos | unbuilt |
| 11 | Document scan — receipts, permits, signed releases | unbuilt |
| 12 | Vendor / subcontractor sends | unbuilt |
| 13 | Bulk import — drone, GoPro, bodycam, SD dump | unbuilt |
| 14 | Mesh handoff from another node/desktop | unbuilt |

**Channel 2 is the strategic one.** A homeowner texts "here's the leak" and it lands on the job,
gets described by vision, and the agent replies — with **nothing installed on the customer's side.**
CompanyCam structurally cannot do this; it requires the customer to install their app. Verified
unbuilt: media/MMS handling greps to a single file across the whole SMS path, and the
`inkbox-expert` skill has zero MMS or media references.

Media messaging is symmetric — it is also an **output** channel (text the customer the before/after,
text the report link). It is the only channel requiring zero customer installs in either direction.

### Schema impact
```json
{
  "source": {
    "channel": "customer_mms",        // one of the 14
    "from": "+16025551234",           // normalized identity where known
    "linked_person_id": null,         // Office people/ resolution
    "received_at": "..."
  }
}
```
`captured_at` semantics from the original plan are **unchanged for channel 1** (client clock at
shutter = the legal timestamp). For inbound channels, `captured_at` is unknown — record
`received_at` and leave `captured_at` null rather than backfilling it with arrival time. **An
inbound asset must never claim a capture timestamp it does not have** — that would poison the
evidence property the whole design rests on.

---

## A.4 — One asset store. Do not create store #7.

As written, Phase 1 adds `captures/` as a seventh store and correctly supersedes `routes/albums.py`
— but leaves `uploads/`, `canvas-pages/`, `music/` and `generated_music/` fragmented. The agent
still cannot see across them.

**Rule: one asset store, one writer.** Model it on the ticket system, which already proves this
shape in production: a single central router is the only writer, and every consumer view is a
projection re-rendered after each write (`CLIENT-INBOX.md` from `TICKETS/*.json`).

- All 14 channels write **through the router**, never directly to disk.
- Every existing surface becomes a **reader**: camera page, report builder, invoice, website
  builder, `article-writer`, social, CRM, share portal.
- Existing stores are **adapted, never migrated destructively** — index in place, copy on demand.
  Per the never-delete rule: `jambot-capture-migrate.py` already copies rather than moves; hold that
  line for every other store.

---

## A.5 — The job record: CRM/Office join, and the agent creates it

The original plan has zero CRM references. Two additions:

**A.5.1 — Invert ownership. Twenty is a projection, not the source.** Twenty models a sales
pipeline: deals, companies, people. A job site is not a deal — it has phases, required shots, a
crew, a physical work address, and a completion gate. If Twenty owns the record we import a sales
schema into a field-operations problem and fight it permanently. **Our collection record is the
primitive; Twenty, the invoice, the report and the share portal are all views of it.**

Because our clients arrive owning nothing, there is no foreign CRM to sync with and no integration
partner to negotiate. CompanyCam must integrate with a dozen FSMs because their customers already
own one. We define the record once.

**A.5.2 — The agent creates the collection.** This is the differentiator CompanyCam structurally
cannot match: they can only sync what a human already typed into a CRM. We have an agent that can do
the typing, and it is the same agent that answers the phone and reads the Office matter files.

```
POST /api/capture/collections/from-intent
     {"utterance": "starting the Johnson attic tomorrow"}
  → resolve "Johnson" against Office people/companies + Twenty
  → pull address, forward-geocode to lat/lng
  → create collection with collection_type + default folders ready
  → return the collection for confirmation
```

**MUST confirm, never silently create.** Return a proposal the human accepts. A wrong auto-created
job pinned to the wrong house is worse than no job.

**Known wrinkle — do not skip:** a CRM deal is not a job site. One company can have several jobs,
and a Twenty company address is often the *billing* address, not where work happens. Either deals
carry a work-address field or the Office `matters/` record does. **Resolve this before wiring
auto-creation**, or we will pin collections to wrong addresses at scale.

---

## A.6 — Release and consent: publishability is not a toggle

The original plan has `hidden_from_share`, which is access control. It is not consent. Phase 4
publishes photos of customers' homes, faces and property to social media and public case studies —
that requires a release, and our client (a brand-new company with no lawyer) is exactly who needs
this system provided for them rather than assumed.

```json
{
  "subject_type": "customer_property",   // customer_property | person | our_work | product | none
  "release_status": "none",              // none | verbal | signed
  "release_asset_id": null,              // the signed release, itself an asset (channel 11)
  "publish_ok": false                    // hard gate — default false, never inferred
}
```

**Rules:**
- `publish_ok` defaults **false** and is never inferred from any other field.
- Any Phase 4 destination that leaves the tenant boundary (social, GMB, website, case study, ad)
  **MUST** check `publish_ok` and refuse otherwise. Internal destinations (job report, insurance
  packet, invoice, client's own share portal) do not require it — the customer already owns that
  view of their own property.
- `needs_release` (Axis 3) is the work-queue tag: it is what the agent surfaces as "you need a
  release before this can be posted."

---

## A.7 — Provenance: keep AI edits out of evidence

The original plan's never-delete section already gets the mechanism right — annotation writes an
`.annotated.jpg` sibling and never touches the original. **Generalize the principle: evidence and
marketing are opposing requirements on the same asset.** Evidence needs the original untouched and
verifiable. Marketing needs cropping, color, branding and AI edits.

```json
{
  "derived_from": "p7h8j9k0l1m2",   // null for originals
  "transform": "crop_9x16",          // annotate | crop | color | upscale | ai_edit | ai_generate
  "generator": "fal:flux",           // model/tool that produced it, null if human
  "is_ai_generated": false
}
```

**Rules:**
- Evidence-facing views (damage report, insurance packet, dispute export) serve **originals only** —
  never a derivative, never an AI-edited asset. Enforce in the serving layer, not by convention.
- A derivative can never overwrite its parent. Parent retention is unconditional.
- `is_ai_generated` assets are marked in every client-facing surface. This also satisfies the
  standing rule that AI-generated content is written to the server the instant it is produced —
  generated assets enter the same store through the same router, so they are durable by default
  instead of living in a browser variable.

---

## A.8 — Close the attribution loop

Lifecycle stage 7. Nobody in this market does it, and we already have the two systems needed
(`social-dashboard`, `seo-platform`).

```json
{
  "published_to": [
    {"destination": "social_post", "ref": "x:1234567", "at": "...", "engagement": null}
  ]
}
```

The chain worth being able to walk: **photo → post → lead → deal.** For a company with no marketing
department, "this before/after shot produced three calls" is the single most useful sentence the
system can say.

---

## A.9 — Corrections and defects found while reconciling

**A.9.1 — `GET /albums/nearby` ignores per-album `geofence_m`.** It applies one caller-supplied
`radius` to every album, while each album record already carries its own `geofence_m` (default 120).
Nothing reads that field. 120m is right for a house and wrong for a 40-acre commercial site — and on
a dense residential street it spans a whole block while a typical lot is 20–40m. Fix: per-album
fence, caller radius only as an outer bound.

Also, on geofence priority: it is a **multi-crew** feature. Value scales with headcount and job
count and is near zero for a solo operator, who already knows what job they are on. Keep `lat`/`lng`
on the record from day one (free, already there) but do not prioritize the auto-assign UI ahead of
the report, checklist and share portal, which deliver at one user. When it is built, it **must
confirm rather than auto-file** ("You're at Johnson Residence · 12m — add here?"), show a
distance-sorted picker on multiple hits, and stamp raw lat/lng on the asset regardless of which
collection it lands in so a mis-file is a re-file rather than lost evidence. Phone GPS is 5–20m
outdoors and far worse inside a building — which is exactly where a foam crew works.

**A.9.2 — The share arm is severed today.** `POST /albums/<id>/share` mints a token and `app.py:187`
whitelists `/share/capture/` as public, but **no route serves it.** A tenant can generate a client
link that leads nowhere. It is a known Phase 3 item, not an oversight — but the mint half shipping
without the view half is exactly the `ack-must-come-from-receiver` pattern. Either land both halves
together or have the mint endpoint refuse until the viewer exists.

**A.9.3 — Video is blocked by media size caps.** `MIKE-AI/docs/TECH-DEBT-REGISTRY.md:83` — default
caps (image 10MB, audio 20MB, video 50MB) are *below* typical phone-camera and screen-recording
output. Raise before Phase 4, or video capture fails on real devices.

**A.9.4 — test-dev is missing the openclaw workspace captures mount.** The template mounts captures
three times, including `/home/node/.openclaw/workspace/captures` (template line 53). test-dev's live
compose has only the two `/app/runtime/captures` mounts. **That third mount is what lets the agent
read the photos** — the entire "the agent has eyes" premise. Reconcile before claiming any
agent-over-assets capability works. (Host action; a clone does not touch tenant compose.)

**A.9.5 — Two corrections in our favour, from the original plan.** Recorded here because they were
independently re-derived wrongly in the 07-30 session:
- Reports need **no PDF library** — print-CSS HTML plus browser Print-to-PDF.
- Video needs **no Remotion container** — ffmpeg 7.1.5 is already inside the OVU image.

---

## A.10 — Revised sequencing

Phases 1–4 above stand. This is the ordering across them, with amendment work folded in.

| # | Work | Why here |
|---|---|---|
| 0 | **Get Phase 1 out of the dark** — image rebuild off `main`, seed the page to test-dev, add the A.9.4 mount | Nothing is judgeable until the current code is what's running. Closes both open SMS items. Host approval. |
| 1 | **Unified asset store + single-writer router** (A.4) | Every later item writes through it. Retrofitting is far more expensive than starting here. |
| 2 | **Inbound media messaging** (A.3 ch. 2–3) | Highest-leverage unbuilt channel; zero customer install; impossible for CompanyCam |
| 3 | **Report builder + watermark + share portal** (P2/P3, A.9.2) | Replaces the office admin the client doesn't have. Delivers at one user. |
| 4 | **Photo checklists** (P2) | Replaces the project manager. Works off existing phase folders; needs no geofence. |
| 5 | **Before/after ghost-overlay** (P2) | CompanyCam's most-loved feature. Cheap. Do not skip. |
| 6 | **Release/consent + provenance** (A.6, A.7) | Must land **before** any Phase 4 publishing path goes live |
| 7 | **Collection-type generalization** (A.2) | Additive; unblocks non-contractor tenants |
| 8 | **Phase 4 content creation** — reels, timelapse, marketing copy | The differentiator, gated on 1/6 |
| 9 | **Agent-creates-collection + CRM/Office join** (A.5) | Needs the A.5.2 work-address question resolved first |
| 10 | **Attribution loop** (A.8) | Needs published assets to exist |
| 11 | **Offline queue** (P2) | Real requirement; independent of the above, slot by tenant need |
| 12 | **Geofence auto-assign** (A.9.1) | When a tenant actually has crews |

All development on `test-dev` with synthetic data. No client workspace decisions (standing
constraint, top of this amendment).

---

*Amendment A ends.*
