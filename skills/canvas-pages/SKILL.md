---
name: canvas-pages
description: Create, open, verify, update and share OpenVoiceUI canvas pages (the HTML pages shown in the canvas panel and on the desktop). Covers the write path, the [CANVAS:] tag, verification, lint, icons, categories, page data storage, uploads and privacy. Read before building any canvas page.
---

# Canvas Pages: complete workflow

A canvas page is a self-contained HTML file that OpenVoiceUI shows in its canvas panel
and lists on the desktop. You create it by writing a file; you show it by putting a
`[CANVAS:page-id]` tag in your spoken reply.

## Where things live

| What | Docker install (agent container) | Browser URL |
|------|----------------------------------|-------------|
| Canvas pages | `/app/runtime/canvas-pages/<name>.html` (same dir is also mounted at `~/.openclaw/workspace/canvas-pages/`) | `/pages/<name>.html` |
| Page data (JSON) | `/app/runtime/canvas-pages/_data/<name>.json` | `/api/canvas/data/<name>.json` |
| Uploads | `/app/runtime/uploads/<file>` | `/uploads/<file>` |

On a native (non-Docker) install the same folders live under the OpenVoiceUI `runtime/`
directory (or wherever `CANVAS_PAGES_DIR` points). The page id is the filename without
`.html`.

API examples use `http://localhost:5001`; if you changed `PORT`, use that port. Every call
sends `-H "X-Agent-Key: ${AGENT_API_KEY:-}"`, which is only needed when the install requires
login (Clerk + `CANVAS_REQUIRE_AUTH=true`) and `AGENT_API_KEY` is set; it is harmless otherwise.

In HTML (`src`, `href`, `fetch`) always use the browser URLs (`/pages/...`, `/uploads/...`),
never filesystem paths.

## Step 0: check the user's chosen style (always first)

```bash
cat /app/runtime/canvas-pages/canvas-styles/ACTIVE-STYLE.md
```

- If it exists, the user picked a design system on the Canvas Styles page. Follow its
  rules and start every new page from
  `/app/runtime/canvas-pages/canvas-styles/active-template.html` (keep its whole `<style>`
  foundation, replace the demo content). It overrides the default colors below.
- If it does not exist, use the default dark theme below and the `canvas-web-design` skill.
- List or switch styles yourself when the user asks:

```bash
curl -s http://localhost:5001/api/canvas/styles -H "X-Agent-Key: ${AGENT_API_KEY:-}"
curl -s -X POST http://localhost:5001/api/canvas/styles/<style-id>/activate -H "X-Agent-Key: ${AGENT_API_KEY:-}"
```

## Step 1: write the file

Write the complete HTML to `/app/runtime/canvas-pages/<page-name>.html` with your file
write tool. Alternatively POST it:

```bash
curl -s -X POST http://localhost:5001/api/canvas/pages \
  -H "X-Agent-Key: ${AGENT_API_KEY:-}" -H "Content-Type: application/json" \
  -d '{"filename": "my-page.html", "title": "My Page", "html": "<!DOCTYPE html>..."}'
# -> {"filename": "my-page.html", "page_id": "my-page", "url": "/pages/my-page.html", ...}
```

`desktop.html` and `file-explorer.html` are system pages; never overwrite them.
Every write is versioned automatically (last 20 versions per page):
`GET /api/canvas/versions/<page-id>` lists them and
`POST /api/canvas/versions/<page-id>/<timestamp>/restore` restores one.

## Step 2: open it

Put the tag inside a spoken sentence:

```
Here's the plan I built for you. [CANVAS:my-page] It covers the next three weeks.
```

- `[CANVAS:page-id]` opens an EXISTING page. It does not create anything: write the file first.
- Repeating `[CANVAS:same-page]` on an already-open page reloads it; do that after an update.
- `[CANVAS_CLOSE]` closes the canvas. `[CANVAS_URL:https://example.com]` loads an external
  site (only sites that allow iframe embedding; localhost and private IPs are refused, because
  the user's browser cannot reach them).
- Do not use OpenClaw's built-in `canvas` tool for this. That tool drives OpenClaw node
  canvases (desktop/mobile node apps), a different feature, and fails without a node.
- A tag on its own is silence. Always say words with it.

## Step 3: verify it opened

```bash
curl -s http://localhost:5001/api/canvas/context -H "X-Agent-Key: ${AGENT_API_KEY:-}"
```

`current_page` should be your file. If it is still the old page, say so and send the tag
again. If it is empty, the canvas panel may be closed; send the tag again.

## Step 4: lint it

```bash
curl -s http://localhost:5001/api/canvas/lint/my-page -H "X-Agent-Key: ${AGENT_API_KEY:-}"
# -> {"ok": true|false, "issues": [...]}
```

Fix every issue (emoji used as icons, banned purple/pink, CDN frameworks, missing viewport or
page-icon meta, active-style mismatch). A 404 means the server predates the style system; skip.

## Step 5: check three widths

Check about 1280px (desktop), 768px (tablet) and 375px (phone). Nothing may overflow or force
the page body to scroll sideways: wide tables, diagrams and code scroll inside their own
`overflow-x:auto` container. Tap targets are at least 44px. A layout break is a bug, not a note.

## Step 6: prove it is done

A page is done when the SERVED page shows the real content, not when the file exists. Fetch it
the way the user will and look for one specific real value only the finished page contains (a
name, a price, a date from the data), not a heading or template text:

```bash
curl -s http://localhost:5001/pages/my-page.html | grep -c "<a real value>"
```

0 hits means not done. For a page that fills itself from `_data/*.json`, also check that the
data file exists and that the page fetches that exact path. On a login-required install a
private page answers 401 to this unauthenticated fetch; that is the correct state of a private
page, not a fault. Check the file on disk and the data files it reads instead.

## HTML rules

- **Desktop icon (required):** `<meta name="page-icon" content="TYPE">` near the top of `<head>`
  (only the first 2 KB of the file are read for it). Common
  types: `dashboard`, `game`, `music`, `tools`, `book`, `upload`, `document`, `image`, `video`,
  `chart`, `calendar`, `settings`, `website`, `internet`, `crm`, `voice`, `terminal`,
  `interactive-map`, `file-explorer`, `style-guide`, `voice-studio`. Pick the closest match.
- **Self-contained:** all CSS in `<style>`, all JS in `<script>`. Google Fonts `@import` is fine.
  No Tailwind or Bootstrap CDN. `cdn.jsdelivr.net` is allowed by the canvas CSP for libraries
  such as Three.js.
- **Theme:** follow the active style if one is set. Otherwise dark: background `#0d1117` or
  `#13141a`, text `#e2e8f0`, accents blue `#3b82f6`, cyan `#06b6d4`, amber `#f59e0b`,
  emerald `#10b981`.
- **Banned:** purple (`#764ba2`, `#667eea`), pink and magenta as theme colors; emoji as UI icons.
  Use the inline SVG icons in `/app/runtime/canvas-pages/canvas-styles/icons.html` (written when
  a style is activated; `stroke="currentColor"`), or plain Unicode symbols / hand-written SVG.
- **Real content:** cards, grids, tables and real data. No placeholder text, no blank pages.
- **Phones:** the canvas injects a 25px safe-area padding on `html,body` (also exposed as
  `--canvas-safe-top/right/bottom/left`). That is the only outer inset: on screens up to 640px,
  wrappers and sections get no extra side padding (cards keep their own 16-20px). Never stack
  padded containers.
- **No `href="#"`:** it does nothing inside the canvas iframe. Use the bridge below.
- **No browser storage:** never keep state in `localStorage`/`sessionStorage`; the server is the
  record (see Page data).
- For premium layout, motion and component patterns read the `canvas-web-design` skill.

## Buttons and the canvas bridge (postMessage)

```html
<script>
function speak(text) {   // sends text to the assistant as if the user said it
  window.parent.postMessage({type:'canvas-action', action:'speak', text: text, userInitiated: true}, '*');
}
function nav(page) {     // open another canvas page by id
  window.parent.postMessage({type:'canvas-action', action:'navigate', page: page}, '*');
}
function menu() {        // open the page menu
  window.parent.postMessage({type:'canvas-action', action:'menu'}, '*');
}
</script>
<button onclick="speak('Show me this week\'s numbers')">Weekly numbers</button>
```

The canvas injects default `nav()` and `speak()` helpers into every page that does not define
its own. The injected `speak()` sets `userInitiated` when it runs during a tap or click in the
page, so a plain `onclick="speak(...)"` works without defining your own. Other actions: `open-url` (with `url`) loads a URL in the canvas, `close` hides the canvas.
Every `speak` is a paid assistant turn: call it only from a user action. Speaks without
`userInitiated: true` are dropped within 15 seconds of a page load and limited to one per minute.
External links: `<a href="..." target="_blank">`.

The assistant can also drive a page: `[CANVAS_ACTION:{"action":"<verb>","payload":{...}}]` in a
reply is delivered to the open page as `{type:'canvas-action', action, payload}` (queued until the
page has loaded). The verbs are yours to define (for example a form fill); the page must declare a
`message` listener that handles them. `close`, `menu` and `navigate` (with `payload.page`) act on
the shell itself.

Uncaught JavaScript errors on the open page are reported back to you with the user's next message
(the canvas error bridge), so a broken page tells you why. On login-required installs a page can
call the API with the injected `authFetch(url, opts)`, which adds the user's session token.

## Page data that must persist

Pages store state on the server through the canvas data API (JSON files in
`canvas-pages/_data/`):

```js
// load on page init ({} if the file does not exist yet)
const state = await fetch('/api/canvas/data/my-page-state.json', {cache: 'no-store'}).then(r => r.json());
// save (debounce input handlers, about 1s)
const r = await fetch('/api/canvas/data/my-page-state.json', {
  method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(state)});
// r.ok false -> show a visible "not saved, retrying" state; never drop a save silently
```

You read the same data directly at `/app/runtime/canvas-pages/_data/my-page-state.json`. This is
how a notes box or checklist on a page becomes an async channel from the user to you: the page
saves the user's notes with a `synced_at` timestamp, you read the file and act on it.

A list page (media library, track list, file list) builds its list from a server listing or a
data file at load time, never from a hard-coded array that drifts from the real files.

## Desktop categories

```bash
curl -s http://localhost:5001/api/canvas/manifest/category -H "X-Agent-Key: ${AGENT_API_KEY:-}"   # list category ids
curl -s -X PATCH http://localhost:5001/api/canvas/manifest/page/my-page \
  -H "X-Agent-Key: ${AGENT_API_KEY:-}" -H "Content-Type: application/json" \
  -d '{"category": "CATEGORY_ID"}'
```

The same PATCH accepts `display_name`, `description`, `tags`, `starred` and `icon`. Tell the user
where the page will appear on their desktop.

## Privacy

Pages are private by default when the install requires login. Never make a page public while
creating it. Only make one public when the user explicitly asks, in a separate request, and warn
them first. See the `canvas-page-visibility` skill for the call and its rules.

When you hand the user a page, give the full link: `https://<your-domain>/pages/<name>.html`
(or `http://localhost:5001/pages/<name>.html` on a local install).

## Uploads

```bash
curl -s -X POST http://localhost:5001/api/upload -H "X-Agent-Key: ${AGENT_API_KEY:-}" -F "file=@/path/to/photo.png"
# -> {"url": "/uploads/photo__1a2b3c4d.png", "original_name": "photo.png", ...}
```

- Max 100 MB. The stored name gets a short random suffix: use the returned `url`.
- Uploaded files are permanent; UI actions never delete them.
- `/uploads/` shows only inert media inline (png, jpg, jpeg, gif, webp, bmp, ico, avif, mp4,
  webm, ogg, mov, mp3, wav, m4a, pdf). Office files, CSV and zip download with their real type;
  everything else, including `.html`, `.svg` and `.js`, downloads as plain text. That is a
  deliberate security control (stored XSS): anything a person should READ as a page goes into
  canvas pages, never into uploads.
- Give the user a file by putting it on a small delivery page with a clear download button per
  file, and send one link: the page. Verify every file URL the page uses actually serves.

## Code the user will copy

When a page exists so the user can copy code out of it:

1. HTML-escape every code block (`&`, `<`, `>` as entities inside `<pre><code>`); a copy button
   copies `textContent`, never `innerHTML`.
2. Keep the code as a real file next to the page and regenerate the whole page from it on every
   change; never hand-patch code inside the HTML.
3. Before announcing it, fetch the served page, extract the block's text with an HTML parser (the
   way a browser renders it) and check it equals the source file and still compiles
   (`python3 -m py_compile`, `node --check`).

## Large pages

For a large polished page (hundreds of lines), consider delegating the build to a coding
sub-agent with the brief template in the `canvas-web-design` skill. Quick internal pages can be
written inline.
