---
sidebar_position: 1
title: API Reference
---

# API Reference

:::info
Full API documentation is being written. This page will be populated with all endpoints shortly.
:::

## Base URL

```
http://localhost:5001
```

All endpoints return JSON. Errors return `{"error": "message"}`.

## Quick Reference

| Module | Base Path | Description |
|--------|-----------|-------------|
| [Conversation](#conversation) | `/api/conversation` | Voice conversation, TTS generation |
| [Canvas](#canvas) | `/api/canvas/*` | Canvas page CRUD, versioning, manifest |
| [Music](#music) | `/api/music` | Music player control, uploads, playlists |
| [Suno](#suno) | `/api/suno` | AI music generation |
| [Profiles](#profiles) | `/api/profiles` | Agent profile CRUD, activation |
| [Plugins](#plugins) | `/api/plugins` | Plugin install, uninstall, listing |
| [Image Generation](#image-generation) | `/api/image-gen` | Image creation, saved designs |
| [Vision](#vision) | `/api/vision` | Camera frame analysis, face registration |
| [Admin](#admin) | `/api/admin/*` | Gateway RPC, system stats, client management |
| [Workspace](#workspace) | `/api/workspace/*` | File browser, file content |
| [Theme](#theme) | `/api/theme` | Color theme save/load |
| [Transcripts](#transcripts) | `/api/transcripts` | Conversation transcript storage |
| [Instructions](#instructions) | `/api/instructions` | Instruction file management |
| [Greetings](#greetings) | `/api/greetings` | Greeting phrases pool |
| [Icons](#icons) | `/api/icons/*` | Icon library search |
| [Onboarding](#onboarding) | `/api/onboarding/state` | Onboarding progress |

*Detailed endpoint documentation for each module coming soon.*
