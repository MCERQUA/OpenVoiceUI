# Desktop Refactor — Implementation Guide

This document provides code-level details for contributors working on the desktop canvas refactor. Read `PLAN.md` first for the full picture.

**Target file:** `default-pages/desktop.html`

---

## Architecture Overview

```
default-pages/desktop.html (single file, ~3000 lines)
├── CSS (lines 8-773)
│   ├── Base styles (layout, windows, icons, menus)
│   ├── Theme: Windows XP
│   ├── Theme: Windows 95
│   ├── Theme: macOS
│   ├── Theme: Ubuntu
│   └── Theme: Windows 3.1
├── HTML (lines 774-814)
│   ├── #app container
│   ├── #desktop (icon surface)
│   ├── #taskbar
│   ├── #context-menu
│   ├── #start-menu
│   └── #modal-overlay
└── JavaScript (lines 815-2970)
    ├── Data: SVG icons, CATEGORIES, PAGES
    ├── State: 13 global variables
    ├── API: fetchManifest(), saveState(), loadState()
    ├── Theme: setTheme(), getDesktopTop(), updateClock()
    ├── Icons: buildDesktopIcons(), drag/drop, selection
    ├── Windows: createWindow(), maximize, resize, drag
    ├── Taskbar: updateTaskbar(), buildDock()
    ├── Context menus: showContextMenu(), right-click handlers
    ├── File ops: folders, rename, delete, recycle bin
    ├── Start menu: buildStartMenu()
    ├── Explorer: openExplorer(), buildExplorerGrid()
    ├── Canvas nav: openCanvasPage() via postMessage
    ├── Recycle bin: openRecycleBin(), restore, empty
    ├── Modals: showConfirmModal(), showPromptModal()
    ├── Wallpaper: custom wallpaper upload
    └── Init: init(), event listeners, manifest polling
```

---

## Data Flow

```
Server                          Desktop Page
──────                          ────────────
/api/canvas/manifest  ──poll──> fetchManifest() ──> CATEGORIES, PAGES
                                    │
                                    ├──> rebuildDesktopItems()
                                    ├──> buildDesktopIcons()
                                    └──> detect new pages → add to desktopPages
                                            │
User action (drag/rename/delete)            │
    │                                       │
    └──> modify local state ──> saveState() ──debounce──> PATCH /api/canvas/manifest/page/desktop
```

**State lives in three places:**
1. `PATCH /api/canvas/manifest/page/desktop` — master state (positions, folders, recycle bin, theme)
2. `PATCH /api/canvas/manifest/page/desktop-tablet` — tablet-specific icon positions
3. `PATCH /api/canvas/manifest/page/desktop-phone` — phone-specific icon positions

**Page navigation:**
```javascript
window.parent.postMessage({ type: 'canvas-action', action: 'navigate', page: pageId }, '*');
```
Parent frame (OpenVoiceUI app.js) listens for this and loads the target page in the canvas iframe.

---

## Key Functions Reference

| Function | Lines | Purpose | Called By |
|----------|-------|---------|-----------|
| `init()` | 2933 | Startup: loadState, fetchManifest, event listeners | DOMContentLoaded |
| `loadState()` | 916 | Load persisted state from 3 manifest endpoints | init |
| `saveState()` | 884 | Debounced save to server (500ms) | Any state change |
| `fetchManifest()` | 966 | Poll manifest API, rebuild PAGES/CATEGORIES | 30s interval, init |
| `buildDesktopIcons()` | 1100 | Render all desktop icons from desktopPages | fetchManifest, theme change, folder ops |
| `createWindow()` | 1399 | Create draggable/resizable window | Explorer, recycle bin, shortcuts |
| `openCanvasPage()` | 2535 | Navigate to canvas page via postMessage | Icon double-click |
| `buildStartMenu()` | 2106 | Render theme-specific start menu | Start button click |
| `openExplorer()` | 2206 | Open file explorer window | My Computer icon |
| `setTheme()` | 1045 | Switch OS theme, rebuild all UI | Start menu theme picker |

---

## Testing

### Manual Test Checklist

Before submitting any PR, verify:

- [ ] All 5 themes render correctly (XP, macOS, Win95, Win 3.1, Ubuntu)
- [ ] Icons can be dragged and positions persist after reload
- [ ] Right-click context menu works on desktop, icons, and folders
- [ ] New Folder creation works
- [ ] Drag icon into folder works
- [ ] Recycle bin: delete, restore, empty all work
- [ ] File explorer opens and shows categories
- [ ] Double-click icon opens canvas page in parent frame
- [ ] Start menu opens with correct theme styling
- [ ] Theme switch preserves icon positions
- [ ] Window manager: create, drag, resize, minimize, maximize, close
- [ ] Wallpaper upload and persistence works
- [ ] Mobile: icons render correctly at phone breakpoint
- [ ] Mobile: touch drag works for icons
- [ ] Mobile: context menu triggers on long-press
- [ ] 30s manifest poll doesn't cause visible UI stutter
- [ ] Creating a new canvas page (via agent) auto-appears on desktop within 30s

### Performance Test

1. Create 100+ canvas pages
2. Open desktop
3. Verify initial load < 2 seconds
4. Verify 30s poll doesn't cause visible frame drop
5. Open Chrome DevTools > Performance > record 60s
6. Check for memory growth (should be stable, no leak)
7. Check Listeners tab — count should not grow over time

---

## Contribution Guidelines

1. **Branch from `dev`** — never push directly to main
2. **One phase per PR** — don't mix bug fixes with new features
3. **Test on mobile** — use Chrome DevTools device emulation at minimum
4. **No external dependencies** — this is a single HTML file, keep it self-contained
5. **No emojis in UI** — use SVG icons from the existing icon set
6. **Preserve all 5 themes** — every CSS change must work across all themes
7. **Don't break the iframe contract** — page must work inside OpenVoiceUI's canvas iframe. Use postMessage for parent communication, never try to access parent DOM directly.

---

## File Locations

| File | Purpose |
|------|---------|
| `default-pages/desktop.html` | Source of truth — deployed to all new users |
| `default-pages/*.html` | Other default canvas pages |
| `routes/canvas.py` | Manifest API (CRUD, sync, categories) |
| `src/app.js` | Parent frame — listens for postMessage from canvas pages |
| `services/paths.py` | CANVAS_PAGES_DIR and other path constants |
