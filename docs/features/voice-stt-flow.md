---
sidebar_position: 5
title: Voice & STT
---

# Voice & STT

OpenVoiceUI is a voice-first interface. The full pipeline runs: **microphone -> STT -> AI gateway -> TTS -> speaker**. This page documents the speech-to-text (STT) side of that pipeline and the voice session lifecycle that ties it all together.

For agent-side processing (prompt construction, tool execution, response generation), see the [OpenClaw documentation](https://openclaw.org).

## Voice Session Lifecycle

A voice session is managed by `VoiceSession` (`src/core/VoiceSession.js`). The session coordinates STT input, AI communication, and TTS output in a continuous loop:

1. **Session starts** -- STT begins listening, greeting is sent to AI
2. **User speaks** -- STT transcribes speech, accumulates text across Chrome results
3. **Silence detected** -- after a configurable delay with no new speech, the accumulated text is sent to the AI gateway
4. **AI responds** -- response text is sent to TTS for synthesis
5. **TTS plays** -- microphone is muted to prevent echo capture
6. **TTS ends** -- after a settling delay, microphone resumes listening
7. Repeat from step 2

## STT Modes

OpenVoiceUI supports three input modes, selectable per-profile or toggled at runtime:

### Continuous Listening

The default mode. STT runs continuously, transcribing everything the user says. Silence detection determines when the user has finished speaking and triggers the send.

The silence timer resets on every speech recognition result (interim or final), so it only fires when the user truly stops talking.

### Push-to-Talk (PTT)

The user holds a button to speak, releases to send. When PTT is active:

- Mic is muted by default (recognition is stopped)
- **Press**: `pttActivate()` -- clears mute, starts recognition
- **Release**: `pttRelease()` -- sends accumulated text, stops recognition, re-mutes

PTT bypasses silence detection entirely. Chrome finalizes any pending speech when `recognition.stop()` is called on release. If Chrome's async finalization hasn't arrived yet, a 400ms fallback waits for it.

### Wake Word

A passive listening mode that waits for a trigger phrase before starting a full conversation:

1. `WakeWordDetector` runs in the background, checking all speech (interim + final) for wake words
2. Default wake word: `"wake up"` (configurable per-profile)
3. When detected, the wake word detector stops and a full voice session starts
4. When the session ends (e.g., user says "go to sleep"), the wake word detector restarts

**Chrome constraint:** Only one `SpeechRecognition` instance can be actively `.start()`ed at a time. The wake word detector and conversation STT use separate instances but never run simultaneously.

**The abort/restart loop in WakeWordDetector is normal.** Chrome periodically drops SpeechRecognition connections. The `onend` handler restarts after a 300ms delay. Speech is captured between cycles.

## Silence Detection

Silence detection is the mechanism that decides when the user has finished speaking in continuous mode. It works by accumulating text across multiple Chrome `SpeechRecognition` results and sending only after a period of silence.

### How it works

1. Every `onresult` event (interim or final) **resets** the silence timer
2. Final transcripts are **appended** to `accumulatedText` -- the user can speak across multiple Chrome final results without triggering a premature send
3. When no results arrive for `silenceDelayMs`, the timer fires and sends the accumulated text
4. Garbage filtering discards punctuation-only or single-character results

### Configuration

The silence timeout is configurable per-profile:

```json
{
  "stt": {
    "silence_timeout_ms": 3500
  }
}
```

| Value | Behavior |
|-------|----------|
| `null` | Platform default (3500ms) |
| `500` (minimum) | Very aggressive -- sends quickly, may cut off mid-thought |
| `3500` (default) | Balanced -- waits long enough for natural pauses |
| `10000` (maximum) | Very patient -- good for users who pause frequently |

The default of 3500ms was tuned up from 3000ms after reports of mid-sentence cutoffs.

## Echo Prevention (Mute/Unmute Cycle)

When TTS plays the AI's response through the speaker, the microphone picks up that audio. Without mitigation, STT would transcribe the AI's own speech as user input, creating a feedback loop.

OpenVoiceUI prevents this with a mute/unmute cycle:

### Mute phase (TTS starts)

When TTS begins playing (`tts.onSpeakingChange(true)`):

1. `stt.mute()` is called immediately
2. `isProcessing` is set to `true` (blocks `onresult` from processing)
3. `recognition.abort()` is called (discards in-flight results)
4. Silence timer is cleared
5. Accumulated text is discarded

`abort()` is used instead of `stop()` because `stop()` finalizes pending results (which would be TTS echo), while `abort()` discards them.

### Unmute phase (TTS ends)

When TTS stops playing (`tts.onSpeakingChange(false)`):

1. A **250ms settling delay** lets speaker audio decay before re-enabling the mic
2. `stt.resume()` is called -- clears the mute flag and explicitly calls `recognition.start()`
3. The recognition engine may have fully stopped during mute (since `onend` no longer auto-restarts while muted), so `resume()` must restart it

The 250ms delay was reduced from 600ms. Providers like DeepgramStreamingSTT that mute their audio pipeline during TTS need less settling time.

## STT Providers

OpenVoiceUI supports multiple STT backends. The provider is selected per-profile.

| Provider | Type | API Key | Notes |
|----------|------|---------|-------|
| `webspeech` | Browser-native (Chrome Web Speech API) | None | Free, no setup. Default. Chrome/Edge only. |
| `deepgram` | Cloud API (batch) | `DEEPGRAM_API_KEY` | Accurate, handles accents well |
| `deepgram-streaming` | Cloud API (WebSocket) | `DEEPGRAM_API_KEY` | Real-time streaming, lowest latency |
| `deepgram-batch` | Cloud API (batch) | `DEEPGRAM_API_KEY` | Alias for `deepgram` |
| `groq` | Cloud API (Whisper) | `GROQ_API_KEY` | Fast cloud transcription via Groq |
| `whisper` | Local model | None | On-device, requires `whisper` Python package |
| `hume` | Cloud API | Via adapter | Part of Hume EVI adapter |
| `elevenlabs` | Cloud API | Via adapter | Part of ElevenLabs adapter |

### WebSpeech Provider Details

`WebSpeechSTT` (`src/providers/WebSpeechSTT.js`) wraps the browser's native `SpeechRecognition` API:

- **Free** -- no API key, no network requests for transcription
- Requires Chrome, Edge, or another Chromium-based browser
- The recognition instance is created lazily on first `start()` and persists for the lifetime of the page
- Uses `continuous: true` and `interimResults: true` for real-time feedback
- Manages its own mic stream (`getUserMedia`) and keeps it alive during active listening to avoid re-triggering permission prompts on iOS

## Profile STT Configuration

The full STT configuration block in a profile JSON:

```json
{
  "stt": {
    "provider": "webspeech",
    "language": "en-US",
    "silence_timeout_ms": 3500,
    "vad_threshold": null,
    "max_recording_s": null,
    "continuous": true,
    "wake_words": ["wake up"],
    "wake_word_required": false,
    "ptt_default": false
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | `"webspeech"` | STT provider ID |
| `language` | string | `"en-US"` | BCP-47 language code |
| `silence_timeout_ms` | int or null | `null` (3500ms) | Silence delay before sending to AI |
| `vad_threshold` | int or null | `null` (35) | FFT amplitude threshold for voice detection (10-80, lower = more sensitive) |
| `max_recording_s` | int or null | `null` | Maximum recording duration in seconds (10-120) |
| `continuous` | bool or null | `null` | Enable continuous listening |
| `wake_words` | array or null | `["wake up"]` | Phrases that trigger wake word detection |
| `wake_word_required` | bool or null | `null` | Require wake word before starting session |
| `ptt_default` | bool or null | `null` | Start in push-to-talk mode |

## Architecture Notes

### Two SpeechRecognition Instances

The system creates up to two `SpeechRecognition` instances (for the WebSpeech provider):

1. **`WebSpeechSTT.recognition`** -- conversation STT, shared between VoiceSession and ClawdbotMode
2. **`WakeWordDetector.recognition`** -- passive wake word listener

Only one can be `.start()`ed at a time due to Chrome's constraint. Both can exist simultaneously.

### Why Instances Are Never Destroyed

Several parts of `app.js` apply monkey-patches to `stt.recognition` (for PTT integration, mode switching, etc.) within the first 200ms of creation. Destroying and recreating the instance would lose these patches. The instance is created once and persists for the page lifetime.

### Error Reporting

Real STT errors (not `no-speech` or `aborted`, which are normal Chrome behavior) are reported to `/api/stt-events` for server-side session monitoring. This enables tracking of microphone hardware failures and other issues across sessions.
