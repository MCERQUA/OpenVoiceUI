---
sidebar_position: 2
title: PR Review Checklist
---

# PR Review Checklist

> Use this checklist for EVERY pull request — internal or external.
> See [Architecture Reference](/reference/architecture) for system details.

---

## Security

- [ ] **innerHTML** — Does this PR add or modify any `innerHTML` assignment? If yes, verify `escapeHtml()` is called first.
- [ ] **Error messages** — Do any new error responses include `str(e)`, stack traces, or file paths? They must use generic messages only.
- [ ] **Input validation** — Any new user input (query params, POST body, URL params) must be validated and bounded.
- [ ] **CSP headers** — If new external resources are loaded, update CSP in `app.py`.
- [ ] **File paths** — Any user-controlled path must be sanitized (no `../` traversal).
- [ ] **Secrets** — No API keys, tokens, or credentials in committed code.

## Agent Tag System

> Tags must be consistent across 4 files. If ANY tag is changed, check all four.

- [ ] **System prompt** (`prompts/voice-system-prompt.md`) — Tag documented for the agent?
- [ ] **Backend strip** (`routes/conversation.py: clean_for_tts()`) — Tag stripped before TTS?
- [ ] **Frontend parse** (`src/app.js: checkCanvasInStream()`) — Tag detected and action fired?
- [ ] **Frontend strip** (`src/app.js: stripCanvasTags()`) — Tag removed from display text?
- [ ] **VoiceSession parse** (`src/core/VoiceSession.js: _checkCmdsInStream()`) — Tag detected and event emitted?
- [ ] **VoiceSession strip** (`src/core/VoiceSession.js: _stripCmdTags()`) — Tag removed from display?

## Display Pipeline

- [ ] **escapeHtml before innerHTML** — Every path that sets innerHTML must escape first
- [ ] **stripCanvasTags complete** — All tags stripped (check: CANVAS, MUSIC, SUNO, SPOTIFY, SLEEP, REGISTER_FACE, SOUND, SESSION_RESET, code blocks)
- [ ] **No raw HTML in chat** — LLM output must never render as HTML (XSS vector)

## TTS Pipeline

- [ ] **clean_for_tts strips all tags** — Verify no [TAG:...] patterns are spoken aloud
- [ ] **Markdown stripped** — No #, *, -, tables read by TTS
- [ ] **Code blocks removed** — No ``` content spoken

## Voice Pipeline

- [ ] **STT mute/unmute** — If touching TTS or STT, verify mute cycle still works (mute on TTS start, unmute 600ms after TTS end)
- [ ] **Wake word** — If touching STT, verify wake word detection still works

## Tests

- [ ] **All tests pass** — `venv/bin/python3 -m pytest tests/ -q` (currently 457+)
- [ ] **Manual voice test** — If touching conversation flow, tag system, or TTS: test a live voice session
- [ ] **No new test failures** — Check that existing tests weren't broken

## Breaking Change Detection

These systems are fragile — changes in one file can silently break another:

| If you change... | Also check... |
|-----------------|---------------|
| Tag name/format | All 4 tag files (see above) |
| innerHTML path | escapeHtml applied |
| clean_for_tts | Tags not spoken, spoken text not stripped |
| System prompt | Context prefix, generated tracks injection |
| Profile schema | Frontend profile loading, default.json |
| EventBus event names | All subscribers (grep for event name) |
| Route paths | Frontend fetch URLs |
| Canvas manifest | CanvasMenu.loadManifest(), page list injection |
| Music metadata | Music player, track list injection |

## External PR Testing

For PRs from external contributors, follow the devtest workflow:

1. Review against this checklist first
2. Create `devtest/pr-<number>` branch from `dev`
3. Apply changes, run full test suite + manual test
4. If passes: merge the ORIGINAL PR (contributor keeps credit)
5. If fails: request changes with specific feedback
6. Delete devtest branch

---

## Quick Reference: Files That Must Stay In Sync

```
Tag System (4 files):
  prompts/voice-system-prompt.md     — Agent instructions
  routes/conversation.py             — clean_for_tts() + context prefix
  src/app.js                         — stripCanvasTags() + checkCanvasInStream()
  src/core/VoiceSession.js           — _stripCmdTags() + _checkCmdsInStream()

Display Security (1 rule):
  escapeHtml() BEFORE innerHTML — ALWAYS
```
