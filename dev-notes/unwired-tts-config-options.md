# Unwired TTS Config Options

**Date:** 2026-02-28 (original), updated 2026-03-17
**Related:** tts-stream-cutoff-issue.md, GitHub #115 (closed)
**Priority:** Medium — directly contributes to choppy audio. Issue closed with only `min_sentence_chars` wired; other two deferred.

## Summary

Three TTS settings exist in the profile config schema. One is now wired; two are still disconnected.

## 1. `min_sentence_chars` — WIRED (fixed March 2026)

**What it does:** Minimum characters before a sentence gets sent to TTS. Prevents short fragments from becoming their own audio clip.

**Status:** `conversation.py` line 885-894 reads the profile value with a default of 40. The profile can override this per-voice-config.

**Files:**
- `routes/conversation.py` → `_min_sentence_chars` (line 1018), `_extract_sentence(_tts_buf, min_len=_min_sentence_chars)` (line 1318)
- `profiles/default.json` → `"min_sentence_chars": 20`
- `profiles/manager.py` → `min_sentence_chars: Optional[int] = None`

## 2. `inter_sentence_gap_ms` — NOT IMPLEMENTED

**What it does:** Adds a small pause (ms) between audio clips during playback. Like a person taking a breath between sentences.

**The problem:** Sentence 2 starts playing the instant sentence 1 ends. With no gap, fast responses sound like a machine gun of short clips. A small gap (100-300ms) would sound more natural.

**Current state:** Setting exists in profile schema and `default.json` (`"inter_sentence_gap_ms": null`) but:
- Backend doesn't insert silence frames between audio chunks
- Frontend `TTSPlayer.js` doesn't add delays between queue items
- Nothing reads this value anywhere

**Files:**
- `profiles/default.json` → `"inter_sentence_gap_ms": null` — defined but unused
- `src/providers/TTSPlayer.js` → `_playNext()` — plays immediately, no gap logic

## 3. `parallel_sentences` — NOT IMPLEMENTED

**What it does:** Controls whether all sentences get sent to TTS simultaneously (parallel=true) or one at a time (sequential=false).

**The problem:** Code always fires all TTS in parallel background threads. This is fast but means multiple audio clips arrive at the frontend almost simultaneously, causing queue pile-up.

**Current state:** Setting exists in profile schema (`"parallel_sentences": true`) but the backend always runs parallel regardless.

**Files:**
- `profiles/default.json` → `"parallel_sentences": true` — defined but unused
- `routes/conversation.py` → `_fire_tts()` always spawns threads, never checks config

## GitHub Tracking

All three were tracked in **#115** (closed) — feat: wire TTS sentence streaming config options from profile. Issue closed with only `min_sentence_chars` implemented; `parallel_sentences` and `inter_sentence_gap_ms` deferred.
