# TTS Stream Audio Cutoff Issue

**Date:** 2026-02-27 (original), updated 2026-03-17
**Priority:** Medium — tracked in GitHub #115, #81
**Status:** Partially addressed — `min_sentence_chars` now wired, other config options still unwired

## The Problem

When the AI streams a response, it's broken into sentences and each sentence gets TTS generated in parallel. Multiple audio clips arrive at the frontend almost simultaneously, causing choppy/overlapping playback.

## Current State (March 2026)

### What's been fixed:
- **`min_sentence_chars` is now wired** — `conversation.py` line 885-894 reads the profile value (default 40) instead of only using a hardcode. Profile can now override this.

### What's still broken:
- **`parallel_sentences`** — still always parallel, config value ignored (exists in `profiles/default.json` but code always fires TTS in background threads)
- **`inter_sentence_gap_ms`** — still not implemented, no pause between audio chunks
- **Frontend audio queue** — `TTSPlayer.js` uses `HTMLAudioElement` with `onended` callbacks. Works for well-spaced chunks but unreliable when chunks arrive rapidly. No pre-buffering or gap logic.

## Backend Flow (current)

1. LLM streams tokens → accumulate in `_tts_buf`
2. `_extract_sentence()` splits on `.!?` with configurable `min_len` (from profile, default 40)
3. Each sentence fires `_fire_tts()` in a **background thread** — all run in parallel
4. Audio chunks yielded sequentially to client (waits for each thread in order)
5. Client receives NDJSON: `{"type": "audio", "audio": "<base64>", "chunk": N}`

## Frontend Flow (current)

1. `TTSPlayer.queue(base64Audio)` creates `Audio` element with `onended → _playNext()`
2. `_playNext()` shifts from `audioQueue[]`, plays next
3. No gap, no pre-buffering, no validation of empty chunks

## Config Options Status

| Config | Default | Status |
|--------|---------|--------|
| `min_sentence_chars` | `40` | **WIRED** — reads from profile (line 894) |
| `parallel_sentences` | `true` | NOT IMPLEMENTED — always parallel |
| `inter_sentence_gap_ms` | `null` | NOT IMPLEMENTED — no silence between chunks |

## GitHub Issues

- **#115** — feat: wire TTS sentence streaming config options from profile (covers `parallel_sentences` and `inter_sentence_gap_ms`)
- **#81** — perf: high TTS latency on greeting responses (related — parallel TTS is fast but causes overlap)

## Key Files

**Backend:**
- `routes/conversation.py` — `_extract_sentence()` (~line 1014), `_fire_tts()` (~line 1026), `_min_sentence_chars` (~line 885)
- `profiles/manager.py` — VoiceConfig schema (lines 56-62)
- `profiles/default.json` — default values

**Frontend:**
- `src/providers/TTSPlayer.js` — queue-based audio player (queue/_playNext/stop)
- `src/core/VoiceSession.js` line 342 — queuing audio from stream
