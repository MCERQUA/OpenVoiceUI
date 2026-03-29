---
sidebar_position: 3
title: Building Face Plugins
---

# Building Face Plugins

Face plugins add custom animated avatars to OpenVoiceUI. They extend the `BaseFace` class and are distributed as plugins.

## Quick Start

Create a directory with this structure:

```
my-face-plugin/
  plugin.json
  faces/
    my-face.js
    preview.svg
```

### plugin.json

```json
{
  "id": "my-face-plugin",
  "name": "My Custom Face",
  "version": "1.0.0",
  "type": "face",
  "description": "A custom animated face for OpenVoiceUI",
  "author": "Your Name",
  "faces": [
    {
      "id": "my-face",
      "name": "My Face",
      "description": "Description of what this face looks like",
      "script": "faces/my-face.js",
      "preview": "faces/preview.svg",
      "moods": ["neutral", "happy", "sad", "angry", "thinking", "surprised", "listening"],
      "features": ["audio-reactive", "mood-support"]
    }
  ]
}
```

### Face Module (my-face.js)

```javascript
import { BaseFace } from '/src/face/BaseFace.js';

export default class MyFace extends BaseFace {
  init(container) {
    // Create your DOM elements or canvas inside `container`
    this.canvas = document.createElement('canvas');
    this.canvas.width = container.clientWidth;
    this.canvas.height = container.clientHeight;
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this._animate();
  }

  setMood(mood) {
    // Respond to mood changes
    // mood is one of: neutral, happy, sad, angry, thinking, surprised, listening
    this.currentMood = mood;
  }

  setAmplitude(amplitude) {
    // React to audio levels (0.0 to 1.0)
    // Called continuously during TTS playback
    this.amplitude = amplitude;
  }

  blink() {
    // Optional: trigger a blink animation
  }

  destroy() {
    // Clean up: cancel animation frames, remove DOM elements
    if (this._animFrame) cancelAnimationFrame(this._animFrame);
    this.canvas?.remove();
  }

  _animate() {
    // Your render loop
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    // ... draw your face ...
    this._animFrame = requestAnimationFrame(() => this._animate());
  }
}
```

## BaseFace API Reference

| Method | Required | Parameters | Description |
|--------|----------|------------|-------------|
| `init(container)` | Yes | DOM element | Initialize face inside the container |
| `setMood(mood)` | Yes | String (one of 7 moods) | Update emotional expression |
| `destroy()` | Yes | None | Clean up all resources |
| `blink()` | No | None | Trigger blink animation |
| `setAmplitude(amplitude)` | No | Number (0.0–1.0) | React to audio level |

## Valid Moods

`neutral`, `happy`, `sad`, `angry`, `thinking`, `surprised`, `listening`

Your face doesn't have to support all moods. List only the ones you implement in the `moods` array of your plugin manifest.

## Tips

- **Canvas vs DOM:** Use `<canvas>` for complex animations (particles, shaders, smooth motion). Use DOM elements for simpler faces (CSS transitions, SVG manipulation).
- **Audio reactivity:** `setAmplitude()` is called at ~60fps during TTS playback. Use it to drive visual effects like pulsing, scaling, or particle emission.
- **Performance:** Keep your render loop efficient. The face runs alongside the entire UI.
- **Preview image:** Provide a small SVG preview (the face picker shows these as thumbnails).

## Installing

Copy your plugin directory to the `plugins/` folder in your OpenVoiceUI installation and restart, or install from the admin dashboard if your plugin is in the catalog.

## Distribution

Submit your face plugin to the [OpenVoiceUI Plugins Repository](https://github.com/MCERQUA/openvoiceui-plugins). See the [BHB Animated Characters](https://github.com/MCERQUA/openvoiceui-plugins) plugin as a reference implementation.
