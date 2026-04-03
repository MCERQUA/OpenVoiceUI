# Browser Extension — Session Status (2026-03-19)

## What This Is
Chrome MV3 extension — JamBot AI in a persistent side panel with full browser automation.
User's real browser = residential IP + real sessions = no datacenter IP blocking.

## Current State
**Working:**
- Side panel opens on extension icon click
- Voice STT + streaming TTS via JamBot backend (`/api/conversation?stream=1`)
- Page context: reads current tab URL, title, body text (4000 chars), interactive elements
- Screenshot thumbnail in context pill
- Agent can scroll, click, fill inputs, navigate, open tabs, wait
- Visual action banners on page (colored overlay showing what agent is doing)
- Autonomous task loop with task bar (step counter + Stop button)
- TTS mute button (stops current audio immediately)
- TTS provider/voice selector (Groq, Supertonic, Browser)
- Settings drawer (collapsible ⚙️)

**Just fixed in last session (2026-03-19):**
- Scroll now works on Facebook/SPAs: uses `document.scrollingElement.scrollTop += px` directly
- Fill now works on Facebook comment boxes: uses `document.execCommand('insertText')` for contenteditable divs
- Task loop stays alive: re-prompts agent when it gives text-only response (no command tags)
- Interactive DOM snapshot: agent gets exact selectors `BUTTON "Like" [aria-label="Like"] → [CLICK:[aria-label="Like"]]`
- Agent guided to use `[SCROLL:+1200]` for feeds, not `[SCROLL:bottom]`
- Comment confirmation: agent fills box then asks user to approve before clicking Post

**NOT yet tested after last fix round:**
- The scroll fix + DOM snapshot + task loop continuity — needs real Facebook test

## Files to Know

### Extension files (load unpacked in Chrome)
```
/home/mike/MIKE-AI/tools/jambot-browser-extension/
```

### Server changes (in Docker image — rebuild needed to deploy)
- `/mnt/system/base/OpenVoiceUI/routes/conversation.py` — BROWSER COMPANION MODE context
- `/mnt/system/base/OpenVoiceUI/app.py` — CORS origins includes chrome-extension://

### Rebuild command (run on VPS)
```bash
cd /mnt/system/base && sg docker -c "docker build -t jambot/openvoiceui:latest OpenVoiceUI/"
sg docker -c "docker compose -f /mnt/clients/test-dev/compose/docker-compose.yml --env-file /mnt/clients/test-dev/compose/.env up -d --force-recreate openvoiceui"
sg docker -c "docker network connect jambot-shared openvoiceui-test-dev"
```

## Key Architecture

### Tab tracking (the hard-won fix)
Background service worker maintains `lastWebTabId` updated ONLY by `onActivated`/`onUpdated`.
Side panel queries it via `get_active_tab_id` message. NEVER use `currentWindow` query from side panel.

### Auth
```javascript
const cookie = await chrome.cookies.get({ url: `https://${domain}`, name: '__session' });
headers['Authorization'] = `Bearer ${cookie.value}`;
```

### Stream field names (CRITICAL — server sends these exact names)
```javascript
if (d.type === 'delta' && d.text) // text chunk
if (d.type === 'audio' && d.audio) // base64 mp3 audio chunk
```
NOT `text_delta` / `audio_base64` — those are WRONG.

### Task loop flow
```
startTask(text) → sendMessage(task prompt)
  → agent responds with [SCROLL:+1200]
  → parseAgentCommands() → execute_commands → background.js → executeScript on page
  → 1800ms wait → _continueTask()
  → _fetchPageContextNow() → page text + interactive elements
  → sendMessage("[Step N] Page updated... OUTPUT NEXT COMMAND NOW")
  → repeat until [TASK_COMPLETE:] or Stop button
```

If agent responds with NO command tags:
- Re-prompt: "OUTPUT A COMMAND NOW" (up to 3 times)
- After 3 failures: "Agent stalled" → stop task

### DOM snapshot (interactive elements)
`background.js readAndPushContext()` extracts inputs/buttons/links with reliable selectors.
Sent to server via `buildUIContext()` as `interactive` array.
`conversation.py` formats them: `BUTTON "Like" [aria-label="Like"] → [CLICK:[aria-label="Like"]]`

### Scroll (Facebook-compatible)
```javascript
const sc = document.scrollingElement || document.documentElement;
sc.scrollTop += px; // Direct — works on Facebook/SPAs
window.scrollBy({ top: px }); // Also try standard method
```

### Fill (Facebook comment boxes)
```javascript
if (el.contentEditable === 'true') {
  el.focus();
  // Select all, then insert
  const range = document.createRange(); range.selectNodeContents(el);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  document.execCommand('insertText', false, cmd.value);
} else {
  // Standard input/textarea: use native setter
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  setter.call(el, cmd.value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}
```

## Next Session TODO

### Priority 1 — Test and validate
- Load extension, go to Facebook group, say "scroll through this feed"
- Verify: page actually scrolls (banner + movement)
- Verify: task loop keeps going step after step
- Verify: interactive elements appear in agent context (check browser console for `[JamBot] ui_context:`)

### Priority 2 — Step-by-step confirmation mode
Toggle in settings drawer: "Step mode: ON". When on, before each action the task bar shows:
`"About to: [CLICK:[aria-label='Like']] — Go / Skip / Stop"` with buttons.
User approves each step. Allows full human oversight + override.
This is the next major feature to build.

### Priority 3 — Browser skills
Port the behavioral strategy prompts from https://github.com/MCERQUA/fb-openclaw-skills
into `/mnt/system/base/skills/browser-tasks/`
- Strip all Puppeteer code
- Keep: what to look for, how to score leads, what to write in replies
- These become the agent's strategy knowledge; the extension handles execution

### Priority 4 — Post-scroll feedback
After SCROLL command, check if new `[role="article"]` elements appeared.
Report: "3 new posts loaded" or "End of feed reached" back to agent.
This closes the feedback loop for infinite scroll detection.

## Known Issues

### Facebook scroll container
Facebook's body has `overflow: hidden`. The actual scroll container is the HTML element.
`window.scrollBy` fails silently. Fixed by using `document.scrollingElement.scrollTop += px`.
If this still doesn't work on a specific page, check `document.querySelector('[role="main"]')` as fallback.

### Agent model (GLM-4.7) limitations
GLM-4.7 is a chat model, not a browser automation model. It sometimes:
- Narrates actions instead of executing tags (fixed: re-prompt loop)
- Uses wrong/guessed selectors (fixed: DOM snapshot with exact selectors)
- Gives up after reading feed content (fixed: "OUTPUT NEXT COMMAND" directive language)

### Context growth
Each `_continueTask` step sends 2500 chars of page text + interactive elements.
After ~20 steps, context window starts to fill up. GLM-4.7 may slow down.
Mitigation: `[NOTES:...]` tag system (agent saves running notes to scratchpad) — NOT YET BUILT.
