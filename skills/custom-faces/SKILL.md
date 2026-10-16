---
name: custom-faces
description: Create a custom FACE for OpenVoiceUI - a fullscreen HTML/WebGL page that becomes the assistant's actual face and reacts live to speech. Covers the face:* postMessage contract, meta tags, the full-load performance gate, promotion, activation and editing. Use when the user wants a new, custom, animated or visualizer face.
---

# Custom faces: a page in the real face slot

The face box loads a custom face in an iframe from `/faces/custom/<id>.html` (files live in the
OpenVoiceUI runtime `faces/` dir) and streams live data into it with `postMessage`. Any HTML page
can become the face in three steps: build, measure, promote, then activate.

Examples use `http://localhost:5001`; if you changed `PORT`, use that port. The
`X-Agent-Key: ${AGENT_API_KEY:-}` header is needed only on login-required installs.

## Step 0: build the page

Start from the official starter; it shows the exact handler shape:

```bash
curl -s http://localhost:5001/api/custom-faces/template -H "X-Agent-Key: ${AGENT_API_KEY:-}"
```

Build it as a normal canvas page first (`/app/runtime/canvas-pages/<id>.html`, see the
`canvas-pages` skill). Requirements:

1. **Meta tags** in `<head>` (the face picker reads them):

   ```html
   <meta name="face-name" content="My Face">
   <meta name="face-description" content="One line about it">
   <meta name="face-author" content="<your agent name>">
   ```

2. **Message listener plus a ready signal.** The face box sends nothing until the face says it is
   ready, so the page MUST post `face:ready` once its listener is installed:

   ```js
   const state = { mood: 'neutral', amplitude: 0, speaking: false, agentState: 'idle',
                   theme: { primary: '#0088ff', accent: '#00ffff' } };
   window.addEventListener('message', (e) => {
     const d = e.data;
     if (!d || typeof d !== 'object') return;
     switch (d.type) {
       case 'face:mood':      state.mood = d.mood; break;          // neutral|happy|sad|angry|thinking|surprised|listening
       case 'face:amplitude': state.amplitude = d.value; break;    // 0..1 while the assistant speaks
       case 'face:speaking':  state.speaking = d.speaking; break;  // true|false
       case 'face:state':     state.agentState = d.state; break;   // speaking|listening|thinking|idle
       case 'face:theme':     state.theme = { primary: d.primary || state.theme.primary,
                                              accent:  d.accent  || state.theme.accent }; break;
     }
   });
   window.parent.postMessage({ type: 'face:ready' }, '*');
   ```

   Drive the visuals from `amplitude`: a face that ignores it looks dead while the assistant talks.
3. **Self-contained:** all CSS and JS inline, no CDN frameworks. Fullscreen:
   `html,body{margin:0;height:100%;overflow:hidden}`.

## Step 0.5: measure it at full load (required, enforced)

The face runs permanently next to voice, speech recognition and TTS. A heavy face makes the whole
interface stutter and drains laptop and phone batteries. Promotion is refused (HTTP 428) without a
fresh passing performance report for the current page bytes.

1. Open it as a canvas page (`[CANVAS:<id>]`) and keep talking for a couple of minutes: the rest
   of the interface (mic button, chat) must stay instant.
2. Add this probe temporarily. It forces the heaviest state (loud continuous speech), samples
   per-second fps for 35 seconds and reports the result:

   ```js
   (function () {
     const fpsSamples = []; let f = 0, t0 = performance.now();
     const load = setInterval(() => {
       window.postMessage({ type: 'face:speaking', speaking: true }, '*');
       window.postMessage({ type: 'face:state', state: 'speaking' }, '*');
       window.postMessage({ type: 'face:amplitude', value: 0.9 + Math.random() * 0.1 }, '*');
     }, 50);
     (function tick() {
       f++; const el = performance.now() - t0;
       if (el >= 1000) { fpsSamples.push(f * 1000 / el); f = 0; t0 = performance.now(); }
       if (fpsSamples.length < 35) { requestAnimationFrame(tick); return; }
       clearInterval(load);
       fetch('/api/custom-faces/perf-report', { method: 'POST',
         headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify({ page_id: location.pathname.split('/').pop().replace('.html', ''),
                                samples: fpsSamples, user_agent: navigator.userAgent }) })
         .then(r => r.json()).then(v => { document.title = v.verdict; console.log('PERF', v); });
     })();
   })();
   ```

   Also trigger any other heavy effects the face has (mood changes, theme changes). Idle fps is a
   false pass: a face that idles at 60 and drops to 20 while speaking fails.
3. The response says PASS or FAIL with `fps_low` (5th percentile of the samples) against the
   threshold (`FACE_MIN_FPS`, default 50). The report is tied to the page's content hash (edit the
   page, re-test) and expires after 7 days. Remove the probe before promoting, then measure again
   if the page changed.
4. Test on the device the user actually uses (phone, kiosk, small single-board computer). A
   desktop pass proves nothing about a weak device.

**Budget rules** (stricter than a canvas page, because a face never closes):

- `requestAnimationFrame` only; never `setInterval` for rendering.
- Pause when hidden (`document.visibilitychange`), and idle down when amplitude has been 0 for a
  few seconds.
- Cap the pixel ratio: `Math.min(devicePixelRatio, 1.5)` for canvas sizing.
- Start particle and geometry counts low and raise them until it looks good, not the reverse.
- No unbounded arrays or history: the page never closes, so memory must not grow.

If it fails, reduce counts and pixel ratio first, re-test, then promote. Tell the user the
measured full-load fps when you declare the face ready: a number, not "it seems smooth".

## Step 1: promote it into the face slot

```bash
curl -s -X POST http://localhost:5001/api/custom-faces/promote \
  -H "X-Agent-Key: ${AGENT_API_KEY:-}" -H "Content-Type: application/json" \
  -d '{"canvas_page_id": "<id>"}'
```

This COPIES the canvas page to the faces dir as `<id>.html` and registers it. A 428 explains what
is missing (never measured, failed the bar, stale, or the page changed since measurement): fix it,
re-run the probe, retry. A `"force": true` option exists for administrators and is logged; do not
use it to skip the bar.

After promotion, edits go to the faces copy (`/app/runtime/faces/<id>.html` in the Docker app
container), not to the canvas page: promotion is a snapshot. Either edit the faces copy or edit the
canvas page and promote again (re-measure first). A face file written directly into the faces dir
is auto-registered on the next `GET /api/custom-faces`.

## Step 2: activate it

```bash
curl -s -X PUT http://localhost:5001/api/profiles/default \
  -H "X-Agent-Key: ${AGENT_API_KEY:-}" -H "Content-Type: application/json" \
  -d '{"ui": {"face_mode": "custom:<id>"}}'
```

`default` is the profile id; use `GET /api/profiles/active` (or `GET /api/profiles`) when the user
runs named profiles. Or tell the user: Face picker, then the face's name. The choice is stored
server-side per profile, so every device follows it.

## Verify (never report done without this)

```bash
curl -s http://localhost:5001/api/custom-faces -H "X-Agent-Key: ${AGENT_API_KEY:-}"            # your id listed?
curl -s http://localhost:5001/api/profiles/default -H "X-Agent-Key: ${AGENT_API_KEY:-}"        # ui.face_mode == "custom:<id>"?
```

Then ask the user to confirm the face switched.

## Notes

- Administrators can also promote and edit faces in the admin panel (Custom Faces).
- `DELETE /api/custom-faces/<id>` archives the face to `.bak`; it never hard-deletes.
- Face mood comes from the profile (`ui.face_mood`) and the assistant's state; you do not need to
  send messages to the face yourself.
