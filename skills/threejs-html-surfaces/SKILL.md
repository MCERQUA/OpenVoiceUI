---
name: threejs-html-surfaces
description: Experimental - paint live HTML (canvas pages, inline HTML, DOM elements) onto named meshes in a Three.js canvas page with /pages/_shared/html-surfaces.js. Needs Chrome with chrome://flags/#canvas-draw-element; other browsers get a placeholder. Load after threejs-canvas-integration.
---

# HTML surfaces on Three.js meshes

`html-surfaces.js` turns a named mesh into a screen: a TV in a room shows a canvas page, a
tablet on a desk shows a widget, and clicks on the 3D surface are routed back to the HTML.

**Experimental.** It uses Chrome's HTML-in-Canvas API (`gl.texElementImage2D`, the canvas
`layoutsubtree` attribute, `canvas.requestPaint()`), which is behind
`chrome://flags/#canvas-draw-element` and may change. Without the flag (and in every other
browser) the scene still renders and each surface gets a placeholder texture that names the flag.
Never make core gameplay or information depend on a surface.

Build the page shell first (import map, full-viewport canvas, CSP rules): see
`threejs-canvas-integration`.

## Module location

Import it from `/pages/_shared/html-surfaces.js`. OpenVoiceUI copies its `canvas-shared/` files
into `<canvas-pages>/_shared/` at startup. If that URL returns 404, the server predates that
seeding: copy `canvas-shared/html-surfaces.js` from the OpenVoiceUI repo into
`<canvas-pages>/_shared/` (in Docker, `/app/runtime/canvas-pages/_shared/`).

The module does `import * as THREE from "three"`, so the page's import map **must** map
`"three"`; it then shares your Three.js instance.

## Usage

```javascript
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mountHtmlSurfaces } from '/pages/_shared/html-surfaces.js';

// scene, camera, renderer created as in threejs-canvas-integration
const gltf = await new GLTFLoader().loadAsync('/pages/room-assets/living-room.glb');
scene.add(gltf.scene);                                   // meshes must be in the scene before mounting
gltf.scene.traverse(o => o.isMesh && console.log('mesh:', o.name));   // find names, then remove

const surfaces = mountHtmlSurfaces({
  scene, camera, renderer,
  targets: [
    { object: 'tv_screen', src: '/pages/news-board.html', width: 1600, height: 900 },
    { object: 'desk_tablet', element: buildControlPanel(), width: 768, height: 1024 },
    { object: 'wall_sign', html: '<h1 style="color:#e2e8f0">Welcome</h1>', width: 1024, height: 512 },
  ],
});
console.log('live HTML surfaces:', surfaces.supported);
```

`createHtmlSurfaces` is exported as an alias of `mountHtmlSurfaces`.

## Options

| Option | Default | Notes |
|--------|---------|-------|
| `scene`, `camera`, `renderer` | required | throws if any is missing |
| `targets` | `[]` | array of target objects (below) |
| `fallbackMode` | `"placeholder"` | `"hidden"` leaves the mesh untouched; any other value (including `"snapshot"`, which the module's header comment mentions but does not implement) gives the placeholder |
| `log` | `console` | receives warnings |

## Target fields

| Field | Default | Notes |
|-------|---------|-------|
| `object` | required | mesh name (first exact `object.name` match in `scene.traverse`) or an `Object3D`; must be a `THREE.Mesh`, otherwise it is skipped with a warning |
| `src` | | URL loaded in an iframe; keep it same-origin (`/pages/...`), the canvas CSP blocks most external frames |
| `html` | | HTML string set as a div's `innerHTML` (its `<script>` tags do not run) |
| `element` | | an element you built in JS; in live mode it is moved inside the WebGL canvas. Do not also add it to the page, or it shows as an overlay when the placeholder is used |
| `width`, `height` | `1024` | source size in CSS pixels; match the mesh's aspect ratio |
| `materialSlot` | `"map"` | material property that receives the texture |
| `refreshMs` | `500` | iframe repaint interval; `0` repaints only after interaction |

Precedence when several are given: `element`, then `src`, then `html`.

## Returned handles

`mountHtmlSurfaces()` returns:

- `supported`: `true` when live rendering is active.
- `get(key)`: the surface for a target, by the same name or object you passed as `object`
  (undefined if its mesh was not found).
- `mesh(key)`, `all()`, `refreshAll()`.
- `dispose()`: removes the window listeners and every surface's element, texture and timers.

A live surface has `mesh`, `sourceEl`, `texture`, `mode` (`"iframe"`, `"inline"` or
`"external"`), `refresh()`, `navigate(url)` (iframe surfaces only), `setHtml(html)` (not iframe
surfaces) and `dispose()`. Placeholder surfaces have no `navigate` or `setHtml`, so guard calls:

```javascript
surfaces.get('tv_screen')?.navigate?.('/pages/weather.html');
```

## What interaction really does

The module listens on `window` for `mousedown`, `mousemove` and `mouseup`, raycasts the surface
meshes, converts the hit UV to a point on the source element, finds the element there with
`document.elementFromPoint`, and dispatches synthetic mouse events to it (plus a `click` on
mouseup). Consequences:

- **`html` and `element` surfaces are interactive:** click handlers, buttons and links fire.
  Synthetic events do not trigger CSS `:hover`; the module adds a `hover` class to the element
  under the cursor instead, so style `.hover`.
- **`src` (iframe) surfaces are display-only in practice.** `elementFromPoint` in the page cannot
  see inside the iframe, so events land on the `<iframe>` element and do not reach the embedded
  page's buttons. To make something clickable, build it as an `element` surface; to let the user
  operate a full page, open it with `nav('<page-id>')`.
- **No keyboard routing, no focus.** Text inputs on a surface cannot be typed into.
- **Mouse only.** Pointer and touch events are not handled; taps may arrive as the browser's
  compatibility mouse events, drags will not.
- Your camera controls receive the same events. Pause them while the cursor is over a surface:
  ```javascript
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  renderer.domElement.addEventListener('pointermove', e => {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    controls.enabled = ray.intersectObjects(surfaces.all().map(s => s.mesh), false).length === 0;
  });
  ```

## Repainting

- iframe surfaces repaint every `refreshMs` after their page loads.
- `html` and `element` surfaces repaint when their DOM changes (MutationObserver). CSS
  animations, `<video>` frames and drawing on a `<canvas>` change no DOM, so call
  `surface.refresh()` yourself on an interval for those.
- Every routed mouse event triggers a repaint of that surface.

## Mesh and material rules

- The texture goes into `material[materialSlot]` and `material.color` is set to white. For
  `materialSlot: "emissiveMap"` also set `material.emissive` to white yourself, or the screen
  stays black.
- A mesh with no material, a material array, or a material lacking that slot gets a new
  `MeshBasicMaterial`. For faithful HTML colors, give screen meshes
  `new THREE.MeshBasicMaterial({ toneMapped: false })`.
- UVs must span 0 to 1 across the screen face. `PlaneGeometry` is ideal; for a GLB whose screen
  UVs are tiled or partial, put a plane just in front of it and target the plane.
- Point at the actual mesh: if `tv_screen` is a Group, target its child mesh.
- Mount once per scene. A second `mountHtmlSurfaces` call adds duplicate listeners; call
  `surfaces.dispose()` and mount again to change the target list.
- Sizes are in pixels. Large sources (1920x1080 and up) cost texture upload time on every repaint.

## Building your own variant

For skinned meshes, cloth or custom shaders, the module (about 400 lines) is the reference:
set `layoutsubtree` on the WebGL canvas, make the source element a child of it, create a
`THREE.Texture` with `image = { width, height }` and `renderer.initTexture()`, then on each
`paint` event (after `canvas.requestPaint()`) bind the GL texture from
`renderer.properties.get(texture).__webglTexture` and call `gl.texElementImage2D(...)`. Flip V
with `texture.repeat.set(1, -1); texture.offset.set(0, 1)`. Prior art: the `fimbox/html-in-canvas`
repository on GitHub (PlayCanvas, technique is engine-agnostic).

## Test checklist

1. In Chrome open `chrome://flags/#canvas-draw-element`, enable it, relaunch.
2. Open the page; the console must show no `[html-surfaces]` warnings about missing meshes.
3. `surfaces.supported` is `true` and each surface shows its HTML within a couple of frames.
4. Click a button on an `element`/`html` surface and confirm its handler runs.
5. Turn the flag off, reload: every surface shows the placeholder and the scene still works.
