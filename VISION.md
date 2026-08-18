# Vision

**OpenVoiceUI is the open voice + canvas shell for agents that do work — not a chat widget
with a microphone.**

You speak. The agent plans, builds, and **shows** — live canvas pages, tools, music, a face
with presence — on hardware you control.

---

## The gap this fills

Most voice AI is a transport layer for conversation. Audio in, audio out, a transcript in a
scrolling box. The interesting part of an agent, though, is not that it talks back — it is
that it *produces things*: a dashboard, a report, a working page, a generated track, a
finished task.

Voice interfaces have no way to hand you those things. Chat interfaces can show them but
make you type. OpenVoiceUI is the missing surface: a shell where the agent's output is a
**live rendered artifact you can look at and use**, and your input is your voice.

The practical shape of that, in this repo:

- **Voice in.** Speech recognition in the browser (Web Speech) or through a server-side STT
  provider, with wake-word and push-to-talk modes, silence timeouts, interrupt and steer.
- **A canvas.** The agent writes real HTML pages that are stored, versioned, indexed in a
  manifest, and rendered in the shell. It can open one mid-sentence, fill a form on it,
  update it, or switch to another. Pages persist; they are files on your disk, not chat
  bubbles.
- **Voice out.** Server-side TTS through a pluggable provider layer, with a fallback chain
  and text normalisation so speech sounds like speech.
- **Presence.** An animated face reacting to speech and mood, swappable at runtime, and
  extensible with your own.
- **Everything else the agent reaches for.** Music, images, 3D, transcripts, camera,
  workspace file browsing, an admin surface — because an agent that does work needs to
  hand back more than sentences.

---

## First principles

### 1. Provider-agnostic, everywhere

Every layer where a vendor could lock you in is a swappable interface with a registry.

- **The agent runtime** is behind a `GatewayBase` implementation. OpenClaw is the default;
  Hermes and community gateways plug in the same way. A gateway plugin is a `plugin.json`
  and one Python class.
- **TTS** is an ABC (`TTSProvider`) with a catalog of implementations and a configurable
  fallback chain.
- **STT** is an ABC with a registry, resolvable in the browser or on the server.
- **Faces** are a base class plus a manifest.
- **The LLM** is not our decision at all — it belongs to whatever runtime you put behind the
  gateway.

If you cannot swap it, we consider that a design defect.

### 2. The shell is not the brain

OpenVoiceUI deliberately owns no model, no planning loop, no tool execution. That boundary
is what lets it be a stable, well-defined surface that any agent framework can sit behind.

### 3. Show, do not narrate

An agent that says "I've created a dashboard for you" has done nothing. An agent that
renders the dashboard has. The canvas system exists so producing an artifact is the normal
mode of response, not a special case.

### 4. Your hardware, your data

Self-hosted by default. Canvas pages, transcripts, music, and configuration are files on
your machine. API keys stay server-side and never reach the browser. There is no account,
no telemetry requirement, no remote store you depend on.

### 5. Extension without forking

Plugins drop into a folder and load on restart — no rebuild, no core patch. Gateways,
canvas pages, API routes, faces, profiles, and agent knowledge all install this way. A
contributor who wants to add a TTS backend touches two files and a JSON catalog entry.

### 6. No build step on the frontend

The frontend is vanilla ES modules served straight to the browser. No bundler, no
transpiler, no `node_modules` between you and the code you are reading. This is a
constraint we keep on purpose: it means anyone can open a file, understand it, and change
it.

---

## What OpenVoiceUI is not

**It is not the agent brain.** OpenClaw, Hermes, or whatever you plug in does the
reasoning, planning, and tool use. If you want to change how the agent thinks, change the
runtime behind the gateway. Pull requests that try to build model routing or an agent loop
into this repo are pointing at the wrong layer.

**It is not a hosted SaaS.** There is no cloud service to sign up for, no seats, no managed
tier that is the "real" product with this as a demo. It is software you run.

**It is not a chatbot UI.** The transcript exists, but it is not the point. If a feature
only makes conversation prettier and does not help the agent do or show work, it is a low
priority here.

**It is not tied to one vendor.** No provider is privileged in the architecture. Defaults
exist for convenience and are replaceable in configuration.

**It is not a multi-user application.** The shell assumes one operator talking to their own
agent on their own machine. Optional authentication gates access; it does not turn this
into a multi-tenant product.

---

## How to tell if a contribution fits

Ask: **does this help the agent hear you, speak to you, show you something, or plug into
something new?**

Good fits:

- A new TTS, STT, or gateway provider
- Canvas system capability — new page primitives, better versioning, better addressing
- Face and presence work
- Frontend UX for the voice loop: interruption, wake word, latency, mobile
- Making setup, configuration, or self-hosting easier
- Tests and documentation for any of the above

Poor fits:

- Model selection logic, prompt engineering frameworks, or agent planning loops
- Anything that hardcodes a single vendor into a layer that currently has an interface
- Frontend rewrites into a framework, or introducing a build step
- Hosted-service or billing scaffolding

---

MIT licensed. See [ARCHITECTURE.md](ARCHITECTURE.md) for how it works,
[AGENTS.md](AGENTS.md) if you are an AI coding agent, and
[CONTRIBUTING.md](CONTRIBUTING.md) to start contributing.
