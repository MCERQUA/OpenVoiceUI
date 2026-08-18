# Voice Agent Tool Bus — system overview

> **Status: SPEC + PHASE 1 BUILD.**
> The layer that lets a voice agent *do things* — regardless of which brain is behind it.
> Companion: `future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md` (the adapter framework).
>
> Read this BEFORE touching `config/tools.yaml`, `services/tool_catalog.py`,
> `services/tool_jobs.py`, `routes/tools.py`, `src/shell/tool-bridge.js`, or the
> marker-parsing blocks in `src/core/VoiceSession.js` / `src/app.js`.

---

## 1. Why this exists

Two agent classes need the same tool surface but speak different protocols:

| | Brain | How it asks for a tool | Gets a result back? |
|---|---|---|---|
| **Text-streaming** | OpenClaw (`clawdbot`), Hermes | `[MARKER]` scraped from prose | ❌ no |
| **Realtime speech-to-speech** | Grok Voice, OpenAI Realtime, Hume EVI | native `function_call` | ✅ yes |

Before this bus, only the first existed — and it was defined implicitly by regexes
duplicated across `src/core/VoiceSession.js` and `src/app.js`. There was no catalog, no
schema, no return value, and no way to hand a realtime model a tool list.

The bus fixes that with **one catalog and three generated projections**, so both classes
drive the same tools off the same definitions.

---

## 2. Architecture

```
  ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
  │ realtime     │        │ text-stream  │        │  admin /     │
  │ agent (Grok) │        │ agent        │        │  scripts     │
  └──────┬───────┘        └──────┬───────┘        └──────┬───────┘
     function_call            [MARKER]                 HTTP
         │                       │                       │
         ▼                       ▼                       ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                    src/shell/tool-bridge.js                  │
  │   normalize → capability gate → route by execution class     │
  └───────────────┬──────────────────────────┬───────────────────┘
                  │ client-side              │ server-side
                  ▼                          ▼
        EventBridge AgentEvents      POST /api/tools/invoke
        (canvas, music, mood,                │
         sound, face)                 ┌──────┴───────┐
                                      │ sync         │ async
                                      ▼              ▼
                              inline result    job store
                                              (runtime/tool-jobs/)
                                                     │
                                              CLI-agent worker
                                              claude -p --model …
                                                     │
                                                     ▼
                                          /ws/agent-events  (push)
                                                     │
                                                     ▼
                                    bridge.emit(FORCE_MESSAGE)
                                                     │
                                                     ▼
                                     adapter injects → agent SPEAKS
                                            the conclusion
```

### The single source of truth

`config/tools.yaml`. Every tool declares: name, description, JSON-schema params,
execution class, capability tag, and (for legacy compat) its marker form.

`services/tool_catalog.py` generates from it:

1. **`tools[]`** — OpenAI-Realtime-shaped function definitions for `session.update`
   (Grok, OpenAI Realtime; Hume's tool format derives from the same objects).
2. **Marker regexes** — the `[CANVAS:x]` family, so text-streaming agents keep working
   **unchanged**. Generated, not hand-duplicated.
3. **Prompt docs** — the "here are your tools" block injected into a system prompt for
   brains without native function calling.

> **Invariant:** never hand-write a marker regex again. Add the tool to the catalog and
> regenerate. A marker that exists in code but not in the catalog is invisible to every
> realtime agent — the same class of bug as `feedback_tools_md_routing`.

### Execution classes

| Class | Meaning | Result |
|---|---|---|
| `client` | Runs in the browser via EventBridge — canvas, music, sound, mood | Inline, immediate |
| `server_sync` | Server call that returns fast (< ~2 s) — list pages, read state | Inline |
| `server_async` | Long-running — image gen, song gen, **`code_task`** | `{job_id, status: "dispatched"}` then a push |

`server_async` is the whole reason the bus exists. A voice agent must **not** block for four
minutes while a CLI agent builds something. It dispatches, says "on it," keeps the
conversation alive, and gets told the outcome when it lands.

---

## 3. The async round trip (the new part)

```
1. User (voice): "add a dark mode toggle to the dashboard page"
2. Grok emits function_call: code_task{ brief: "...", target: "dashboard.html" }
3. tool-bridge → POST /api/tools/invoke → job store writes runtime/tool-jobs/<id>.json
4. Server returns { job_id, status: "dispatched", eta_hint: "a few minutes" }
5. tool-bridge returns THAT to Grok as the function result
   → Grok says "On it — I'll tell you when it's done." Conversation continues.
6. Worker runs:  scripts/with-claude-env.sh claude -p --model sonnet …
7. Worker writes result to the job file, appends to the event log
8. /ws/agent-events pushes { type: "job.done", job_id, summary } to the browser
9. tool-bridge → bridge.emit(AgentActions.FORCE_MESSAGE, { text: "[TOOL RESULT] ..." })
10. Adapter injects it (already implemented in EVERY adapter — see table below)
11. Grok speaks the conclusion and may chain a tool call to show the result
```

**Step 10 is already built.** Every adapter implements both push actions:

| Adapter | CONTEXT_UPDATE (silent) | FORCE_MESSAGE (must act) |
|---|---|---|
| `xai-realtime` | `:194` `_sendContextUpdate` → `session.update` | `:195` `_sendForceMessage` → `conversation.item.create` + `response.create` |
| `hume-evi` | `:118` | `:119` |
| `elevenlabs-classic` | `:168` | `:169` |
| `elevenlabs-hybrid` | `:194` | `:195` |
| `clawdbot` | `:94` `[CONTEXT: …]` | `:87` `sendMessage` |

Working precedent for a non-agent subsystem pushing into a live conversation:
`src/shell/music-bridge.js:44-53` and `src/shell/commercial-bridge.js:37`.

The **only** genuinely new plumbing is the server→browser half: `/ws/agent-events`.

### Why not just poll?

Polling is what `routes/suno.py` does today and it is fine for a modal that a user is
staring at. It is wrong here: the browser tab may be mid-conversation, the agent needs to
be *interrupted* with news, and per root `CLAUDE.md` no job state may live in the browser.
Push keeps the job state entirely server-side, which is also what makes the result survive
a page reload.

---

## 4. Capability gating — required, not optional

A voice agent that can dispatch arbitrary coding work to a CLI agent on this box is a
**real privilege surface**. Two live burns say so:

- Memory `mac-listener-task-kind-auto-executes` — `KIND=task` to a Mac listener auto-ran in
  permissionless headless Claude (paid-API + arbitrary-execution risk).
- SEC item filed 2026-08-01 by `bun-desktop@mesh` — `ovui_mcp.py` binds `0.0.0.0:8091`
  with **no inbound auth** and serves `terminal_execute` to anything that can reach it.

So gating is in the schema from the first commit, not bolted on:

- Every tool carries a `capability` tag.
- Every profile carries an allowlist. A tool not in the profile's allowlist is **not sent**
  to the model at all — it never learns the tool exists. Refusal-by-omission beats
  refusal-by-rejection.
- `server_async` tools that shell out (`code_task`) additionally require an explicit
  `dangerous: true` in the catalog and an explicit profile opt-in.
- The worker runs with a **pinned model** and a scoped working directory. Never
  account-default (root `CLAUDE.md` model discipline: that silently selects opus for bulk
  work), never the whole filesystem.
- Every invocation is logged with `{profile, tool, params, user_id}` — the surface must be
  auditable after the fact, not just gated before it.

---

## 5. Files

| Path | Role |
|---|---|
| `config/tools.yaml` | **Canonical catalog.** The only place a tool is defined. |
| `services/tool_catalog.py` | Loads catalog; generates realtime `tools[]`, marker regexes, prompt docs. |
| `services/tool_jobs.py` | Durable job store — create / read / update / list. Filesystem-backed. |
| `routes/tools.py` | `/api/tools/catalog`, `/api/tools/invoke`, `/api/tools/jobs/<id>`. |
| `server.py` `/ws/agent-events` | Per-session push channel for job completion. |
| `src/shell/tool-bridge.js` | Client router: normalize → gate → EventBridge or HTTP. |
| `src/adapters/xai-realtime.js` | `tools[]` in `session.update`; handles `function_call`. |
| `runtime/tool-jobs/` | Job state on disk (gitignored, bind-mounted, survives recreate). |

Job state deliberately lives under `runtime/` alongside `OVUI_DB_PATH` — see
`services/paths.py`, which documents why the container's writable layer is **not** safe
(wiped on recreate).

---

## 6. Adding a tool

1. Add it to `config/tools.yaml` — name, description, params schema, `execution`,
   `capability`, and `marker` if text-streaming agents should reach it too.
2. If `client`: handle it in `src/shell/tool-bridge.js` by emitting the right `AgentEvents`.
3. If `server_sync` / `server_async`: add the handler in `routes/tools.py`.
4. Add the capability to whichever `profiles/*.json` should have it.

Do **not** add a marker regex to `VoiceSession.js` or `app.js`. They consume generated
output.

---

## 7. Relationship to issue #244

#244 (Provider Pluggability) is the **backend** for server-side tools, not a prerequisite
for this bus. When `generate_image` fires, its handler should resolve a provider through
`providers/registry.py` rather than a hardcoded vendor. Build the tool first, then swap
what's behind it. Detail: `future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md` §6.

---

## 8. Known gaps / not yet done

- `XAI_API_KEY` exists only in `test-dev`'s compose `.env` — **not** in
  `/mnt/system/base/.platform-keys.env`. Grok Voice is one-tenant until that moves.
- Grok voice (`Celeste`) and model (`grok-voice-think-fast-1.0`, hardcoded in the
  `server.py` proxy URL) are not profile-configurable.
- `elevenlabs-classic` / `elevenlabs-hybrid` remain commented out in `adapter-registry.js`.
- Marker parsing in `VoiceSession.js` / `app.js` is still hand-written; migrating it to
  generated output is Phase 2 and must be behaviour-identical (it is load-bearing for every
  OpenClaw tenant).
