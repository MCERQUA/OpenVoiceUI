---
sidebar_position: 3
title: Agent Tags
---

# Agent Tags

OpenVoiceUI uses bracket tags (`[TAG:value]`) in agent responses to trigger actions in the UI. The agent includes these tags in its text output, and the frontend strips them before displaying the response and executes the corresponding action.

:::info
Agent tags are part of the OpenVoiceUI frontend — the agent learns about them from its TOOLS.md workspace file. The tag definitions and instructions are managed by [OpenClaw](https://openclaw.org).
:::

## Canvas Tags

| Tag | Description |
|-----|-------------|
| `[CANVAS:page-id]` | Open a canvas page by ID in the iframe |
| `[CANVAS_MENU]` | Open the page picker / canvas menu |
| `[CANVAS_URL:https://...]` | Load an external URL in the canvas iframe |

## Music Tags

| Tag | Description |
|-----|-------------|
| `[MUSIC_PLAY]` | Play a random track from the library |
| `[MUSIC_PLAY:track name]` | Play a specific track by name |
| `[MUSIC_STOP]` | Stop music playback |
| `[MUSIC_NEXT]` | Skip to the next track |
| `[SUNO_GENERATE:description]` | Generate an AI song via Suno (~45 seconds) |
| `[SPOTIFY:song name]` | Play from Spotify |
| `[SPOTIFY:song name\|artist]` | Play from Spotify with artist filter |
| `[SOUND:name]` | Play a DJ soundboard effect |

## Mood Tags

| Tag | Description |
|-----|-------------|
| `[MOOD:neutral]` | Set face to neutral expression |
| `[MOOD:happy]` | Set face to happy expression |
| `[MOOD:sad]` | Set face to sad expression |
| `[MOOD:angry]` | Set face to angry expression |
| `[MOOD:thinking]` | Set face to thinking expression |
| `[MOOD:surprised]` | Set face to surprised expression |
| `[MOOD:listening]` | Set face to listening expression |

See [Animated Faces](/features/faces) for details on the mood system.

## System Tags

| Tag | Description |
|-----|-------------|
| `[SLEEP]` | End the conversation and return to wake-word mode |
| `[SESSION_RESET]` | Clear conversation history and restart the session |

## Vision Tags

| Tag | Description |
|-----|-------------|
| `[REGISTER_FACE:name]` | Capture a face from the camera and save it with a name |

## Browser Navigation Tags

These tags enable the agent to interact with web pages loaded in the canvas iframe:

| Tag | Description |
|-----|-------------|
| `[SCROLL:[selector]]` | Scroll to an element |
| `[CLICK:[selector]]` | Click an element |
| `[FILL:[selector]]` | Fill an input field |
| `[HIGHLIGHT:[selector]]` | Highlight an element |
| `[NAVIGATE:[url]]` | Navigate to a URL |
| `[OPEN_TAB:[url]]` | Open a URL in a new tab |
| `[READ_PAGE]` | Read the current page content |
| `[WAIT:seconds]` | Wait for a specified duration |
| `[START_TASK:[selector]]` | Begin a tracked task |
| `[TASK_COMPLETE:[selector]]` | Mark a task as complete |

## How Tags Work

1. The agent includes tags in its text response (e.g., `"Here's your dashboard [CANVAS:my-dashboard]"`)
2. The frontend parses the response text and extracts all bracket tags
3. Tags are stripped from the displayed text (the user sees `"Here's your dashboard"`)
4. Each tag triggers its corresponding action (open page, play music, set mood, etc.)
5. Multiple tags can appear in a single response
