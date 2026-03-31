---
sidebar_position: 2
title: TTS Providers
---

# TTS Providers

OpenVoiceUI supports multiple text-to-speech providers. The active provider is set per-profile via `voice.tts_provider`. All TTS is generated server-side and streamed as base64 audio to the browser.

## Provider Comparison

| Provider | Type | Cost | Latency | Quality | Voice Cloning |
|----------|------|------|---------|---------|---------------|
| **Supertonic** | Local (Docker) | Free | ~1-2s | Good | No |
| **Groq Orpheus** | Cloud API | Free tier | ~0.5-1s | High | No |
| **Resemble AI** | Cloud API | Paid | ~1-2s | Premium | Yes |
| **Qwen3-TTS** (fal.ai) | Cloud API | Paid | ~8-11s | High | Yes |
| **ElevenLabs** | Cloud API | Paid | ~1-2s | Premium | Yes |
| **Hume EVI** | Cloud API | Paid | ~1-2s | High | Emotion-aware |

## Supertonic (Local, Free)

Supertonic is a local TTS server that ships with the Docker setup. No API key needed.

### Configuration

No `.env` key required — Supertonic starts automatically with `docker compose up`.

### Voice Styles

Supertonic ships with 10 built-in voices:

| Voice ID | Gender | Description |
|----------|--------|-------------|
| `F1`–`F5` | Female | 5 female voice styles |
| `M1`–`M5` | Male | 5 male voice styles |

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `total_step` | 40 | Diffusion steps (higher = better quality, slower) |
| `speed` | 1.05 | Playback speed (must be >= 1.0) |
| `silence_duration` | 0.1 | Silence padding between sentences (seconds) |

### Troubleshooting

- **No audio:** Check that the supertonic container is running (`docker compose ps`)
- **Slow generation:** Reduce `total_step` to 20 for faster (lower quality) output
- **Container not starting:** Ensure Docker has enough memory allocated (minimum 1GB for Supertonic)

## Groq Orpheus (Cloud, Free Tier)

Fast cloud TTS using the Orpheus model via Groq's inference API.

### Configuration

```env
GROQ_API_KEY=your-groq-api-key
```

Get a key at [console.groq.com](https://console.groq.com).

### Voices

| Voice ID | Gender |
|----------|--------|
| `troy` | Male |
| `austin` | Male |
| `daniel` | Male |
| `autumn` | Female |
| `diana` | Female |
| `hannah` | Female |

### Notes

- Outputs WAV audio, converted to MP3 via ffmpeg
- Very low latency (~0.5-1s)
- Free tier has rate limits — suitable for personal use

## Qwen3-TTS (fal.ai)

Cloud TTS with voice cloning capability. Clone any voice from a short audio sample.

### Configuration

```env
FAL_KEY=your-fal-api-key
```

### Voice Cloning

1. Upload a voice sample via the admin dashboard or API
2. Clone endpoint: `POST /api/tts/clone` — processes the sample (~37 seconds)
3. Generate with cloned voice: `POST /api/tts/generate` with `provider=qwen3`

### Notes

- Clone time: ~37 seconds per voice
- Generation time: ~8-11 seconds per utterance
- Not suitable for real-time conversation — use for special content

## ElevenLabs

Premium cloud TTS with a large voice library and voice cloning.

### Configuration

```env
ELEVENLABS_API_KEY=your-elevenlabs-api-key
```

### Notes

- Many pre-built voices available
- Voice cloning with short samples
- Per-character billing

## Resemble AI

Premium cloud TTS with voice cloning.

### Configuration

```env
RESEMBLE_API_KEY=your-resemble-api-key
```

## Hume EVI

Emotion-aware TTS that adjusts tone based on emotional context.

### Configuration

```env
HUME_API_KEY=your-hume-api-key
```

### Notes

- Emotion detection influences voice tone
- WebSocket-based streaming
- Requires the Hume adapter (`src/adapters/hume-evi.js`)

## Profile Configuration

Set the TTS provider in your profile JSON:

```json
{
  "voice": {
    "tts_provider": "groq",
    "voice_id": "autumn",
    "speed": 1.1,
    "parallel_sentences": true,
    "min_sentence_chars": 40
  }
}
```

| Field | Description |
|-------|-------------|
| `tts_provider` | Provider ID: `supertonic`, `groq`, `elevenlabs`, `hume`, `qwen3`, `resemble` |
| `voice_id` | Provider-specific voice identifier |
| `speed` | Playback speed multiplier |
| `parallel_sentences` | Generate multiple sentences simultaneously for lower latency |
| `min_sentence_chars` | Minimum characters before a sentence is sent to TTS |

## Adding a New Provider

See `src/providers/tts/BaseTTSProvider.js` for the abstract base class. New providers implement:

1. `generateSpeech(text, options)` — Returns audio buffer
2. `getVoices()` — Returns available voice list
3. Register in `src/providers/tts/index.js`

Server-side providers extend `tts_providers/base_provider.py` and register in `tts_providers/__init__.py`.
