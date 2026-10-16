---
name: suno-music
description: Make AI music through OpenVoiceUI's Suno integration - full songs, 10-15s brand jingles, sound effects, extends, covers, added vocals or instrumentals, section edits, vocal stems and download links. Always via OVU tags or /api/suno. Needs SUNO_API_KEY in .env.
---

# Suno Music

OpenVoiceUI (OVU) wraps the Suno API (sunoapi.org) behind two voice tags and `/api/suno`. It
submits the job, saves the finished audio in `generated_music/` with its metadata (title,
lyrics, Suno ids) and adds it to the music player's Generated playlist.

- **Requires `SUNO_API_KEY` in the OVU `.env`**; without it every `/api/suno` call returns
  `{"action":"error","response":"SUNO_API_KEY not configured ..."}`.
- Examples use `http://localhost:5001`; if you changed PORT, use that port.
- `-H "X-Agent-Key: ${AGENT_API_KEY:-}"` is needed only when the install requires login (Clerk +
  `CANVAS_REQUIRE_AUTH`) and `AGENT_API_KEY` is set in `.env`; harmless otherwise.
- Everything is async: songs and jingles take about 45-90 s, sound effects about 20-60 s.

## Rules

- **Always go through OVU** (the tags or `/api/suno`). Never call the Suno provider directly and
  never use OpenClaw's built-in music tool: only OVU saves the file in `generated_music/` with
  metadata and a shareable URL. Anything else is paid for and invisible to the user.
- **Never write into `generated_music/` yourself, never invent a filename or URL.**
- **Every generation costs credits** (approximate: song or jingle ~12, since Suno makes two clips
  and OVU keeps the first; sound effect ~2.5; the live balance comes from the provider). Never
  fire the same request twice.
- **Do not say it is done** until you see the completion notice or `status` returns `complete`.
- **No real artist or band names** anywhere in a request; the provider rejects them. Describe the
  sound (genre, era, instruments, mood, tempo).
- **Never put `[` or `]` inside a tag.** The tag ends at the first `]`; the request is truncated
  and the rest is spoken aloud.

## Which method

| Request | Method |
|---|---|
| A song, during a conversation | `[SUNO_GENERATE:...]` |
| A jingle, audio logo, sting, intro or on-hold greeting | `[JINGLE_GENERATE:...]` |
| A sound effect (game blip, impact, ambient bed) | API `action=sfx` (no tag) |
| Structured lyrics, female vocal, guaranteed instrumental, several songs, canvas page or script | API `action=generate` |
| Change an existing song | API edit actions |
| Separate vocals from music | API `action=stem_separate` |

## Tag: [SUNO_GENERATE:...] (full song)

```
[SUNO_GENERATE:<description> | style:<style> | title:<title> | voice]
```

Options after the first `|` are optional. The browser submits it, shows a status banner and
polls until the song is saved. On the user's next turn you get
`[SYSTEM: Suno just finished generating "<title>" ...]`; if they want to hear it, reply with
`[MUSIC_PLAY:<title>]`. The tag is removed from speech; say it will be ready in about a minute.

- **Description mode** (normal): Suno writes the lyrics and picks the title, so `title:` is
  ignored and the filename follows Suno's title. Style plus description is cut to 500 chars.
- Vocals default to male and the tag cannot change that; use the API with `"vocal_gender":"f"`.
- "No vocals" is only a hint; use the API with `"instrumental":true` when it must be instrumental.
- **One tag per reply.** The browser tracks one job at a time and drops an identical prompt
  repeated within 60 s. For several songs, use the API.
- `voice`: sings the description as lyrics (lines separated by newlines or `/`) in the cloned
  voice stored as `voice_persona_id` in the active profile (see Personas); without it, a default voice.

```
[SUNO_GENERATE:upbeat corporate background music, warm piano and soft strings, no vocals, 110 BPM]
[SUNO_GENERATE:fun country pop song about a roofer who never misses a deadline | style:country pop, upbeat]
```

## Tag: [JINGLE_GENERATE:...] (10-15 s brand jingle)

```
[JINGLE_GENERATE:brand|style|gender|instrumental|repeat]
```

| Field | Values | Default |
|---|---|---|
| `brand` (required) | Name to sing, spelled as it should sound (`B-M-W`, `Open Voice U-I`) | - |
| `style` | A preset key, or freetext (only the first 3 words are used), or empty | random preset |
| `gender` | `m` or `f` | `m` |
| `instrumental` | `true` for an instrumental audio logo | `false` |
| `repeat` | How many times the brand is sung, `1`-`3` (`1` is shortest) | `2` |

Presets (`action=jingle_styles`): `rock`, `country`, `cinematic`, `gospel`, `lofi-hiphop`, `jazz`,
`edm`, `bluegrass`, `synthwave`, `latin`, `reggae`, `gritty-blues`, `orchestral`, `punk`, `rnb`.
With no style from the user, leave it empty.

Users call this a jingle, logo jingle, audio or sonic logo, brand sting, stinger, bumper,
station ID, podcast or channel intro, on-hold or IVR intro. Treat all of them as a jingle and ask
only for the brand, a style (or "surprise me") and male, female or instrumental.

**Clean the brand, keep the delivery.** When users type how it should be sung (`OOOOhhhPIN-VoIcE-
UUUUUIIIIII`, `BEEEEM-DOUBLE-YOUUU`), put the plain phonetic name in `brand` (`OpenVoice U-I`,
`B-M-W`) and the delivery in up to three freetext style words: stretched vowels -> `sustained
operatic vocal`; hyphenated letters -> `punchy spelled-out chant`; CAPS with `!!!` -> `shouty
stadium chant`; lowercase with `...` -> `breathy intimate whisper`. Anything that is not exactly
a preset key is freetext, so a preset cannot be combined with extra words. If unsure, ask one
short question before the paid generation.

```
[JINGLE_GENERATE:Acme Plumbing|country|m]
[JINGLE_GENERATE:OpenVoice U-I||f]
[JINGLE_GENERATE:Stark Industries|cinematic|m|true]
```

The server builds a short prompt (`<brand> <brand> <style word> vocal logo jingle, [Intro`, model
`V5`) designed to stop early. To omit the style word entirely (`bare`), use the API.

**Length is not guaranteed.** Suno often overshoots. Over 20 s, the `status` reply says so;
check `duration_seconds` via `action=list_jingles` and offer a retry rather than presenting a
long clip as a finished jingle.

## Sound effects: action=sfx

```bash
curl -s -X POST http://localhost:5001/api/suno \
  -H "Content-Type: application/json" -H "X-Agent-Key: ${AGENT_API_KEY:-}" \
  -d '{"action":"sfx","prompt":"retro 8-bit coin pickup blip","title":"coin pickup"}'
```

Fields: `prompt` (required, short and literal, max 500 chars), `title`, `loop` (`true` for a
seamless bed), `tempo` (BPM 1-300), `key` (e.g. `Cm`; omit for any).

- **Poll `action=status` yourself** until `complete`: sound effects have no callback, so your
  poll is what downloads the file.
- Saved to `generated_music/sfx/<file>`; not in the music player or `action=list`.
- The `url` that `status` returns (`/generated_music/sfx/<file>`) plays directly; add
  `?download=1` to save it. To bundle the clip with a canvas page instead, copy it and reference
  `/canvas-data/<file>`:
  `mkdir -p ~/.openclaw/workspace/canvas-pages/_data && cp ~/.openclaw/workspace/generated_music/sfx/coin-pickup.mp3 ~/.openclaw/workspace/canvas-pages/_data/`
- Generate once and reuse the file; never regenerate per play.

## Direct API

`POST /api/suno` with JSON containing `action` (reads also work as `GET /api/suno?action=...`).
Submit returns `{"action":"generating","job_id":"...","task_id":"...",...}`; any failure returns
`{"action":"error","response":"<reason>"}`.

### Song: action=generate

| Field | Notes |
|---|---|
| `prompt` | Description; treated as lyrics if it contains `[Verse`, `[Chorus`, `[Hook` or `[Bridge` |
| `lyrics` | Explicit lyrics (custom mode, where `title` is used) |
| `style` | Genre and sound |
| `title` | Custom mode only |
| `instrumental` | default `false` |
| `vocal_gender` | `m` (default) or `f` |
| `persona_id`, `persona_model` | See Personas |

Model `V5_5`. Section tags like `[Verse]` are fine here because they are not inside a voice tag.

```bash
curl -s -X POST http://localhost:5001/api/suno \
  -H "Content-Type: application/json" -H "X-Agent-Key: ${AGENT_API_KEY:-}" \
  -d '{"action":"generate","title":"From Rooftops to Results","style":"country pop, upbeat","vocal_gender":"f",
       "lyrics":"[Verse]\nStarted on a roof with a hammer in hand\n[Chorus]\nFrom rooftops to results"}'
```

**Jingle:** `{"action":"jingle","brand":"Acme Plumbing","style":"country","vocal_gender":"m","instrumental":false,"repeat":1}`
(also `bare`).

### Poll: action=status

```bash
curl -s "http://localhost:5001/api/suno?action=status&job_id=$JOB_ID" -H "X-Agent-Key: ${AGENT_API_KEY:-}"
```

- Poll every 10-15 s until `action` is `complete` or `failed`. When the provider's callback
  cannot reach this server (no public domain), polling is what saves the file, so an API job you
  never poll may never land.
- **Always pass `job_id`**; without it you get an arbitrary job.
- `complete`: `title`, `url`, `duration_seconds` (first reply), and a `response` to relay (for a
  jingle it notes a long clip). `failed`: `reason`. `not_found`: wrong id or the server restarted
  (jobs live in memory); check `action=list`.

### Lists and lookups

| Call | Returns |
|---|---|
| `GET /api/suno?action=list` | `songs[]` newest first: `filename`, `title`, `url`, `duration_seconds`, `lyrics`, `has_word_sync` |
| `GET /api/suno?action=list_jingles` | `jingles[]`: `filename`, `brand`, `style_key`, `vocal_gender`, `instrumental`, `duration_seconds` |
| `GET /api/music?action=latest` | `track`: `filename`, `title`, `url`, `download_url` (newest by file time) |
| `GET /api/suno?action=credits` | `credits`; on `action:"error"` the balance is unknown, say so and never guess |
| `DELETE /api/suno/song/<filename>` | Archives a song (renamed `.deleted`, never destroyed) |

`generated_music/` is `/app/runtime/generated_music/` in the OVU container,
`~/.openclaw/workspace/generated_music/` in the agent container, and `runtime/generated_music/`
in a native install. `generated_metadata.json` there holds per-file `title`, `kind` (song or
jingle), `lyrics`, `duration_seconds`, `suno_id`, `task_id` and jingle fields. Read it; do not edit it.

## Delivering a song (download link)

```
https://<your-domain>/generated_music/<filename>?download=1
```

- `<filename>` is the **exact** filename. `?download=1` saves the file; drop it to play inline.
- `/generated_music/` is public even when login is required, so this is the link to text or email.
- A `http://localhost:5001/...` link only works on that machine.
- **Latest song:** the `[Latest generated song: '<title>' (file: <file>) ...]` line in your
  context, or `GET /api/music?action=latest`.
- **A song by name:** look it up, never guess:
  ```bash
  curl -s "http://localhost:5001/api/suno?action=list" \
    | python3 -c "import sys,json; [print(s['filename'],'::',s['title']) for s in json.load(sys.stdin)['songs']]"
  ```
  If several match (e.g. `-2` variants), ask which one or offer the newest; give one link.
- The `[Available tracks — ... Generated (N): ...]` line lists titles for `[MUSIC_PLAY:<title>]`,
  not filenames.
- Never use `/music/<file>` for a generated song (that is the uploaded library and returns
  `{"error":"Track not found"}`), never emit a `[CANVAS:...]` download page (none exists), never
  paste the whole list of URLs.

## Editing existing songs

Name the source with `filename` (preferred; OVU finds its Suno ids), or `audio_id` / `task_id`,
or `song_url`. Each returns a `job_id`; poll `status`; the result is a new track.

| Action | Needs | Fields |
|---|---|---|
| `extend` | Suno audio id | `prompt`, `style`, `title`, `continue_at` (s), `model`; omit `prompt` and `continue_at` for provider defaults |
| `cover` | Public URL | `style` (enables custom mode), `prompt`, `title`, `instrumental`, `model` |
| `add_vocals` | Public URL, `prompt` | `style`, `title`, `vocal_gender`, `negative_tags`, `model` |
| `add_instrumental` | Public URL | `tags` or `style`, `title`, `negative_tags`, `model` |
| `replace_section` | Task id + audio id, `prompt` | `start_time`, `end_time` (s, end > start), `tags`, `title`, `full_lyrics`; provider window roughly 6-60 s |

```bash
curl -s -X POST http://localhost:5001/api/suno \
  -H "Content-Type: application/json" -H "X-Agent-Key: ${AGENT_API_KEY:-}" \
  -d '{"action":"extend","filename":"shop-light.mp3","prompt":"a final chorus that builds","continue_at":120}'
```

- "Public URL": the provider downloads the source from the internet. OVU builds it from
  `PUBLIC_BASE_URL` or `DOMAIN` in `.env`; without a public address, pass `song_url` (https).
- Tracks with no stored Suno ids (old or uploaded files) cannot be edited or split.
- `extend`, `cover`, `add_vocals`, `add_instrumental` default to model `V5_5`.

## Stems: action=stem_separate

`{"action":"stem_separate","filename":"shop-light.mp3"}`

- Billed per submit but idempotent: stems already on disk return `complete` at once, and a
  separation in progress is reused, not resubmitted.
- Reply: `complete` with `vocal_url` + `instrumental_url`, or `generating` with `task_id`; poll
  `{"action":"stem_result","task_id":"<id>","filename":"<same file>"}`. OVU also finishes the
  download in the background.
- Files: `canvas-pages/_data/stems/<name>-vocal.mp3` and `-instrumental.mp3`, served at
  `/canvas-data/stems/...`.
- `type` defaults to `separate_vocal`. `split_stem` / `split_stem_advanced` (+ `stem_name`) are
  passed through, but OVU keeps only the vocal and instrumental files.
- `code: "stem_ids_missing"`: the track has no Suno ids.

## Lyrics, timing and helpers

- Lyrics: `action=list` (`lyrics`) or the metadata file.
- Word timings are fetched once, automatically, when a song completes, and cached at
  `canvas-pages/_data/singalong/lyrics-<name>.json` (used by `[CANVAS:sing-along]`).
  `action=timestamped_lyrics` is a paid call; read the cache first.
- `action=style_boost` `{content}` returns an enhanced style string (`boosted_style`).
- `action=song_details` `{filename}` returns the provider's record (debugging).
- `generate_lyrics` `{prompt}`, `wav_convert` `{filename}` and `music_video` `{filename}` return
  a `job_id`. Poll `action=status&job_id=...` until `complete`: lyrics come back as `lyrics`
  (`[{title, text}]`); the WAV and the video come back as `url`
  (`/generated_music/wav/<file>`, `/generated_music/video/<file>`, add `?download=1` to save).

## Personas and cloned voices

On `action=generate`, `persona_id` + `persona_model`: `style_persona` (default, a reusable style
identity) or `voice_persona` (a cloned singing voice; forces custom mode, so `prompt` is sung as
lyrics).

**Style persona:** `action=generate_persona` with `filename` (or `task_id` + `audio_id`), `name`
and `description` (required), optional `style`, `vocal_start` / `vocal_end` (s, default 0-30).
`personaId` is in the returned `data`. One persona per audio clip.

**Cloned voice** (only the user's own voice, with consent; audio goes to the provider's file host):
1. `file_upload` `{fileUrl}`: re-hosts audio already at a public URL; returns the hosted URL.
2. `voice_validate` `{voiceUrl, vocalStartS, vocalEndS}` (whole seconds, optional `language`).
3. `voice_validate_info` `{task_id}`: poll for the phrase to sing.
4. The user records the phrase; host it with `file_upload`.
5. `voice_generate` `{task_id, verifyUrl}` (+ `voiceName`, `description`, `style`, `singerSkillLevel`).
6. `voice_info` `{task_id}` returns the `voiceId`; `voice_check` `{task_id}` returns `isAvailable`.

Cloned voices have been observed to expire within days: run `voice_check` first and re-clone if
needed; a style persona minted from a song in that voice is a durable fallback. Keep ids in your
notes. The tag's `| voice` reads `voice_persona_id` from the active profile file (OVU runtime
`profiles/<id>.json`), which the profile API does not manage, so `persona_id` on the API is the
reliable path.

## When things fail

Whether refused at submit (out of credits, bad input) or failed during generation, you get on
the user's next turn: `[SYSTEM: Music generation you started did NOT go through: '<title>' (<reason>) ...]`.
If you promised it, correct that now: say plainly it did not go through, no technical detail, no
new time promise, and offer to retry (unless credits ran out).

| Problem | Fix |
|---|---|
| Jingle came out as a full song | Use `JINGLE_GENERATE`, not `SUNO_GENERATE` |
| Jingle still long | Expected sometimes; offer a retry, or API with `repeat: 1` |
| Brand sung as one word | Spell it out: `Open Voice U-I` |
| Part of a tag was spoken | A `]` inside the tag ended it early |
| `/api/suno/generate` 404 | Use `/api/suno` with `"action":"generate"` |
| API song never appeared | Poll `action=status&job_id=...` until `complete` |

Good prompts name genre, instruments, mood, tempo and vocals, e.g. `lo-fi hip hop beats for
focus, chill instrumental, soft drums, 85 BPM` or `epic cinematic orchestral music, heavy
strings and percussion, no vocals`.
