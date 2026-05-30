# Voice Flow — Mic Mute Drain Window (2026-05-19)

**Component:** `src/app.js` — ClawdbotMode streaming response handler + `playNextAudio()`
**Branch / PR:** `fix/mic-mute-hold-during-tts-pending` → see GitHub PR
**Origin incident:** 2026-05-19, mic captured tail of TTS audio as user speech mid-response.

---

## Symptom

During a long streamed response, the user reported the mic was "off in timing with the actual audio playback" — STT picked up the end of the TTS being played, then the mic appeared hot before the next TTS chunk played. The Action Console showed:

```
Response complete (1381 chars, LLM: 39679ms)
🔊 Playing TTS (TTS: 0ms)            ← chunk 1
🔊 Playing TTS (TTS: 0ms)            ← chunk 2
                                       ← 22-second silent gap
🔊 Playing TTS (TTS: 22021ms)        ← chunk 3 (generation took 22s)
🔊 Playing TTS (TTS: 22068ms)
🔊 Playing TTS (TTS: 22069ms)
🔊 Playing TTS (TTS: 23637ms)
🔊 Playing TTS (TTS: 23644ms)
🔊 Playing TTS (TTS: 23646ms)
🔊 Playing TTS (TTS: 23650ms)
```

## Root Cause

`ClawdbotMode.playNextAudio()` ran an 800ms drain timer when the audio queue emptied. That window was a debounce to handle short inter-sentence gaps. But Groq Orpheus TTS has been observed taking **22–25 seconds** to generate a single chunk under load. The 800ms drain timer fired long before the next chunk arrived, the empty queue triggered `onListening()` → `stt.resume()` → mic hot. When the late chunk finally played, the mic captured it as speech.

## Fix

Extend the drain window dynamically based on whether the **server response stream is still open**:

| Stream state | Drain wait |
|---|---|
| `_streamingResponseActive = true` (chunks may still arrive) | **30,000 ms** |
| `_streamingResponseActive = false` (stream ended) | **800 ms** (unchanged) |

New flag `_streamingResponseActive` (declared in constructor):
- Set `true` immediately before the `fetch(?stream=1)` call
- Set `false` in the streaming handler's `finally{}` block
- When the stream ends and a long drain timer is pending against an empty queue, that block collapses the pending timer and re-invokes `playNextAudio()` so the short-window drain fires and the mic returns promptly

## Why 30s

Worst observed Orpheus gen latency in the incident was ~24s. 30s gives margin without crossing into "something's actually wrong" territory. If a chunk truly never arrives, the existing `INACTIVITY_TIMEOUT_MS = 60000` in the stream reader aborts the request, after which the `finally{}` clears `_streamingResponseActive` and the short-window drain releases the mic.

## What did NOT change

- SpeechRecognition lifecycle — still the same single instance, still `abort()` on mute, `start()` on resume. Per project rule, NEVER destroy/recreate SR instances.
- The 800ms inter-sentence debounce is preserved for the normal case (post-stream drain).
- AudioContext + queue ordering — untouched.
- `_textDoneReceived` flag and interject logic — untouched.
- PTT and wake-word flows — untouched.

## Rollback

Single commit. `git revert <sha>` returns the file to pre-fix behavior — every drain falls back to 800ms unconditionally and the new flag is unused (declared as `false`, never read).

## Monitoring

Things to watch after deploy:
1. **Echo captures decrease** — search `[VoiceSession] Ignoring transcript during TTS` in browser logs. Should drop on long responses.
2. **Mic-hot timing matches audio playback** — Action Console "Playing TTS" lines should always precede `LISTENING` status transitions for streamed responses with audio.
3. **Stop button behavior** — should remain visible the entire time TTS is in-flight, even during 20+ second Orpheus generation gaps.
4. **No new stuck-in-listening states** — if `_streamingResponseActive` ever leaks `true` after a stream ends, the mic would stay muted indefinitely. The `finally{}` block and the 60s inactivity timeout both guard against this; verify by checking that long responses fully release the mic afterward.

## Related

- `src/providers/WebSpeechSTT.js` — `mute()` / `resume()` semantics (no changes here)
- `src/core/VoiceSession.js` — `onSpeakingChange` handler (no changes here)
- Server-side TTS chunk timing — see openclaw response chunking + Groq Orpheus provider in OpenVoiceUI/`tts_providers/`
