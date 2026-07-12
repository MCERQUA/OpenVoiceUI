# Voice latency — verified findings deferred for telemetry/refactor (2026-06-13)

From the Fable conversation-flow review. The low-risk set already shipped
(commit `2c08376`: tts_ready wake, no pre-send ping, greeting fast-path,
non-blocking status TTS, mic echoCancellation). These remaining items are
REAL but need measurement or a bigger refactor — do not blind-tune them.

## Status update — 2026-06-13 (After-23 pass, host)
- **#6 SHIPPED** (the one safe, no-decision win): added a 10s-TTL cache
  (`_cached_music_names`) scoped to the agent's per-turn `[Available tracks…]`
  context build in `routes/conversation.py`, so `get_music_files()`'s full
  dir-scan + per-file stat is no longer paid twice on every turn. The music
  UI/API still call `get_music_files()` directly (always fresh). Note: the
  *canvas manifest* half of #6 was already mtime-cached (`load_canvas_manifest`),
  so only the music read needed fixing. LIVE on test-dev (canary, hotpatch);
  source committed; rolls to fleet with the next OVU image rebuild.
- **#1, #2, #3, #4, #5, #7 remain telemetry/decision-gated** exactly as written
  below. They are NOT blind-tunes — each needs the named measurement or design
  decision first. Concrete next-step for each:
  - #1 — persist an empty-response **rate** metric (the counter
    `_consecutive_empty_responses` already exists) and A/B the 200ms settle vs a
    ~50ms asyncio.Event before changing it.
  - #2 — the live path was confirmed `WebSpeechSTT.js:41 = 1500ms`; add cutoff
    telemetry, then gate the conjunction/filler guard on it.
  - #3 — build the server-side STT-vs-just-spoken-TTS fuzzy-match content filter
    BEFORE shortening the 2500ms echo blackout; measure whether the shipped
    echoCancellation already reduced false echoes.
  - #4 — needs an explicit server "all-audio-flushed" event after the final TTS
    flush before `drainMs` can drop; gating on `_textDoneReceived` alone is unsafe.
  - #5 — verbatim-greeting pre-gen needs LLM-echo suppression to avoid double-speak.
  - #7 — revisit text_interim blocking flush only if interim turns feel laggy.

## 1. Abort-before-send settle (openclaw.py `_send_and_stream` ~line 1100)
On barge-in (stale sub exists) we poll sub-clearance at 100ms granularity
(up to 2s) then sleep an unconditional 200ms "settle". Adds 0.2–2.2s to the
restart-after-interrupt case. Fix: dispatcher signals sub-cleared via
asyncio.Event; shrink settle to ~50ms. RISK: the settle was added to stop
Z.AI empty-response accumulation — A/B with empty-rate logging before/after.

## 2. STT silence delay (WebSpeechSTT.js:41 = 1500ms governs the live path;
app.js:3196 has a separate 3500ms for the local-recorder path)
Trades responsiveness against #79 (mid-sentence cutoff). Safe lever:
grammatically-incomplete guard — don't fire when accumulatedText ends on a
conjunction/filler ("and", "but", "um", "so"). Tune with cutoff telemetry.

## 3. Echo cooldown 2500ms post-TTS (app.js ~5241, guard at ~3737)
Blanket STT blackout after TTS ends — kills barge-in and eats fast "yes"/"no"
replies. Proper fix: content-filter (drop STT results that fuzzy-match the
just-spoken TTS text — we know it server-side), THEN shorten timer to ~1200ms.
echoCancellation constraint (shipped) may already reduce the need — measure.

## 4. Audio drain timer 30s while stream open (app.js ~4995 `drainMs`)
Mic can stay muted up to 30s after the agent finished speaking if the stream
stays open (silent tail events). Gating on `_textDoneReceived` is NOT safe
as-is: the server's final TTS flush arrives AFTER text_done, and slow TTS
(Orpheus 20s+) would get cut. Needs an explicit server "all audio flushed"
event after the final flush; then drainMs can drop to ~800ms on that signal.

## 5. Verbatim-greeting TTS pre-generation (conversation.py ~1490)
When a profile defines a fixed conversation.greeting, the text is known
server-side — TTS could fire in parallel with the LLM round-trip, removing
the LLM entirely from greeting audio latency. RISK: must suppress the TTS of
the LLM's echo or it double-speaks. Gate on "profile has verbatim greeting".

## 6. Per-turn context build loads music library + canvas manifest from disk
(conversation.py ~1344-1395). Usually <50ms; add short TTL cache if the
timing log starts warning on big-library tenants.

## 7. text_interim flush still blocks (conversation.py ~1920, wait(30) per
chunk). Status TTS was made non-blocking; interim flush kept blocking
deliberately (bounded, rare, ordering-sensitive). Revisit with the tts_ready
sentinel pattern if interim turns feel laggy.

## Pre-existing noise (not from this work)
- `Failed to load plugin song-tagger: name already registered` on every boot —
  the song-tagger plugin duplicates the now-core routes/song_tagger.py
  blueprint. Loader catches it; plugin entry is stale. Uninstall the plugin
  entry or namespace the plugin blueprint.
