---
sidebar_position: 6
title: Animated Faces
---

# Animated Faces

OpenVoiceUI displays an animated face/avatar in the voice interface that reacts to speech, moods, and audio levels. The face system is fully extensible through plugins.

## Built-in Faces

### Halo Smoke Orb (default)

Canvas-based audio-reactive orb with a halo frequency ring and wispy smoke particles. Calm at rest, reacts to TTS speech amplitude.

- **Features:** audio-reactive, smoke, halo, speech-reactive
- **Moods:** None — reacts to audio amplitude only

### AI Eyes

DOM-based animated eyes with mouse tracking and mood-driven eyelid expressions.

- **Features:** blink, eye-tracking, mood-eyelids
- **Moods:** All 7 supported (neutral, happy, sad, angry, thinking, surprised, listening)

## Mood System

Faces can express 7 emotional states, triggered by the agent via `[MOOD:state]` tags:

| Mood | Description |
|------|-------------|
| `neutral` | Default resting state |
| `happy` | Positive, upbeat |
| `sad` | Downcast |
| `angry` | Intense, frustrated |
| `thinking` | Processing, contemplative |
| `surprised` | Startled, unexpected |
| `listening` | Attentive, receiving input |

Not all faces support moods. The Halo Smoke Orb is audio-reactive only. EyeFace uses moods to adjust eyelid shape and expression.

## Face Manifest

Faces are registered in `src/face/manifest.json`:

```json
{
  "version": 2,
  "default": "halo-smoke",
  "faces": [
    {
      "id": "eyes",
      "name": "AI Eyes",
      "description": "Classic animated eyes with mood expressions",
      "module": "/src/face/EyeFace.js",
      "preview": "/src/face/previews/eyes.svg",
      "moods": ["neutral", "happy", "sad", "angry", "thinking", "surprised", "listening"],
      "features": ["blink", "eye-tracking", "mood-eyelids"],
      "configurable": false
    }
  ]
}
```

## BaseFace API

All faces extend the `BaseFace` abstract class in `src/face/BaseFace.js`.

### Required Methods

| Method | Description |
|--------|-------------|
| `init(container)` | Initialize the face inside the given DOM container. Called once when the face is activated. |
| `setMood(mood)` | Set the face's emotional state. Receives one of the 7 valid moods. |
| `destroy()` | Clean up timers, animation frames, and DOM mutations when the face is deactivated. |

### Optional Methods

| Method | Description |
|--------|-------------|
| `blink()` | Trigger a blink animation. No-op by default. |
| `setAmplitude(amplitude)` | React to audio amplitude (0.0–1.0) for speaking animations. |

## Building a Custom Face

See [Building Face Plugins](/extending/building-face-plugins) for a step-by-step guide to creating and distributing custom faces.

## Face Selection

The active face is set per-profile via `ui.face_mode` in the profile JSON, or switched from the admin dashboard. See [Profiles](/customization/profiles) for details.

## Plugin Faces

Community-built faces are installed as plugins. The first community face pack is [BHB Animated Characters](https://github.com/MCERQUA/openvoiceui-plugins). See the [Plugin System](/extending/plugin-system) for installation and development guides.
