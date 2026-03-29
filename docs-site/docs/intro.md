---
sidebar_position: 1
slug: /
title: Introduction
---

# OpenVoiceUI

**The open-source voice AI that actually does work.**

Install, open `localhost:5001`, say *"build me a dashboard"*, and watch it render live.

OpenVoiceUI is a hands-free, AI-controlled computer. You talk — it builds. Live web apps, dashboards, games, full websites — rendered in real time while you watch. No mouse, no keyboard, no typing prompts into a chat box.

It runs on [OpenClaw](https://openclaw.org) and works with any LLM. The AI agent can build and display apps mid-conversation, switch between projects with a voice command, generate music on the fly, delegate work to parallel sub-agents, and remember everything across sessions.

Self-hosted. Your hardware, your data. MIT licensed, forever free.

## Quick Install

**Prerequisite:** [Docker](https://docs.docker.com/get-docker/) must be installed and running.

```bash
npx openvoiceui setup
npx openvoiceui start
```

Open **http://localhost:5001** and start talking.

See [Installation](/getting-started/installation) for all install methods including Pinokio (one-click) and Docker Compose.

## Core Features

- **[Canvas System](/features/canvas-system)** — AI builds live HTML pages mid-conversation: dashboards, tools, reports, full web apps
- **[Music Generation](/features/music-system)** — Generate songs on the fly with your voice using Suno
- **[Desktop Interface](/features/desktop-interface)** — Windows-style OS with draggable windows, themes, and taskbar
- **[Image Generation](/features/image-generation)** — Create images with FLUX.1, Stable Diffusion, and Gemini
- **[Voice Control](/features/voice-stt-flow)** — Wake word, push-to-talk, or continuous listening
- **[Animated Faces](/features/faces)** — Animated eye-face avatar, audio-reactive orb, and plugin faces
- **[Admin Dashboard](/features/admin-dashboard)** — Full control panel for agents, providers, plugins, and system health
- **[Plugin System](/extending/plugin-system)** — Community-built face packs, canvas pages, and extensions

## Extend It

OpenVoiceUI is built to be extended. Build [plugins](/extending/plugin-system), [canvas pages](/extending/building-canvas-pages), [custom faces](/extending/building-face-plugins), or connect any LLM provider.

Check out the [Plugins Repository](https://github.com/MCERQUA/openvoiceui-plugins) for community contributions, starting with the [BHB Animated Characters](https://github.com/MCERQUA/openvoiceui-plugins) face pack.

## What's Documented Here vs OpenClaw

This documentation covers **OpenVoiceUI-specific features** — the voice interface, canvas, music player, admin dashboard, plugin system, profiles, and API endpoints.

For agent configuration, skills, tools, sub-agent orchestration, memory engine, and LLM routing, see the [OpenClaw documentation](https://openclaw.org).
