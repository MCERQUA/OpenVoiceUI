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
