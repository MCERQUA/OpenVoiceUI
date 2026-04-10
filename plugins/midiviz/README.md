# MidiViz Audio Visualizer

Audio-reactive MIDI visualizer plugin for OpenVoiceUI. Embeds [midiviz.com](https://midiviz.com/) in a canvas page with microphone permission passthrough.

## What it does

Adds a "MidiViz" canvas page that streams your microphone audio into the MidiViz visualizer. The visualizer reacts to sound in real-time — music, voice, ambient noise.

## Requirements

The host OpenVoiceUI instance must have microphone permissions enabled on the canvas iframe. Specifically:

- **`AppShell.js`** — canvas iframe needs `allow="microphone"` attribute
- **`app.py`** — `Permissions-Policy` header must allow `microphone` delegation (e.g. `microphone=*`)
- **`canvas.py`** — CSP `frame-src` must include `https://midiviz.com`

These are app-level changes (not plugin-managed). Without them, the browser blocks mic access inside the nested iframe.

## Install

Install from the OpenVoiceUI plugin catalog in Settings, or manually copy `pages/midiviz.html` to your canvas-pages directory.
