---
sidebar_position: 1
title: Canvas System
---

# Canvas System

The canvas is the visual surface of OpenVoiceUI. It renders full-screen, interactive HTML pages inside an iframe alongside the voice conversation interface. Any page the AI agent creates -- dashboards, tools, data visualizations, games, forms -- is a canvas page. The canvas is what turns a voice assistant into a visual workspace.

Pages are single-file HTML documents stored on the server filesystem. There is no build step, no framework requirement, and no compilation. The agent writes HTML, the server saves it, and the iframe displays it immediately. This makes the canvas the fastest path from "I need a tool" to a working, interactive application.

## How Pages Are Created

The AI agent creates canvas pages through the conversation flow:

1. The user asks for something visual ("build me a weather dashboard").
2. The agent generates a complete HTML document.
3. The conversation handler extracts the HTML code block and sends a `POST /api/canvas/pages` request.
4. The server saves the file to the `canvas-pages/` directory on disk.
5. The server registers the page in `canvas-manifest.json` with metadata (title, category, timestamps).
6. The page is immediately available at `/pages/<filename>`.
7. The agent includes a `[CANVAS:page-id]` tag in its response to open the page in the iframe.

**Request body for page creation:**

```json
{
  "filename": "weather-dashboard.html",
  "html": "<!DOCTYPE html><html>...</html>",
  "title": "Weather Dashboard"
}
```

**Response:**

```json
{
  "filename": "weather-dashboard.html",
  "page_id": "weather-dashboard",
  "url": "/pages/weather-dashboard.html",
  "title": "Weather Dashboard",
  "category": "weather"
}
```

If no filename is provided, one is generated from the title by slugifying it. The filename is always sanitized to prevent directory traversal, and `.html` is appended if missing.

### Protected Pages

Certain system pages cannot be overwritten via the creation API. Currently protected: `desktop.html` and `file-explorer.html`. Attempts to overwrite them return a `403` response.

## How Pages Open

There are three ways to display a canvas page:

### Agent Tags

The agent includes special tags in its text response that the frontend parses:

- **`[CANVAS:page-id]`** -- Opens a specific page. Example: `[CANVAS:weather-dashboard]` loads `/pages/weather-dashboard.html` in the iframe.
- **`[CANVAS_MENU]`** -- Opens the canvas page menu so the user can browse available pages.

### Voice Commands

Users can say things like "show me the dashboard" or "open the weather page." The agent sees the full page catalog in its context and responds with the appropriate `[CANVAS:page-id]` tag.

### Direct URL

Pages are served at `/pages/<filename>` and can be accessed directly in a browser.

## Manifest System

Every canvas page is tracked in `canvas-manifest.json`, a JSON file that serves as the page registry. The manifest is the source of truth for page metadata, categories, access tracking, and desktop state.

### Manifest Structure

```json
{
  "version": 1,
  "last_updated": "2026-03-29T12:00:00",
  "categories": {
    "dashboards": {
      "name": "Dashboards",
      "icon": "...",
      "color": "#4a9eff",
      "pages": ["status-dashboard", "system-monitor"]
    }
  },
  "pages": {
    "weather-dashboard": {
      "filename": "weather-dashboard.html",
      "display_name": "Weather Dashboard",
      "description": "",
      "category": "weather",
      "tags": [],
      "created": "2026-03-29T12:00:00",
      "modified": "2026-03-29T12:30:00",
      "starred": false,
      "is_public": false,
      "is_locked": false,
      "voice_aliases": ["weather dashboard", "weather", "dashboard", "weather page"],
      "access_count": 5
    }
  },
  "uncategorized": [],
  "recently_viewed": ["weather-dashboard", "status-dashboard"],
  "user_custom_order": null
}
```

### Page Entry Fields

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | The HTML filename on disk (e.g., `weather-dashboard.html`) |
| `display_name` | string | Human-readable title shown in menus and desktop |
| `description` | string | Short description; also used by desktop to store layout state as JSON |
| `category` | string | Auto-assigned or manually set category ID |
| `tags` | array | User-defined tags for filtering |
| `created` | string | ISO 8601 timestamp of page creation |
| `modified` | string | ISO 8601 timestamp of last modification |
| `starred` | boolean | Whether the user has favorited this page |
| `is_public` | boolean | Whether the page can be viewed without authentication |
| `is_locked` | boolean | Admin-only lock that prevents agent from changing visibility |
| `voice_aliases` | array | Auto-generated spoken names for voice navigation (max 5) |
| `access_count` | integer | Number of times the page has been accessed |

### Manifest Sync

The manifest automatically syncs with the filesystem. When pages are added or removed outside the API (e.g., by file copy), the sync process detects changes:

- New `.html` files in `canvas-pages/` are added to the manifest with auto-categorized metadata.
- Files removed from disk are cleaned out of the manifest.
- Sync is throttled to at most once per 60 seconds (`_SYNC_THROTTLE_SECONDS`).
- New pages discovered during sync are automatically injected into the desktop icon layout.

## Category System

Pages are auto-categorized based on keyword matching against the title and first 500 characters of content. The system scores each category by keyword hits and assigns the highest-scoring match.

### Built-in Categories

| Category | Keywords | Color |
|----------|----------|-------|
| `dashboards` | dashboard, monitor, status, overview, control panel, panel | `#4a9eff` |
| `weather` | weather, temperature, forecast, climate, rain, sunny, humidity | `#ffb347` |
| `research` | research, analysis, study, compare, investigate, explore | `#9b59b6` |
| `social` | twitter, x.com, social, post, tweet, follower, engagement | `#1da1f2` |
| `finance` | price, cost, budget, money, crypto, stock, market | `#2ecc71` |
| `tasks` | todo, task, project, plan, roadmap, checklist | `#e74c3c` |
| `reference` | guide, reference, documentation, help, how to, tutorial | `#95a5a6` |
| `entertainment` | music, radio, playlist, dj, audio, song | `#e91e63` |
| `video` | video, remotion, render, animation, movie, clip, recording | `#ff6b35` |
| `uncategorized` | (fallback when no keywords match) | `#6e7681` |

Categories can also be created and updated manually via the API. Each category has a name, icon, and color.

## Version History

Every canvas page has automatic version tracking. A background watcher thread scans for file changes every 15 seconds (`CHECK_INTERVAL_SECONDS`) and saves the previous content before any overwrite.

### How It Works

- Versions are stored in `canvas-pages/.versions/` as timestamped copies: `<page-stem>.<unix-timestamp>.html`.
- The watcher uses SHA-256 content hashing to detect real changes (not just mtime bumps).
- On startup, all existing pages are scanned to establish baseline hashes. No versions are created during this initial scan.
- When a file changes, the **old** content is saved as a version before the new content is recorded.
- A maximum of 20 versions per page are retained (`MAX_VERSIONS_PER_PAGE`). Older versions are pruned automatically.

### Restoring a Version

Restoring a version is a safe operation:

1. The **current** page content is saved as a new version first (so you never lose the current state).
2. The selected version's content replaces the current file.
3. The internal hash tracking is updated to prevent the watcher from creating a duplicate version.

## Default Pages

OpenVoiceUI ships with a set of default pages in the `default-pages/` directory. These are copied into each user's canvas pages on first setup.

| Page | Description |
|------|-------------|
| `desktop.html` | The main desktop surface with draggable app icons, recycle bin, and dynamic page discovery |
| `file-explorer.html` | File browser for uploads, music, and generated content on the server |
| `ai-image-creator.html` | AI image generation interface using configured image providers |
| `bulk-image-uploader.html` | Batch upload tool for images with drag-and-drop support |
| `style-guide.html` | Visual reference for canvas page design (colors, typography, components) |
| `interactive-map.html` | Map-based visualization page |
| `chatgpt-import.html` | Tool for importing ChatGPT conversation exports |
| `dapp-builder.html` | Decentralized application builder interface |
| `voice-studio.html` | Voice cloning and TTS experimentation interface |
| `website-setup.html` | Website project configuration and management tool |

## Context Injection

Every conversation turn, the agent receives a canvas context block in its system prompt. This tells the agent what the user is currently looking at and what pages are available. The context is built by `get_canvas_context()` and includes:

- **Currently viewing:** The title and a content summary (first 800 characters of visible text, with scripts and styles stripped) of the page displayed in the canvas iframe.
- **Starred pages:** Up to 5 user-favorited pages with their voice aliases.
- **Full page catalog:** All pages organized by category, each with its `[CANVAS:page-id]` open command.
- **Recently viewed:** The last 3 pages the user accessed.
- **Voice commands:** Instructions for how the user can navigate pages by speaking.
- **Agent canvas control:** Instructions for how the agent opens pages using `[CANVAS:page-id]` and `[CANVAS_MENU]` tags.

This context enables the agent to reference existing pages, suggest relevant ones, and update the current page based on what the user is viewing.

## Canvas Page Development Tips

### Single-File HTML

Canvas pages are self-contained HTML files. All CSS and JavaScript should be inline. The server strips Tailwind CDN script tags (they break sandboxed iframes), so use inline styles instead of Tailwind classes.

### Automatic Injections

When serving `.html` files, the server injects several helpers automatically:

- **Base styles:** Dark theme defaults (`background: #0a0a0a`, `color: #e2e8f0`), safe-area padding (52px on left/right for edge tab clearance), and CSS custom properties (`--canvas-safe-left`, `--canvas-safe-right`, etc.).
- **Error bridge:** Captures `window.onerror` and `unhandledrejection` events and posts them to the parent frame for debugging.
- **`nav(page)`:** Navigate to another canvas page from within a page. Calls `window.parent.postMessage()`.
- **`speak(text)`:** Send text to the voice agent from within a page.
- **`authFetch(url, opts)`:** A `fetch()` wrapper that automatically attaches the Clerk JWT token. Use this instead of raw `fetch()` for any `/api/*` calls that require authentication.

### Server API Access

Canvas pages can call any `/api/*` endpoint on the server. Common patterns:

- **`/api/canvas/data/<name>.json`** -- Read/write JSON data files from `canvas-pages/_data/`. This is the recommended way to persist page-specific state. `GET` returns `{}` if the file does not exist yet (graceful empty state). `POST` writes JSON data.
- **`/api/upload`** -- Upload files to the server.
- **`/api/canvas/manifest`** -- Read the full page manifest.

### Content Security Policy

Canvas pages run under a Content Security Policy that allows inline scripts and styles, Google Fonts, jsDelivr CDN, and same-origin API calls. Outbound connections to arbitrary domains are blocked to prevent data exfiltration from prompt-injected scripts. The full CSP is:

```
default-src 'none';
script-src 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net blob:;
style-src 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net;
img-src 'self' data: blob: https:;
media-src 'self' blob:;
font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:;
connect-src 'self' https://cdn.jsdelivr.net;
worker-src blob:;
frame-src 'self' blob:
```

### Caching

HTML canvas pages are served with `Cache-Control: no-cache, no-store, must-revalidate` to ensure the user always sees the latest version. Non-HTML assets (images, media) are cached for up to 1 hour client-side and 24 hours at the CDN layer.

## API Reference

### Page Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/canvas/pages` | Create or overwrite a canvas page from HTML content |
| `GET` | `/pages/<filename>` | Serve a canvas page (with auth check, CSP, and helper injection) |
| `GET` | `/api/canvas/mtime/<filename>` | Get last modified time of a page (for change detection polling) |
| `GET` | `/images/<path>` | Serve image files from the canvas images directory |

### Manifest

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/canvas/manifest` | Get the full manifest with all pages, categories, and recently viewed |
| `POST` | `/api/canvas/manifest/sync` | Force a filesystem sync (adds new pages, removes deleted) |
| `GET/PATCH/DELETE` | `/api/canvas/manifest/page/<page_id>` | Get, update metadata, or archive a page |
| `GET/POST/PATCH` | `/api/canvas/manifest/category` | List, create, or update categories |
| `POST` | `/api/canvas/manifest/access/<page_id>` | Track a page access (increments counter, updates recently viewed) |

### Context

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/canvas/context` | Update what page the frontend is currently displaying |
| `GET` | `/api/canvas/context` | Get the current canvas context state |

### Version History

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/canvas/versions/<page_id>` | List all saved versions of a page (newest first) |
| `GET` | `/api/canvas/versions/<page_id>/<timestamp>` | Preview a specific version's HTML content |
| `POST` | `/api/canvas/versions/<page_id>/<timestamp>/restore` | Restore a page to a previous version |

### Display Control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/canvas/update` | Forward a display command to the Canvas SSE server |
| `POST` | `/api/canvas/show` | Quick helper to show a page on canvas |

### Data Bridge

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/canvas/data/<filename>.json` | Read a JSON data file (returns `{}` if not found) |
| `POST` | `/api/canvas/data/<filename>.json` | Write a JSON data file |

### Proxies

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/canvas-proxy` | Proxy the Canvas SSE live display page over HTTPS |
| `GET` | `/canvas-sse/<path>` | Proxy SSE event streams from the Canvas SSE server |
| `GET/POST` | `/canvas-session/<path>` | Proxy Canvas session API requests |
| `ALL` | `/website-dev/<path>` | Proxy requests to the local website dev server |
| `ALL` | `/openclaw-ui/<path>` | Proxy the OpenClaw Control UI behind auth |

### Build Logs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/canvas/build-log/<project>` | Parsed z-code JSONL session activity as human-readable lines |
