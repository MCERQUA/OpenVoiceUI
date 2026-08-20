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
