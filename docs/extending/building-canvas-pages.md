---
sidebar_position: 2
title: Building Canvas Pages
---

# Building Canvas Pages

Canvas pages are single-file HTML documents that run inside an iframe in the OpenVoiceUI interface. They can use the full OpenVoiceUI API and are the primary way the AI delivers visual output.

## Structure

A canvas page is a single `.html` file with inline CSS and JavaScript:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My Page</title>
  <style>
    /* All CSS inline — no external stylesheets */
    body { margin: 0; background: #0a0a0f; color: #e0e0e0; font-family: system-ui; }
  </style>
</head>
<body>
  <div id="app">
    <!-- Your page content -->
  </div>

  <script>
    // All JavaScript inline — no external scripts
    // You can use fetch() to call OpenVoiceUI API endpoints
  </script>
</body>
</html>
```

## Key Rules

1. **Single file** — All CSS and JS must be inline. No external stylesheets or script imports (the iframe is sandboxed).
2. **No CDN dependencies** — Everything must be self-contained. The page runs on a local VPS, not the public internet.
3. **Inline styles only** — Use `<style>` tags, not `<link>` tags.
4. **Dark theme** — Match the OpenVoiceUI aesthetic. Use dark backgrounds (`#0a0a0f` to `#1a1a2e`) with light text.

## Using the API

Canvas pages can call any OpenVoiceUI API endpoint via `fetch()`:

```javascript
// Load canvas manifest
const resp = await fetch('/api/canvas/manifest');
const manifest = await resp.json();

// Save page-specific data
await fetch('/api/canvas/data/my-page-data.json', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ key: 'value' })
});

// Load page-specific data
const data = await fetch('/api/canvas/data/my-page-data.json');

// Upload a file
const formData = new FormData();
formData.append('file', blob, 'filename.png');
await fetch('/api/upload', { method: 'POST', body: formData });
```

## Data Persistence

Canvas pages persist data through the server API — **never use `localStorage`**:

- **Page-specific data:** `GET/POST /api/canvas/data/<filename>` — stores JSON in the canvas data directory
- **File uploads:** `POST /api/upload` — saves files to the uploads directory
- **Manifest metadata:** `PATCH /api/canvas/manifest/page/<page_id>` — update page title, category, etc.

## Auth Token Bridge

For pages that need authenticated API calls (e.g., when `CANVAS_REQUIRE_AUTH=true`), the parent window pushes a Clerk JWT to the iframe via `postMessage`. Use the `authFetch()` helper pattern:

```javascript
let authToken = null;

window.addEventListener('message', (e) => {
  if (e.data?.type === 'auth-token') {
    authToken = e.data.token;
  }
});

async function authFetch(url, opts = {}) {
  const headers = { ...opts.headers };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  return fetch(url, { ...opts, headers });
}
```

## Distributing as a Plugin

To distribute a canvas page as a plugin, see [Plugin System](/extending/plugin-system). The page plugin type copies your HTML files into the canvas pages directory on install.

## Default Pages

OpenVoiceUI ships with several default pages as references:

- `desktop.html` — Windows-style OS desktop
- `ai-image-creator.html` — Image generation UI
- `file-explorer.html` — File browser
- `voice-studio.html` — Voice recording and synthesis
- `website-setup.html` — Website builder wizard
- `style-guide.html` — Design tokens and component reference
- `bulk-image-uploader.html` — Batch image upload
- `interactive-map.html` — Map visualization
- `chatgpt-import.html` — ChatGPT conversation importer
- `dapp-builder.html` — Web3 dApp builder

Study these for patterns and conventions.
