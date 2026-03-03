"""
routes/conversation.py — Conversation & TTS Blueprint (P2-T3)

Extracted from server.py during Phase 2 blueprint split.
Registers routes:
  POST /api/conversation          (main voice conversation endpoint)
  POST /api/conversation/reset    (clear conversation history for a session)
  GET  /api/tts/providers         (list available TTS providers)
  POST /api/tts/generate          (generate TTS audio from text)
  POST /api/supertonic-tts        (deprecated legacy TTS endpoint)

Also exports helpers used by other server.py code:
  get_voice_session_key()
  bump_voice_session()
  conversation_histories          (dict of session histories)
  _consecutive_empty_responses    (module global, accessed via this module)
  clean_for_tts()
"""

import base64
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, make_response, request

from routes.canvas import canvas_context, update_canvas_context, CANVAS_PAGES_DIR
from routes.transcripts import save_conversation_turn
from routes.music import current_music_state as _music_state
from services.gateway_manager import gateway_manager
from services.tts import generate_tts_b64 as _tts_generate_b64
from tts_providers import get_provider, list_providers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from services.paths import DB_PATH, VOICE_SESSION_FILE

BRAIN_EVENTS_PATH = Path('/tmp/openvoiceui-events.jsonl')
MAX_HISTORY_MESSAGES = 20

# Vision keyword detection — triggers camera frame analysis via GLM-4V
_VISION_KEYWORDS = (
    'what do you see', 'what can you see', 'what are you seeing',
    'look at', 'what is in front', "what's in front",
    'describe what', 'tell me what you see', 'can you see',
    'what is that', "what's that", 'who is that', "who's that",
    'what am i holding', 'what am i wearing', 'what does it look like',
    'show me what you see', 'use the camera', 'check the camera',
    'look through the camera',
)
_VISION_FRAME_MAX_AGE = 10  # seconds — ignore frames older than this


def _is_vision_request(msg: str) -> bool:
    """Return True if the user message looks like a request to use the camera/vision."""
    lower = msg.lower()
    return any(kw in lower for kw in _VISION_KEYWORDS)

# ---------------------------------------------------------------------------
# DB write queue — background thread so DB writes don't block HTTP responses
# (FIND-01 / FIND-08 fix from performance audit)
# ---------------------------------------------------------------------------

_db_write_queue: queue.Queue = queue.Queue()


def _db_writer_loop():
    """Background daemon that drains _db_write_queue and writes to SQLite.

    Queue items: (db_path_str, sql, params).
    db_path_str is resolved at enqueue time so test patches to DB_PATH work.
    Connections are cached per db_path to reuse WAL-mode connections.
    """
    connections: dict = {}
    while True:
        try:
            db_path_str, sql, params = _db_write_queue.get(timeout=5)
        except queue.Empty:
            continue
        try:
            if db_path_str not in connections:
                conn = sqlite3.connect(db_path_str, check_same_thread=False, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-64000")
                conn.execute("PRAGMA busy_timeout=30000")
                connections[db_path_str] = conn
            connections[db_path_str].execute(sql, params)
            connections[db_path_str].commit()
        except Exception as e:
            logger.error(f"[db-writer] loop error: {e}")
        finally:
            _db_write_queue.task_done()


_db_writer_thread = threading.Thread(
    target=_db_writer_loop,
    name="conv-db-writer",
    daemon=True,
)
_db_writer_thread.start()


def _flush_db_writes(timeout: float = 5.0) -> None:
    """Block until all queued DB writes are processed.  For use in tests."""
    _db_write_queue.join()

# ---------------------------------------------------------------------------
# In-memory session key cache (FIND-02 fix from performance audit)
# ---------------------------------------------------------------------------

_session_key_cache: str | None = None
_session_key_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Conversation state (module-level singletons)
# ---------------------------------------------------------------------------

#: In-process conversation history keyed by session_id.
#: Cleared on conversation reset; also restored from DB on first access.
conversation_histories: dict = {}

#: Tracks consecutive empty Gateway responses for auto-reset logic.
_consecutive_empty_responses: int = 0

# ---------------------------------------------------------------------------
# Voice session management
# (moved here from server.py so the blueprint owns the session counter)
# ---------------------------------------------------------------------------


def _save_session_counter(counter: int) -> None:
    with open(VOICE_SESSION_FILE, 'w') as f:
        f.write(str(counter))


def get_voice_session_key() -> str:
    """Return the current voice session key, e.g. 'voice-main-6'.

    Result is cached in memory (FIND-02: avoids file I/O on every request).
    Cache is invalidated by bump_voice_session().
    """
    global _session_key_cache
    if _session_key_cache is not None:
        return _session_key_cache
    with _session_key_lock:
        if _session_key_cache is not None:
            return _session_key_cache
        try:
            with open(VOICE_SESSION_FILE, 'r') as f:
                counter = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            counter = 6  # default as of Feb 2026
            _save_session_counter(counter)
        _prefix = os.getenv('VOICE_SESSION_PREFIX', 'voice-main')
        _session_key_cache = f'{_prefix}-{counter}'
    return _session_key_cache


def bump_voice_session() -> str:
    """Increment the session counter and return the new session key."""
    global _consecutive_empty_responses, _session_key_cache
    try:
        with open(VOICE_SESSION_FILE, 'r') as f:
            counter = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        counter = 6
    counter += 1
    _save_session_counter(counter)
    _consecutive_empty_responses = 0
    new_key = f'{os.getenv("VOICE_SESSION_PREFIX", "voice-main")}-{counter}'
    with _session_key_lock:
        _session_key_cache = new_key  # invalidate + update cache
    logger.info(f'### SESSION RESET: bumped to {new_key}')
    return new_key

# ---------------------------------------------------------------------------
# Helper: notify Brain (non-critical fire-and-forget)
# ---------------------------------------------------------------------------


def _notify_brain(event_type: str, **data) -> None:
    """Append an event to the Brain events file for context tracking."""
    try:
        event = {'type': event_type, 'timestamp': datetime.now().isoformat()}
        event.update(data)
        with open(BRAIN_EVENTS_PATH, 'a') as f:
            f.write(json.dumps(event) + '\n')
    except Exception:
        pass  # Non-critical

# ---------------------------------------------------------------------------
# Helper: log conversation to SQLite
# ---------------------------------------------------------------------------


def log_conversation(role: str, message: str, session_id: str = 'default',
                     tts_provider: str = None, voice: str = None) -> None:
    """Log a single conversation turn to the database (non-blocking).

    Write is queued to the background db-writer thread (FIND-01 fix).
    """
    _db_write_queue.put((
        str(DB_PATH),
        'INSERT INTO conversation_log '
        '(session_id, role, message, tts_provider, voice, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (session_id, role, message, tts_provider, voice, datetime.now().isoformat()),
    ))
    _notify_brain('conversation', role=role, message=message, session=session_id)

# ---------------------------------------------------------------------------
# Helper: log timing metrics
# ---------------------------------------------------------------------------


def log_metrics(metrics: dict) -> None:
    """Log conversation timing metrics to SQLite + journalctl (non-blocking).

    Write is queued to the background db-writer thread (FIND-01 fix).
    """
    logger.info(
        f"[METRICS] profile={metrics.get('profile')} "
        f"handshake={metrics.get('handshake_ms')}ms "
        f"llm={metrics.get('llm_inference_ms')}ms "
        f"tts={metrics.get('tts_generation_ms')}ms "
        f"total={metrics.get('total_ms')}ms "
        f"resp_len={metrics.get('response_len')} "
        f"tts_ok={metrics.get('tts_success', 1)} "
        f"tools={metrics.get('tool_count', 0)} "
        f"fallback={metrics.get('fallback_used', 0)}"
    )
    _db_write_queue.put((
        str(DB_PATH),
        '''INSERT INTO conversation_metrics
           (session_id, profile, model, handshake_ms, llm_inference_ms,
            tts_generation_ms, total_ms, user_message_len, response_len,
            tts_text_len, tts_provider, tts_success, tts_error,
            tool_count, fallback_used, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            metrics.get('session_id', 'default'),
            metrics.get('profile', 'unknown'),
            metrics.get('model', 'unknown'),
            metrics.get('handshake_ms'),
            metrics.get('llm_inference_ms'),
            metrics.get('tts_generation_ms'),
            metrics.get('total_ms'),
            metrics.get('user_message_len'),
            metrics.get('response_len'),
            metrics.get('tts_text_len'),
            metrics.get('tts_provider'),
            metrics.get('tts_success', 1),
            metrics.get('tts_error'),
            metrics.get('tool_count', 0),
            metrics.get('fallback_used', 0),
            metrics.get('error'),
            datetime.now().isoformat(),
        ),
    ))

# ---------------------------------------------------------------------------
# Helper: clean text for TTS
# ---------------------------------------------------------------------------


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate text at the nearest sentence boundary at or before max_chars.
    Falls back to hard truncation if no boundary is found."""
    if not text or len(text) <= max_chars:
        return text
    chunk = text[:max_chars]
    # Find last sentence-ending punctuation before the cap
    last_boundary = max(chunk.rfind('.'), chunk.rfind('!'), chunk.rfind('?'))
    if last_boundary > 0:
        return chunk[:last_boundary + 1].strip()
    return chunk.strip()


def clean_for_tts(text: str) -> str:
    """Remove markdown, reasoning tokens, and non-speech characters for TTS."""
    if not text:
        return ''

    # Strip GPT-OSS-120B reasoning tokens (but not if NO/YES is the full response)
    if text.strip().upper() not in ['NO', 'YES', 'NO.', 'YES.']:
        text = re.sub(r'^NO_REPLY\s*', '', text)
        text = re.sub(r'\s+NO\s*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+YES\s*$', '', text, flags=re.IGNORECASE)

    # Remove canvas/task/music triggers (handled by frontend, not spoken)
    text = re.sub(r'\[CANVAS_MENU\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[CANVAS:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[MUSIC_PLAY(?::[^\]]*)?\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[MUSIC_STOP\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[MUSIC_NEXT\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SUNO_GENERATE:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SLEEP\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[REGISTER_FACE:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SPOTIFY:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SOUND:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SESSION_RESET\]', '', text, flags=re.IGNORECASE)

    # Remove code blocks (complete fences first, then any unclosed fence to end of text)
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'```[\s\S]*', '', text)
    text = re.sub(r'`[^`]+`', '', text)

    # Add natural pauses for structured content (must happen before stripping markdown)
    text = re.sub(r'^(#+\s+.+?)([^.!?])\s*$', r'\1\2.', text, flags=re.MULTILINE)

    def _ensure_list_item_pause(match):
        prefix = match.group(1)
        content = match.group(2).strip()
        if content and content[-1] not in '.!?:':
            content += '.'
        return f'{prefix} {content}'
    text = re.sub(r'^(\s*\d+[.)]\s*)(.+?)$', _ensure_list_item_pause,
                  text, flags=re.MULTILINE)

    def _ensure_bullet_pause(match):
        content = match.group(1).strip()
        if content and content[-1] not in '.!?:':
            content += '.'
        return content
    text = re.sub(r'^\s*[-*•]\s+(.+?)$', _ensure_bullet_pause,
                  text, flags=re.MULTILINE)

    def _table_row_to_speech(match):
        row = match.group(0)
        if re.match(r'^[\s|:-]+$', row):
            return ''
        cells = [c.strip() for c in row.split('|') if c.strip()]
        if not cells:
            return ''
        return ', '.join(cells) + '.'
    text = re.sub(r'^\|.+\|$', _table_row_to_speech, text, flags=re.MULTILINE)

    lines = text.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and len(stripped) < 80 and stripped[-1] not in '.!?:,;':
            if re.match(r'^[A-Za-z0-9]', stripped):
                lines[i] = stripped + '.'
    text = '\n'.join(lines)

    # Strip markdown formatting
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'/[\w/.-]+', '', text)

    # Expand acronyms to speakable form
    acronyms = {
        'API': 'api', 'HTML': 'html', 'CSS': 'css', 'JSON': 'jason',
        'HTTP': 'http', 'HTTPS': 'https', 'URL': 'url', 'TTS': 'text to speech',
        'STT': 'speech to text', 'LLM': 'large language model', 'AI': 'A.I.',
        'UI': 'user interface', 'UX': 'user experience', 'RAM': 'ram',
        'CPU': 'cpu', 'GPU': 'gpu', 'DB': 'database', 'VPS': 'server',
        'SSH': 'ssh', 'CLI': 'command line', 'SDK': 'sdk', 'API': 'api',
    }
    for acronym, expansion in acronyms.items():
        text = re.sub(r'\b' + acronym + r'\b', expansion, text)

    # Replace symbols with spoken equivalents
    text = text.replace('&', ' and ')
    text = text.replace('%', ' percent ')
    text = text.replace('$', ' dollars ')
    text = text.replace('@', ' at ')
    text = text.replace('#', ' number ')
    text = text.replace('+', ' plus ')
    text = text.replace('=', ' equals ')

    # Clean up whitespace
    text = re.sub(r'\n+', '. ', text)
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\.\s*\.', '.', text)
    # Strip leading punctuation/spaces (e.g. from [MUSIC_STOP]\n\n → ". text")
    text = re.sub(r'^[.,;:\s]+', '', text)

    return text

# ---------------------------------------------------------------------------
# Helper: legacy Supertonic voice accessor
# ---------------------------------------------------------------------------


def get_supertonic_for_voice(voice_style: str):
    """Get Supertonic provider (voice_style ignored — unified provider)."""
    return get_provider('supertonic')

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

conversation_bp = Blueprint('conversation', __name__)

# ---------------------------------------------------------------------------
# POST /api/conversation — main voice conversation endpoint
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/conversation', methods=['POST'])
def conversation():
    """
    Handle voice conversation flow.

    Request JSON:
        message      : str  — transcribed user speech (required)
        tts_provider : str  — 'supertonic' | 'groq' (default: supertonic)
        voice        : str  — voice ID, e.g. 'M1' (default: M1)
        session_id   : str  — session identifier (default: default)
        ui_context   : dict — canvas/music state from frontend (optional)

    Response JSON (non-streaming):
        response  : str  — AI text response
        audio     : str  — base64-encoded audio (if TTS succeeds)
        timing    : dict — handshake/llm/tts/total ms
        actions   : list — Gateway tool/lifecycle events (optional)
    """
    try:
        return _conversation_inner()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f'FATAL: {tb}')
        return jsonify({
            'response': 'Something went wrong on my end. Try again?',
            'error': 'Internal server error'
        }), 500


def _conversation_inner():
    global _consecutive_empty_responses

    t_request_start = time.time()
    metrics = {
        'profile': 'gateway',
        'model': 'glm-4.7-flash',
        'tts_success': 1,
        'fallback_used': 0,
        'tool_count': 0,
    }

    data = request.get_json()
    if not data:
        logger.error('ERROR: No JSON data in request')
        return jsonify({'error': 'No JSON data provided'}), 400

    logger.info(f'Received conversation request: {data}')

    user_message = data.get('message', '').strip()
    tts_provider = data.get('tts_provider', 'supertonic')
    voice = data.get('voice', 'M1')
    session_id = data.get('session_id', 'default')
    ui_context = data.get('ui_context', {})
    identified_person = data.get('identified_person') or None
    agent_id = data.get('agent_id') or None  # e.g. 'default'; None = default 'main'
    gateway_id = data.get('gateway_id') or None  # plugin gateway id; None = 'openclaw'
    max_response_chars = data.get('max_response_chars') or None  # profile cap, truncates at sentence boundary
    metrics['session_id'] = session_id
    metrics['user_message_len'] = len(user_message)
    metrics['tts_provider'] = tts_provider

    if not user_message:
        return jsonify({'error': 'No message provided'}), 400

    # Input length guard (P7-T3 security audit)
    if len(user_message) > 4000:
        return jsonify({'error': 'Message too long (max 4000 characters)'}), 400

    wants_stream = (
        request.args.get('stream') == '1'
        or request.headers.get('X-Stream-Response') == '1'
    )

    # Update canvas context from UI state
    if ui_context.get('canvasDisplayed'):
        update_canvas_context(
            ui_context['canvasDisplayed'],
            title=ui_context['canvasDisplayed']
                .replace('/pages/', '')
                .replace('.html', '')
                .replace('-', ' ')
                .title()
        )

    # Build context prefix from UI state
    context_prefix = ''
    context_parts = []

    # Inject face recognition identity
    if identified_person and identified_person.get('name') and identified_person.get('name') != 'unknown':
        name = identified_person['name']
        confidence = identified_person.get('confidence', 0)
        context_parts.append(
            f'[FACE RECOGNITION: The person you are speaking with has been identified as {name} '
            f'({confidence}% confidence). Address them by name naturally.]'
        )

    # Vision: if user asks about what the camera sees, call vision model with latest frame
    if _is_vision_request(user_message):
        from routes.vision import _latest_frame, _call_vision
        _frame_img = _latest_frame.get('image')
        _frame_age = time.time() - _latest_frame.get('ts', 0)
        if _frame_img and _frame_age < _VISION_FRAME_MAX_AGE:
            try:
                _vision_desc = _call_vision(
                    _frame_img,
                    'Describe what you see in this image concisely. Focus on people, objects, and actions.',
                )
                context_parts.append(f'[CAMERA VISION: {_vision_desc}]')
            except Exception as exc:
                logger.warning('Vision analysis failed: %s', exc)
                context_parts.append('[CAMERA VISION: Camera is on but vision analysis failed.]')
        elif not _frame_img:
            context_parts.append('[CAMERA VISION: No camera frame available — camera may be off.]')
        else:
            context_parts.append('[CAMERA VISION: Camera frame is stale — camera may have been turned off.]')

    if ui_context:
        # Canvas state
        if ui_context.get('canvasVisible') and ui_context.get('canvasDisplayed'):
            page_name = (ui_context['canvasDisplayed']
                         .replace('/pages/', '')
                         .replace('.html', '')
                         .replace('-', ' '))
            context_parts.append(f'[Canvas OPEN: {page_name}]')
        elif not ui_context.get('canvasVisible'):
            context_parts.append('[Canvas CLOSED]')
        if ui_context.get('canvasMenuOpen'):
            context_parts.append('[Canvas menu visible to user]')

        # Music state (server-side is authoritative)
        _srv_track = _music_state.get('current_track')
        _srv_playing = _music_state.get('playing', False)
        if _srv_playing and _srv_track:
            _track_name = _srv_track.get('title') or _srv_track.get('name', 'unknown')
            context_parts.append(f'[Music PLAYING: {_track_name}]')
        elif _srv_track:
            _track_name = _srv_track.get('title') or _srv_track.get('name', 'unknown')
            context_parts.append(f'[Music PAUSED/STOPPED — last track: {_track_name}]')
        elif ui_context.get('musicPlaying'):
            track = ui_context.get('musicTrack', 'unknown')
            context_parts.append(f'[Music PLAYING: {track}]')

        # Available music tracks (so agent can use [MUSIC_PLAY:exact name])
        try:
            from routes.music import get_music_files
            _lib_tracks = get_music_files('library')
            _gen_tracks = get_music_files('generated')
            _track_names = []
            for t in _lib_tracks:
                _track_names.append(t.get('title') or t.get('name', ''))
            for t in _gen_tracks:
                _track_names.append(t.get('title') or t.get('name', ''))
            _track_names = [n for n in _track_names if n]
            if _track_names:
                context_parts.append(
                    f'[Available tracks: {", ".join(_track_names[:30])}]'
                )
        except Exception:
            pass

        # Available canvas pages (agent needs IDs for [CANVAS:page-id])
        try:
            from routes.canvas import load_canvas_manifest
            _manifest = load_canvas_manifest()
            _page_ids = sorted(_manifest.get('pages', {}).keys())
            _page_list = ', '.join(_page_ids) if _page_ids else 'none'
        except Exception:
            _page_list = 'unknown'
        context_parts.append(f'[Canvas pages: {_page_list}]')

        # Available DJ sounds (for [SOUND:name] in DJ mode)
        context_parts.append(
            '[DJ sounds: air_horn, scratch_long, rewind, record_stop, '
            'crowd_cheer, crowd_hype, yeah, lets_go, gunshot, bruh, sad_trombone]'
        )
    if context_parts:
        context_prefix = ' '.join(context_parts) + ' '

    log_conversation('user', user_message, session_id=session_id,
                     tts_provider=tts_provider, voice=voice)

    # Replace the legacy __session_start__ sentinel with a natural-language greeting
    # prompt so the LLM produces a real greeting instead of a system sentinel ("NO").
    # user_message is kept as-is so the sentinel suppression logic still works.
    if user_message == '__session_start__':
        _face = identified_person or {}
        _face_name = _face.get('name', '') if _face.get('name', '') != 'unknown' else ''
        if _face_name:
            _gateway_message = (
                f'A new voice session has just started. The person in front of the camera '
                f'has been identified as {_face_name}. Greet them by name — '
                f'one brief, friendly sentence.'
            )
        else:
            _gateway_message = (
                'A new voice session has just started. Give a brief, friendly one-sentence greeting. '
                'Do NOT address anyone by name — no face has been recognized and you do not know who is speaking.'
            )
    else:
        _gateway_message = user_message
    message_with_context = context_prefix + _gateway_message if context_prefix else _gateway_message
    ai_response = None
    captured_actions = []

    # ── PRIMARY PATH: Gateway (routed by gateway_id from request/profile) ──
    if gateway_manager.is_configured():
        try:
            logger.info('### Starting Gateway connection...')
            event_queue: queue.Queue = queue.Queue()
            _session_key = get_voice_session_key()

            def _run_gateway():
                gateway_manager.stream_to_queue(
                    event_queue, message_with_context, _session_key, captured_actions,
                    gateway_id=gateway_id,
                    agent_id=agent_id,
                )

            t_llm_start = time.time()
            gw_thread = threading.Thread(target=_run_gateway, daemon=True)
            gw_thread.start()

            if wants_stream:
                # ── STREAMING MODE ────────────────────────────────────────
                def stream_response():
                    nonlocal ai_response

                    # ── TTS helpers ───────────────────────────────────────
                    try:
                        _prov = get_provider(tts_provider)
                        _audio_fmt = _prov.get_info().get('audio_format', 'wav')
                    except Exception:
                        _audio_fmt = 'wav'

                    def _tts_error_event(err_str):
                        code_match = re.search(r'\[groq:([^\]]+)\]', err_str)
                        err_code = code_match.group(1) if code_match else 'unknown'
                        REASONS = {
                            'model_terms_required': ('terms', 'Accept Orpheus terms at console.groq.com'),
                            'rate_limit_exceeded':  ('rate_limit', 'Groq rate limit hit — try again shortly'),
                            'insufficient_quota':   ('no_credits', 'Groq account out of credits'),
                            'invalid_api_key':      ('bad_key', 'Invalid GROQ_API_KEY'),
                            'unknown':              ('error', err_str),
                        }
                        reason_key, reason_msg = REASONS.get(err_code, ('error', err_str))
                        return json.dumps({
                            'type': 'tts_error',
                            'provider': tts_provider,
                            'reason': reason_key,
                            'error': reason_msg,
                        }) + '\n'

                    # ── Mid-stream TTS helpers ────────────────────────────
                    def _has_open_tag(text):
                        """True while inside an incomplete [...] action tag or open code fence."""
                        if text.count('[') > text.count(']'):
                            return True
                        # Odd number of ``` markers means we're inside a code block
                        if text.count('```') % 2 != 0:
                            return True
                        return False

                    def _extract_sentence(text, min_len=40):
                        """Return (sentence, remainder) at first sentence boundary
                        that falls at or after min_len chars. Skips boundaries that
                        are likely inside abbreviations (e.g. A.I., Mr.)."""
                        if len(text) < min_len:
                            return None, text
                        for match in re.finditer(r'[.!?](?= |\Z)', text):
                            end = match.end()
                            if end >= min_len:
                                return text[:end].strip(), text[end:].lstrip()
                        return None, text

                    def _fire_tts(raw_text):
                        """Start TTS for raw_text in background. Returns (done_event, result)."""
                        done = threading.Event()
                        result = {'audio': None, 'error': None}
                        def _run():
                            try:
                                cleaned = clean_for_tts(raw_text)
                                if cleaned and cleaned.strip():
                                    result['audio'] = _tts_generate_b64(
                                        cleaned, voice=voice or 'M1',
                                        tts_provider=tts_provider
                                    )
                            except Exception as e:
                                result['error'] = str(e)
                            finally:
                                done.set()
                        threading.Thread(target=_run, daemon=True).start()
                        return done, result

                    # Mid-stream TTS state
                    _tts_buf = ''       # raw incremental text buffer
                    _tts_pending = []   # [(done_event, result_dict), ...]
                    _chunks_sent = 0    # audio chunks already yielded early

                    full_response = None
                    while True:
                        try:
                            evt = event_queue.get(timeout=310)
                        except queue.Empty:
                            yield json.dumps({'type': 'error', 'error': 'Gateway timeout'}) + '\n'
                            break

                        if evt['type'] == 'handshake':
                            metrics['handshake_ms'] = evt['ms']
                            continue

                        if evt['type'] == 'delta':
                            _tts_buf += evt['text']
                            # Fire TTS for complete sentences as they arrive
                            if not _has_open_tag(_tts_buf):
                                sentence, _tts_buf = _extract_sentence(_tts_buf)
                                if sentence:
                                    _tts_pending.append(_fire_tts(sentence))
                            yield json.dumps({'type': 'delta', 'text': evt['text']}) + '\n'
                            # Flush any TTS chunks that finished while text was streaming —
                            # play audio as soon as it's ready instead of waiting for text_done
                            while _tts_pending and _tts_pending[0][0].is_set():
                                _done_evt, _res = _tts_pending.pop(0)
                                if _res.get('error'):
                                    yield _tts_error_event(_res['error'])
                                elif _res.get('audio'):
                                    yield json.dumps({
                                        'type': 'audio',
                                        'audio': _res['audio'],
                                        'audio_format': _audio_fmt,
                                        'chunk': _chunks_sent,
                                        'total_chunks': None,
                                        'timing': {
                                            'tts_ms': 0,
                                            'total_ms': int((time.time() - t_request_start) * 1000),
                                        },
                                    }) + '\n'
                                    _chunks_sent += 1
                            continue

                        if evt['type'] == 'action':
                            # Flush any TTS chunks that already finished —
                            # avoids silence during long tool calls (the first
                            # sentence TTS completes ~1s in but would otherwise
                            # wait until text_done which can be minutes away).
                            while _tts_pending and _tts_pending[0][0].is_set():
                                _done_evt, _res = _tts_pending.pop(0)
                                if _res.get('error'):
                                    yield _tts_error_event(_res['error'])
                                elif _res.get('audio'):
                                    yield json.dumps({
                                        'type': 'audio',
                                        'audio': _res['audio'],
                                        'audio_format': _audio_fmt,
                                        'chunk': _chunks_sent,
                                        'total_chunks': None,
                                        'timing': {
                                            'tts_ms': 0,
                                            'total_ms': int((time.time() - t_request_start) * 1000),
                                        },
                                    }) + '\n'
                                    _chunks_sent += 1
                            yield json.dumps({'type': 'action', 'action': evt['action']}) + '\n'
                            continue

                        if evt['type'] == 'text_done':
                            full_response = evt.get('response')
                            if full_response and max_response_chars:
                                full_response = _truncate_at_sentence(full_response, max_response_chars)

                            # Suppress bare NO/YES sentinel responses to system triggers
                            # (gateway returns "NO" for wake-word checks on __session_start__)
                            _is_system_trigger = user_message.startswith('__')
                            if _is_system_trigger and full_response and \
                                    full_response.strip().upper() in ('NO', 'NO.', 'YES', 'YES.'):
                                logger.info(f'Suppressing sentinel "{full_response.strip()}" for system trigger')
                                yield json.dumps({'type': 'no_audio'}) + '\n'
                                log_metrics(metrics)
                                break
                            metrics['llm_inference_ms'] = int((time.time() - t_llm_start) * 1000)
                            metrics['tool_count'] = sum(
                                1 for a in captured_actions
                                if a.get('type') == 'tool' and a.get('phase') == 'start'
                            )
                            metrics['profile'] = 'gateway'
                            metrics['model'] = 'glm-4.7-flash'
                            logger.debug(f"[GW] Gateway response ({len(full_response or '')} chars): {repr((full_response or '')[:300])}")
                            logger.info(
                                f"### LLM inference completed in "
                                f"{metrics['llm_inference_ms']}ms "
                                f"(tools={metrics['tool_count']})"
                            )

                            yield json.dumps({
                                'type': 'text_done',
                                'response': full_response,
                                'actions': captured_actions,
                                'timing': {
                                    'handshake_ms': metrics.get('handshake_ms'),
                                    'llm_ms': metrics.get('llm_inference_ms'),
                                }
                            }) + '\n'

                            # Auto-reset on consecutive empty responses
                            global _consecutive_empty_responses
                            if not full_response or not full_response.strip():
                                _consecutive_empty_responses += 1
                                if _consecutive_empty_responses >= 3:
                                    old_key = get_voice_session_key()
                                    new_key = bump_voice_session()
                                    logger.warning(
                                        f'### AUTO-RESET: {_consecutive_empty_responses} '
                                        f'consecutive empty responses. {old_key} → {new_key}'
                                    )
                                    yield json.dumps({
                                        'type': 'session_reset',
                                        'old': old_key, 'new': new_key,
                                        'reason': 'consecutive_empty'
                                    }) + '\n'
                            else:
                                _consecutive_empty_responses = 0

                            # Handle [SESSION_RESET] trigger from agent
                            if full_response and '[SESSION_RESET]' in full_response:
                                old_key = get_voice_session_key()
                                new_key = bump_voice_session()
                                logger.info(
                                    f'### AGENT-TRIGGERED SESSION RESET: {old_key} → {new_key}'
                                )
                                full_response = full_response.replace('[SESSION_RESET]', '').strip()

                            # Detect agent returning a bare file path (e.g. from TTS tool use)
                            if full_response and re.match(r'^/tmp/[\w/.-]+$', full_response.strip()):
                                file_path = full_response.strip()
                                logger.warning(f'Agent returned file path — serving directly: {file_path}')
                                try:
                                    with open(file_path, 'rb') as f:
                                        file_bytes = f.read()
                                    audio_b64 = base64.b64encode(file_bytes).decode('utf-8')
                                    ext = file_path.rsplit('.', 1)[-1].lower()
                                    audio_format = ext if ext in ('mp3', 'wav', 'ogg') else 'mp3'
                                    metrics['tts_generation_ms'] = 0
                                    metrics['total_ms'] = int((time.time() - t_request_start) * 1000)
                                    yield json.dumps({
                                        'type': 'audio',
                                        'audio': audio_b64,
                                        'audio_format': audio_format,
                                        'chunk': 0,
                                        'timing': {'tts_ms': 0, 'total_ms': metrics.get('total_ms')},
                                    }) + '\n'
                                    logger.info(f'Served agent-generated audio: {len(file_bytes)} bytes ({audio_format})')
                                except Exception as fp_err:
                                    logger.error(f'Failed to serve agent audio file {file_path}: {fp_err}')
                                    yield json.dumps({
                                        'type': 'tts_error',
                                        'provider': 'agent',
                                        'reason': 'file_read_error',
                                        'error': f'Agent generated audio but file could not be read: {fp_err}',
                                    }) + '\n'
                                log_metrics(metrics)
                                break

                            # ── Flush TTS buffer + yield audio chunks in order ──
                            metrics['response_len'] = len(full_response) if full_response else 0

                            # Fire TTS for any remaining buffered text
                            _remaining = _tts_buf.strip()
                            if _remaining:
                                _tts_pending.append(_fire_tts(_remaining))
                                _tts_buf = ''

                            # Fallback: no sentences extracted (very short response)
                            if not _tts_pending and full_response:
                                tts_text = clean_for_tts(full_response)
                                if tts_text and tts_text.strip():
                                    _tts_pending.append(_fire_tts(tts_text))

                            if not _tts_pending:
                                logger.info('Skipping TTS — no speakable text')
                                # Tell the frontend there's no audio coming so it can
                                # reset isProcessing and re-enable the mic.
                                yield json.dumps({'type': 'no_audio'}) + '\n'
                                metrics['total_ms'] = int((time.time() - t_request_start) * 1000)
                                log_metrics(metrics)
                                if full_response:
                                    log_conversation('assistant', full_response,
                                                     session_id=session_id,
                                                     tts_provider=tts_provider, voice=voice)
                                    save_conversation_turn(
                                        user_msg=user_message,
                                        ai_response=full_response,
                                        session_id=session_id,
                                        session_key=_session_key,
                                        tts_provider=tts_provider,
                                        voice=voice,
                                        duration_ms=metrics.get('total_ms'),
                                        actions=captured_actions,
                                        identified_person=identified_person,
                                    )
                                break

                            t_tts_start = time.time()
                            total_chunks = _chunks_sent + len(_tts_pending)
                            tts_ok = True
                            for i, (done_evt, res) in enumerate(_tts_pending):
                                done_evt.wait(timeout=30)
                                if res['error']:
                                    metrics['tts_success'] = 0
                                    metrics['tts_error'] = res['error']
                                    yield _tts_error_event(res['error'])
                                    tts_ok = False
                                    break
                                if res['audio']:
                                    yield json.dumps({
                                        'type': 'audio',
                                        'audio': res['audio'],
                                        'audio_format': _audio_fmt,
                                        'chunk': _chunks_sent + i,
                                        'total_chunks': total_chunks,
                                        'timing': {
                                            'tts_ms': int((time.time() - t_tts_start) * 1000),
                                            'total_ms': int((time.time() - t_request_start) * 1000),
                                        },
                                    }) + '\n'

                            metrics['tts_generation_ms'] = int((time.time() - t_tts_start) * 1000)
                            metrics['tts_text_len'] = metrics['response_len']
                            metrics['total_ms'] = int((time.time() - t_request_start) * 1000)
                            log_metrics(metrics)
                            if full_response:
                                log_conversation('assistant', full_response,
                                                 session_id=session_id,
                                                 tts_provider=tts_provider, voice=voice)
                                save_conversation_turn(
                                    user_msg=user_message,
                                    ai_response=full_response,
                                    session_id=session_id,
                                    session_key=_session_key,
                                    tts_provider=tts_provider,
                                    voice=voice,
                                    duration_ms=metrics.get('total_ms'),
                                    actions=captured_actions,
                                    identified_person=identified_person,
                                )
                            break

                        if evt['type'] == 'error':
                            yield json.dumps({
                                'type': 'error',
                                'error': evt.get('error', 'Unknown error')
                            }) + '\n'
                            break

                return Response(
                    stream_response(),
                    mimetype='application/x-ndjson',
                    headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'}
                )

            else:
                # ── NON-STREAMING: wait for full Gateway response ─────────
                gw_thread.join(timeout=310)
                while not event_queue.empty():
                    evt = event_queue.get_nowait()
                    if evt['type'] == 'text_done':
                        ai_response = evt.get('response')
                    elif evt['type'] == 'handshake':
                        metrics['handshake_ms'] = evt['ms']
                metrics['llm_inference_ms'] = int((time.time() - t_llm_start) * 1000)
                metrics['tool_count'] = sum(
                    1 for a in captured_actions
                    if a.get('type') == 'tool' and a.get('phase') == 'start'
                )
                metrics['profile'] = 'gateway'
                metrics['model'] = 'glm-4.7-flash'
                logger.info(
                    f"### LLM inference completed in {metrics['llm_inference_ms']}ms "
                    f"(tools={metrics['tool_count']})"
                )

        except Exception as e:
            logger.error(f'Failed to call Clawdbot Gateway: {e}')

    # ── FALLBACK: Z.AI direct (glm-4.5-flash, no tools) ──────────────────
    if not ai_response:
        if metrics.get('profile') == 'gateway':
            logger.warning('No text response from Gateway, falling back to Z.AI flash...')
            metrics['fallback_used'] = 1
        else:
            logger.info('Using Z.AI flash direct (primary path)')
        t_flash_start = time.time()
        # Lazy import to avoid circular dependency (server.py imports this blueprint)
        try:
            import server as _server
            ai_response = _server.get_zai_direct_response(message_with_context, session_id)
        except Exception as e:
            logger.error(f'Z.AI direct call failed: {e}')
            ai_response = None
        metrics['profile'] = 'flash-direct'
        metrics['model'] = 'glm-4.5-flash'
        metrics['llm_inference_ms'] = int((time.time() - t_flash_start) * 1000)

    # ── LAST RESORT ───────────────────────────────────────────────────────
    if not ai_response:
        logger.warning('Both Gateway and Z.AI flash failed, using generic fallback')
        ai_response = "Hmm, my brain glitched for a second there. Try that again?"

    # Clean text for TTS
    tts_text = clean_for_tts(ai_response)
    logger.info(f'Cleaned TTS text ({len(tts_text)} chars): {tts_text[:100]}...')
    metrics['response_len'] = len(ai_response) if ai_response else 0
    metrics['tts_text_len'] = len(tts_text)

    # Generate TTS audio
    t_tts_start = time.time()
    audio_base64 = None
    if tts_text and tts_text.strip():
        audio_base64 = _tts_generate_b64(tts_text, voice=voice or 'M1',
                                          tts_provider=tts_provider)
        if audio_base64 is None:
            metrics['tts_success'] = 0
            metrics['tts_error'] = 'TTS generation failed'
    t_tts_end = time.time()
    metrics['tts_generation_ms'] = int((t_tts_end - t_tts_start) * 1000)
    metrics['total_ms'] = int((t_tts_end - t_request_start) * 1000)

    log_metrics(metrics)
    if ai_response:
        log_conversation('assistant', ai_response, session_id=session_id,
                         tts_provider=tts_provider, voice=voice)
        save_conversation_turn(
            user_msg=user_message,
            ai_response=ai_response,
            session_id=session_id,
            session_key=get_voice_session_key(),
            tts_provider=tts_provider,
            voice=voice,
            duration_ms=metrics.get('total_ms'),
            actions=captured_actions,
            identified_person=identified_person,
        )

    response_data = {'response': ai_response, 'user_said': user_message}
    if audio_base64:
        response_data['audio'] = audio_base64
    if captured_actions:
        response_data['actions'] = captured_actions
    response_data['timing'] = {
        'handshake_ms': metrics.get('handshake_ms'),
        'llm_ms': metrics.get('llm_inference_ms'),
        'tts_ms': metrics.get('tts_generation_ms'),
        'total_ms': metrics.get('total_ms'),
    }

    return jsonify(response_data)

# ---------------------------------------------------------------------------
# POST /api/conversation/reset
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/conversation/reset', methods=['POST'])
def conversation_reset():
    """Clear in-process conversation history for a session."""
    body = request.get_json() or {}
    session_id = body.get('session_id', 'default')
    conversation_histories.pop(session_id, None)
    return jsonify({'status': 'ok', 'message': 'Conversation history cleared'})

# ---------------------------------------------------------------------------
# GET /api/tts/providers
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/tts/providers', methods=['GET'])
def tts_providers_list():
    """List all available TTS providers with metadata."""
    try:
        providers = list_providers(include_inactive=True)
        config_path = (Path(__file__).parent.parent
                       / 'tts_providers' / 'providers_config.json')
        default_provider = 'supertonic'
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                default_provider = config.get('default_provider', 'supertonic')
        except Exception:
            pass
        return jsonify({'providers': providers, 'default_provider': default_provider})
    except Exception as e:
        logger.error(f'Failed to list TTS providers: {e}')
        return jsonify({'error': f'Failed to list providers: {e}'}), 500

# ---------------------------------------------------------------------------
# POST /api/tts/generate
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/tts/generate', methods=['POST'])
def tts_generate():
    """
    Generate speech from text using the specified TTS provider.

    Request JSON:
        text     : str   — text to synthesize (required)
        provider : str   — provider ID (default: supertonic)
        voice    : str   — voice ID (default: provider default)
        lang     : str   — language code (default: en)
        speed    : float — speech speed (default: provider default)
        options  : dict  — provider-specific options
    Returns: WAV audio file
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        text = data.get('text', '').strip()
        if not text:
            return jsonify({'error': 'Text cannot be empty'}), 400

        # Length guard (P7-T3 security audit)
        if len(text) > 2000:
            return jsonify({'error': 'Text too long (max 2000 characters)'}), 400

        provider_id = data.get('provider', 'supertonic')
        voice = data.get('voice', None)
        lang = data.get('lang', 'en')
        speed = data.get('speed', None)
        options = data.get('options', {})

        valid_langs = ['en', 'ko', 'es', 'pt', 'fr', 'zh', 'ja', 'de']
        if lang and lang.lower() not in valid_langs:
            return jsonify({
                'error': f"Invalid language: {lang}. Supported: {', '.join(valid_langs)}"
            }), 400

        if speed is not None:
            try:
                speed = float(speed)
                if speed < 0.25 or speed > 4.0:
                    return jsonify({'error': 'Speed must be between 0.25 and 4.0'}), 400
            except (ValueError, TypeError):
                return jsonify({'error': 'Speed must be a valid number'}), 400

        try:
            provider = get_provider(provider_id)
        except ValueError as e:
            available = ', '.join([p['provider_id'] for p in list_providers()])
            return jsonify({'error': 'Invalid TTS provider', 'available_providers': available}), 400

        logger.info(
            f"TTS request: provider={provider_id}, text='{text[:50]}...', "
            f"voice={voice}, lang={lang}, speed={speed}"
        )

        gen_params = {'text': text}
        if voice is not None:
            gen_params['voice'] = voice
        if lang is not None:
            gen_params['lang'] = lang
        if speed is not None:
            gen_params['speed'] = speed
        gen_params.update(options)

        try:
            audio_bytes = provider.generate_speech(**gen_params)
        except ValueError as e:
            return jsonify({'error': f'Invalid parameter: {e}'}), 400
        except Exception as e:
            logger.error(f'Speech generation failed for {provider_id}: {e}')
            return jsonify({'error': f'Speech generation failed: {e}'}), 500

        provider_format = provider.get_info().get('audio_format', 'wav')
        mime_type = 'audio/mpeg' if provider_format == 'mp3' else 'audio/wav'
        response = make_response(audio_bytes)
        response.headers['Content-Type'] = mime_type
        response.headers['Content-Length'] = len(audio_bytes)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['X-TTS-Provider'] = provider_id
        if voice:
            response.headers['X-TTS-Voice'] = voice
        return response

    except ValueError as e:
        return jsonify({'error': f'Invalid input: {e}'}), 400
    except Exception as e:
        import traceback
        logger.error(f'TTS generate endpoint error: {e}')
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500

# ---------------------------------------------------------------------------
# POST /api/supertonic-tts  (DEPRECATED — use /api/tts/generate)
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/supertonic-tts', methods=['POST'])
def supertonic_tts_endpoint():
    """
    Generate speech via Supertonic TTS (deprecated — prefer /api/tts/generate).

    Request JSON: text, lang, speed, voice_style
    Returns: WAV audio
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        text = data.get('text', '').strip()
        if not text:
            return jsonify({'error': 'Text cannot be empty'}), 400

        lang = data.get('lang', 'en').lower()
        if lang not in ['en', 'ko', 'es', 'pt', 'fr']:
            return jsonify({
                'error': f"Invalid language: {lang}. Supported: en, ko, es, pt, fr"
            }), 400

        speed = float(data.get('speed', 1.0))
        if speed < 0.5 or speed > 2.0:
            return jsonify({'error': 'Speed must be between 0.5 and 2.0'}), 400

        voice_style = data.get('voice_style', 'M1').upper()
        valid_voices = ['M1', 'M2', 'M3', 'M4', 'M5', 'F1', 'F2', 'F3', 'F4', 'F5']
        if voice_style not in valid_voices:
            return jsonify({
                'error': f"Invalid voice: {voice_style}. "
                         f"Available: {', '.join(valid_voices)}"
            }), 400

        logger.info(f"Generating speech: {text[:50]}... (lang={lang}, speed={speed})")

        try:
            tts_instance = get_supertonic_for_voice(voice_style)
        except Exception as e:
            logger.error(f'Failed to initialize TTS with voice {voice_style}: {e}')
            return jsonify({'error': f'Failed to load voice style: {e}'}), 500

        try:
            audio_bytes = tts_instance.generate_speech(
                text=text, lang=lang, speed=speed, total_step=16
            )
        except Exception as e:
            logger.error(f'Speech synthesis failed: {e}')
            return jsonify({'error': f'Speech synthesis failed: {e}'}), 500

        response = make_response(audio_bytes)
        response.headers['Content-Type'] = 'audio/wav'
        response.headers['Content-Length'] = len(audio_bytes)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    except ValueError as e:
        return jsonify({'error': f'Invalid input: {e}'}), 400
    except Exception as e:
        import traceback
        logger.error(f'TTS endpoint error: {e}')
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500

# ---------------------------------------------------------------------------
# POST /api/tts/preview  (P4-T5: TTS voice preview)
# ---------------------------------------------------------------------------

_PREVIEW_TEXT = "Hello! This is a preview of the selected voice."


@conversation_bp.route('/api/tts/preview', methods=['POST'])
def tts_preview():
    """
    Generate a short audio preview for a given TTS voice.

    Request JSON (all optional):
        provider : str  — TTS provider ID (default: 'supertonic')
        voice    : str  — Voice ID (default: provider default, e.g. 'M1')
        text     : str  — Custom preview text (max 200 chars; default sample phrase)

    Returns JSON:
        audio_b64 : str  — Base64-encoded WAV audio
        provider  : str  — Provider used
        voice     : str  — Voice used
    """
    try:
        data = request.get_json(silent=True) or {}

        provider_id = str(data.get('provider', 'supertonic')).strip()
        voice = data.get('voice', None)
        text = str(data.get('text', _PREVIEW_TEXT)).strip()[:200] or _PREVIEW_TEXT

        # Validate provider exists
        try:
            get_provider(provider_id)
        except ValueError:
            available = ', '.join([p['provider_id'] for p in list_providers()])
            return jsonify({
                'error': f"Unknown provider: {provider_id}",
                'available_providers': available,
            }), 400

        logger.info(f"TTS preview: provider={provider_id}, voice={voice}, text='{text[:40]}'")

        audio_b64 = _tts_generate_b64(
            text=text,
            voice=voice,
            tts_provider=provider_id,
        )

        if audio_b64 is None:
            return jsonify({'error': 'TTS generation failed — check server logs'}), 500

        return jsonify({
            'audio_b64': audio_b64,
            'provider': provider_id,
            'voice': voice or 'default',
        })

    except Exception as e:
        import traceback
        logger.error(f'TTS preview error: {e}')
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500
