# TTS Stream Audio Cutoff Issue

**Date:** 2026-02-27 (original), updated 2026-03-17
**Priority:** Medium
**Status:** CLOSED — `min_sentence_chars` wired; `parallel_sentences` and `inter_sentence_gap_ms` deferred. GitHub #115 and #81 both closed.

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

- **#115** (closed) — feat: wire TTS sentence streaming config options from profile (`min_sentence_chars` wired; `parallel_sentences` and `inter_sentence_gap_ms` deferred)
- **#81** (closed) — perf: high TTS latency on greeting responses

## Key Files

**Backend:**
- `routes/conversation.py` — `_extract_sentence()` (~line 1156), `_fire_tts()` (~line 1173), `_min_sentence_chars` (~line 1018)
- `profiles/manager.py` — VoiceConfig schema (lines 54-62)
- `profiles/default.json` — default values

**Frontend:**
- `src/providers/TTSPlayer.js` — queue-based audio player (queue ~line 149, _playNext ~line 181, stop ~line 223)
