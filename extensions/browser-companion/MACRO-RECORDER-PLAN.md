# JamBot Extension — Macro Recorder Plan

## What's Already Built (Phase 1 ✅)

**Passive action recording** — `content.js` silently watches every page:
- Clicks: `{type, tag, text, selector, timestamp}`
- Form changes: `{type:'input', selector, timestamp}` (no values for privacy)
- Navigation: URL changes, link clicks, popstate
- Rolling 20-action log, sent in `ui_context.action_history` with every message

This gives the agent context about what you've been doing, but can't replay it.

---

## Phase 2 — Manual Record Mode

**Goal:** User presses REC, does a task manually, stops recording. The sequence is saved
as a named macro the agent can replay exactly.

### UI Changes

**Sidebar:** Add a `⏺ Record` button next to the mic area (idle state only).
When active: button turns red `⏹ Stop`, sidebar shows a live counter "Recording — 4 actions".

**Content script:** When recording mode is ON, switches from passive → high-fidelity capture:

| Field | Description |
|-------|-------------|
| `selector` | CSS selector (best-effort unique) |
| `xpath` | Full XPath as fallback |
| `text` | Visible label/text of the element |
| `x`, `y` | Click coordinates (% of viewport, portable across screen sizes) |
| `value` | Form input value (opt-in — recording is intentional, not passive) |
| `tag` | Element tag |
| `timestamp` | ms since recording start |
| `type` | `click` / `fill` / `select` / `navigate` / `scroll` / `key` |

### Stop → Name the Macro

When user hits Stop, sidebar shows:
- List of recorded steps (expandable)
- Text field: "Name this macro" (e.g. "login to LinkedIn", "post daily update")
- Save / Discard buttons

Saved to `chrome.storage.local` as:
```json
{
  "macros": {
    "login to LinkedIn": {
      "name": "login to LinkedIn",
      "url": "https://linkedin.com/login",
      "steps": [
        { "type": "fill", "selector": "#username", "value": "user@email.com", "timestamp": 0 },
        { "type": "fill", "selector": "#password", "value": "••••••••", "timestamp": 200 },
        { "type": "click", "selector": ".btn__primary--large", "text": "Sign in", "timestamp": 400 }
      ],
      "created": 1742000000000
    }
  }
}
```

Note: passwords stored locally in `chrome.storage.local` (never sent to server, never in `ui_context`).

---

## Phase 3 — Agent Integration

### Macro List in ui_context

When the user sends a message, `buildUIContext()` includes macro names (not full steps):

```json
"macros": ["login to LinkedIn", "post daily update", "scrape job listings"]
```

The agent knows what macros exist and can offer to run them.

### New Agent Command: `[RUN_MACRO:name]`

Agent response includes tag → extension executes the saved step sequence:

```
Sure, I'll log you in now. [RUN_MACRO:login to LinkedIn]
```

`content.js` runs each step with a short delay between them:
1. Navigate to recorded URL (optional — only if current URL doesn't match)
2. Step through: `fill`, `click`, `select`, `scroll` in order
3. Respect recorded timing (scaled — don't wait exact ms, but preserve relative pacing)

On completion, `content.js` posts back `{type: 'macro_complete', name, steps_run, errors}` to the panel.
Panel adds a system message: `✅ Macro "login to LinkedIn" completed (3 steps)`.

### Fallback Strategy

If a selector fails (element not found):
1. Try XPath fallback
2. Try text-match fallback (`document.querySelector` by visible text)
3. If still not found → stop, report to user: `⚠️ Macro stopped at step 2 — element not found`

---

## Phase 4 — Power Features

These are nice-to-have, build when Phase 3 is stable.

### Visual Step Overlay

During recording: inject a floating HUD showing the live step list on the page itself.
During playback: highlight each element as the step executes (cyan outline, label "Step 2/5").

### Macro Editor

In sidebar: expandable macro list with:
- Rename, delete macros
- View/edit individual steps (change value, reorder, delete a step)
- Duplicate macro
- Export as JSON (for sharing between machines)

### Conditional Steps

For fragile macros (login → might already be logged in):
- `{type: 'if_exists', selector: '.feed', then: 'skip_to:3'}` — if already on feed, skip login steps
- `{type: 'wait_for', selector: '.spinner', timeout: 5000}` — wait for element to appear before next step

### Macro Scheduling

"Run this macro every day at 9am" → `chrome.alarms` to trigger playback on a schedule.
Requires the extension side panel to be open OR a background-triggered tab open.

---

## Implementation Order

1. `content.js` — add `recordingMode` flag, high-fidelity event capture when active
2. `sidepanel.html/css` — REC button, live counter, stop + name dialog
3. `sidepanel.js` — recording state, save/load macros from `chrome.storage.local`, send names in ui_context
4. `content.js` — `execute_macro` message handler, step runner with fallback
5. `background.js` — relay `macro_complete` from content → panel
6. `skills/browser-companion/SKILL.md` — teach agent about `[RUN_MACRO:name]` tag and when to offer it

## Files Changed

| File | Change |
|------|--------|
| `content.js` | Recording mode flag, high-fidelity capture, macro step runner |
| `sidepanel.html` | REC button, step preview, name dialog |
| `sidepanel.css` | REC button states, dialog, step list styles |
| `sidepanel.js` | Recording state, chrome.storage macros, ui_context injection |
| `background.js` | Relay `start_recording`/`stop_recording`/`macro_complete` messages |
| `skills/browser-companion/SKILL.md` | `[RUN_MACRO:name]` docs |

## Privacy Notes

- Recorded values (form fields) stay in `chrome.storage.local` — never sent to the server
- Macro **names** are sent in `ui_context` so the agent knows what's available
- Macro **steps** are only sent when the user explicitly asks the agent to run or inspect one
- Passwords in macros are stored as-recorded (browser-local only) with a ⚠️ warning on save
