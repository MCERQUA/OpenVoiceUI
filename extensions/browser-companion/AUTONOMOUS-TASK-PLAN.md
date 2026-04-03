# JamBot Browser Companion — Autonomous Task Architecture

## The Vision

The extension isn't a "what are you looking at" tool. It's a **browser agent terminal**.

The agent can:
- Scroll through feeds and find leads
- Click, type, submit forms
- Run tasks autonomously for minutes or hours
- Report findings back to the workspace (canvas pages, reports, task logs)
- Do this on any site with the user's real session and residential IP

This is the same thing as `fb-openclaw-skills` on GitHub — but instead of running
headless Puppeteer on the VPS (datacenter IP, blocked by Facebook/LinkedIn), it runs
in the user's real Chrome (residential IP, logged-in session, undetectable).

The fb-openclaw-skills repo becomes the **skill library** — the agent behaviors
(find leads, reply to posts, scrape groups) are the same. Only the execution backend
changes: instead of Puppeteer → JamBot extension.

---

## What's Built (as of now)

### Immediate fixes (just deployed)
- `[SCROLL:+800]` / `[SCROLL:-400]` — incremental scroll (essential for feed browsing)
- `[TASK_COMPLETE:summary]` — agent signals end of task
- **Task bar** — shows active task name, step counter, Stop button
- **Page context loop** — after SCROLL/CLICK, page is re-read and fed back to agent
- **Agent context injection** — explicit instruction in conversation.py listing all commands
  with Facebook/LinkedIn CSS selectors

### Core command set (already working)
| Command | What it does |
|---------|-------------|
| `[SCROLL:+1200]` | Scroll down to load more feed posts |
| `[SCROLL:bottom]` | Jump to bottom |
| `[CLICK:selector]` | Click button, link, tab |
| `[FILL:selector:value]` | Type into search, comment box, message |
| `[HIGHLIGHT:selector]` | Visual outline (debugging/showing user) |
| `[READ_PAGE]` | Request full 15k char page text |
| `[TASK_COMPLETE:msg]` | End task, report back |
| `[CANVAS:page-id]` | Open/create canvas page in workspace |

---

## What's Still Needed

### Phase 1 — Task Reliability
**Problem:** The current loop sends the full page text in every step message, which
bloats the conversation context fast and makes GLM-4.7 slow.

**Fix needed:**
- Agent should send a `[NOTES:...]` tag to store running notes (leads found, actions taken)
  so it doesn't need to restate everything each step
- Or: side panel maintains a task scratchpad on the server side
- Incremental context: only send what changed since last step (new posts, scroll position)

### Phase 2 — Smart Scroll / Infinite Loading
**Problem:** Facebook/LinkedIn load content dynamically. Scrolling to bottom → wait for
new posts → scroll again. Currently no feedback on whether new content loaded.

**Fix needed:**
- After `[SCROLL:+1200]`, wait 1.5s, check if new `[role="article"]` elements appeared
- Report count: `[5 new posts loaded — total 23 posts visible]`
- Auto-detect "end of feed" (no new content after 3 scrolls) → `[TASK_COMPLETE]`

### Phase 3 — Action Queue / Replay
**Problem:** Agent should be able to queue multiple actions and execute them without
waiting for a server round-trip between each one.

**Fix:**
- Parse ALL commands in one response, execute in sequence with short delays
- Report batch result back: `[Executed: SCROLL +1200, SCROLL +1200, CLICK .see-more — 3 new posts]`

### Phase 4 — Workspace Integration
This is the key bridge. The agent should be able to:

1. **Create a canvas page** with task results mid-task
   ```
   [CANVAS:fb-leads-2025-03-19] then immediately build the page content
   ```

2. **Update canvas page** as task progresses (not just at the end)
   - Live canvas: rows of leads appear as they're found

3. **Call workspace APIs** from inside the browser task:
   - `POST /api/canvas/save-data` with structured lead data
   - Social Dashboard API for CRM-style lead tracking

4. **Cross-session memory**: what the agent found in the browser is stored in
   openclaw workspace memory so next conversation can reference it

### Phase 5 — Task Scheduler
- "Do this every day at 9am" → `chrome.alarms` triggers task on schedule
- Panel must be open (or: background opens it automatically)
- Task log → canvas page updated each run

---

## fb-openclaw-skills: Still Needed?

The skills in that repo are behavioral scripts for the agent:
- `facebook-group-leads.md` — how to find leads in FB groups
- `linkedin-connect.md` — connection request workflow
- `post-engagement.md` — how to reply to posts as a persona

**Short answer: YES, still needed** — but as JamBot extension skills, not Puppeteer scripts.

The workflow becomes:
1. Copy the skill `.md` files into `/mnt/system/base/skills/browser-tasks/`
2. They teach the agent the *strategy* (what to look for, how to write replies)
3. The extension provides the *execution* (actually clicking and scrolling)
4. The agent already knows HOW to use `[SCROLL:]`, `[CLICK:]`, `[FILL:]`

The Puppeteer code in those skills is **not needed** — throw it away.
Keep the behavioral prompts and lead-scoring logic.

---

## Full Architecture Diagram

```
User: "Find leads in the contractor Facebook group for 30 minutes"
         ↓
    JamBot Extension Panel (sidepanel.js)
         ↓ startTask("Find leads...", 30)
    sendMessage() → POST /api/conversation  ─────────────────────────────┐
                                                                          │
    OpenClaw (openclaw-<user> container)                                  │
         ↓ + skills/browser-tasks/facebook-group-leads.md                 │
    GLM-4.7 (via Z.AI)                                                    │
         ↓ Response: "I'll scroll through the feed. [SCROLL:+1200]"       │
                                                                          │
    Extension executes [SCROLL:+1200]                                     │
         ↓ 1.8s wait                                                      │
    _fetchPageContextNow() → executeScript → new post content             │
         ↓                                                                │
    _continueTask() → sendMessage("Step 2. Page updated: [new posts]...")─┘
         ↓ (loop continues...)

    Agent finds leads → [CANVAS:fb-leads-today]
         ↓
    Canvas page created with lead list
         ↓
    Agent continues scrolling...
         ↓ after 30 min or [TASK_COMPLETE:]
    Task bar shows: "Task complete — 12 leads found, canvas created"
```

---

## Immediate Next Steps

1. **Deploy updated files** — background.js, sidepanel.js, sidepanel.css, content.js
2. **Rebuild openvoiceui image** — conversation.py was updated with new agent context
3. **Copy fb-openclaw-skills prompts** into `/mnt/system/base/skills/browser-tasks/`
   (strip Puppeteer code, keep behavioral prompts)
4. **Test scroll loop** — open Facebook group, ask agent to scroll and list posts
5. **Test task mode** — "Find 5 leads in this group" → agent runs autonomously
