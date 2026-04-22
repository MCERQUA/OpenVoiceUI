// html-surfaces.js
// Paint live, interactive HTML onto named meshes in a Three.js scene.
// Requires Chrome with chrome://flags/#canvas-draw-element enabled.
//
// Usage:
//   import * as THREE from "three";
//   import { mountHtmlSurfaces } from "/pages/_shared/html-surfaces.js";
//
//   const surfaces = mountHtmlSurfaces({
//     scene, camera, renderer,
//     targets: [
//       { object: "tv_screen",   src: "/pages/ai-radio.html",    width: 1600, height: 900 },
//       { object: "desk_tablet", html: "<h1>Notes</h1>",          width: 512,  height: 768 },
//       { object: "wall_chart",  element: document.getElementById("chart") },
//     ],
//     fallbackMode: "snapshot", // or "placeholder" | "hidden"
//   });
//
// `object` is a mesh name (string) or a THREE.Object3D reference.
// The surface API: surfaces.get("tv_screen").navigate("/pages/other.html")
//                  surfaces.get("desk_tablet").setHtml("<p>...</p>")
//                  surfaces.get("tv_screen").refresh()
//                  surfaces.dispose()
//
// The module self-detects `gl.texElementImage2D`. If missing, each target falls
// back to a static placeholder/snapshot texture per `fallbackMode`.

import * as THREE from "three";

export function mountHtmlSurfaces({
  scene,
  camera,
  renderer,
  targets = [],
  fallbackMode = "placeholder",
  log = console,
}) {
  if (!scene || !camera || !renderer) {
    throw new Error("[html-surfaces] scene, camera, renderer are required");
  }

  const canvas = renderer.domElement;
  const gl = renderer.getContext();
  const supported = typeof gl.texElementImage2D === "function";

  if (supported) {
    canvas.setAttribute("layoutsubtree", "true");
  } else {
    log.warn?.(
      "[html-surfaces] gl.texElementImage2D not available. " +
      "Enable chrome://flags/#canvas-draw-element for live HTML surfaces. " +
      `Using fallback mode: ${fallbackMode}.`
    );
  }

  const surfaces = new Map();
  const meshToSurface = new WeakMap();
  const pickMeshes = [];

  for (const target of targets) {
    const mesh = resolveMesh(scene, target.object);
    if (!mesh) {
      log.warn?.(`[html-surfaces] no mesh found for "${target.object}"`);
      continue;
    }
    if (!mesh.isMesh) {
      log.warn?.(`[html-surfaces] "${target.object}" is not a THREE.Mesh`);
      continue;
    }

    const surface = supported
      ? createLiveSurface(mesh, target, { canvas, renderer, log })
      : createFallbackSurface(mesh, target, { fallbackMode });

    if (!surface) continue;
    surfaces.set(keyFor(target.object), surface);
    meshToSurface.set(mesh, surface);
    pickMeshes.push(mesh);
  }

  // ── Pointer routing (one listener, routes to whichever surface the ray hits)
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let dispatching = false;
  let hoveredTarget = null;
  let hoveredSurface = null;

  function pickSurface(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObjects(pickMeshes, false);
    return hits[0] || null;
  }

  function clearHover() {
    if (!hoveredTarget) return;
    hoveredTarget.dispatchEvent(new MouseEvent("mouseleave", { bubbles: false }));
    hoveredTarget.classList?.remove("hover");
    hoveredSurface?.refresh();
    hoveredTarget = null;
    hoveredSurface = null;
    canvas.style.cursor = "";
  }

  function handlePointer(e, type) {
    if (!supported || dispatching) return;
    const hit = pickSurface(e.clientX, e.clientY);
    if (!hit || !hit.uv) { clearHover(); return; }

    const surface = meshToSurface.get(hit.object);
    if (!surface || !surface.sourceEl) return;

    // UV → element clientX/clientY. Three.js V=0 is mesh bottom; texture
    // has HTML top at v=0 via repeat/offset flip — so invert here.
    const elRect = surface.sourceEl.getBoundingClientRect();
    const x = elRect.left + hit.uv.x * elRect.width;
    const y = elRect.top + (1 - hit.uv.y) * elRect.height;

    // Bring source above the canvas for hit-testing, then dispatch.
    const prevZ = surface.sourceEl.style.zIndex;
    surface.sourceEl.style.pointerEvents = "auto";
    surface.sourceEl.style.zIndex = "999999";

    const target =
      document.elementFromPoint(x, y) || surface.sourceEl;

    if (target !== hoveredTarget) {
      if (hoveredTarget) {
        hoveredTarget.dispatchEvent(new MouseEvent("mouseleave", { bubbles: false }));
        hoveredTarget.classList?.remove("hover");
      }
      target.dispatchEvent(new MouseEvent("mouseenter", { bubbles: false }));
      target.classList?.add("hover");
      hoveredTarget = target;
      hoveredSurface = surface;
    }

    const tag = target.tagName;
    canvas.style.cursor =
      tag === "A" || tag === "BUTTON" || tag === "SELECT" || tag === "INPUT"
        ? (target.type === "range" ? "grab" : "pointer")
        : "default";

    dispatching = true;
    target.dispatchEvent(new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      clientX: x,
      clientY: y,
      button: e.button,
      buttons: e.buttons,
    }));
    if (type === "mouseup") {
      target.dispatchEvent(new MouseEvent("click", {
        bubbles: true, cancelable: true, clientX: x, clientY: y,
      }));
    }
    dispatching = false;

    surface.sourceEl.style.pointerEvents = "none";
    surface.sourceEl.style.zIndex = prevZ;
    surface.refresh();
  }

  const onDown = (e) => handlePointer(e, "mousedown");
  const onMove = (e) => handlePointer(e, "mousemove");
  const onUp   = (e) => handlePointer(e, "mouseup");
  window.addEventListener("mousedown", onDown, true);
  window.addEventListener("mousemove", onMove, true);
  window.addEventListener("mouseup", onUp, true);

  return {
    supported,
    get(name) { return surfaces.get(keyFor(name)); },
    all() { return [...surfaces.values()]; },
    mesh(name) { return surfaces.get(keyFor(name))?.mesh || null; },
    refreshAll() { surfaces.forEach(s => s.refresh()); },
    dispose() {
      window.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("mousemove", onMove, true);
      window.removeEventListener("mouseup", onUp, true);
      surfaces.forEach(s => s.dispose?.());
      surfaces.clear();
    },
  };
}

// Alias for readability in agent code
export { mountHtmlSurfaces as createHtmlSurfaces };

// ─────────────────────────────────────────────────────────────────────────────

function keyFor(ref) {
  if (typeof ref === "string") return ref;
  if (ref && ref.isObject3D) return ref.uuid;
  return String(ref);
}

function resolveMesh(scene, ref) {
  if (ref && ref.isObject3D) return ref;
  if (typeof ref !== "string") return null;
  let found = null;
  scene.traverse((o) => {
    if (!found && o.name === ref) found = o;
  });
  return found;
}

function applyTextureToMesh(mesh, texture, slot = "map") {
  const mat = mesh.material;
  if (!mat) {
    mesh.material = new THREE.MeshBasicMaterial({
      map: texture, side: THREE.DoubleSide,
    });
    return;
  }
  // If the material already has a `map` slot, just swap.
  if (slot in mat) {
    mat[slot] = texture;
    if (mat.color) mat.color.set(0xffffff);
    mat.needsUpdate = true;
    return;
  }
  // Material without a map slot (e.g. LineBasicMaterial) — replace.
  mesh.material = new THREE.MeshBasicMaterial({
    map: texture, side: mat.side ?? THREE.DoubleSide,
  });
}

function createLiveSurface(mesh, target, { canvas, renderer, log }) {
  const width = target.width || 1024;
  const height = target.height || 1024;

  // Source element: iframe (src), div (html), or caller-provided element.
  let sourceEl;
  let mode;
  if (target.element) {
    sourceEl = target.element;
    mode = "external";
  } else if (target.src) {
    sourceEl = document.createElement("iframe");
    sourceEl.src = target.src;
    sourceEl.setAttribute("frameborder", "0");
    sourceEl.setAttribute("allow", "autoplay; clipboard-read; clipboard-write");
    mode = "iframe";
  } else {
    sourceEl = document.createElement("div");
    sourceEl.innerHTML = target.html || "";
    mode = "inline";
  }

  // Layout: in-flow inside the canvas, at (0,0), hidden behind the WebGL output.
  Object.assign(sourceEl.style, {
    width: width + "px",
    height: height + "px",
    position: "absolute",
    left: "0",
    top: "0",
    pointerEvents: "none",
    zIndex: "0",
  });
  canvas.appendChild(sourceEl);

  // Texture — we use an empty three.js Texture and hijack its GL handle.
  const texture = new THREE.Texture();
  texture.image = { width, height };
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.generateMipmaps = false;
  // HTML paints top→bottom, three.js UV has V=0 at bottom. Flip via texture matrix
  // so the rendered surface isn't upside-down. Interaction code mirrors this flip.
  texture.repeat.set(1, -1);
  texture.offset.set(0, 1);
  texture.needsUpdate = true;
  renderer.initTexture(texture);

  applyTextureToMesh(mesh, texture, target.materialSlot || "map");

  const gl = renderer.getContext();
  const getGlTex = () => renderer.properties.get(texture)?.__webglTexture || null;

  let paintPending = false;
  let disposed = false;

  function uploadOnce() {
    const glTex = getGlTex();
    if (!glTex) return;
    gl.bindTexture(gl.TEXTURE_2D, glTex);
    try {
      gl.texElementImage2D(
        gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, sourceEl
      );
    } catch (err) {
      log.warn?.("[html-surfaces] texElementImage2D failed:", err);
      return;
    }
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindTexture(gl.TEXTURE_2D, null);
  }

  function refresh() {
    if (disposed || paintPending) return;
    paintPending = true;
    const onPaint = () => {
      paintPending = false;
      uploadOnce();
    };
    canvas.addEventListener("paint", onPaint, { once: true });
    canvas.requestPaint();
  }

  // Auto-refresh on DOM mutations (inline mode only; iframes can't be observed
  // from outside their document).
  let observer = null;
  if (mode === "inline" || mode === "external") {
    observer = new MutationObserver(() => refresh());
    observer.observe(sourceEl, {
      subtree: true, childList: true,
      attributes: true, characterData: true,
    });
  }

  // Initial upload + iframe polling
  let pollIvl = null;
  if (mode === "iframe") {
    sourceEl.addEventListener("load", () => {
      // Give the iframe a tick to settle its own layout, then paint.
      requestAnimationFrame(() => requestAnimationFrame(refresh));
      if (target.refreshMs !== 0) {
        pollIvl = setInterval(refresh, target.refreshMs || 500);
      }
    }, { once: false });
  } else {
    requestAnimationFrame(() => requestAnimationFrame(refresh));
  }

  return {
    mesh,
    sourceEl,
    texture,
    mode,
    refresh,
    navigate(url) {
      if (mode !== "iframe") return;
      sourceEl.src = url;
    },
    setHtml(html) {
      if (mode === "iframe") return;
      sourceEl.innerHTML = html;
      refresh();
    },
    dispose() {
      disposed = true;
      observer?.disconnect();
      if (pollIvl) clearInterval(pollIvl);
      sourceEl.remove();
      texture.dispose();
    },
  };
}

function createFallbackSurface(mesh, target, { fallbackMode }) {
  if (fallbackMode === "hidden") {
    return { mesh, sourceEl: null, refresh() {}, dispose() {} };
  }

  const width = target.width || 1024;
  const height = target.height || 1024;
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#1a1a1a";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#e5e7eb";
  ctx.font = `bold ${Math.round(height * 0.05)}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.fillText("HTML SURFACE", width / 2, height / 2 - 30);
  ctx.font = `${Math.round(height * 0.028)}px system-ui, sans-serif`;
  ctx.fillStyle = "#9ca3af";
  ctx.fillText("Live DOM rendering needs Chrome", width / 2, height / 2 + 10);
  ctx.fillText("chrome://flags/#canvas-draw-element", width / 2, height / 2 + 44);
  if (target.src) {
    ctx.fillText(target.src, width / 2, height / 2 + 78);
  }

  const texture = new THREE.CanvasTexture(c);
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  applyTextureToMesh(mesh, texture, target.materialSlot || "map");

  return {
    mesh,
    sourceEl: null,
    texture,
    mode: "fallback",
    refresh() {},
    dispose() { texture.dispose(); },
  };
}
