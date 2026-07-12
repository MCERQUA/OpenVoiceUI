"""
routes/suno.py — Suno AI Song Generation Blueprint

Provides endpoints for generating songs via Suno API (sunoapi.org).
Generated songs land in generated_music/ and show up in the music player.

Endpoints:
  GET/POST  /api/suno              (action: generate|jingle|sfx|status|list|credits)
  POST      /api/suno/callback     (webhook from sunoapi.org)
  GET/POST  /api/suno/completed    (frontend polls for completed songs)

Agent trigger:
  Include [SUNO_GENERATE:prompt text here] in a response to kick off generation.
  The frontend detects the tag, calls /api/suno?action=generate, and polls for completion.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests as http_requests
from flask import Blueprint, jsonify, request

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------

from services.paths import GENERATED_MUSIC_DIR

GENERATED_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_METADATA_FILE = GENERATED_MUSIC_DIR / 'generated_metadata.json'

# SFX (action=sfx) land in a dedicated subdir so they are NOT mixed in with the
# music library/player. Kept under generated_music/ so it's already mounted +
# web-served (/generated_music/sfx/<file>) with no compose/mount changes. The
# music list (_action_list) and music metadata/queue intentionally skip these.
GENERATED_SOUNDS_DIR = GENERATED_MUSIC_DIR / 'sfx'
GENERATED_SOUNDS_DIR.mkdir(parents=True, exist_ok=True)

SUNO_API_KEY = os.environ.get('SUNO_API_KEY', '')
SUNO_API_BASE = 'https://api.sunoapi.org'
SUNO_WEBHOOK_SECRET = os.environ.get('SUNO_WEBHOOK_SECRET', '')
SUNO_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB cap on audio downloads

# Callback URL: explicit > auto-derived from DOMAIN > empty
# sunoapi.org requires callBackUrl; auto-derive from DOMAIN if not set explicitly.
_domain = os.environ.get('DOMAIN', '')
SUNO_CALLBACK_URL = (
    os.environ.get('SUNO_CALLBACK_URL')
    or (f'https://{_domain}/api/suno/callback' if _domain else '')
)

# ---------------------------------------------------------------------------
# In-memory job tracking (single-worker deployment)
# ---------------------------------------------------------------------------

suno_jobs: dict = {}  # job_id -> {status, prompt, title, style, created_at, task_id, ...}
completed_songs_queue: list = []  # [{song_id, title, job_id, completed_at, url}, ...]
failed_songs_queue: list = []     # [{job_id, kind, brand, reason, failed_at}, ...]
_suno_lock = threading.Lock()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-call provider receipt (JamFlow Watch live pulse)
#
# Host-side provider calls emit one JSONL row per call via provider-call-log.sh
# so the Watch map lights the provider node the instant a call happens. This
# container can't reach the host's canonical provider-calls dir, but the
# monitoring-events mount (host: /home/mike/monitoring/events) is RW — receipts
# drop into a provider-calls/ subdir there, same one-line schema, and the Watch
# tailer scans that drop dir too. Best-effort: NEVER blocks or fails a
# generation; on containers without the mount the write is invisible/harmless.
# ---------------------------------------------------------------------------
PROVIDER_RECEIPTS_DIR = Path(os.environ.get(
    'PROVIDER_RECEIPTS_DIR', '/app/runtime/monitoring-events/provider-calls'))
_RECEIPT_TENANT = os.environ.get('JAMBOT_TENANT') or os.environ.get('HOST_TENANT', '') or 'unknown'


def _provider_receipt(op: str, units: str = '1') -> None:
    """Append one {ts,provider,tenant,op,units} receipt row — fire-and-forget."""
    try:
        PROVIDER_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        row = json.dumps({'ts': now.isoformat(timespec='seconds'),
                          'provider': 'suno', 'tenant': _RECEIPT_TENANT,
                          'op': op, 'units': units})
        with open(PROVIDER_RECEIPTS_DIR / f"suno-{now.strftime('%Y-%m-%d')}.jsonl", 'a') as f:
            f.write(row + '\n')
    except Exception:  # never let telemetry touch the generation path
        pass
    # JamBot Books: also record as an api_call to the host-tailed books queue
    # (the receipt path above is unmounted on most containers; this one isn't).
    try:
        from services.jambot_books_hook import record_provider_call
        record_provider_call('suno', endpoint='/generate', op=op, units=units)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Jingle style presets — proven 2026-05-05. The recipe is:
#   customMode: false
#   instrumental: false
#   model: V5
#   prompt: "<brand> vocal logo jingle, <STYLE_DESCRIPTOR>, [Intro"
# The truncated "[Intro" (no closing bracket) signals "this is just an intro"
# so Suno renders ~10-15s instead of a full song.
# ---------------------------------------------------------------------------
JINGLE_STYLE_PRESETS = {
    # `keyword` = 1-3 word adjective inserted before "vocal logo jingle" in v4 recipe.
    # Minimal style signal that adds variety without lengthening the prompt enough
    # to break the truncated `, [Intro` short-form trigger.
    'rock':          {'keyword': 'rock'},
    'country':       {'keyword': 'country'},
    'cinematic':     {'keyword': 'cinematic trailer'},
    'gospel':        {'keyword': 'gospel'},
    'lofi-hiphop':   {'keyword': 'lo-fi hip hop'},
    'jazz':          {'keyword': 'jazz'},
    'edm':           {'keyword': 'EDM festival'},
    'bluegrass':     {'keyword': 'bluegrass'},
    'synthwave':     {'keyword': '80s synthwave'},
    'latin':         {'keyword': 'Latin bossa'},
    'reggae':        {'keyword': 'reggae'},
    'gritty-blues':  {'keyword': 'delta blues'},
    'orchestral':    {'keyword': 'epic orchestral'},
    'punk':          {'keyword': 'punk rock'},
    'rnb':           {'keyword': 'R&B'},
}
_JINGLE_GENDER_WORD = {'m': 'male', 'f': 'female'}


def _is_safe_download_url(url: str) -> bool:
    """Reject URLs that point to private/reserved IP ranges (SSRF protection)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname or parsed.scheme not in ('http', 'https'):
            return False
        # Resolve hostname to IP and check if private/reserved
        for info in socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP):
            addr = info[4][0]
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                logger.warning(f'SSRF blocked: {url} resolves to private IP {addr}')
                return False
        return True
    except (ValueError, socket.gaierror, OSError) as exc:
        logger.warning(f'SSRF check failed for {url}: {exc}')
        return False


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

suno_bp = Blueprint('suno', __name__)

# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _load_generated_metadata() -> dict:
    if GENERATED_METADATA_FILE.exists():
        try:
            with open(GENERATED_METADATA_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_generated_metadata(metadata: dict) -> None:
    """Persist generated music metadata (atomic write)."""
    tmp = GENERATED_METADATA_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(metadata, indent=2))
    tmp.replace(GENERATED_METADATA_FILE)


def _add_song_to_metadata(filename: str, title: str, prompt: str, style: str,
                          duration: float = 0, song_id: str = '',
                          kind: str = 'song', extra: dict = None,
                          task_id: str = '') -> None:
    """Write a new song entry to generated_metadata.json in the format music.py expects.

    `kind` is 'song' (full track) or 'jingle' (10-15s logo jingle). `extra` is merged
    into the entry — used by jingles to record brand, style_key, vocal_gender.
    """
    metadata = _load_generated_metadata()
    entry = {
        'title': title,
        'artist': 'Clawdbot AI',
        'description': prompt[:200] if prompt else 'AI-generated track',
        'genre': _guess_genre(style or prompt),
        'energy': 'high',
        'duration_seconds': round(duration, 1) if duration else 0,
        'fun_facts': [],
        'dj_intro_hints': [],
        'dj_backstory': f'Generated by Clawdbot from prompt: {prompt[:100]}' if prompt else '',
        'made_by': 'Clawdbot',
        'created_date': datetime.now().strftime('%Y-%m-%d'),
        'suno_id': song_id,
        'task_id': task_id,
        'kind': kind,
    }
    if extra:
        entry.update(extra)
    metadata[filename] = entry
    _save_generated_metadata(metadata)


def _is_uuid(s: str) -> bool:
    """Check if string looks like a UUID (hex-hex-hex-hex-hex pattern)."""
    import re
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', s.strip(), re.IGNORECASE))


def _slugify_title(title: str) -> str:
    """Convert a song title to a safe filename slug (no extension)."""
    import re
    import unicodedata
    # If the title is a UUID (Suno sometimes returns song ID as title), reject it
    if _is_uuid(title):
        return 'generated-track'
    # Normalize unicode (e.g., smart quotes → ascii)
    s = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode('ascii')
    # Lowercase, replace non-alnum with hyphens, collapse multiples, strip edges
    s = re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
    return s[:80] or 'generated-track'


def _unique_filename(directory: Path, base: str, ext: str = '.mp3') -> str:
    """Return a unique filename in directory, appending -2, -3, etc. if needed."""
    candidate = f'{base}{ext}'
    if not (directory / candidate).exists():
        return candidate
    counter = 2
    while (directory / f'{base}-{counter}{ext}').exists():
        counter += 1
    return f'{base}-{counter}{ext}'


def _guess_genre(text: str) -> str:
    """Rough genre guess from prompt keywords."""
    if not text:
        return 'Unknown'
    t = text.lower()
    for genre, keywords in [
        ('Hip-Hop', ['hip hop', 'hiphop', 'rap', 'trap', 'beats']),
        ('Electronic', ['electronic', 'edm', 'techno', 'house', 'synth', 'dance']),
        ('Rock', ['rock', 'metal', 'guitar', 'punk', 'grunge']),
        ('Pop', ['pop', 'catchy', 'radio', 'chorus']),
        ('Country', ['country', 'western', 'cowboy', 'twang']),
        ('Reggae', ['reggae', 'ska', 'dub', 'jamaican']),
        ('Jazz', ['jazz', 'blues', 'soul', 'funk', 'groove']),
    ]:
        if any(kw in t for kw in keywords):
            return genre
    return 'Unknown'


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@suno_bp.route('/api/suno', methods=['GET', 'POST'])
def handle_suno():
    """
    Unified Suno endpoint.
    action=generate  — Submit a generation job
    action=status    — Poll job status (downloads when ready)
    action=list      — List generated songs
    action=credits   — Check API credits
    """
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        action = body.get('action') or request.args.get('action', 'list')
        # Allow POST body params to override query params
        _q = lambda k, default='': body.get(k) or request.args.get(k, default)
    else:
        body = {}
        action = request.args.get('action', 'list')
        _q = lambda k, default='': request.args.get(k, default)

    if not SUNO_API_KEY:
        return jsonify({'action': 'error', 'response': 'SUNO_API_KEY not configured — add it to .env'})

    try:
        # ------------------------------------------------------------------
        if action == 'list':
            return _action_list()

        elif action == 'generate':
            return _action_generate(_q, body)

        elif action == 'jingle':
            return _action_jingle(_q, body)

        elif action == 'sfx':
            return _action_sfx(_q, body)

        elif action == 'list_jingles':
            return _action_list_jingles()

        elif action == 'jingle_styles':
            return jsonify({
                'action': 'jingle_styles',
                'styles': sorted(JINGLE_STYLE_PRESETS.keys()),
                'previews': {k: v['keyword'] for k, v in JINGLE_STYLE_PRESETS.items()},
            })

        elif action == 'status':
            return _action_status(_q('job_id') or _q('song_id'))

        elif action == 'credits':
            return _action_credits()

        # --- Studio editing / processing actions (2026-06-25) ---------------
        elif action == 'extend':
            return _action_extend(_q, body)

        elif action == 'cover':
            return _action_cover(_q, body)

        elif action == 'add_vocals':
            return _action_add_vocals(_q, body)

        elif action == 'add_instrumental':
            return _action_add_instrumental(_q, body)

        elif action == 'replace_section':
            return _action_replace_section(_q, body)

        elif action == 'generate_lyrics':
            return _action_generate_lyrics(_q, body)

        elif action == 'timestamped_lyrics':
            return _action_timestamped_lyrics(_q, body)

        elif action == 'wav_convert':
            return _action_wav_convert(_q, body)

        elif action == 'stem_separate':
            return _action_stem_separate(_q, body)

        elif action == 'style_boost':
            return _action_style_boost(_q, body)

        elif action == 'music_video':
            return _action_music_video(_q, body)

        elif action == 'song_details':
            return _action_song_details(_q, body)

        else:
            return jsonify({'action': 'error', 'response': f"Unknown action '{action}'. Use: generate, jingle, sfx, list_jingles, jingle_styles, status, list, credits, extend, cover, add_vocals, add_instrumental, replace_section, generate_lyrics, timestamped_lyrics, wav_convert, stem_separate, style_boost, music_video, song_details"})

    except Exception as exc:
        logger.exception('Suno endpoint error')
        return jsonify({'action': 'error', 'response': f'Suno error: {exc}'}), 500


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def _action_list():
    """Return all generated songs with metadata."""
    metadata = _load_generated_metadata()
    songs = []
    for f in sorted(GENERATED_MUSIC_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in {'.mp3', '.wav', '.ogg', '.m4a'}:
            meta = metadata.get(f.name, {})
            songs.append({
                'filename': f.name,
                'title': meta.get('title', f.stem),
                'genre': meta.get('genre', 'Unknown'),
                'description': meta.get('description', ''),
                'duration_seconds': meta.get('duration_seconds', 0),
                'made_by': meta.get('made_by', 'Clawdbot'),
                'created_date': meta.get('created_date', ''),
                'url': f'/generated_music/{f.name}',
                'size_bytes': f.stat().st_size,
            })
    return jsonify({
        'action': 'list',
        'count': len(songs),
        'songs': songs,
        'response': f'Got {len(songs)} AI-generated tracks in the vault!',
    })


def _action_generate(_q, body: dict):
    """Submit a song generation job to Suno API."""
    prompt = _q('prompt') or body.get('prompt', '')
    style = _q('style') or body.get('style', '')
    title = _q('title') or body.get('title', '')
    lyrics = _q('lyrics') or body.get('lyrics', '')
    instrumental = (_q('instrumental') or str(body.get('instrumental', 'false'))).lower() == 'true'
    vocal_gender = _q('vocal_gender') or body.get('vocal_gender', 'm')

    if not prompt and not lyrics and not style:
        return jsonify({'action': 'error', 'response': 'Need a prompt, lyrics, or style — tell me what kind of song to make.'})

    # Determine mode: custom (explicit lyrics) vs description (Suno writes lyrics)
    if lyrics:
        song_prompt = lyrics
        has_lyrics = True
    elif '[Verse' in prompt or '[Chorus' in prompt or '[Hook' in prompt or '[Bridge' in prompt:
        song_prompt = prompt
        has_lyrics = True
    else:
        # Description mode — Suno auto-generates lyrics from the description
        combined = f'{style}. {prompt}' if style and prompt else (style or prompt)
        song_prompt = combined[:500]
        has_lyrics = False

    # Build Suno API request
    if has_lyrics:
        request_body = {
            'prompt': song_prompt,
            'customMode': True,
            'instrumental': instrumental,
            'model': 'V5_5',
            'vocalGender': vocal_gender,
            'negativeTags': 'low quality, mumbling, distorted, off-key',
            'style': style or 'Catchy, Radio-friendly, Professional',
        }
        if title:
            request_body['title'] = title
    else:
        request_body = {
            'prompt': song_prompt,
            'customMode': False,
            'instrumental': instrumental,
            'model': 'V5_5',
            'vocalGender': vocal_gender,
        }

    if SUNO_CALLBACK_URL:
        request_body['callBackUrl'] = SUNO_CALLBACK_URL

    logger.info(f'Suno generate: mode={"custom" if has_lyrics else "auto"} prompt={song_prompt[:80]}')

    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}/api/v1/generate',
            headers={'Authorization': f'Bearer {SUNO_API_KEY}', 'Content-Type': 'application/json'},
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno API response: {resp.status_code} {resp.text[:300]}')

        if resp.status_code == 200:
            data = resp.json()
            if data.get('code') == 200 and data.get('data', {}).get('taskId'):
                task_id = data['data']['taskId']
                job_id = str(uuid.uuid4())
                suno_jobs[job_id] = {
                    'status': 'generating',
                    'prompt': prompt,
                    'title': title,
                    'style': style,
                    'task_id': task_id,
                    'created_at': time.time(),
                }
                _provider_receipt('song')   # live Watch pulse — task accepted, credits committed
                return jsonify({
                    'action': 'generating',
                    'job_id': job_id,
                    'task_id': task_id,
                    'response': f"Cooking! '{title or 'your track'}' is being generated — check back in 30-60 seconds.",
                    'estimated_seconds': 45,
                })
            else:
                return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})
        else:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})

    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _action_sfx(_q, body: dict):
    """Generate a short non-vocal sound effect / ambient stinger.

    Wraps sunoapi.org's "Sounds Generation (V5)" endpoint
    (POST /api/v1/generate/sounds, 2.5 credits). This is NOT a jingle or a song
    — it produces game SFX, UI blips, stingers, ambient beds, etc. with no
    vocals. Returns a taskId in the same shape as /generate, so the normal
    `action=status` poller downloads + saves the clip when ready.

    Inputs:
      prompt   — required, what the sound should be (max 500 chars).
                 e.g. "retro 8-bit coin pickup blip", "wooden mallet thwack",
                 "ominous low brass sting", "arcade game-over jingle no vocals"
      title    — optional label used for the saved filename + metadata.
      loop     — optional bool; soundLoop (seamless looping bed). Default false.
      tempo    — optional int 1-300; soundTempo (BPM). Omit for auto.
      key      — optional musical key (Any, Cm, C#m, ... B). Omit for Any.
    """
    prompt = (_q('prompt') or body.get('prompt', '')).strip()
    if not prompt:
        return jsonify({'action': 'error', 'response': "Need a description of the sound — e.g. 'retro 8-bit coin pickup blip'."})
    prompt = prompt[:500]

    title = (_q('title') or body.get('title', '')).strip()

    loop_raw = _q('loop') or body.get('loop', False)
    if isinstance(loop_raw, bool):
        sound_loop = loop_raw
    else:
        sound_loop = str(loop_raw).lower() in ('true', '1', 'yes')

    request_body = {
        'prompt': prompt,
        # V5_5 matches the proven story.py path (routes/story.py gen_suno_sound,
        # shipped 2026-05-28). sunoapi.org docs say "V5 only" for this endpoint
        # but production uses V5_5 successfully with better quality — match it.
        'model': 'V5_5',
        'soundLoop': sound_loop,
    }

    # Optional tempo (BPM 1-300)
    tempo_raw = _q('tempo') or body.get('tempo', '')
    if tempo_raw not in (None, ''):
        try:
            tempo = int(tempo_raw)
            if 1 <= tempo <= 300:
                request_body['soundTempo'] = tempo
        except (TypeError, ValueError):
            pass

    # Optional musical key
    key = (_q('key') or body.get('key', '')).strip()
    if key and key.lower() != 'any':
        request_body['soundKey'] = key

    # NOTE: deliberately NO callBackUrl for SFX. SFX complete via polling
    # (action=status), which routes them to the sounds subdir and keeps them out
    # of the music library. The webhook callback path registers results as music,
    # so skipping it prevents SFX from leaking into the music player.

    logger.info(f'Suno sfx: loop={sound_loop} prompt={prompt[:80]}')

    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}/api/v1/generate/sounds',
            headers={'Authorization': f'Bearer {SUNO_API_KEY}', 'Content-Type': 'application/json'},
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno sfx response: {resp.status_code} {resp.text[:300]}')

        if resp.status_code == 200:
            data = resp.json()
            if data.get('code') == 200 and data.get('data', {}).get('taskId'):
                task_id = data['data']['taskId']
                job_id = str(uuid.uuid4())
                suno_jobs[job_id] = {
                    'status': 'generating',
                    'prompt': prompt,
                    'title': title or prompt[:60],
                    'style': 'sfx',
                    'kind': 'sfx',
                    'task_id': task_id,
                    'created_at': time.time(),
                }
                _provider_receipt('sfx')   # live Watch pulse — task accepted, credits committed
                return jsonify({
                    'action': 'generating',
                    'job_id': job_id,
                    'task_id': task_id,
                    'kind': 'sfx',
                    'response': f"Generating sound: '{title or prompt[:40]}' — check back in ~20-40 seconds.",
                    'estimated_seconds': 30,
                })
            else:
                return jsonify({'action': 'error', 'response': f"Suno SFX error: {data.get('msg', 'Unknown error')}"})
        else:
            return jsonify({'action': 'error', 'response': f'Suno SFX HTTP {resp.status_code}: {resp.text[:200]}'})

    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _action_jingle(_q, body: dict):
    """Generate a 10-15 second vocal-logo jingle of a brand name.

    Uses the proven recipe (2026-05-05): customMode=false, instrumental=false,
    model=V5, prompt ending in literal `, [Intro` (truncated half-tag) — that
    suffix tells Suno "this is just an intro" so it stops at ~10-15s instead of
    rendering a full song.

    Inputs:
      brand          — required, the brand/company name to be sung
      style          — preset key (see JINGLE_STYLE_PRESETS) OR freetext descriptor.
                       Random preset chosen if omitted.
      vocal_gender   — m | f (default m). Ignored if instrumental=true.
      instrumental   — true to render the jingle as an instrumental audio-logo.
    """
    import random
    brand = (_q('brand') or body.get('brand', '')).strip()
    if not brand:
        return jsonify({'action': 'error', 'response': "Need a brand/company name to sing."})

    style_in = (_q('style') or body.get('style', '')).strip()
    vocal_gender = (_q('vocal_gender') or body.get('vocal_gender', 'm')).lower()
    if vocal_gender not in ('m', 'f'):
        vocal_gender = 'm'

    instrumental_raw = _q('instrumental') or body.get('instrumental', False)
    if isinstance(instrumental_raw, bool):
        instrumental = instrumental_raw
    else:
        instrumental = str(instrumental_raw).lower() in ('true', '1', 'yes')

    # repeat = how many times the brand is sung (1 = shorter ~8-12s, 2 = ~12-15s, default 2)
    repeat_raw = _q('repeat') or body.get('repeat', 2)
    try:
        repeat = max(1, min(3, int(repeat_raw)))
    except (TypeError, ValueError):
        repeat = 2

    # Resolve style: preset key, freetext, random preset, or BARE (no style at all).
    # `bare` flag forces zero style insertion — produces shortest-possible output.
    bare_raw = _q('bare') or body.get('bare', False)
    bare = bool(bare_raw) if isinstance(bare_raw, bool) else str(bare_raw).lower() in ('true', '1', 'yes')
    style_key = ''
    if bare:
        style_in = ''  # force empty so style_keyword resolves to ''
    elif style_in and style_in.lower() in JINGLE_STYLE_PRESETS:
        style_key = style_in.lower()
    elif not style_in:
        style_key = random.choice(list(JINGLE_STYLE_PRESETS.keys()))

    # Resolve short style keyword (1-3 words). For freetext, use as-is but trim hard.
    if style_key:
        style_keyword = JINGLE_STYLE_PRESETS[style_key]['keyword']
    else:
        # freetext — strip to first ~3 words to keep the prompt minimal
        style_keyword = ' '.join(style_in.split()[:3]) if style_in else ''
    style_descriptor = style_keyword  # for metadata logging

    # Recipe v4 (2026-05-05): minimalist — matches Mike's verified-working pattern from
    # 2026-05-05 with a single short style keyword inserted before "vocal logo jingle".
    # The truncated `, [Intro` (no closing bracket) signals "this is just an intro" so
    # Suno renders 10-15s instead of a full song. Recipe history:
    #   v1 — multi-clause style descriptor → 30-141s (too long, descriptor inflated output)
    #   v2 — customMode=true skeleton lyrics → 531 refunded (lyrics too short)
    #   v3 — customMode=false with inline [Intro]...[End] block → 400 malformed
    #   v4 — minimal keyword + Mike's exact `, [Intro` truncation
    negative_tags = 'verse, chorus, full song, long intro, padding, extended outro, lengthy, repeat hook, second verse'
    # v5 uses model V5 (not V5.5) — V5.5 tends to render fuller/longer outputs;
    # V5 hits the 10-15s short-form sweet spot more reliably with the truncated
    # `, [Intro` trick.
    if instrumental:
        kw_phrase = f'{style_keyword} ' if style_keyword else ''
        jingle_prompt = f'{brand} {kw_phrase}instrumental audio logo, [Intro'
        request_body = {
            'prompt': jingle_prompt,
            'customMode': False,
            'instrumental': True,
            'model': 'V5',
            'negativeTags': negative_tags + ', vocals, lyrics, singing',
        }
    else:
        kw_phrase = f'{style_keyword} ' if style_keyword else ''
        jingle_prompt = f'{brand} {kw_phrase}vocal logo jingle, [Intro'
        request_body = {
            'prompt': jingle_prompt,
            'customMode': False,
            'instrumental': False,
            'model': 'V5',
            'vocalGender': vocal_gender,
            'negativeTags': negative_tags,
        }

    if SUNO_CALLBACK_URL:
        request_body['callBackUrl'] = SUNO_CALLBACK_URL

    logger.info(f'Suno jingle: brand={brand!r} style_key={style_key!r} '
                f'gender={vocal_gender} instrumental={instrumental} repeat={repeat} '
                f'prompt={jingle_prompt[:120]!r}')

    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}/api/v1/generate',
            headers={'Authorization': f'Bearer {SUNO_API_KEY}', 'Content-Type': 'application/json'},
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno jingle API response: {resp.status_code} {resp.text[:300]}')

        if resp.status_code != 200:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})

        data = resp.json()
        if data.get('code') != 200 or not data.get('data', {}).get('taskId'):
            return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})

        task_id = data['data']['taskId']
        job_id = str(uuid.uuid4())
        suno_jobs[job_id] = {
            'status': 'generating',
            'prompt': jingle_prompt,
            'title': f'{brand} Jingle',
            'style': style_descriptor,
            'task_id': task_id,
            'created_at': time.time(),
            'kind': 'jingle',
            'brand': brand,
            'style_key': style_key,
            'vocal_gender': vocal_gender,
            'instrumental': instrumental,
            'repeat': repeat,
        }
        _provider_receipt('jingle')   # live Watch pulse — task accepted, credits committed
        return jsonify({
            'action': 'generating',
            'job_id': job_id,
            'task_id': task_id,
            'kind': 'jingle',
            'brand': brand,
            'style_key': style_key,
            'response': f"Cooking your '{brand}' jingle ({style_key or 'custom'}) — ready in ~45-60s.",
            'estimated_seconds': 45,
        })

    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _action_list_jingles():
    """Return only generations tagged kind=jingle, newest first."""
    metadata = _load_generated_metadata()
    jingles = []
    for f in sorted(GENERATED_MUSIC_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() not in {'.mp3', '.wav', '.ogg', '.m4a'}:
            continue
        meta = metadata.get(f.name, {})
        if meta.get('kind') != 'jingle':
            continue
        jingles.append({
            'filename': f.name,
            'title': meta.get('title', f.stem),
            'brand': meta.get('brand', ''),
            'style_key': meta.get('style_key', ''),
            'vocal_gender': meta.get('vocal_gender', ''),
            'instrumental': meta.get('instrumental', False),
            'duration_seconds': meta.get('duration_seconds', 0),
            'created_date': meta.get('created_date', ''),
            'description': meta.get('description', ''),
            'url': f'/generated_music/{f.name}',
            'size_bytes': f.stat().st_size,
        })
    return jsonify({
        'action': 'list_jingles',
        'count': len(jingles),
        'jingles': jingles,
        'response': f'Got {len(jingles)} jingles in the vault.',
    })


def _action_status(job_id: str):
    """Poll generation status; downloads song when Suno reports SUCCESS."""
    if not job_id:
        # Return status of most recent job
        if suno_jobs:
            job_id = max(suno_jobs.keys())
        else:
            return jsonify({'action': 'status', 'status': 'no_jobs', 'response': 'No songs cooking right now.'})

    if job_id not in suno_jobs:
        return jsonify({'action': 'status', 'status': 'not_found', 'response': "Can't find that job."})

    job = suno_jobs[job_id]

    # If already complete, just report
    if job.get('status') == 'complete':
        return jsonify({
            'action': 'complete',
            'status': 'complete',
            'job_id': job_id,
            'song_id': job.get('song_id', ''),
            'title': job.get('title', 'Generated Track'),
            'url': job.get('url', ''),
            'response': f"Done! '{job.get('title', 'your track')}' is ready to spin!",
        })

    elapsed = time.time() - job['created_at']

    # Don't bother polling Suno for the first 20 seconds
    if elapsed < 20:
        return jsonify({
            'action': 'status',
            'status': 'generating',
            'elapsed_seconds': int(elapsed),
            'response': f'Still cooking — about {max(0, 30 - int(elapsed))} more seconds...',
        })

    task_id = job.get('task_id')
    if not task_id:
        return jsonify({'action': 'status', 'status': 'generating', 'elapsed_seconds': int(elapsed), 'response': 'Generating...'})

    try:
        check = http_requests.get(
            f'{SUNO_API_BASE}/api/v1/generate/record-info',
            headers={'Authorization': f'Bearer {SUNO_API_KEY}'},
            params={'taskId': task_id},
            timeout=15,
        )
        logger.debug(f'Suno status check: {check.status_code} {check.text[:300]}')

        if check.status_code == 200:
            cdata = check.json()
            if cdata.get('code') == 200:
                status_data = cdata.get('data', {})
                gen_status = status_data.get('status', '')

                if gen_status == 'SUCCESS':
                    songs = status_data.get('response', {}).get('sunoData', [])
                    # Suno returns 2 clips per generation — only take the first one
                    songs = songs[:1] if songs else []
                    for song in songs:
                        # sourceAudioUrl is the original/high-quality URL the
                        # sounds endpoint returns (see routes/story.py); kept as
                        # a fallback so SFX jobs download correctly. Additive —
                        # songs/jingles still prefer audioUrl, unchanged.
                        audio_url = song.get('audioUrl') or song.get('audio_url') or song.get('sourceAudioUrl')
                        if not audio_url:
                            continue
                        song_id = song.get('id', task_id)
                        _raw_title = song.get('title') or ''
                        # Suno API sometimes returns song ID as title — reject UUIDs
                        if _raw_title and not _is_uuid(_raw_title):
                            song_title = _raw_title
                        else:
                            song_title = job.get('title') or job.get('prompt', '')[:60] or 'Generated Track'
                        duration = song.get('duration', 0)
                        slug = _slugify_title(song_title)
                        # SFX go to the dedicated sounds subdir, separate from music.
                        _is_sfx = job.get('kind') == 'sfx'
                        _dir = GENERATED_SOUNDS_DIR if _is_sfx else GENERATED_MUSIC_DIR
                        _url_base = '/generated_music/sfx' if _is_sfx else '/generated_music'
                        filename = _unique_filename(_dir, slug)
                        save_path = _dir / filename

                        if not save_path.exists():
                            if not _is_safe_download_url(audio_url):
                                continue
                            audio_resp = http_requests.get(audio_url, timeout=60, stream=True)
                            if audio_resp.status_code == 200:
                                content_length = int(audio_resp.headers.get('Content-Length', 0))
                                if content_length > SUNO_MAX_DOWNLOAD_BYTES:
                                    logger.warning(f'Suno download rejected: Content-Length {content_length} exceeds limit')
                                    continue
                                chunks = []
                                total = 0
                                for chunk in audio_resp.iter_content(chunk_size=65536):
                                    total += len(chunk)
                                    if total > SUNO_MAX_DOWNLOAD_BYTES:
                                        logger.warning(f'Suno download aborted: exceeded {SUNO_MAX_DOWNLOAD_BYTES} bytes')
                                        break
                                    chunks.append(chunk)
                                else:
                                    save_path.write_bytes(b''.join(chunks))
                                    logger.info(f'Suno downloaded: {song_title} → {filename}')
                            else:
                                logger.warning(f'Suno download failed: {audio_resp.status_code}')
                                continue

                        kind = job.get('kind', 'song')
                        song_lyrics = song.get('prompt', '') or song.get('lyrics', '')

                        # SFX are NOT music — keep them out of the music metadata
                        # AND the music player's completed-songs queue so they
                        # don't pollute the music library. The status response
                        # below still returns the URL so the caller gets the clip.
                        if not _is_sfx:
                            extra = {}
                            if kind == 'jingle':
                                extra = {
                                    'brand': job.get('brand', ''),
                                    'style_key': job.get('style_key', ''),
                                    'vocal_gender': job.get('vocal_gender', ''),
                                    'instrumental': job.get('instrumental', False),
                                }
                            if song_lyrics:
                                extra['lyrics'] = song_lyrics
                            _add_song_to_metadata(
                                filename=filename,
                                title=song_title,
                                prompt=job.get('prompt', ''),
                                style=job.get('style', ''),
                                duration=duration,
                                song_id=song_id,
                                kind=kind,
                                extra=extra,
                                task_id=task_id,
                            )

                        # Update job
                        job['status'] = 'complete'
                        job['song_id'] = song_id
                        job['title'] = song_title
                        job['url'] = f'{_url_base}/{filename}'

                        # Notify frontend poller — music only (SFX skip the music queue)
                        if not _is_sfx:
                            completed_songs_queue.append({
                                'song_id': song_id,
                                'filename': filename,
                                'title': song_title,
                                'job_id': job_id,
                                'kind': kind,
                                'url': f'{_url_base}/{filename}',
                                'completed_at': datetime.now().isoformat(),
                                'prompt': job.get('prompt', ''),
                                'lyrics': song_lyrics,
                            })

                        return jsonify({
                            'action': 'complete',
                            'status': 'complete',
                            'job_id': job_id,
                            'song_id': song_id,
                            'title': song_title,
                            'url': f'{_url_base}/{filename}',
                            'response': f"Done! '{song_title}' is ready to spin!",
                        })

                    return jsonify({'action': 'status', 'status': 'complete_no_audio', 'response': 'Song generated but audio unavailable.'})

                elif gen_status in ('PENDING', 'TEXT_SUCCESS', 'FIRST_SUCCESS'):
                    return jsonify({
                        'action': 'status',
                        'status': 'generating',
                        'elapsed_seconds': int(elapsed),
                        'response': f'Still cooking ({gen_status})...',
                    })
                else:
                    # Anything not in (SUCCESS, PENDING, TEXT_SUCCESS, FIRST_SUCCESS) is a failure
                    reason = status_data.get('errorMessage') or status_data.get('msg') or gen_status or 'generation failed'
                    job['status'] = 'failed'
                    job['error'] = reason
                    failed_songs_queue.append({
                        'job_id': job_id,
                        'task_id': task_id,
                        'kind': job.get('kind', 'song'),
                        'brand': job.get('brand', ''),
                        'title': job.get('title', ''),
                        'reason': reason,
                        'failed_at': datetime.now().isoformat(),
                    })
                    logger.warning(f'Suno job {job_id} failed: {reason}')
                    return jsonify({
                        'action': 'failed',
                        'status': 'failed',
                        'job_id': job_id,
                        'reason': reason,
                        'response': f"Suno couldn't make the song: {reason}",
                    })

    except Exception as exc:
        logger.warning(f'Suno status poll error: {exc}')

    return jsonify({
        'action': 'status',
        'status': 'generating',
        'elapsed_seconds': int(elapsed),
        'response': f'Still working... ({int(elapsed)}s elapsed)',
    })


def _action_credits():
    """Check remaining Suno API credits."""
    try:
        resp = http_requests.get(
            f'{SUNO_API_BASE}/api/v1/account/credits',
            headers={'Authorization': f'Bearer {SUNO_API_KEY}'},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            credits = data.get('data', {}).get('credits', data.get('credits', '?'))
            return jsonify({'action': 'credits', 'credits': credits, 'response': f'Suno credits remaining: {credits}'})
        return jsonify({'action': 'error', 'response': f'Credits check failed: HTTP {resp.status_code}'})
    except Exception as exc:
        return jsonify({'action': 'error', 'response': f'Credits check error: {exc}'})


# ---------------------------------------------------------------------------
# Studio editing / processing helpers + handlers (2026-06-25)
#
# These wrap sunoapi.org edit/process endpoints (extend, cover, add vocals,
# add/strip instrumental, replace section, lyrics, timestamped lyrics, WAV,
# stem separation, style boost, music video, song details). Endpoint paths
# confirmed against docs.sunoapi.org (2026-06-25). They follow the same
# job-tracking pattern as _action_generate: POST → store taskId in suno_jobs →
# the caller polls action=status. Operations that produce audio (extend, cover,
# add_vocals, add_instrumental, replace_section) return a taskId in the standard
# /generate shape, so the existing _action_status poller downloads + saves them.
#
# Identity resolution: most edit/process endpoints need a Suno {taskId, audioId}
# pair. We resolve these from generated_metadata.json (suno_id == audioId,
# task_id == parent taskId, persisted since 2026-06-25). Upload-based endpoints
# (cover, add_vocals, add_instrumental) instead take a public uploadUrl, which
# we build from the song's web URL via PUBLIC_BASE_URL/DOMAIN.
# ---------------------------------------------------------------------------

# Public base used to build absolute uploadUrl values that sunoapi.org can fetch.
_PUBLIC_BASE_URL = (
    os.environ.get('PUBLIC_BASE_URL')
    or (f'https://{_domain}' if _domain else '')
).rstrip('/')


def _suno_headers() -> dict:
    return {'Authorization': f'Bearer {SUNO_API_KEY}', 'Content-Type': 'application/json'}


def _resolve_song(_q, body: dict) -> dict:
    """Resolve a song reference from request params into a normalized dict.

    Accepts any of: filename, audio_id (suno_id), task_id, song_url.
    Returns {filename, audio_id, task_id, title, style, lyrics, upload_url, meta}.
    Looks up generated_metadata.json by filename when available to backfill
    audio_id / task_id / title / style / lyrics.
    """
    filename = (_q('filename') or body.get('filename', '')).strip()
    audio_id = (_q('audio_id') or body.get('audio_id', '')).strip()
    task_id = (_q('task_id') or body.get('task_id', '')).strip()
    song_url = (_q('song_url') or body.get('song_url', '')).strip()

    meta = {}
    metadata = _load_generated_metadata()

    # If we only got a URL, derive the filename from its last path segment.
    if not filename and song_url:
        filename = song_url.rstrip('/').split('/')[-1]

    if filename and filename in metadata:
        meta = metadata[filename]
        audio_id = audio_id or meta.get('suno_id', '')
        task_id = task_id or meta.get('task_id', '')

    # Build an absolute uploadUrl Suno can fetch.
    upload_url = ''
    if song_url.startswith('http'):
        upload_url = song_url
    elif filename:
        web_path = f'/generated_music/{filename}'
        if _PUBLIC_BASE_URL:
            upload_url = f'{_PUBLIC_BASE_URL}{web_path}'

    return {
        'filename': filename,
        'audio_id': audio_id,
        'task_id': task_id,
        'title': meta.get('title', '') or (Path(filename).stem if filename else ''),
        'style': meta.get('style', '') or meta.get('genre', ''),
        'lyrics': meta.get('lyrics', ''),
        'upload_url': upload_url,
        'meta': meta,
    }


def _submit_suno_job(endpoint: str, request_body: dict, kind: str,
                     job_extra: dict = None, op: str = None,
                     est_seconds: int = 60, response_msg: str = None):
    """POST a generation-style request, register a job, return the standard JSON.

    `endpoint` is the path under SUNO_API_BASE. Used by all audio-producing
    studio actions (extend, cover, add_vocals, add_instrumental, replace_section)
    which return a taskId in the /generate shape the action=status poller reads.
    """
    if SUNO_CALLBACK_URL:
        request_body.setdefault('callBackUrl', SUNO_CALLBACK_URL)

    logger.info(f'Suno {kind}: POST {endpoint} body={json.dumps(request_body)[:300]}')
    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}{endpoint}',
            headers=_suno_headers(),
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno {kind} response: {resp.status_code} {resp.text[:300]}')

        if resp.status_code != 200:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})

        data = resp.json()
        if data.get('code') != 200 or not data.get('data', {}).get('taskId'):
            return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})

        task_id = data['data']['taskId']
        job_id = str(uuid.uuid4())
        job = {
            'status': 'generating',
            'prompt': request_body.get('prompt', ''),
            'title': request_body.get('title', '') or kind.title(),
            'style': request_body.get('style', '') or request_body.get('tags', ''),
            'task_id': task_id,
            'created_at': time.time(),
            'kind': kind,
        }
        if job_extra:
            job.update(job_extra)
        suno_jobs[job_id] = job
        _provider_receipt(op or kind)
        return jsonify({
            'action': 'generating',
            'job_id': job_id,
            'task_id': task_id,
            'kind': kind,
            'response': response_msg or f'{kind.replace("_", " ").title()} started — check back shortly.',
            'estimated_seconds': est_seconds,
        })
    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _action_extend(_q, body: dict):
    """Extend/continue an existing song. POST /api/v1/generate/extend."""
    src = _resolve_song(_q, body)
    if not src['audio_id']:
        return jsonify({'action': 'error', 'response': 'Need a song with a known Suno audio id to extend.'})

    prompt = (_q('prompt') or body.get('prompt', '')).strip()
    style = (_q('style') or body.get('style', '')).strip() or src['style'] or 'Same style as original'
    title = (_q('title') or body.get('title', '')).strip() or src['title'] or 'Extended Track'
    model = (_q('model') or body.get('model', '')).strip() or 'V5'

    continue_at_raw = _q('continue_at') or body.get('continue_at', '')
    request_body = {
        'audioId': src['audio_id'],
        'model': model,
    }
    if src['task_id']:
        request_body['taskId'] = src['task_id']

    # defaultParamFlag=true → we provide prompt/style/title/continueAt.
    if prompt or continue_at_raw not in (None, ''):
        request_body['defaultParamFlag'] = True
        request_body['prompt'] = prompt or 'Continue the song naturally'
        request_body['style'] = style
        request_body['title'] = title
        try:
            request_body['continueAt'] = float(continue_at_raw)
        except (TypeError, ValueError):
            request_body['continueAt'] = 0
    else:
        request_body['defaultParamFlag'] = False

    return _submit_suno_job(
        '/api/v1/generate/extend', request_body, 'extend',
        job_extra={'title': title, 'style': style},
        op='extend',
        response_msg=f"Extending '{title}' — check back in ~60s.",
    )


def _action_cover(_q, body: dict):
    """Generate a cover/remix in a new style. POST /api/v1/generate/upload-cover."""
    src = _resolve_song(_q, body)
    if not src['upload_url']:
        return jsonify({'action': 'error', 'response': 'Need a public song URL to cover — set PUBLIC_BASE_URL/DOMAIN or pass song_url.'})

    prompt = (_q('prompt') or body.get('prompt', '')).strip()
    style = (_q('style') or body.get('style', '')).strip()
    title = (_q('title') or body.get('title', '')).strip() or (f"{src['title']} (Cover)" if src['title'] else 'Cover')
    model = (_q('model') or body.get('model', '')).strip() or 'V5'
    instrumental_raw = _q('instrumental') or body.get('instrumental', False)
    instrumental = instrumental_raw if isinstance(instrumental_raw, bool) else str(instrumental_raw).lower() in ('true', '1', 'yes')

    # Custom mode when an explicit style is supplied (lets us steer the remix).
    custom_mode = bool(style)
    request_body = {
        'uploadUrl': src['upload_url'],
        'customMode': custom_mode,
        'instrumental': instrumental,
        'model': model,
    }
    if custom_mode:
        request_body['style'] = style
        request_body['title'] = title
        if not instrumental:
            request_body['prompt'] = prompt or 'Reimagine this song in the new style'
    else:
        request_body['prompt'] = (prompt or 'Cover version')[:500]

    return _submit_suno_job(
        '/api/v1/generate/upload-cover', request_body, 'cover',
        job_extra={'title': title, 'style': style},
        op='cover',
        response_msg=f"Generating cover of '{src['title'] or 'track'}' — check back in ~60s.",
    )


def _action_add_vocals(_q, body: dict):
    """Add vocals to an instrumental track. POST /api/v1/generate/add-vocals."""
    src = _resolve_song(_q, body)
    if not src['upload_url']:
        return jsonify({'action': 'error', 'response': 'Need a public song URL to add vocals — set PUBLIC_BASE_URL/DOMAIN or pass song_url.'})

    prompt = (_q('prompt') or body.get('prompt', '')).strip()
    if not prompt:
        return jsonify({'action': 'error', 'response': 'Describe the vocals/lyrics to add (prompt is required).'})
    style = (_q('style') or body.get('style', '')).strip() or src['style'] or 'Pop'
    title = (_q('title') or body.get('title', '')).strip() or (f"{src['title']} (Vocals)" if src['title'] else 'Vocal Version')
    model = (_q('model') or body.get('model', '')).strip() or 'V5'
    vocal_gender = (_q('vocal_gender') or body.get('vocal_gender', '')).strip().lower()

    request_body = {
        'uploadUrl': src['upload_url'],
        'prompt': prompt,
        'title': title,
        'style': style,
        'negativeTags': (_q('negative_tags') or body.get('negative_tags', '')) or 'low quality, off-key, distorted',
        'model': model,
    }
    if vocal_gender in ('m', 'f'):
        request_body['vocalGender'] = vocal_gender

    return _submit_suno_job(
        '/api/v1/generate/add-vocals', request_body, 'add_vocals',
        job_extra={'title': title, 'style': style},
        op='add_vocals',
        response_msg=f"Adding vocals to '{src['title'] or 'track'}' — check back in ~60s.",
    )


def _action_add_instrumental(_q, body: dict):
    """Generate an instrumental backing for an a-cappella/vocal track.
    POST /api/v1/generate/add-instrumental."""
    src = _resolve_song(_q, body)
    if not src['upload_url']:
        return jsonify({'action': 'error', 'response': 'Need a public song URL — set PUBLIC_BASE_URL/DOMAIN or pass song_url.'})

    tags = (_q('tags') or body.get('tags', '')).strip() or (_q('style') or body.get('style', '')).strip() or src['style'] or 'instrumental backing'
    title = (_q('title') or body.get('title', '')).strip() or (f"{src['title']} (Instrumental)" if src['title'] else 'Instrumental Version')
    model = (_q('model') or body.get('model', '')).strip() or 'V5'

    request_body = {
        'uploadUrl': src['upload_url'],
        'title': title,
        'tags': tags,
        'negativeTags': (_q('negative_tags') or body.get('negative_tags', '')) or 'vocals, singing, lyrics',
        'model': model,
    }

    return _submit_suno_job(
        '/api/v1/generate/add-instrumental', request_body, 'add_instrumental',
        job_extra={'title': title, 'style': tags},
        op='add_instrumental',
        response_msg=f"Building instrumental for '{src['title'] or 'track'}' — check back in ~60s.",
    )


def _action_replace_section(_q, body: dict):
    """Replace a section of a song. POST /api/v1/generate/replace-section."""
    src = _resolve_song(_q, body)
    if not (src['audio_id'] and src['task_id']):
        return jsonify({'action': 'error', 'response': 'Replace section needs a song with both Suno task id and audio id (re-generate or pick a newer track).'})

    prompt = (_q('prompt') or body.get('prompt', '')).strip()
    if not prompt:
        return jsonify({'action': 'error', 'response': 'Describe the replacement content (prompt is required).'})
    title = (_q('title') or body.get('title', '')).strip() or src['title'] or 'Edited Track'
    tags = (_q('tags') or body.get('tags', '')).strip() or (_q('style') or body.get('style', '')).strip() or src['style'] or 'Same style'
    full_lyrics = (_q('full_lyrics') or body.get('full_lyrics', '')) or src['lyrics'] or prompt

    try:
        start_s = round(float(_q('start_time') or body.get('start_time', 0)), 2)
        end_s = round(float(_q('end_time') or body.get('end_time', 0)), 2)
    except (TypeError, ValueError):
        return jsonify({'action': 'error', 'response': 'start_time and end_time must be numbers (seconds).'})
    if not (end_s > start_s):
        return jsonify({'action': 'error', 'response': 'end_time must be greater than start_time.'})

    request_body = {
        'taskId': src['task_id'],
        'audioId': src['audio_id'],
        'prompt': prompt,
        'tags': tags,
        'title': title,
        'infillStartS': start_s,
        'infillEndS': end_s,
        'fullLyrics': full_lyrics,
    }

    return _submit_suno_job(
        '/api/v1/generate/replace-section', request_body, 'replace_section',
        job_extra={'title': title, 'style': tags},
        op='replace_section',
        response_msg=f"Replacing {start_s}-{end_s}s of '{title}' — check back in ~60s.",
    )


def _action_generate_lyrics(_q, body: dict):
    """Generate lyrics from a prompt. POST /api/v1/lyrics.

    Lyrics come back asynchronously via callback; the studio polls the
    timestamped/record endpoints — but for the common case we return the taskId
    so the UI can fetch results. We DON'T register an audio job (no audio).
    """
    prompt = (_q('prompt') or body.get('prompt', '')).strip()
    if not prompt:
        return jsonify({'action': 'error', 'response': 'Need a prompt describing the lyrics you want.'})

    request_body = {'prompt': prompt[:200]}
    if SUNO_CALLBACK_URL:
        request_body['callBackUrl'] = SUNO_CALLBACK_URL

    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}/api/v1/lyrics',
            headers=_suno_headers(),
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno generate_lyrics response: {resp.status_code} {resp.text[:300]}')
        if resp.status_code != 200:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})
        data = resp.json()
        if data.get('code') != 200 or not data.get('data', {}).get('taskId'):
            return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})
        task_id = data['data']['taskId']
        _provider_receipt('generate_lyrics')
        return jsonify({
            'action': 'lyrics_generating',
            'task_id': task_id,
            'kind': 'lyrics',
            'response': 'Writing lyrics — fetch results with action=song_details&task_id=...',
            'estimated_seconds': 15,
        })
    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _action_timestamped_lyrics(_q, body: dict):
    """Get timestamped (per-word) lyrics for an existing track.
    POST /api/v1/generate/get-timestamped-lyrics."""
    src = _resolve_song(_q, body)
    task_id = src['task_id'] or (_q('task_id') or body.get('task_id', '')).strip()
    audio_id = src['audio_id'] or (_q('audio_id') or body.get('audio_id', '')).strip()
    if not (task_id and audio_id):
        return jsonify({'action': 'error', 'response': 'Timestamped lyrics need both Suno task id and audio id for the track.'})

    request_body = {'taskId': task_id, 'audioId': audio_id}
    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}/api/v1/generate/get-timestamped-lyrics',
            headers=_suno_headers(),
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno timestamped_lyrics response: {resp.status_code} {resp.text[:300]}')
        if resp.status_code != 200:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})
        data = resp.json()
        if data.get('code') != 200:
            return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})
        _provider_receipt('timestamped_lyrics')
        return jsonify({
            'action': 'timestamped_lyrics',
            'data': data.get('data', {}),
            'response': 'Got timestamped lyrics.',
        })
    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _action_wav_convert(_q, body: dict):
    """Convert an existing track to lossless WAV. POST /api/v1/wav/generate."""
    src = _resolve_song(_q, body)
    task_id = src['task_id'] or (_q('task_id') or body.get('task_id', '')).strip()
    audio_id = src['audio_id'] or (_q('audio_id') or body.get('audio_id', '')).strip()
    if not (task_id and audio_id):
        return jsonify({'action': 'error', 'response': 'WAV conversion needs both Suno task id and audio id for the track.'})

    request_body = {'taskId': task_id, 'audioId': audio_id}
    if SUNO_CALLBACK_URL:
        request_body['callBackUrl'] = SUNO_CALLBACK_URL
    return _submit_process_job(
        '/api/v1/wav/generate', request_body, 'wav_convert',
        op='wav_convert', est_seconds=20,
        response_msg='Converting to WAV — results delivered when ready.',
    )


def _action_stem_separate(_q, body: dict):
    """Separate vocals from music (or full stem split).
    POST /api/v1/vocal-removal/generate."""
    src = _resolve_song(_q, body)
    task_id = src['task_id'] or (_q('task_id') or body.get('task_id', '')).strip()
    audio_id = src['audio_id'] or (_q('audio_id') or body.get('audio_id', '')).strip()
    if not (task_id and audio_id):
        return jsonify({'action': 'error', 'response': 'Stem separation needs both Suno task id and audio id for the track.'})

    sep_type = (_q('type') or body.get('type', '')).strip() or 'separate_vocal'
    if sep_type not in ('separate_vocal', 'split_stem'):
        sep_type = 'separate_vocal'

    request_body = {'taskId': task_id, 'audioId': audio_id, 'type': sep_type}
    if SUNO_CALLBACK_URL:
        request_body['callBackUrl'] = SUNO_CALLBACK_URL
    return _submit_process_job(
        '/api/v1/vocal-removal/generate', request_body, 'stem_separate',
        op='stem_separate', est_seconds=40,
        response_msg=('Splitting into stems — results delivered when ready.'
                      if sep_type == 'split_stem' else
                      'Separating vocals from music — results delivered when ready.'),
    )


def _action_style_boost(_q, body: dict):
    """Boost/enhance a style description. POST /api/v1/style/generate.

    Synchronous-ish: returns an enhanced style string. Takes free text in
    `content`/`style`/`prompt`.
    """
    content = (_q('content') or body.get('content', '')
               or _q('style') or body.get('style', '')
               or _q('prompt') or body.get('prompt', '')).strip()
    if not content:
        return jsonify({'action': 'error', 'response': 'Need a style description to boost.'})

    request_body = {'content': content[:200]}
    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}/api/v1/style/generate',
            headers=_suno_headers(),
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno style_boost response: {resp.status_code} {resp.text[:300]}')
        if resp.status_code != 200:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})
        data = resp.json()
        if data.get('code') != 200:
            return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})
        result = data.get('data', {})
        boosted = result.get('result') or result.get('content') or result
        _provider_receipt('style_boost')
        return jsonify({
            'action': 'style_boost',
            'data': result,
            'boosted_style': boosted,
            'response': 'Style enhanced.',
        })
    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _action_music_video(_q, body: dict):
    """Generate a music video for a track. POST /api/v1/mp4/generate."""
    src = _resolve_song(_q, body)
    task_id = src['task_id'] or (_q('task_id') or body.get('task_id', '')).strip()
    audio_id = src['audio_id'] or (_q('audio_id') or body.get('audio_id', '')).strip()
    if not (task_id and audio_id):
        return jsonify({'action': 'error', 'response': 'Music video needs both Suno task id and audio id for the track.'})

    request_body = {'taskId': task_id, 'audioId': audio_id}
    author = (_q('author') or body.get('author', '')).strip()
    domain_name = (_q('domain_name') or body.get('domain_name', '')).strip()
    if author:
        request_body['author'] = author[:50]
    if domain_name:
        request_body['domainName'] = domain_name[:50]
    if SUNO_CALLBACK_URL:
        request_body['callBackUrl'] = SUNO_CALLBACK_URL
    return _submit_process_job(
        '/api/v1/mp4/generate', request_body, 'music_video',
        op='music_video', est_seconds=90,
        response_msg='Rendering music video — this can take a couple minutes.',
    )


def _action_song_details(_q, body: dict):
    """Get full Suno metadata for a generation task.
    GET /api/v1/generate/record-info?taskId=..."""
    src = _resolve_song(_q, body)
    task_id = src['task_id'] or (_q('task_id') or body.get('task_id', '')).strip()
    if not task_id:
        return jsonify({'action': 'error', 'response': 'Need a Suno task id (or a song that has one) for details.'})
    try:
        resp = http_requests.get(
            f'{SUNO_API_BASE}/api/v1/generate/record-info',
            headers={'Authorization': f'Bearer {SUNO_API_KEY}'},
            params={'taskId': task_id},
            timeout=15,
        )
        if resp.status_code != 200:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})
        data = resp.json()
        if data.get('code') != 200:
            return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})
        return jsonify({
            'action': 'song_details',
            'data': data.get('data', {}),
            'response': 'Fetched song details.',
        })
    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


def _submit_process_job(endpoint: str, request_body: dict, kind: str,
                        op: str = None, est_seconds: int = 30,
                        response_msg: str = None):
    """POST a process request (WAV/stem/video) that returns a taskId but whose
    output is NOT a normal song download. Registers a lightweight job so the UI
    can show it in the queue; results are delivered via callback / song_details.
    """
    logger.info(f'Suno {kind}: POST {endpoint} body={json.dumps(request_body)[:300]}')
    try:
        resp = http_requests.post(
            f'{SUNO_API_BASE}{endpoint}',
            headers=_suno_headers(),
            json=request_body,
            timeout=30,
        )
        logger.info(f'Suno {kind} response: {resp.status_code} {resp.text[:300]}')
        if resp.status_code != 200:
            return jsonify({'action': 'error', 'response': f'Suno API HTTP {resp.status_code}: {resp.text[:200]}'})
        data = resp.json()
        if data.get('code') != 200 or not data.get('data', {}).get('taskId'):
            return jsonify({'action': 'error', 'response': f"Suno API error: {data.get('msg', 'Unknown error')}"})
        task_id = data['data']['taskId']
        job_id = str(uuid.uuid4())
        suno_jobs[job_id] = {
            'status': 'generating',
            'title': kind.replace('_', ' ').title(),
            'task_id': task_id,
            'created_at': time.time(),
            'kind': kind,
            'process_only': True,  # status poller won't try to download as music
        }
        _provider_receipt(op or kind)
        return jsonify({
            'action': 'generating',
            'job_id': job_id,
            'task_id': task_id,
            'kind': kind,
            'response': response_msg or f'{kind.replace("_", " ").title()} started.',
            'estimated_seconds': est_seconds,
        })
    except http_requests.RequestException as exc:
        return jsonify({'action': 'error', 'response': f"Couldn't reach Suno API: {exc}"})


# ---------------------------------------------------------------------------
# Webhook callback (sunoapi.org POSTs here when song is done)
# ---------------------------------------------------------------------------


@suno_bp.route('/api/suno/callback', methods=['POST'])
def suno_callback():
    """Webhook from sunoapi.org — downloads song and queues frontend notification."""
    try:
        # Verify HMAC signature when a webhook secret is configured.
        # NOTE: SUNO_WEBHOOK_SECRET is NOT provisioned in the fleet .env template
        # yet (verified 2026-07-11), so we cannot fail-closed without breaking
        # every tenant's song delivery. Until the secret is provisioned
        # fleet-wide, an unsigned callback is accepted but constrained: the
        # download is SSRF-checked, restricted to https, and hard-capped at
        # SUNO_MAX_DOWNLOAD_BYTES (50 MB). TODO: add SUNO_WEBHOOK_SECRET to
        # templates/.env then flip this to a 503 fail-closed.
        _require_https = not SUNO_WEBHOOK_SECRET
        if SUNO_WEBHOOK_SECRET:
            sig_header = request.headers.get('X-Suno-Signature', '')
            payload = request.get_data()
            expected = hmac.new(SUNO_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig_header, expected):
                logger.warning('Suno callback rejected: invalid signature')
                return jsonify({'status': 'forbidden'}), 403
        else:
            logger.warning(
                'Suno callback accepted UNSIGNED — SUNO_WEBHOOK_SECRET not configured. '
                'Download restricted to https + %d byte cap. Provision the secret '
                'fleet-wide to fail closed.', SUNO_MAX_DOWNLOAD_BYTES
            )

        data = request.json or {}
        logger.info(f'Suno callback: {json.dumps(data, indent=2)[:500]}')

        if data.get('code') == 200:
            callback_type = data.get('data', {}).get('callbackType', '')
            task_id = data.get('data', {}).get('taskId', '')

            # Failure callback — surface to user + agent
            if callback_type == 'error':
                reason = data.get('data', {}).get('errorMessage') or data.get('msg') or 'Suno reported an error'
                _job_id = None
                _job = None
                for jid, job in suno_jobs.items():
                    if job.get('task_id') == task_id:
                        _job_id = jid
                        _job = job
                        job['status'] = 'failed'
                        job['error'] = reason
                        break
                failed_songs_queue.append({
                    'job_id': _job_id or task_id,
                    'task_id': task_id,
                    'kind': (_job or {}).get('kind', 'song'),
                    'brand': (_job or {}).get('brand', ''),
                    'title': (_job or {}).get('title', ''),
                    'reason': reason,
                    'failed_at': datetime.now().isoformat(),
                })
                logger.warning(f'Suno callback reported error for task {task_id}: {reason}')
                return jsonify({'status': 'ok'})

            # sunoapi.org sends: "text" (lyrics ready), "first"/"second" (audio ready), "complete"
            # Only process 'complete' to avoid duplicates (first/second are partial deliveries
            # of the same songs that appear again in complete).
            if callback_type == 'complete' or (
                callback_type not in ('text', 'first', 'second') and data.get('data', {}).get('data')
            ):
                songs = data.get('data', {}).get('data', [])
                # Suno returns 2 clips per generation — only take the first one
                # (user asked for 1 song, not 2 variations)
                songs = songs[:1] if songs else []
                for song in songs:
                    audio_url = song.get('audioUrl') or song.get('audio_url')
                    if not audio_url:
                        continue  # "text" callback — lyrics only, no audio yet
                    song_id = song.get('id', task_id)
                    _raw_cb_title = song.get('title', '')
                    if _raw_cb_title and not _is_uuid(_raw_cb_title):
                        song_title = _raw_cb_title
                    else:
                        song_title = 'Generated Track'
                    duration = song.get('duration', 0)
                    slug = _slugify_title(song_title)
                    filename = _unique_filename(GENERATED_MUSIC_DIR, slug)
                    save_path = GENERATED_MUSIC_DIR / filename

                    if audio_url and not save_path.exists():
                        if not _is_safe_download_url(audio_url):
                            continue
                        # Unsigned callbacks: only https audio URLs are allowed.
                        if _require_https and not audio_url.lower().startswith('https://'):
                            logger.warning('Unsigned Suno callback: rejecting non-https audioUrl')
                            continue
                        try:
                            audio_resp = http_requests.get(audio_url, timeout=60, stream=True)
                            if audio_resp.status_code == 200:
                                content_length = int(audio_resp.headers.get('Content-Length', 0))
                                if content_length > SUNO_MAX_DOWNLOAD_BYTES:
                                    logger.warning(f'Callback download rejected: size {content_length} exceeds limit')
                                    continue
                                chunks = []
                                total = 0
                                for chunk in audio_resp.iter_content(chunk_size=65536):
                                    total += len(chunk)
                                    if total > SUNO_MAX_DOWNLOAD_BYTES:
                                        logger.warning(f'Callback download aborted: exceeded {SUNO_MAX_DOWNLOAD_BYTES} bytes')
                                        break
                                    chunks.append(chunk)
                                else:
                                    save_path.write_bytes(b''.join(chunks))
                                    logger.info(f'Callback downloaded: {song_title} → {filename}')

                                # Find matching job
                                prompt = ''
                                style = ''
                                job_id = None
                                kind = 'song'
                                extra = {}
                                for jid, job in suno_jobs.items():
                                    if job.get('task_id') == task_id:
                                        job['status'] = 'complete'
                                        job['song_id'] = song_id
                                        job['url'] = f'/generated_music/{filename}'
                                        prompt = job.get('prompt', '')
                                        style = job.get('style', '')
                                        job_id = jid
                                        kind = job.get('kind', 'song')
                                        if kind == 'jingle':
                                            extra = {
                                                'brand': job.get('brand', ''),
                                                'style_key': job.get('style_key', ''),
                                                'vocal_gender': job.get('vocal_gender', ''),
                                                'instrumental': job.get('instrumental', False),
                                            }
                                        break

                                # Capture lyrics from Suno response (description-mode jingles
                                # have Suno-written lyrics; custom-mode songs have provided lyrics)
                                song_lyrics = song.get('prompt', '') or song.get('lyrics', '')
                                if song_lyrics:
                                    extra['lyrics'] = song_lyrics

                                _add_song_to_metadata(filename, song_title, prompt, style,
                                                      duration, song_id, kind=kind, extra=extra,
                                                      task_id=task_id)

                                completed_songs_queue.append({
                                    'song_id': song_id,
                                    'filename': filename,
                                    'title': song_title,
                                    'job_id': job_id or task_id,
                                    'kind': kind,
                                    'url': f'/generated_music/{filename}',
                                    'completed_at': datetime.now().isoformat(),
                                    'prompt': prompt,
                                    'lyrics': song_lyrics,
                                })
                        except Exception as exc:
                            logger.warning(f'Callback download error: {exc}')

        return jsonify({'status': 'ok'})

    except Exception as exc:
        logger.error(f'Suno callback error: {exc}')
        logger.error('Suno callback error: %s', exc)
        return jsonify({'status': 'error', 'message': 'Internal server error'})


# ---------------------------------------------------------------------------
# Completed songs queue (frontend polls this)
# ---------------------------------------------------------------------------


@suno_bp.route('/api/suno/completed', methods=['GET', 'POST'])
def suno_completed():
    """
    GET  — Returns completed songs waiting for notification.
    POST — Clears specific song (or all) from queue after UI has shown it.
    """
    global completed_songs_queue

    if request.method == 'POST':
        song_id = request.args.get('song_id') or (request.get_json(silent=True) or {}).get('song_id')
        if song_id:
            completed_songs_queue = [s for s in completed_songs_queue if s['song_id'] != song_id]
        else:
            completed_songs_queue = []
        return jsonify({'status': 'ok', 'cleared': True})

    if completed_songs_queue:
        return jsonify({'has_completed': True, 'songs': completed_songs_queue, 'count': len(completed_songs_queue)})
    return jsonify({'has_completed': False, 'songs': [], 'count': 0})


@suno_bp.route('/api/suno/failed', methods=['GET', 'POST'])
def suno_failed():
    """
    GET  — Returns failed Suno jobs waiting for notification.
    POST — Clears specific job (or all) from the failed queue after UI showed it.
    """
    global failed_songs_queue

    if request.method == 'POST':
        job_id = request.args.get('job_id') or (request.get_json(silent=True) or {}).get('job_id')
        if job_id:
            failed_songs_queue = [f for f in failed_songs_queue if f.get('job_id') != job_id]
        else:
            failed_songs_queue = []
        return jsonify({'status': 'ok', 'cleared': True})

    if failed_songs_queue:
        return jsonify({'has_failed': True, 'failures': failed_songs_queue, 'count': len(failed_songs_queue)})
    return jsonify({'has_failed': False, 'failures': [], 'count': 0})


@suno_bp.route('/api/suno/song/<filename>', methods=['DELETE'])
def delete_song(filename):
    """Delete a generated song file. Renames to .deleted (never truly removes)."""
    import re
    # Validate filename — only allow safe characters
    if not re.match(r'^[\w\-. ]+\.(mp3|wav|ogg|m4a)$', filename):
        return jsonify({'error': 'Invalid filename'}), 400

    target = GENERATED_MUSIC_DIR / filename
    if not target.exists():
        return jsonify({'error': 'File not found'}), 404

    # Rename to .deleted instead of removing (NEVER DELETE rule)
    renamed = target.with_suffix(target.suffix + '.deleted')
    try:
        target.rename(renamed)
        logger.info(f'[suno] Archived song: {filename} -> {renamed.name}')
        return jsonify({'ok': True, 'archived': renamed.name})
    except Exception as e:
        logger.error(f'[suno] Failed to archive {filename}: {e}')
        return jsonify({'error': str(e)}), 500
