# archive/

Files that no longer belong in the working tree but are kept for reference instead of being
deleted. Nothing here is imported, served, installed or copied into the Docker image (`archive/`
is listed in `.dockerignore`, and `package.json` `files` does not include it). To restore a file,
`git mv` it back to its original path.

| File | Original path | Archived | Why |
|---|---|---|---|
| `app.py.pre-csp-leaflet-20260727T192343Z` | `app.py.pre-csp-leaflet-20260727T192343Z` | 2026-10-16 | Dated pre-change backup of `app.py` from 2026-07-27, committed by mistake; the live `app.py` supersedes it. |
| `icon.old.png` | `icon.old.png` | 2026-10-16 | Previous app icon; `pinokio.js` uses `icon.png` and nothing references this file. |
| `deploy-legacy/setup-sudo.sh` | `deploy/setup-sudo.sh` | 2026-10-16 | Stale March 2026 fork of the root `setup-sudo.sh`. It resolves `INSTALL_DIR` to `deploy/`, so it looks for `deploy/.env` and exits on a normal checkout; it also lacks the OpenClaw version pin that `bump-openclaw-version.sh` maintains. Use the root `setup-sudo.sh`. |
| `deploy-legacy/setup-nginx.sh` | `deploy/setup-nginx.sh` | 2026-10-16 | Duplicate of the root `setup-nginx.sh` (comments differ only). |
| `deploy-legacy/openvoiceui.service` | `deploy/openvoiceui.service` | 2026-10-16 | Older copy of the root `openvoiceui.service` (`Restart=always` instead of the fixed `Restart=on-failure`). `setup-sudo.sh` writes its own unit file. |
