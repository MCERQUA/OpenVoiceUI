# Desktop Canvas Refactor Plan

**File:** `default-pages/desktop.html`
**Current size:** ~2,970 lines (single HTML file with inline CSS + JS)
**Purpose:** OS-style desktop that runs as a canvas page inside an iframe

---

## Current State

The desktop is a fully self-contained HTML page with 5 OS themes (XP, macOS, Win95, Win 3.1, Ubuntu), a window manager, file explorer, recycle bin, drag-and-drop icons, start menu, and 30-second auto-sync with the canvas manifest API.

It works but has critical performance issues, dead code, hardcoded values, accessibility gaps, and won't scale past ~200 pages.

---

## Phase 1: Critical Bug Fixes

Priority: **Immediate** — these affect all users right now.

### 1.1 Event Listener Memory Leak
**Severity:** Critical
**Lines:** 1202-1204, called from buildDesktopIcons()

Every 30 seconds (manifest poll), ALL desktop icons are destroyed and recreated via innerHTML. Each new icon gets fresh addEventListener calls. Old DOM nodes are garbage collected but the pattern creates O(n) new listeners every cycle.

**Fix:** Use event delegation on the desktop container instead of per-icon listeners.

```javascript
// BEFORE (leaks):
icons.forEach(item => {
  const el = createIconElement(item);
  el.addEventListener('mousedown', (e) => onIconMouseDown(e, item));
  el.addEventListener('dblclick', () => openItem(item));
  desktop.appendChild(el);
});

// AFTER (delegated):
desktop.addEventListener('mousedown', (e) => {
  const icon = e.target.closest('.desktop-icon');
  if (icon) onIconMouseDown(e, getItemById(icon.dataset.id));
});
desktop.addEventListener('dblclick', (e) => {
  const icon = e.target.closest('.desktop-icon');
  if (icon) openItem(getItemById(icon.dataset.id));
});
```

Event delegation: attach once, never leak.

### 1.2 Race Condition: fetchManifest vs. saveState
**Severity:** Critical
**Lines:** 966 (fetchManifest), 884 (saveState)

saveState is debounced at 500ms. fetchManifest polls every 30s and rebuilds desktop items. If user drags an icon and manifest fires before the debounced save, icon position is lost.

**Fix:** Add a dirty flag. Skip manifest rebuild if state is dirty (unsaved local changes). Save first, then allow refresh.

```javascript
let _stateDirty = false;

function saveState() {
  _stateDirty = true;
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    _doSaveState();
    _stateDirty = false;
  }, 500);
}

async function fetchManifest(forceSync) {
  if (_stateDirty && !forceSync) return; // don't clobber unsaved changes
  // ... rest of fetch
}
```

### 1.3 Window Button Duplication
**Severity:** High
**Lines:** 1424-1445

createWindow() generates titlebar buttons twice (once for Mac layout, once for Windows layout), then deletes the wrong set. Creates unnecessary DOM nodes.

**Fix:** Use a single conditional template:

```javascript
const isMacLike = currentTheme === 'mac' || currentTheme === 'ubuntu';
const btnsHtml = `<div class="titlebar-btns">
  <button class="tb-btn close" onclick="closeWindow(${id})">${btnSymbols.close}</button>
  <button class="tb-btn minimize" onclick="minimizeWindow(${id})">${btnSymbols.minimize}</button>
  <button class="tb-btn maximize" onclick="maximizeWindow(${id})">${btnSymbols.maximize}</button>
</div>`;

const titlebarHtml = isMacLike
  ? `${btnsHtml}<span class="titlebar-title">${title}</span>`
  : `<span class="titlebar-title">${title}</span>${btnsHtml}`;
```

### 1.4 postMessage Security
**Severity:** High
**Line:** 2550

```javascript
// BEFORE:
window.parent.postMessage({...}, '*');  // any origin can receive

// AFTER:
window.parent.postMessage({...}, window.location.origin);
```

### 1.5 Mobile Breakpoint Mismatch
**Severity:** High
**Lines:** JS 871-880, CSS 335-392

JavaScript uses 480px/1024px breakpoints. CSS uses 400px/600px media queries. These must match or icons render at wrong sizes on tablets.

**Fix:** Align both to 480px (phone) / 768px (tablet) / 1024px+ (desktop).

### 1.6 Canvas Navigation Opens Wrong Page (Fuzzy Match Bug)
**Severity:** Critical
**Files:** `default-pages/desktop.html` line 2546, `src/app.js` lines 6510-6512, 6663-6693, 7373-7430
**Observed:** User clicks "Awesome App Library" on desktop, parent frame loads a different page (e.g. "JAMBOT Admin Dashboard").

**Root cause:** The navigation path has a design flaw — programmatic ID-based navigation and voice fuzzy-name navigation share the same code path.

1. Desktop sends `openCanvasPage('ai-app-library')` via postMessage (line 2546)
2. Parent receives it, calls `CanvasControl.showPage('ai-app-library')` (line 6512)
3. `showPage()` calls `menu.findPageByName('ai-app-library')` (line 6679)
4. `findPageByName()` does check exact page ID first (line 7380), BUT:
   - If `menu.manifest` isn't loaded yet, it falls through to string-transform fallback (line 6669)
   - The fuzzy matching (lines 7388-7425) scores partial substring matches on voice_aliases and display_names. A page with an alias that's a substring of the query (or vice versa) can score 80+ and win over the intended target
   - Word-level matching (line 7417-7425) scores individual words — "app" alone could match multiple pages

5. The fuzzy scorer has no minimum threshold. ANY partial match wins, even score 53 ("app" matching a 3-letter word).

**Fix:** Split into two navigation modes:

```javascript
// In CanvasControl:
showPageById(pageId) {
  // Exact match only — used by desktop postMessage, agent [CANVAS:id] tags
  const menu = window.CanvasMenu;
  if (menu?.manifest?.pages?.[pageId]) {
    menu.showPage(menu.manifest.pages[pageId].filename);
    return;
  }
  // Direct fallback — pageId IS the filename stem
  const filename = pageId + '.html';
  if (this.iframe) {
    this.iframe.src = `/pages/${filename}?t=${Date.now()}`;
    localStorage.setItem('canvas_last_page', filename);
    this.show();
  }
},

showPageByName(query) {
  // Fuzzy match — used by voice input only
  const match = window.CanvasMenu?.findPageByName(query);
  if (match && match.score >= 70) {
    window.CanvasMenu.showPage(match.page.filename);
  }
}
```

Then update the postMessage handler (line 6510-6512):
```javascript
case 'navigate':
  // Desktop/programmatic sends exact page IDs — never fuzzy match
  if (page) CanvasControl.showPageById(page);
  break;
```

And voice/agent navigation uses `showPageByName()` with a minimum score threshold.

**Also needed:** Add a minimum score threshold to `findPageByName()` — currently ANY match wins, no matter how weak. Suggested minimum: 70 (rejects single-word substring matches).

---

## Phase 2: Hardcoded Values & Config

Priority: **High** — blocks multi-user deployment.

### 2.1 Extract Config Object

Replace all magic numbers and hardcoded values with a config object loaded from the manifest or a dedicated endpoint.

```javascript
const DESKTOP_CONFIG = {
  // Timing
  MANIFEST_POLL_MS: 30000,
  SAVE_DEBOUNCE_MS: 500,
  TOUCH_LONG_PRESS_MS: 300,
  DRAG_THRESHOLD_PX: 4,

  // Window constraints
  WINDOW_MIN_WIDTH: 200,
  WINDOW_MIN_HEIGHT: 120,
  WINDOW_DEFAULT_WIDTH: 700,
  WINDOW_DEFAULT_HEIGHT: 500,

  // Layout
  ICON_GRID_SIZE: 90,
  ICON_GRID_SIZE_TABLET: 80,
  ICON_GRID_SIZE_PHONE: 72,

  // User (should come from server)
  USERNAME: 'User',
  PINNED_PAGES: [],
  DOCK_ITEMS: [],
  DEFAULT_THEME: 'xp',
};
```

### 2.2 Remove Hardcoded Username
**Line:** 2165

```javascript
// BEFORE:
html += `<div class="sm-user">Mike</div>`;

// AFTER:
const username = DESKTOP_CONFIG.USERNAME;
html += `<div class="sm-user">${username}</div>`;
```

Load username from `/api/config` or parent frame's `window.AGENT_CONFIG.clientName`.

### 2.3 Dynamic Pinned Pages
**Line:** 2171

```javascript
// BEFORE:
const pinned = ['bored-arcade','civ-sim','music-library','wolf3d-engine','office-hub'];

// AFTER:
const pinned = DESKTOP_CONFIG.PINNED_PAGES.filter(id => PAGES[id]);
```

Pinned pages should come from desktop state (user-configurable via right-click > Pin to Start).

### 2.4 Dynamic Dock Items
**Lines:** 2631-2639

System dock items are hardcoded. Should be configurable and user-extendable (drag to dock to pin).

---

## Phase 3: Performance & Scalability

Priority: **Medium** — needed when page count exceeds ~100.

### 3.1 DOM Element Reuse

Instead of destroying and recreating all icons every cycle:

```javascript
function updateDesktopIcons() {
  const existing = new Map();
  desktop.querySelectorAll('.desktop-icon').forEach(el => {
    existing.set(el.dataset.id, el);
  });

  const needed = new Set(desktopPages);

  // Remove icons no longer needed
  existing.forEach((el, id) => {
    if (!needed.has(id)) el.remove();
  });

  // Add or update icons
  desktopPages.forEach(id => {
    if (existing.has(id)) {
      // Update existing (name, icon, position)
      updateIconElement(existing.get(id), PAGES[id]);
    } else {
      // Create new
      desktop.appendChild(createIconElement(id, PAGES[id]));
    }
  });
}
```

### 3.2 Manifest ETag Caching

Add conditional fetching so 30s polls don't re-parse unchanged data:

```javascript
let _manifestEtag = null;

async function fetchManifest(forceSync) {
  const headers = {};
  if (_manifestEtag && !forceSync) headers['If-None-Match'] = _manifestEtag;

  const resp = await fetch(url, { headers });
  if (resp.status === 304) return; // unchanged

  _manifestEtag = resp.headers.get('ETag');
  const data = await resp.json();
  // ... process
}
```

Requires server-side ETag support on `/api/canvas/manifest`.

### 3.3 Virtual Scrolling for Explorer

When a category has 100+ pages, the explorer grid should only render visible items:

```javascript
// Render only items in viewport
function renderVisibleItems(container, items, itemHeight) {
  const scrollTop = container.scrollTop;
  const viewHeight = container.clientHeight;
  const startIdx = Math.floor(scrollTop / itemHeight);
  const endIdx = Math.ceil((scrollTop + viewHeight) / itemHeight);
  // Render only items[startIdx..endIdx]
}
```

### 3.4 Lazy Icon Loading

For 500+ pages, render only icons in the visible viewport area of the desktop. Use IntersectionObserver or viewport math.

---

## Phase 4: Missing Features

Priority: **Medium** — quality-of-life improvements.

### 4.1 Search

Add search to taskbar and start menu. Filter pages by name as user types.

```
[Taskbar] [ Search pages...  ] [Start]
```

Implementation: input field in taskbar, filters PAGES object, shows dropdown results. Click opens page.

### 4.2 Sort Icons

Right-click desktop > Sort by:
- Name (A-Z)
- Date Created
- Category
- Size (file size from manifest)
- Auto-arrange (grid snap)

### 4.3 Window Snap

Drag window to left/right edge of screen for 50% width split view. Drag to top for maximize.

```javascript
function onWindowDrag(e, winId) {
  const x = e.clientX;
  const w = window.innerWidth;
  if (x < 20) snapWindow(winId, 'left');       // left half
  else if (x > w - 20) snapWindow(winId, 'right'); // right half
  else if (e.clientY < 5) maximizeWindow(winId);    // top = maximize
}
```

### 4.4 Notification Area / System Tray

Show in taskbar:
- Connection status (green dot = connected to gateway)
- Active agent name
- Unread alert count
- Clock (already exists)

Pull data from `/health/live` and `/api/profiles/active`.

### 4.5 Recent Pages in Start Menu

Track last 10 opened pages. Show in start menu under "Recent".

```javascript
let recentPages = []; // persisted in desktop state

function openCanvasPage(pageId) {
  recentPages = [pageId, ...recentPages.filter(id => id !== pageId)].slice(0, 10);
  saveState();
  // ... navigate
}
```

### 4.6 Pin to Dock / Pin to Start

Right-click on icon > "Pin to Dock" or "Pin to Start Menu". Persisted in desktop state.

### 4.7 Desktop Widgets

Optional floating widgets on the desktop:
- Clock/Calendar widget
- System stats (CPU/RAM from `/api/server-stats`)
- Quick notes
- Active agent card

### 4.8 Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Arrow keys | Navigate icons |
| Enter | Open selected icon |
| Delete | Move to recycle bin |
| Ctrl+A | Select all icons |
| Alt+F4 | Close active window |
| Ctrl+W | Close active window |
| Win/Cmd | Open start menu |
| F2 | Rename selected |
| F5 | Refresh desktop |
| Ctrl+Z | Undo last action |

---

## Phase 5: Code Quality

Priority: **Low** — cleanup for maintainability.

### 5.1 State Encapsulation

Wrap 13 global variables in a state object:

```javascript
const state = {
  categories: {},
  pages: {},
  theme: 'xp',
  windows: new Map(),
  nextWinId: 1,
  nextZ: 100,
  recycleBin: [],
  desktopPages: [],
  knownPages: [],
  customFolders: [],
  selectedIcons: new Set(),
  iconPositions: {},
  savedPageCategories: {},
};
```

### 5.2 Fix Dead Code

- **Line 2223:** `onclick="history.length"` — does nothing. Remove or implement proper back navigation within explorer.
- **Line 2224:** `onclick=""` — empty forward button. Remove or implement.

### 5.3 ARIA Accessibility

Add to all interactive elements:
- `role="button"` + `aria-label` on icon buttons
- `role="dialog"` + `aria-modal="true"` on modals
- `role="menuitem"` on context menu items
- `role="application"` on desktop container
- Focus management: auto-focus on window create, return focus on close

### 5.4 DRY Explorer Grid

Three near-identical loops build explorer/folder/recycle grids. Extract to shared function:

```javascript
function buildItemGrid(items, options = {}) {
  return items.map(([id, page]) => `
    <div class="explorer-item"
         ondblclick="${options.onDblClick || `openCanvasPage('${id}')`}"
         oncontextmenu="event.preventDefault();${options.onContext || `showExplorerItemMenu(event,'${id}')`}">
      <div class="explorer-icon">${getIcon(getPageIconType(page.cat, id, page.name))}</div>
      <div class="explorer-label">${page.name}</div>
    </div>
  `).join('');
}
```

---

## Iframe Constraints (cannot fix)

These are inherent limitations of running inside an iframe:

- No native OS file drag-and-drop into the desktop
- Browser back/forward buttons don't apply inside the iframe
- Limited clipboard access (can't paste files from OS)
- No native notifications (would need parent frame postMessage bridge)
- Audio from opened sub-pages may conflict with parent frame TTS
- Print doesn't work from nested iframes
- Window.open() opens in new tab, not inside the desktop

---

## Suggested Issue Breakdown

| Issue | Phase | Labels | Estimate | GitHub |
|-------|-------|--------|----------|--------|
| fix: desktop icon event listener memory leak | 1 | bug, performance | Small | #231 |
| fix: fetchManifest/saveState race condition | 1 | bug | Small | #232 |
| fix: window button duplication in createWindow | 1 | bug | Small | #233 |
| fix: postMessage wildcard origin | 1 | bug, security | Tiny | — |
| fix: mobile breakpoint mismatch JS vs CSS | 1 | bug, mobile | Tiny | — |
| fix: canvas navigation fuzzy match opens wrong page | 1 | bug | Medium | #238 |
| feat: extract desktop config (remove hardcoded values) | 2 | enhancement | Medium | #234 |
| feat: dynamic username, pinned pages, dock items | 2 | enhancement | Small | #234 |
| perf: DOM element reuse instead of innerHTML rebuild | 3 | performance | Medium | #235 |
| perf: manifest ETag caching | 3 | performance | Medium | #235 |
| feat: search in taskbar and start menu | 4 | enhancement | Medium | #236 |
| feat: sort icons (name, date, category, auto-arrange) | 4 | enhancement | Small | #236 |
| feat: window snap (drag to edge) | 4 | enhancement | Small | #236 |
| feat: keyboard shortcuts (arrows, Alt+F4, Ctrl+A) | 4 | enhancement | Medium | #236 |
| feat: pin to dock / pin to start menu | 4 | enhancement | Small | #236 |
| feat: recent pages in start menu | 4 | enhancement | Small | #236 |
| feat: notification area / system tray | 4 | enhancement | Medium | #236 |
| refactor: encapsulate global state | 5 | enhancement | Medium | #237 |
| refactor: DRY explorer grid builders | 5 | enhancement | Small | #237 |
| a11y: add ARIA labels and roles | 5 | enhancement | Medium | #237 |
