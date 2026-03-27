# Empty Response Fallback Chain

**Date:** 2026-02-28 (original), updated 2026-03-17
**Status:** RESOLVED — original bugs fixed, remaining work tracked in GitHub issues

## What Was Happening

The AI returned blank responses (0 chars from `chat.final`), then a broken Z.AI fallback crashed, and the user heard a canned "brain glitched" message.

## What Was Fixed (March 2026)

1. **Z.AI fallback removed** — `get_zai_direct_response()` and the "brain glitched" canned message are gone from the codebase entirely.

2. **Empty response retry** — `conversation.py` now retries once on instant empty responses (2s pause between attempts). Tracked via `_consecutive_empty_responses` module global.

3. **Session auto-reset** — after consecutive empty responses, the session key bumps (`voice-main-N` → `voice-main-N+1`) to escape poisoned session state.

4. **Agent-triggered reset** — agent can send `[SESSION_RESET]` tag to force a clean session.

## Remaining Issues

- **GitHub #82 (CLOSED)** — empty response on tool loop timeout. Fixed with retry logic.
- **Z.AI bad session state** — rapid user interrupts can still poison openclaw sessions with stale abort states. When double-empty occurs (original + retry both fail), a container restart is needed. Permanent fix (auto-restart openclaw session on double-empty) not yet implemented. See CLAUDE.md "Z.AI Bad Session State" section.

## Key Files

- `routes/conversation.py` — retry logic (~line 1485), `_consecutive_empty_responses` (~line 318), session reset
- `services/gateways/openclaw.py` — empty response detection, ABORT logic
