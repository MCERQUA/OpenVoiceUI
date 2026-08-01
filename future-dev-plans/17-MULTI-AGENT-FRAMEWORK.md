# 17 — Multi-Agent Framework

> **Status: BUILT (core) / EXTENDING (tools).**
> This document was cited by `src/adapters/_template.js`, `src/adapters/xai-realtime.js`,
> `src/shell/adapter-registry.js` and `src/shell/orchestrator.js` for months but was never
> committed. This file reconstructs the contract **from the shipped code** so the citations
> resolve, and states where the framework is going next.
>
> Companion doc — the tool layer: `docs/jambot/voice-agent-tool-bus.md`

---

## 1. The problem this solves

OpenVoiceUI started as a front-end for exactly one brain: the OpenClaw gateway, spoken to
over `/ws/clawdbot`. Everything — mic handling, TTS playback, canvas commands, mood, music —
was fused to that one transport.

That is wrong for two reasons:

1. **Realtime speech-to-speech APIs exist** (xAI Grok, OpenAI Realtime, Hume EVI,
   ElevenLabs). They are all-in-one STT+LLM+TTS over a single WebSocket with sub-second
   latency. They do not want our STT, our TTS, or our turn-taking. They want the mic bytes
   and nothing else.
2. **Not every agent should be an OpenClaw agent.** A voice agent that answers questions and
   opens canvas pages does not need a full coding harness behind it. Coding work should be
   *dispatched* to a CLI agent, not carried by the conversation.

The framework's job: let the UI shell stay identical while the **brain behind it is swappable
at profile level**.

---

## 2. Architecture

```
                     ┌──────────────────────────────────┐
                     │            UI SHELL              │
                     │  face · transcript · canvas ·    │
                     │  music · soundboard · mood       │
                     └───────────────┬──────────────────┘
                                     │
                            ┌────────┴─────────┐
                            │   EventBridge    │   src/core/EventBridge.js
                            │  (the ONLY seam) │
                            └────────┬─────────┘
                    AgentEvents ↑    │    ↓ AgentActions
                                     │
                     ┌───────────────┴──────────────────┐
                     │        ADAPTER (exactly one)     │
                     │  init / start / stop / destroy   │
                     └───────────────┬──────────────────┘
                                     │  owns its own audio,
                                     │  WebSocket, SDK, protocol
        ┌────────────┬───────────────┼───────────────┬──────────────┐
        │            │               │               │              │
   clawdbot     hume-evi      xai-realtime    elevenlabs-*    (your next one)
   OpenClaw     Hume EVI      Grok Realtime   ElevenLabs
```

**The single rule that makes this work:** an adapter talks to the outside world *only*
through the bridge. It never imports another adapter, never reaches into the UI, never
touches the DOM. The UI never knows which adapter is loaded — it only knows the
capability list.

### Files

| File | Role |
|---|---|
| `src/core/EventBridge.js` | The seam. `AgentEvents` (agent→UI) + `AgentActions` (UI→agent). |
| `src/shell/adapter-registry.js` | `adapterId → module path`, dynamic import with caching, fallback to default. |
| `src/shell/orchestrator.js` | Loads the profile's adapter, wires bridge, runs lifecycle. |
| `src/adapters/_template.js` | Copy-me starting point. |
| `src/adapters/*.js` | The adapters themselves. |
| `profiles/*.json` | Profile declares `"adapter": "<id>"` + `adapter_config`. |

### Adapter lifecycle

```
init(bridge, config)   load SDK, subscribe to AgentActions.  NO mic, NO connection.
start()                user gesture. AudioContext, mic, connect. emit CONNECTED.
stop()                 tear down audio + socket. Re-startable.
destroy()              stop() + unsubscribe everything + close AudioContext.
```

`init` must not start the mic — browsers require a user gesture for `AudioContext`, and
profile switching calls `init` on adapters that may never be started.

### Capabilities

An adapter declares what it can do; the UI shows/hides features accordingly:

```
'canvas' · 'dj_soundboard' · 'caller_effects' · 'music_sync' · 'multi_voice'
'emotion_detection' · 'commercials' · 'vps_control' · 'wake_word' · 'tools'
```

`'tools'` is new — see §5.

---

## 3. Adding an adapter

1. `cp src/adapters/_template.js src/adapters/my-adapter.js`
2. Add `'my-adapter': '../adapters/my-adapter.js'` to `ADAPTER_PATHS` in
   `src/shell/adapter-registry.js`
3. `profiles/my-profile.json` with `"adapter": "my-adapter"`

Done. Profiles auto-discover via `profiles/manager.py` globbing `profiles/*.json`.

**If the adapter needs a provider API key**, proxy it server-side. Never ship a key to the
browser. The pattern is `/ws/xai-realtime` in `server.py` — a transparent bidirectional
relay that injects `Authorization: Bearer` on the upstream leg. The browser gets a
same-origin WebSocket and never sees the credential. A `/api/<provider>/config` route
returning `{"available": bool}` lets the adapter surface a useful error instead of failing
silently on connect.

---

## 4. Shipped adapters

| ID | Backend | Registered | Notes |
|---|---|---|---|
| `clawdbot` | OpenClaw gateway `/ws/clawdbot` | ✅ default | Text-streaming. Marker protocol (§5). |
| `hume-evi` | Hume EVI | ✅ | Emotion scores per utterance. |
| `xai-realtime` | Grok Voice via `/ws/xai-realtime` | ✅ | PCM16 @ 24 kHz, server VAD. Tools pending (§5). |
| `elevenlabs-classic` | ElevenLabs Conversational | ⛔ commented out | Built, not enabled. |
| `elevenlabs-hybrid` | ElevenLabs + own LLM | ⛔ commented out | Built, not enabled. |

---

## 5. Where the framework is incomplete — the tool layer

The adapter seam is solved. **The tool seam is not.**

Today an agent acts on the world by emitting **text markers** that the client regex-scrapes
out of the prose stream and then strips before display:

```
[CANVAS:dashboard]  [CANVAS_ACTION:{...}]  [CANVAS_MENU]  [CANVAS_STYLE:...]
[CANVAS_SCREENSHOT] [CANVAS_URL:...]  [MUSIC_PLAY] [MUSIC_STOP] [MUSIC_NEXT]
[SOUND:air_horn]  [MOOD:happy]  [IMAGE:...]  [SOUNDCLOUD] [SOUNDCLOUD_PAGE]
```

Parsed in `src/core/VoiceSession.js` (~L420-510) **and** duplicated in `src/app.js`
(~L30-90, L4257+). There is no catalog — the tool surface is defined implicitly by
scattered regexes.

This works for text-streaming brains. It **structurally cannot** work for a realtime
speech-to-speech brain:

1. The model emits **audio**. The text transcript is a byproduct. Grok will say
   *"bracket canvas colon dashboard"* out loud.
2. Markers arrive *after* the audio is already playing → wrong ordering.
3. Markers are fire-and-forget. There is **no return value**, so the agent cannot reason
   about a result — which is exactly what "dispatch a coding job and tell me how it went"
   requires.
4. Realtime APIs have native `tools` / `function_call` with a result round-trip. That is
   the correct channel and we are not using it.

### The fix

One canonical catalog, three generated projections:

```
                    config/tools.yaml            ← single source of truth
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
  realtime tools[]    [MARKER] regexes   system-prompt docs
  (Grok/OpenAI/Hume)  (clawdbot — kept   (both)
   function calling    working, now
                       GENERATED)
```

The marker protocol is **not removed** — it is generated from the catalog instead of
hand-duplicated across two files, so OpenClaw agents keep working unchanged while realtime
agents get real function calling off the same definitions.

Plus two pieces of new plumbing:

- **Async tool contract.** Sync tools (`open_canvas`, `play_song`) return inline. Async
  tools (`code_task`) return `{job_id, status: "dispatched"}` immediately so the agent says
  "on it" and keeps talking — the whole point of sub-second voice.
- **Server→agent push.** `AgentActions.CONTEXT_UPDATE` / `FORCE_MESSAGE` already exist and
  are already implemented in *every* adapter. What's missing is the server→browser half.

Full spec: **`docs/jambot/voice-agent-tool-bus.md`**.

---

## 6. Relationship to issue #244 (Provider Pluggability)

Different axis, commonly confused:

- **#244 is service pluggability** — which vendor does STT / vision / image-gen / music.
- **This doc is agent pluggability** — which brain drives the conversation.

A realtime voice API is all-in-one STT+LLM+TTS and **bypasses the STT/TTS/LLM registries
entirely**. Making STT pluggable does nothing for a Grok-voice agent; Grok does its own STT
internally.

Where #244 *does* land is one layer down: when a tool named `generate_image` fires, the
**server-side handler** should resolve its provider through a registry instead of a
hardcoded vendor. #244 is therefore the **backend for the tools, not a prerequisite for
this framework**.

Recommended order: build the tool catalog first, then pull #244 items in as each tool needs
one. Refactoring seven services with no consumer means guessing the interfaces; with a tool
bus driving it each item becomes "swap what's behind tool N," verifiable per tool.

Groundwork already exists and should be reused, not reinvented: `providers/registry.py`
(singleton + `ProviderType` + autodiscover) and `config/providers.yaml`. Note only the
`stt:` section there is load-bearing today; `llm:` is deprecated
(`providers/llm/DEPRECATED.md`) and `tts:` is informational — live TTS uses
`tts_providers/`.

---

## 7. Invariants

- An adapter communicates **only** through the bridge.
- An adapter **never** imports another adapter.
- `destroy()` releases every resource — AudioContext, sockets, subscriptions, timers.
- Provider credentials **never** reach the browser. Proxy server-side.
- No `localStorage` / `sessionStorage` / IndexedDB for any state. The VPS is the database.
  (Root `CLAUDE.md` — JamBot is a browser terminal for one user's own VPS, not a website.)
- New adapters are **additive**. Adding one must not change behaviour for existing profiles.
