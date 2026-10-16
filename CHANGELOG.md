# Changelog

OpenVoiceUI uses calendar versioning (`YYYY.M.D`; a second release on the same day gets `-N`).
Every release is a git tag `vYYYY.M.D` on `main` with a GitHub Release that carries the full
notes. This file is the index: one short summary per release, newest first, linking to the full
notes. See [docs/RELEASING.md](docs/RELEASING.md) for how releases are cut.

## [Unreleased]

- Repository organization pass: root docs moved into `docs/`, stale duplicates moved into
  `archive/`, README rewritten, community files added (Code of Conduct, this changelog,
  `.editorconfig`, issue-template config). See [docs/REPO-ORGANIZATION.md](docs/REPO-ORGANIZATION.md).

## [2026.9.29] - 2026-09-29

- Gateway reconnect window now covers a real gateway restart; boot warm-up retries until the
  gateway answers and the container entrypoint waits for the gateway port.
- One-click vault Connectors (Supabase, GitHub, Netlify) with validate-before-save.
- Suno submit refusals (for example, out of credits) are reported to the user instead of failing
  silently.
- TTS fixes: the end of a reply is no longer cut off, no more double audio from interim TTS;
  push-to-talk works without a running call.
- Auth and canvas hardening; least-privilege permissions on CI workflows.
- Bundles OpenClaw `2026.5.7`.

## [2026.9.7] - 2026-09-07

- On-box speech-to-text (Parakeet) and Pocket TTS as a free local voice-clone provider.
- Opt-in wake-word auto-arm with a tunable wake threshold; faster first audio.
- Audio clip creator, camera capture with job albums, image splitter and grid creator rework.
- Grok/xAI providers and the booking plugin.
- Canvas pages locked by default; canvas data feeds require auth; upload CSP by file type.
- Bundles OpenClaw `2026.5.7`.

## [2026.7.15] - 2026-07-15

- Security hardening pass: upload stored-XSS, OAuth callback CSRF and auth fail-open fixes.
- Real Hume Octave TTS provider and an opt-in xAI Grok Realtime adapter.
- Full Suno suite (stems, personas, voice cloning, uploads) with durable task-id records.
- Canvas Styles (per-instance design-system picker) and a desktop/canvas overhaul.
- Rebuilt admin panel.

## [2026.6.9] - 2026-06-09

- Resemble TTS hardening (chunking, timeouts, retries, no silent voice substitution).
- `/api/auth/me` and `/api/history` endpoints.
- Dependency updates.

## [2026.5.30] - 2026-05-30

- Z.AI-direct slow/empty-turn fallback; persona-driven greetings replace canned fallbacks.
- Story engine API; mic mute held until the response stream ends.
- Dependency updates and community contributions.

## [2026.5.4] - 2026-05-04

- Fixed a cost leak where one song request could fire multiple paid Suno calls.
- Fixed bare "YES"/"NO" replies leaking to the user.
- Made an optional backend import non-fatal so a fresh image build starts cleanly.

## [2026.4.28-1] - 2026-04-28

- Removed the robotic browser speech filler during retry cascades.

## [2026.4.28] - 2026-04-28

- `bump-openclaw-version.sh` keeps the OpenClaw pin in sync across the Docker, Pinokio and native
  installers; the Pi coding agent ships as the default coding CLI.
- Deepgram marked optional in the installer (browser Web Speech is the default STT).

## [2026.4.19] - 2026-04-19

- Session recovery and interrupt handling: context-preserving recovery, empty-reply retries and
  steer-during-inference recovery.

## [2026.4.13] - 2026-04-13

- Deepgram and Web Speech push-to-talk reliability, server-side garbage-STT filter, Resemble TTS
  reliability.

## [2026.4.10] - 2026-04-10

- Plugin config API and settings panel, plugin catalog stubs, custom faces system, page-icon meta
  tags for canvas pages.

## [2026.4.7] - 2026-04-07

- Remote plugin catalog, conversation interject, external STT provider, text/voice mode toggle,
  sub-agent visibility, AI config admin panel.

## [2026.4.2] - 2026-04-02

- External (bring-your-own) STT provider; AI models and API keys admin panel; gateway auth and
  STT accumulation fixes.

## [2026.3.31] - 2026-03-31

- Text/voice mode toggle, sub-agent visibility, canvas screenshots, version display and update
  detection; Docusaurus documentation site.

## [2026.3.29-1] - 2026-03-29

- Re-publish of 2026.3.29 with an updated npm package.

## [2026.3.29] - 2026-03-29

- GLM-5-turbo default, admin panel overhaul, README overhaul; avatar content moved into the
  plugin system.

## [2026.3.28] - 2026-03-28

- Faster installs (heavy face-recognition dependencies made optional), clearer Docker errors,
  Dependabot and security scanning in CI, device-pairing fix for stock installs.

## [2026.3.27] - 2026-03-27

- First official release: one-click self-update, plugin system, canvas pages, voice studio and
  the initial TTS provider set.

[Unreleased]: https://github.com/MCERQUA/OpenVoiceUI/compare/v2026.9.29...HEAD
[2026.9.29]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.9.29
[2026.9.7]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.9.7
[2026.7.15]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.7.15
[2026.6.9]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.6.9
[2026.5.30]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.5.30
[2026.5.4]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.5.4
[2026.4.28-1]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.4.28-1
[2026.4.28]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.4.28
[2026.4.19]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.4.19
[2026.4.13]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.4.13
[2026.4.10]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.4.10
[2026.4.7]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.4.7
[2026.4.2]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/v2026.4.2
[2026.3.31]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/2026.3.31
[2026.3.29-1]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/2026.3.29-1
[2026.3.29]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/2026.3.29
[2026.3.28]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/2026.3.28
[2026.3.27]: https://github.com/MCERQUA/OpenVoiceUI/releases/tag/2026.3.27
