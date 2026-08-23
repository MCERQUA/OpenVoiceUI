# Retired default pages

Files here are **no longer seeded to tenants**. `server.py` seeds `DEFAULT_PAGES_DIR` with
`for src in DEFAULT_PAGES_DIR.iterdir(): if not src.is_file(): continue` — it iterates the top level
only and skips directories, so moving a page into this folder removes it from the shipped set
without deleting anything.

Nothing here is deleted, and nothing here is removed from any tenant that already has a copy: the
seeder only writes when `not dest.exists()`, so existing `canvas-pages/` copies are untouched by
design. Retiring a default stops NEW tenants from getting it; it does not reach into existing ones.

## 2026-08-20 — retired at Mike's request

- **`website-creator.html`** — superseded by `website-setup.html`, which is the live page.
  ⚠️ The *data channel* `website-creator.json` is NOT retired and must not be touched:
  `website-setup.html` POSTs to `/api/canvas/data/website-creator.json` (lines 2028/2047/2057) and
  `routes/canvas.py` treats `website-setup.json` and `website-creator.json` as a pair. The page is
  gone; the JSON name it shares with its successor is still in use.
- **`ai-app-library.html`** (+ `ai-app-library-icon.svg`) — already hidden from the desktop grid via
  `DESKTOP_HIDDEN` in `desktop.html`.
- **`monaco-editor.html`** — shipped broken. It loads Monaco from cdn.jsdelivr.net (allowed in both
  `script-src` and `style-src`) and calls `/api/workspace/tree|file` (present, and correctly 401 to
  an unauthenticated caller) — but it defines no `MonacoEnvironment`/`getWorkerUrl`, so Monaco's AMD
  loader tries to spawn its language workers from the CDN origin and `worker-src 'self' blob:`
  blocks them. Initialization throws and the sidebar never populates: "empty sidebar, nothing
  clickable". The fix is a ~6-line blob: worker shim, kept here with the page if it is ever revived.
  Its entry was also removed from `_OS_PAGES` in `routes/canvas.py` — that set skips auth, so an
  allowlist entry for a page we no longer ship is a latent hole.

## 2026-08-23 — retired at Mike's request

- **`style-guide.html`** — superseded by `canvas-styles.html`, the per-tenant design-system
  picker (meridian/atelier/obsidian presets + clone/customize). No code references
  `style-guide` in `routes/`, `services/` or `src/`, so the move is inert to the app.

### The half of "retire" that was missing until today

Everything above is true and is only HALF the operation. Because the seeder never reaches
into existing tenants, every page retired here stayed live on all 29 tenant desktops —
in `canvas-manifest.json` and in the desktop state (`desktopPages` / `knownPages` /
custom folders). Mike, 2026-08-23: *"monaco editor was supposed to be removed but i see it
added back to desktops instead"* and *"style guide is supposed to be removed but i keep
seeing that too"*. He was right, and the count was 29/29 for both.

Retiring a page now has a second, host-side step:

1. Move the file here (this repo) — stops NEW tenants getting it.
2. Add the slug to `/home/mike/MIKE-AI/data/canvas-retired-pages.txt` — stops the canvas
   manifest reconciler (cron `7-59/10`) re-registering it from disk, which it otherwise
   WILL do, because that reconciler adds any `.html` it finds and the files are never
   deleted. Without this step, deregistering a page is undone within ten minutes.
3. Deregister from existing tenants' manifests + desktop state.

Step 2 is the non-obvious one: "the file is still on disk" is not evidence a page should be
registered, but that is precisely what the reconciler assumes.
