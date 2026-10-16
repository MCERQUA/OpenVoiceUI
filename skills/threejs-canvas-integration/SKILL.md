---
name: threejs-canvas-integration
description: Build Three.js / WebGL games and 3D scenes as OpenVoiceUI canvas pages - jsdelivr import map, full-viewport shell, same-origin asset loading, server-side save state, and the postMessage bridge to the voice agent. Load before writing any 3D canvas page.
---

# Three.js on OpenVoiceUI canvas pages

A 3D game is an ordinary canvas page: one HTML file in the canvas-pages folder, opened with
`[CANVAS:<page-id>]`. The general write / open / verify / lint workflow is in the `canvas-pages`
skill; this skill covers what is specific to Three.js.

Examples use `http://localhost:5001`; if you changed `PORT`, use that port. API calls send
`-H "X-Agent-Key: ${AGENT_API_KEY:-}"`, which only matters when login is required and
`AGENT_API_KEY` is set.

| What | Docker path (agent side) | Browser URL |
|------|--------------------------|-------------|
| Page | `/app/runtime/canvas-pages/<game>.html` (also mounted at `~/.openclaw/workspace/canvas-pages/`) | `/pages/<game>.html` |
| Game assets | `/app/runtime/canvas-pages/<game>-assets/...` | `/pages/<game>-assets/...` |
| Save data | `/app/runtime/canvas-pages/_data/<name>.json` | `/api/canvas/data/<name>.json` |
| User uploads | `/app/runtime/uploads/<file>` | `/uploads/<file>` |

Native installs use the OpenVoiceUI `runtime/canvas-pages` folder (or `CANVAS_PAGES_DIR`).
Only top-level `*.html` files become pages; the page id is the filename without `.html`.

## What the canvas CSP allows

Canvas pages get their own Content-Security-Policy:

| Need | Allowed | Consequence |
|------|---------|-------------|
| Scripts / ES modules | same origin, inline, `eval`, WebAssembly, `blob:`, `https://cdn.jsdelivr.net` | Load Three.js from **jsdelivr only**. unpkg, esm.sh, skypack and cdnjs are blocked. |
| Stylesheets | inline `<style>`, `fonts.googleapis.com`, `cdn.jsdelivr.net` | No same-origin `<link rel="stylesheet">`; keep all CSS inline. |
| Fonts | same origin, `fonts.gstatic.com`, `cdn.jsdelivr.net` | System fonts or Google Fonts both work. |
| Images, audio, video | same origin, `data:`, `blob:`, any `https:` | `<img>`-based loading (TextureLoader) can reach HTTPS hosts that send CORS headers. |
| `fetch` / XHR | same origin, `blob:`, a few AI API hosts | **GLTFLoader, FileLoader, RGBELoader and the Draco/KTX2 decoders use fetch, so models, HDRs and decoders must be same-origin.** |
| Workers | `blob:` only | `new Worker('/pages/x.js')` is blocked; Blob-URL workers (DRACOLoader's) work. |
| Frames | same origin plus a short list | Embed your own `/pages/...` pages, not arbitrary sites. |

## Page shell

Copy this and replace the demo scene. Things the server adds to every served page, which the
shell is written around:

- `html, body { padding: 25px !important }` plus CSS variables `--canvas-safe-top/right/bottom/left`
  (clearance for the app's own buttons). A `position: fixed; inset: 0` canvas ignores the
  padding and goes full-bleed; place HUD elements with the safe-area variables.
- An error bridge: uncaught errors and unhandled rejections are posted to the app, shown in its
  Action Console and attached to your next message for about 30 seconds.
- Helpers `nav(pageId)`, `speak(text)` and `authFetch(url, opts)` (see below).

Rules the page lint enforces: a `<meta name="viewport">` tag, a `<meta name="page-icon">` tag
within the first 2 KB of the file (`game` is a valid icon), no emoji as UI icons (use inline
SVG), no purple/pink accent colors.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="page-icon" content="game">
<title>My 3D Game</title>
<style>
  html, body { margin: 0; overflow: hidden; background: #000; font-family: system-ui, -apple-system, sans-serif; }
  #scene { position: fixed; inset: 0; display: block; }
  .hud { position: fixed; z-index: 10; color: #e2e8f0; font-size: 14px; text-shadow: 0 1px 3px rgba(0,0,0,.8); }
  #stats { top: var(--canvas-safe-top, 16px); left: var(--canvas-safe-left, 16px); pointer-events: none; }
  #menu { top: var(--canvas-safe-top, 16px); right: var(--canvas-safe-right, 16px); width: 44px; height: 44px;
          display: grid; place-items: center; border-radius: 10px; cursor: pointer;
          background: rgba(15,23,42,.75); border: 1px solid rgba(148,163,184,.3); }
  #loading { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; background: #0d1117; color: #e2e8f0; }
</style>
<!-- The import map must come before any type="module" script. Pin the version. -->
<script type="importmap">
{ "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
} }
</script>
</head>
<body>
<div id="loading">Loading...</div>
<div id="stats" class="hud">Score <b id="score">0</b></div>
<button id="menu" class="hud" aria-label="Menu">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
    <line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>
</button>
<canvas id="scene"></canvas>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.toneMapping = THREE.ACESFilmicToneMapping;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
const camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.1, 1000);
camera.position.set(0, 3, 8);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.48;          // keep the camera above the ground

scene.add(new THREE.AmbientLight(0xffffff, 0.4));
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
sun.position.set(5, 10, 5); sun.castShadow = true; scene.add(sun);

const ground = new THREE.Mesh(new THREE.PlaneGeometry(20, 20), new THREE.MeshStandardMaterial({ color: 0x1e293b }));
ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);
const cube = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: 0x3b82f6 }));
cube.position.y = 1; cube.castShadow = true; scene.add(cube);

document.getElementById('menu').onclick = () =>
  window.parent.postMessage({ type: 'canvas-action', action: 'menu' }, '*');

const clock = new THREE.Clock();
renderer.setAnimationLoop(() => {
  const dt = clock.getDelta();                    // use delta time for all movement
  cube.rotation.y += dt * 0.8;
  controls.update();
  renderer.render(scene, camera);
});
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
document.getElementById('loading').remove();
</script>
</body>
</html>
```

Ship it:

```bash
# after writing /app/runtime/canvas-pages/my-game.html
curl -s http://localhost:5001/api/canvas/lint/my-game -H "X-Agent-Key: ${AGENT_API_KEY:-}"
# reply with words plus the tag:  "Your game is up. [CANVAS:my-game]"
curl -s http://localhost:5001/api/canvas/context -H "X-Agent-Key: ${AGENT_API_KEY:-}"   # current_page should be my-game.html
```

Common addons through the same import map: `controls/OrbitControls.js`,
`controls/PointerLockControls.js`, `loaders/GLTFLoader.js`, `loaders/DRACOLoader.js`,
`loaders/RGBELoader.js`, `loaders/OBJLoader.js`, `loaders/FBXLoader.js`,
`postprocessing/EffectComposer.js`, `postprocessing/RenderPass.js`, `postprocessing/UnrealBloomPass.js`,
`geometries/TextGeometry.js`, `loaders/FontLoader.js`, `objects/Sky.js`, `objects/Water.js`
(all under `three/addons/`).

## Assets

Keep every model, HDR and decoder same-origin: save it into the canvas-pages folder and load it
by its `/pages/` URL.

```bash
mkdir -p /app/runtime/canvas-pages/my-game-assets/models
curl -L -o /app/runtime/canvas-pages/my-game-assets/models/ship.glb "<model URL>"
```

```javascript
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
const gltf = await new GLTFLoader().loadAsync('/pages/my-game-assets/models/ship.glb');
scene.add(gltf.scene);

const tex = new THREE.TextureLoader().load('/pages/my-game-assets/textures/metal.jpg');
tex.colorSpace = THREE.SRGBColorSpace;
```

- **Types:** `.glb` is served as `model/gltf-binary`, `.gltf` as `model/gltf+json`; `.obj`, `.stl`,
  `.usdz` and `.fbx` also get explicit types.
- **Prefer `.glb`** (one file) and `.jpg`/`.png`/`.webp` textures. On a login-required install,
  `/pages/` serves images, scripts, fonts, audio/video, `.glb`, `.gltf` and `.wasm` to anyone, but
  every other type (for example `.bin`, `.hdr`, `.fbx`, `.obj`, `.ktx2`, `.json`) needs the viewer's login.
  That is fine for the owner; it breaks those assets if the page is later made public.
- **Draco-compressed models:** the decoder is fetched, so host it yourself:
  ```bash
  mkdir -p /app/runtime/canvas-pages/_draco && cd /app/runtime/canvas-pages/_draco
  for f in draco_decoder.wasm draco_wasm_wrapper.js; do
    curl -sLO "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/libs/draco/gltf/$f"; done
  ```
  then `dracoLoader.setDecoderPath('/pages/_draco/')`.
- **User uploads:** `POST /api/upload` stores files as `<stem>__<8 hex chars><ext>` and returns
  `{"filename", "url": "/uploads/<filename>", ...}`. Use the returned `url`; to find an earlier
  upload, `ls /app/runtime/uploads/`.
- **Generated assets** (textures, models, sounds from any generation skill) cost money: save each
  one into `canvas-pages/<game>-assets/` the moment it is produced and reference it by URL.
  Never keep generated output only in page memory or as a data URL.
- **Meshy 3D (optional):** if `MESHY_API_KEY` is configured, `POST /api/meshy/text-to-3d` with
  `{"prompt": "..."}` returns a `task_id` for an untextured preview (add
  `"mode": "refine", "preview_task_id": "<id>"` for PBR textures). Poll
  `GET /api/meshy/task/<task_id>?name=ship` until `status` is `SUCCEEDED`; the GLB is saved into
  canvas-pages and `glb_url` gives its URL (`/pages/ship.glb`, or `ship-1.glb` if taken). Stop
  polling once it succeeds: every later poll saves another copy. Each generation uses Meshy credits.

## Saving game state

Never use browser storage (`localStorage`, `sessionStorage`, IndexedDB): state lives on the
server. Use the canvas data store, which keeps a fixed filename:

```javascript
const SAVE_URL = '/api/canvas/data/my-game-save.json';
const api = (url, opts) => (window.authFetch ? window.authFetch(url, opts) : fetch(url, opts));

async function loadSave() {
  try { const r = await api(SAVE_URL); if (r.ok) return await r.json(); } catch (e) {}
  return {};                                     // GET returns {} until the first save
}
async function writeSave(state) {
  const r = await api(SAVE_URL, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state) });
  return r.ok;                                   // server answers {"ok": true}
}
```

- The name must end in `.json`; each POST replaces the whole file. The body must be valid JSON
  sent as `application/json`.
- Read it yourself with `cat /app/runtime/canvas-pages/_data/my-game-save.json`.
- Do not use `/api/upload` for state: it renames every file, so you cannot read a fixed name back.
- On a login-required install both calls need the viewer's session. The page's cookie and
  `authFetch` provide it, so saving works for the logged-in user but not for anonymous visitors
  of a public page.

## Talking to OpenVoiceUI

Page to app: `window.parent.postMessage({ type: 'canvas-action', action, ... }, '*')`

| action | extra fields | effect |
|--------|--------------|--------|
| `speak` | `text`, `userInitiated` | sends `text` to the agent as if the user said it |
| `navigate` | `page` (exact page id) | opens another canvas page |
| `open-url` | `url`, optional `padded: true` | loads a URL in the canvas frame |
| `menu` | none | opens the canvas page menu |
| `close` | none | closes the canvas |

Each `speak` is a paid agent turn, so the app filters them. Messages with `userInitiated: true`
always pass. Others are dropped within 15 seconds of the page loading and limited to one per
60 seconds. The injected `speak()` helper sets `userInitiated` when it is called during a tap or
click (transient user activation), so a button can call it directly:

```javascript
askBtn.onclick = () => speak(`I just scored ${score}. Can you beat that?`);
```

Set `userInitiated` only on a real user action such as a button on the game-over screen, never
on a timer or automatic event.

App to page: when your reply contains
`[CANVAS_ACTION:{"action":"spawn-wave","payload":{"count":5}}]`, the open page receives
`{ type: 'canvas-action', action: 'spawn-wave', payload: { count: 5 } }`. The app queues it
until the page has loaded, so `[CANVAS:my-game]` and the action can go in the same reply. `close`,
`menu` and `navigate` are handled by the app and never reach the page.

```javascript
addEventListener('message', (e) => {
  if (e.source !== window.parent || e.data?.type !== 'canvas-action') return;
  if (e.data.action === 'spawn-wave') spawnWave(e.data.payload?.count ?? 5);
});
```

This lets the voice agent drive the game ("send in five more") while the user plays.

## Controls and input

- **OrbitControls** are the default: they work with mouse and touch.
- **PointerLockControls** work: the canvas iframe is allowed pointer lock. Lock only starts from a
  click (`controls.lock()` in a click handler), Esc releases it, and touch devices have no pointer
  lock. Show a "click to play" overlay and keep a touch/orbit fallback.
- Keyboard events reach the page only after the user clicks into it; say so on the start screen.
- Tell clicks from drags before treating a press as "select this object":
  ```javascript
  let down = null;
  canvas.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY }; });
  canvas.addEventListener('pointerup', e => {
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5) handleClick(e);
  });
  ```

## Performance

- Cap pixel ratio at 2; drive everything from `renderer.setAnimationLoop` and delta time.
- `InstancedMesh` for many copies (trees, bullets, particles); pool objects instead of
  creating and destroying them every frame.
- Shadows only on the key light, shadow map 1024 to 2048.
- Power-of-two textures (512, 1024, 2048); resize before saving, not at runtime.
- `.dispose()` geometries, materials and textures you remove.

## Larger projects with a build step

A single file is best. For a game that needs npm packages:

1. Create the project outside canvas-pages (for example `~/projects/my-game`), add `three` and
   `vite` with your package manager.
2. In `vite.config.js` set `base: '/pages/my-game/'`.
3. Keep CSS in a `<style>` block in `index.html` and never import `.css` from JS: Vite would emit
   a separate stylesheet, and the canvas CSP blocks same-origin stylesheets. Put the viewport and
   `page-icon` meta tags at the top of `<head>`.
4. Build. Copy `dist/index.html` to `/app/runtime/canvas-pages/my-game.html` and everything else
   in `dist/` into `/app/runtime/canvas-pages/my-game/`. Open with `[CANVAS:my-game]`.

A dev server cannot be shown in the canvas: `[CANVAS_URL:]` refuses localhost and private IPs.

## Briefing a coding agent for a big game

When handing a large game (500+ lines) to a coding tool or sub-agent, give it: the output path;
the jsdelivr import map with a pinned version; inline CSS only; viewport and page-icon meta tags;
fixed full-viewport canvas with a safe-area HUD; same-origin assets; saves through
`/api/canvas/data/<game>.json`, never browser storage; a menu button; a loading screen; inline
SVG icons, no emoji. Then lint, open and play the result yourself.
