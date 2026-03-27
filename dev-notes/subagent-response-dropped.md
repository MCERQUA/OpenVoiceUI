# Subagent Results Never Delivered to Voice UI

**Date:** 2026-02-28 (original), updated 2026-03-17
**Priority:** High — breaks multi-agent research workflows
**Status:** CLOSED — gateway handles subagent lifecycle; remaining edge case in #117
**GitHub:** #168 (closed), #102 (closed), #117 (open — partial response lost on WS disconnect)

## The Problem

When the AI spawns subagents (e.g., "I've launched 5 research agents"), the main `chat.final` fires and the HTTP stream closes. Subagent results arrive later but have nowhere to go — the connection is already dead.

## What's Been Fixed

- **#102 (closed)** — subagent delegation causing immediate empty response. The gateway now detects `subagent_active` state and has waiting logic for announce-back events.
- **Empty response retry** — `conversation.py` retries once on instant empty responses so delegation doesn't immediately fail.

## What May Still Be Fragile

The gateway (`openclaw.py` ~line 689+) now tracks subagent lifecycle and has explicit handling at lines 896-932. However, `conversation.py` still closes the HTTP response when the generator completes without explicitly checking subagent state.

### The Disconnect
1. Gateway puts events into `event_queue` and knows about subagent state
2. Conversation route reads from that queue but has its own exit logic
3. Conversation route exits at `chat.final` with text — doesn't check if subagents are still working
4. Even if subagent results arrive later, there's no HTTP connection to send them through

### Also Potentially Fragile: Mic Stays Muted After Subagent Response
- When TTS finishes playing the "I've launched 5 agents" message, mic may not unmute
- Empty/zero-length audio chunks from rapid TTS can break the `onended` callback chain in `TTSPlayer.js`
- If `isPlaying` stays true, `_notifySpeaking(false)` never fires → mic stays muted
- Note: `_notifySpeaking` lives in `TTSPlayer.js` (~line 304), not VoiceSession.js

## What Needs to Happen

### For subagent results:
1. Conversation route must check subagent state before closing the response
2. If subagents are active, keep HTTP stream open and continue reading event queue
3. When subagent results arrive (announce-back with new text), generate TTS and yield more audio
4. Only close when all subagents have reported back OR a timeout is reached

### For stuck mic:
1. TTSPlayer needs to validate audio chunks before queueing (reject 0-byte blobs)
2. Add a timeout fallback — if `isPlaying` is true for longer than expected, force reset
3. After all audio chunks are yielded, send explicit "audio_complete" event so frontend unmutes regardless

## Key Files

- `routes/conversation.py` — HTTP stream lifecycle, exit logic at chat.final
- `services/gateways/openclaw.py` — subagent detection (~line 689), waiting logic (~line 698-700), event queue
- `src/providers/TTSPlayer.js` — audio queue, `_playNext()`, empty chunk handling
- `src/providers/TTSPlayer.js` — `_notifySpeaking()` (~line 304), mic unmute trigger
