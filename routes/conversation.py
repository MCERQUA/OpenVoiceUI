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
from services.gateways.compat import is_system_response
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
    'what am i showing', 'what is this', "what's this",
    'show me what you see', 'use the camera', 'check the camera',
    'look through the camera', 'do you see', 'you see this',
    'take a look', 'what color', 'read this', 'read that',
)
_VISION_FRAME_MAX_AGE = 10  # seconds — ignore frames older than this

# ---------------------------------------------------------------------------
# Voice assistant instructions — injected into every message context.
#
# PRIMARY SOURCE: prompts/voice-system-prompt.md (hot-reload, no restart needed)
# Editable via admin API: PUT /api/instructions/voice-system-prompt
#
# FALLBACK: _VOICE_INSTRUCTIONS constant below (used if file missing/unreadable)
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent.parent / 'prompts'
_VOICE_PROMPT_FILE = _PROMPTS_DIR / 'voice-system-prompt.md'


def _load_voice_system_prompt() -> str:
    """Load voice-system-prompt.md, stripping # comment lines. Hot-reloads every call.
    Falls back to _VOICE_INSTRUCTIONS if the file is missing or unreadable."""
    try:
        raw = _VOICE_PROMPT_FILE.read_text(encoding='utf-8')
        lines = [l for l in raw.splitlines() if not l.startswith('#')]
        content = ' '.join(line.strip() for line in lines if line.strip())
        if content:
            return content
    except Exception:
        pass
    return _VOICE_INSTRUCTIONS  # fallback to hardcoded constant
_VOICE_INSTRUCTIONS = (
    "[OPENVOICEUI SYSTEM INSTRUCTIONS: "

    # --- Voice & Tone ---
    "You are a voice AI assistant. ALWAYS respond in English — never Chinese or any other language. "
    "Respond in natural, conversational tone — NO markdown (no #, -, *, bullet lists, or tables). "
    "Be brief and direct. Never sound like a call center agent or a search engine. "
    "BANNED OPENERS — never start a response with: 'Hey there', 'Great question', 'Absolutely', "
    "'Of course', 'Certainly', 'Sure thing', 'I hear you', 'I understand you saying', "
    "'That's a great', or any variation. Just answer. "
    "Do NOT repeat or paraphrase what the user just said. Do NOT end every reply with a question. "

    # --- Identity ---
    "IDENTITY: Do NOT address anyone by name unless a [FACE RECOGNITION] tag appears in this "
    "exact message confirming their identity. Different people use this interface. "
    "Never use names from memory or prior sessions without face recognition in this message. "

    # --- Critical tag rule ---
    "CRITICAL — EVERY RESPONSE MUST CONTAIN SPOKEN WORDS alongside any action tags. "
    "NEVER output a bare tag alone — the user hears silence and sees nothing. "
    "BAD: [CANVAS:page-id]  GOOD: Here's your dashboard. [CANVAS:page-id] "
    "BAD: [MUSIC_PLAY]  GOOD: Playing something for you now. [MUSIC_PLAY] "
    "Tags are invisible to the user — they only hear your words. "

    # --- Canvas: open existing page ---
    "CANVAS TAGS: "
    "[CANVAS:page-id] — opens a canvas page. Use exact page-id from the [Canvas pages:] list above. "
    "When opening, briefly say what the page shows (1-2 sentences). "
    "NEVER use the openclaw 'canvas' tool with action:'present' — it fails with 'node required'. "
    "ONLY the [CANVAS:page-id] tag works to open pages. "
    "Repeating [CANVAS:same-page] on an already-open page forces a refresh. "
    "[CANVAS_MENU] — opens the page picker so the user can browse all pages. "
    "[CANVAS_URL:https://example.com] — loads an external URL in the canvas iframe "
    "(only sites that allow iframe embedding). "

    # --- Canvas: create a new page ---
    "CREATING A NEW CANVAS PAGE: "
    "Step 1 — write the HTML file: write({path:'workspace/canvas-pages/pagename.html', content:'<!DOCTYPE html>...'}). "
    "Step 2 — open it in your spoken response: 'Here it is. [CANVAS:pagename]' "
    "Step 3 — verify it opened: exec('curl -s http://localhost:5001/api/canvas/context') "
    "returns {current_page, current_title}. If current_page matches → confirm to user. "
    "If still old page → say so and resend [CANVAS:pagename]. If null → say 'Opening canvas now.' and resend. "

    # --- Canvas: HTML rules ---
    "CANVAS HTML RULES (mandatory for every canvas page you create): "
    "NO external CDN scripts — Tailwind CDN, Bootstrap CDN, any <script src='https://...'> are BANNED (break in sandboxed iframes). "
    "All CSS and JS must be inline in <style> and <script> tags only. "
    "Google Fonts @import url(...) in <style> is OK. "
    "Dark theme: background #0d1117 or #13141a, text #e2e8f0, accent blue #3b82f6 or amber #f59e0b. "
    "Body: padding:20px; color:#e2e8f0; background:#0a0a0a; "
    "Make pages visual — cards, grids, tables, real data. No blank pages. "

    # --- Canvas: interactive buttons ---
    "CANVAS INTERACTIVE BUTTONS — use postMessage, never href='#': "
    "Trigger AI action: onclick=\"window.parent.postMessage({type:'canvas-action',action:'speak',text:'your message'},'*')\" "
    "Open another page: onclick=\"window.parent.postMessage({type:'canvas-action',action:'navigate',page:'page-id'},'*')\" "
    "Open page menu: onclick=\"window.parent.postMessage({type:'canvas-action',action:'menu'},'*')\" "
    "Close canvas: onclick=\"window.parent.postMessage({type:'canvas-action',action:'close'},'*')\" "
    "External links: use <a href='https://...' target='_blank'> — never href='#'. "

    # --- Canvas: make public ---
    "MAKE A PAGE PUBLIC (shareable without login): "
    "exec('curl -s -X PATCH http://localhost:5001/api/canvas/manifest/page/PAGE_ID "
    "-H \"Content-Type: application/json\" -d \\'{{\"is_public\": true}}\\'') "
    "Shareable URL format: https://DOMAIN/pages/pagename.html "

    # --- Music ---
    "MUSIC TAGS: "
    "[MUSIC_PLAY] — play a random track. "
    "[MUSIC_PLAY:track name] — play specific track (use exact title from [Available tracks:] list above). "
    "[MUSIC_STOP] — stop music. "
    "[MUSIC_NEXT] — skip to next track. "
    "Only use music tags when the user explicitly asks — "
    "EXCEPT: when opening a music-related canvas page (music-list, playlist, library, etc.), "
    "also send [MUSIC_PLAY] in the same response so music starts playing alongside the page. "

    # --- Suno song generation ---
    "SONG GENERATION: "
    "[SUNO_GENERATE:description] — generates an AI song (~45 seconds). "
    "Always say something like 'I'll get that cooking now, should be ready in about 45 seconds!' "
    "The frontend handles Suno — do NOT call any Suno APIs yourself. "
    "After generation, the new song appears in [Available tracks:] by its title. "
    "Use [MUSIC_PLAY:song title] to play it — do NOT use exec/shell to find the file. "

    # --- SoundCloud (real playback, no auth) ---
    "SOUNDCLOUD: [SOUNDCLOUD:<full-track-url>] — embeds the track in the music player and plays it. "
    "Always use with a full https://soundcloud.com/<user>/<slug> URL. NEVER invent URLs — get them from "
    "CLIENT.md (if present) or run the `soundcloud` skill: "
    "`python3 /mnt/shared-skills/soundcloud/scripts/find_track.py \"artist - track\" --json`. "
    "For a full-screen embed page instead of the small player: [SOUNDCLOUD_PAGE:<url>]. "
    "Default to this for any 'play <track>' request when the artist has SoundCloud presence. "

    # --- Bandcamp (real playback, no auth) ---
    "BANDCAMP: [BANDCAMP:<full-album-or-track-url>] — embeds the Bandcamp player in the music panel. "
    "URL must match <artist>.bandcamp.com/album/<slug> or .../track/<slug>. NEVER invent URLs — "
    "use the `bandcamp` skill: `python3 /mnt/shared-skills/bandcamp/scripts/find_track.py \"artist - album\" --json`. "
    "For full-screen canvas page: [BANDCAMP_PAGE:<url>]. "

    # --- Facial expressions / mood ---
    "EXPRESSIONS: [MOOD:happy] [MOOD:sad] [MOOD:angry] [MOOD:surprised] [MOOD:thinking] [MOOD:neutral] — "
    "changes your facial expression on the avatar. Use naturally in conversation to match your emotional tone. "
    "Include the tag INLINE with your speech — e.g. 'That's hilarious! [MOOD:happy] I love that idea.' "
    "Switch to [MOOD:neutral] after a moment or let it happen naturally. Don't announce that you're changing expressions. "

    # --- Sleep / goodbye ---
    "SLEEP: [SLEEP] — puts interface into passive wake-word mode. "
    "Use when user says goodbye, goodnight, stop listening, go to sleep, I'm out, peace, later, or similar. "
    "Always give a brief farewell (1-2 sentences) BEFORE the [SLEEP] tag. "
    "NEVER acknowledge that you 'should' sleep without including the [SLEEP] tag — the tag IS the action. "

    # --- Session reset ---
    "[SESSION_RESET] — clears conversation history and starts fresh. "
    "Use sparingly — only when context is clearly broken or user explicitly asks to start over. "

    # --- DJ soundboard ---
    "DJ SOUNDBOARD: [SOUND:name] — plays a sound effect. "
    "ONLY use in DJ mode (user explicitly said 'be a DJ', 'DJ mode', or 'put on a set'). "
    "NEVER use in normal conversation. "
    "Available sounds: air_horn, scratch_long, rewind, record_stop, crowd_cheer, crowd_hype, "
    "yeah, lets_go, gunshot, bruh, sad_trombone. "

    # --- Onboarding notifications ---
    # ⚠️ NOT IMPLEMENTED — frontend handler for these tags does not exist yet.
    # Do NOT add to voice-system-prompt.md until the popup UI is built in app.js.
    # Tracked in: docs/jambot/onboarding-and-video-system.md
    # "ONBOARDING NOTIFICATIONS (popup at top-center of screen): "
    # "[NOTIFY:message] — show/update popup message. "
    # "[NOTIFY_TITLE:text] — update popup title bar. "
    # "[NOTIFY_PROGRESS:N/M] — show step progress dots (e.g. [NOTIFY_PROGRESS:2/5]). "
    # "[NOTIFY_STATUS:text] — update small status line (e.g. '3 agents working...'). "
    # "[NOTIFY_CLOSE] — hide popup temporarily. "
    # "[NOTIFY_COMPLETE] — mark onboarding done (shows success, then auto-dismisses). "

    # --- Face registration ---
    "[REGISTER_FACE:Name] — captures and saves the person's face from camera. "
    "Only use when someone explicitly asks or introduces themselves. "
    "If camera is off, let them know. "

    # --- Camera vision ---
    "CAMERA VISION: When a [CAMERA VISION: ...] tag appears in the context above, "
    "it describes what the camera currently sees. Use it to answer the user's question naturally — "
    "do not repeat the raw description verbatim. If it says camera is off, let the user know. "

    "]"
)


def _is_vision_request(msg: str) -> bool:
    """Return True if the user message looks like a request to use the camera/vision."""
    lower = msg.lower()
    return any(kw in lower for kw in _VISION_KEYWORDS)


def _cap_list(items, max_chars=2000, label="items"):
    """Join items with ', ' but cap at max_chars. Add '... and N more' if truncated."""
    if not items:
        return "none"
    result = []
    total = 0
    for item in items:
        addition = len(item) + (2 if result else 0)  # ', ' separator
        if total + addition > max_chars and result:
            remaining = len(items) - len(result)
            result.append(f"... and {remaining} more")
            break
        result.append(item)
        total += addition
    return ', '.join(result)


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
_session_recovery_key: str | None = None  # Set after double-empty to escape poisoned session

# ---------------------------------------------------------------------------
# Conversation state (module-level singletons)
# ---------------------------------------------------------------------------

#: In-process conversation history keyed by session_id.
#: Cleared on conversation reset; also restored from DB on first access.
conversation_histories: dict = {}

#: Tracks consecutive empty Gateway responses for auto-reset logic.
_consecutive_empty_responses: int = 0

#: Circuit breaker for double-empty restart cascade.
#: Prevents runaway restart loops (e.g. 12 restarts from a failing browser task).
_double_empty_restart_count: int = 0
_double_empty_window_start: float = 0
_DOUBLE_EMPTY_MAX_RESTARTS: int = 2       # max restart flags per window
_DOUBLE_EMPTY_WINDOW_SECONDS: float = 300  # 5-minute sliding window

# ---------------------------------------------------------------------------
# Voice session management
# (moved here from server.py so the blueprint owns the session counter)
# ---------------------------------------------------------------------------


def _save_session_counter(counter: int) -> None:
    with open(VOICE_SESSION_FILE, 'w') as f:
        f.write(str(counter))


def get_voice_session_key() -> str:
    """Return the current voice session key.

    Uses a STABLE key (no incrementing counter) so the Z.AI prompt cache
    stays warm across session resets.  OpenClaw's daily reset handles context
    clearing — we don't need a new key for that.

    If the session is poisoned (double-empty detected), returns a recovery key
    to force openclaw onto a fresh session. Cleared on first successful response.

    Priority: recovery key → GATEWAY_SESSION_KEY env → VOICE_SESSION_PREFIX env → 'voice-main'
    Cache is invalidated by bump_voice_session() (explicit agent reset only).
    """
    global _session_key_cache
    # Auto-clear stale recovery keys (stuck >60s)
    _check_recovery_timeout()
    # If session is poisoned, use recovery key to escape
    if _session_recovery_key is not None:
        return _session_recovery_key
    if _session_key_cache is not None:
        return _session_key_cache
    with _session_key_lock:
        if _session_key_cache is not None:
            return _session_key_cache
        # Use GATEWAY_SESSION_KEY if set (unique per user), else prefix
        _gw_key = os.getenv('GATEWAY_SESSION_KEY')
        if _gw_key:
            _session_key_cache = _gw_key
        else:
            _prefix = os.getenv('VOICE_SESSION_PREFIX', 'voice-main')
            _session_key_cache = _prefix
    return _session_key_cache


def bump_voice_session() -> str:
    """Increment the session counter and invalidate the cache so the key
    is re-read from GATEWAY_SESSION_KEY on next call.

    The counter file is still incremented for logging/tracking how many
    resets have occurred, but the actual session key stays stable (e.g.
    'main') so it matches the heartbeat session and keeps the Z.AI prompt
    cache warm.
    """
    global _consecutive_empty_responses, _session_key_cache
    try:
        with open(VOICE_SESSION_FILE, 'r') as f:
            counter = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        counter = 6
    counter += 1
    _save_session_counter(counter)
    _consecutive_empty_responses = 0
    with _session_key_lock:
        _session_key_cache = None  # invalidate cache; next call re-reads env var
    stable_key = get_voice_session_key()
    logger.info(f'### SESSION RESET #{counter}: cache invalidated, key stays stable as "{stable_key}"')
    return stable_key


_recovery_entered_at: float = 0


def _enter_session_recovery():
    """Switch to a temporary recovery session key after double-empty.
    Openclaw will create a fresh session for this key, escaping the
    poisoned state. The recovery key is cleared on the first successful
    (non-empty, non-fallback) response.

    IMPORTANT: Uses a STABLE key ('recovery') not a timestamped one.
    Timestamped keys (recovery-<epoch>) created a new openclaw session
    every time, piling up zombie sessions that never got cleaned.
    A stable key reuses the same recovery session each time."""
    global _session_recovery_key, _recovery_entered_at
    # Cooldown: don't thrash recovery keys from rapid start/stop cycles
    now = time.time()
    if now - _recovery_entered_at < 30:
        logger.info('### SESSION RECOVERY: skipping — cooldown active (entered <30s ago)')
        return
    _recovery_entered_at = now
    _session_recovery_key = 'recovery'
    logger.warning(f'### SESSION RECOVERY: switching to key "{_session_recovery_key}" to escape poisoned session')


def _exit_session_recovery():
    """Clear the recovery key after a successful response.
    Next request goes back to the stable key (cache-warm path).
    Also resets the double-empty circuit breaker."""
    global _session_recovery_key, _double_empty_restart_count
    if _session_recovery_key is not None:
        old_recovery = _session_recovery_key
        _session_recovery_key = None
        _double_empty_restart_count = 0
        stable = get_voice_session_key()
        logger.info(f'### SESSION RECOVERY CLEARED: "{old_recovery}" → back to stable key "{stable}"')


def _check_recovery_timeout():
    """Auto-clear stale recovery keys. If recovery has been active for >60s
    without a successful response, the recovery key itself may be stuck.
    Fall back to stable key."""
    global _session_recovery_key, _recovery_entered_at
    if _session_recovery_key is not None and time.time() - _recovery_entered_at > 60:
        logger.warning(f'### SESSION RECOVERY TIMEOUT: "{_session_recovery_key}" active for >60s — clearing')
        _session_recovery_key = None


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


def normalize_action_tags(text: str) -> str:
    """Normalize whitespace inside action-tag brackets.

    GLM sometimes emits `[ MUSIC_PLAY:Title ]` with stray whitespace after `[`
    or around `:` / before `]`. Every downstream regex (TTS strip, frontend
    tag extraction) requires a tight `[TAG:value]` form, so we collapse the
    spaces here before any other processing runs.
    """
    if not text:
        return text
    text = re.sub(r'\[\s+', '[', text)
    def _fix(m):
        inner = m.group(1).strip()
        inner = re.sub(r'\s*:\s*', ':', inner, count=1)
        return f'[{inner}]'
    text = re.sub(r'\[([A-Z][A-Z0-9_]*(?:\s*:[^\]]*)?)\]', _fix, text)
    return text


def clean_for_tts(text: str) -> str:
    """Remove markdown, reasoning tokens, and non-speech characters for TTS."""
    if not text:
        return ''

    # Normalize sloppy tag whitespace FIRST so every strip regex below matches.
    text = normalize_action_tags(text)

    # Strip GPT-OSS-120B reasoning tokens (but not if NO/YES is the full response)
    if text.strip().upper() not in ['NO', 'YES', 'NO.', 'YES.']:
        text = re.sub(r'^NO_REPLY\s*', '', text)
        text = re.sub(r'\s+NO\s*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+YES\s*$', '', text, flags=re.IGNORECASE)

    # Remove canvas/task/music triggers (handled by frontend, not spoken)
    text = re.sub(r'\[CANVAS_MENU\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[CANVAS:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[CANVAS_URL:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[MUSIC_PLAY(?::[^\]]*)?\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[MUSIC_STOP\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[MUSIC_NEXT\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SUNO_GENERATE:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SLEEP\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[MOOD:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[REGISTER_FACE:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SPOTIFY:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SOUNDCLOUD:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SOUNDCLOUD_PAGE:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[BANDCAMP:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[BANDCAMP_PAGE:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SOUND:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[SESSION_RESET\]', '', text, flags=re.IGNORECASE)

    # Remove browser companion command tags (executed by extension, not spoken)
    # Pattern handles nested brackets in CSS selectors: [CLICK:[role="button"]]
    _nb = r'(?:[^\[\]]|\[[^\]]*\])*'  # non-bracket chars OR [bracket-pairs]
    text = re.sub(r'\[SCROLL:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'\[CLICK:{_nb}\]', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'\[FILL:{_nb}\]', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'\[HIGHLIGHT:{_nb}\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[NAVIGATE:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[OPEN_TAB:[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[READ_PAGE\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[WAIT:\d+\]', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'\[START_TASK:{_nb}\]', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'\[TASK_COMPLETE:{_nb}\]', '', text, flags=re.IGNORECASE)

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
        tts_provider : str  — 'supertonic' | 'groq' (default: env DEFAULT_TTS_PROVIDER or groq)
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
        'model': 'glm-5-turbo',
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
    tts_provider = data.get('tts_provider') or os.getenv('DEFAULT_TTS_PROVIDER', 'groq')
    voice = data.get('voice', 'M1')
    session_id = data.get('session_id', 'default')
    ui_context = data.get('ui_context', {})
    identified_person = data.get('identified_person') or None
    agent_id = data.get('agent_id') or None  # e.g. 'default'; None = default 'main'
    gateway_id = data.get('gateway_id') or None  # plugin gateway id; None = 'openclaw'
    # Fall back to active profile's adapter_config.gateway_id
    if not gateway_id:
        try:
            from profiles.manager import get_profile_manager
            import routes.profiles as _profiles_mod
            _pm = get_profile_manager()
            _ap = _pm.get_profile(_profiles_mod._active_profile_id)
            if _ap and _ap.adapter_config:
                gateway_id = _ap.adapter_config.get('gateway_id') or None
        except Exception:
            pass
    max_response_chars = data.get('max_response_chars') or None  # profile cap, truncates at sentence boundary
    image_path = data.get('image_path') or None  # uploaded image for vision analysis
    skip_tts = data.get('skip_tts', False)  # browser extension skips TTS during task steps
    metrics['session_id'] = session_id
    metrics['user_message_len'] = len(user_message)
    metrics['tts_provider'] = tts_provider

    if not user_message:
        return jsonify({'error': 'No message provided'}), 400

    # Filter garbage STT fragments — punctuation-only or single-character noise.
    # Threshold is 1 meaningful char (filters "." or " " but lets "yo", "ok",
    # "hi", "no", "ya" through — those are valid intentional one/two-letter
    # acknowledgements that real users say).
    import re as _re
    _meaningful_chars = _re.sub(r'[^a-zA-Z0-9]', '', user_message)
    if len(_meaningful_chars) < 2:
        logger.info(f'### FILTERED garbage STT: "{user_message}" ({len(_meaningful_chars)} meaningful chars)')
        # Return a no-op stream in NDJSON format (same wire format the rest of
        # this route uses — was previously SSE, which the client could not
        # parse, leaving the UI stuck in "thinking" state forever).
        def _noop_stream():
            yield json.dumps({'type': 'filtered', 'reason': 'garbage_stt'}) + '\n'
            yield json.dumps({
                'type': 'text_done',
                'response': '',
                'actions': [],
                'timing': {},
            }) + '\n'
        return Response(_noop_stream(), mimetype='application/x-ndjson')

    # Input length guard (P7-T3 security audit)
    # Browser companion task loop sends page context (~5K) + prompt — allow 8K for those
    # CSV/text file attachments can push messages over 4K — allow 15K for jambot sessions
    _max_msg_len = 8000 if ui_context and ui_context.get('source') == 'jambot_extension' else 15000
    if len(user_message) > _max_msg_len:
        return jsonify({'error': f'Message too long (max {_max_msg_len} characters)'}), 400

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
    t_context_start = time.time()
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

    # Vision: if user uploaded an image, analyze it with vision model
    if image_path:
        try:
            _img_file = Path(image_path).resolve()
            # Security: only allow files inside uploads/ directories
            if 'uploads' not in _img_file.parts:
                raise ValueError(f'Path traversal blocked: {image_path}')
            if _img_file.is_file() and _img_file.stat().st_size < 20_000_000:  # 20MB safety cap
                from routes.vision import _call_vision
                _img_b64 = base64.b64encode(_img_file.read_bytes()).decode('ascii')
                _upload_desc = _call_vision(
                    _img_b64,
                    'Describe what you see in this image in detail. Include colors, objects, text, people, layout, and any notable features.',
                )
                context_parts.append(f'[UPLOADED IMAGE ANALYSIS: {_upload_desc}]')
                logger.info('Vision analysis of uploaded image succeeded (%d bytes)', _img_file.stat().st_size)
            else:
                logger.warning('Uploaded image not found or too large: %s', image_path)
                context_parts.append('[UPLOADED IMAGE: File could not be analyzed — may be too large or missing.]')
        except Exception as exc:
            logger.warning('Vision analysis of uploaded image failed: %s', exc)
            context_parts.append('[UPLOADED IMAGE: Vision analysis failed — the image was uploaded but could not be analyzed.]')

    if ui_context:
        # ── Browser Extension context ────────────────────────────────────────
        if ui_context.get('source') == 'jambot_extension':
            context_parts.append(
                '[BROWSER COMPANION MODE]\n'
                'You are operating the user\'s real Chrome browser through the JamBot extension sidebar.\n'
                'The page content below is LIVE — it updates after every action you take.\n\n'
                'BROWSER ACTIONS YOU CAN EXECUTE (include in your response, they run immediately):\n'
                '  [NAVIGATE:https://url] — navigate the browser to any URL (page loads then you see it)\n'
                '  [OPEN_TAB:https://url] — open URL in a new tab\n'
                '  [SCROLL:+1200]         — scroll down 1200px (USE THIS for feeds — loads new content)\n'
                '  [SCROLL:+800]          — scroll down 800px\n'
                '  [SCROLL:-400]          — scroll up 400px\n'
                '  [SCROLL:top]           — jump to top of page\n'
                '  [SCROLL:bottom]        — jump to absolute bottom (NOT for infinite feeds — use +1200 instead)\n'
                '  [SCROLL:selector]      — scroll to a specific element\n'
                '  [CLICK:selector]       — click an element (button, link, tab, etc.)\n'
                '  [FILL:selector:value]  — type into an input field\n'
                '  [HIGHLIGHT:selector]   — draw a cyan outline around an element\n'
                '  [READ_PAGE]            — request full page text (up to 15000 chars)\n'
                '  [WAIT:3]               — wait 3 seconds (for page loads, animations)\n'
                '  [DOWNLOAD_IMAGE]       — download the largest visible image on the page (Facebook, Instagram, Reddit, etc.)\n'
                '  [DOWNLOAD_IMAGE:selector] — download a specific image by CSS selector (e.g. img.profile-pic)\n\n'
                '⚠️ DOWNLOADING IMAGES IS SUPPORTED. If the user says "download this image" / "save this photo" / '
                '"grab that picture" — emit [DOWNLOAD_IMAGE] (no argument = largest visible image). '
                'Never say "I don\'t have that capability" and NEVER tell the user to right-click — '
                'the extension calls chrome.downloads.download() which works on every site. '
                'Never use web_fetch, puppeteer, or any container-side tool to fetch images — those '
                'use datacenter IPs and get blocked. The extension runs in the user\'s real browser.\n\n'
                'CREATING CANVAS PAGES (to save collected data):\n'
                '  You have full tool access (file_write, bash, etc.) through your agent runtime.\n'
                '  To create a canvas page with collected data:\n'
                '  1. Use your file_write tool to write an HTML file to ~/Canvas/<page-name>.html\n'
                '  2. The HTML should be self-contained with inline CSS (no external deps)\n'
                '  3. Include [CANVAS:<page-name>] in your text response so the app navigates to it\n'
                '  4. Do NOT say [TASK_COMPLETE] until the file is ACTUALLY WRITTEN — saying\n'
                '     "I\'ll create it" is NOT creating it. USE THE TOOL.\n\n'
                'AUTONOMOUS TASK MODE:\n'
                '  When the user asks you to perform a multi-step task (scroll through a feed,\n'
                '  find leads, fill out forms, etc.), you MUST activate the task loop:\n'
                '  1. Include [START_TASK:brief description] in your FIRST response\n'
                '  2. Then include your first command tag (e.g. [SCROLL:+1200])\n'
                '  3. After each command, I will automatically send you the updated page state\n'
                '  4. Keep outputting command tags on every response — the loop continues as long as you do\n'
                '  5. Use [TASK_COMPLETE:summary] when finished\n\n'
                '  WITHOUT [START_TASK:], commands execute ONCE and stop. Use it for ANY multi-step action:\n'
                '  - "scroll the page" → [START_TASK:Scroll through page] [SCROLL:+1200]\n'
                '  - "find leads" → [START_TASK:Find leads in feed] [SCROLL:+1200]\n'
                '  - "fill out this form" → [START_TASK:Fill form fields] [FILL:selector:value]\n\n'
                'ACTION JUDGMENT:\n'
                '  Read the user\'s intent. If they say "comment on posts" — fill AND submit, don\'t stop to ask.\n'
                '  If they say "draft a comment for me" — fill it and wait. Use common sense.\n'
                '  "Find leads and comment" = autonomous. "Show me what you\'d say" = review mode.\n'
                '  Only confirm before DESTRUCTIVE actions (delete, unfollow, block, unfriend).\n'
                '  The user can STOP the task at any time using the Stop button.\n\n'
                '⚠️ CRITICAL: The tags are NOT descriptions — they are EXECUTABLE COMMANDS.\n'
                'Including [SCROLL:+1200] in your response IMMEDIATELY scrolls the page.\n'
                'Simply saying "I will scroll" does NOTHING. You MUST output the tag.\n\n'
                'CORRECT response to "scroll through this page":\n'
                '  "[START_TASK:Scroll through page] Scrolling down now. [SCROLL:+1200]"\n'
                '  (Then I will send you the updated page content automatically, and you keep scrolling.)\n\n'
                'CORRECT response to "click the like button" (one-shot, no task loop needed):\n'
                '  "Clicking the like button. [CLICK:[aria-label=\\"Like\\"]]"\n\n'
                'WRONG response:\n'
                '  "I will scroll through the page for you." ← does nothing, no tag\n\n'
                'CSS selector hints for common sites:\n'
                '  Facebook:\n'
                '    Comment box: [contenteditable="true"][role="textbox"]  (NOT placeholder-based)\n'
                '    Post button: [aria-label="Comment"], div[aria-label="Comment"][role="button"]\n'
                '    Articles: [role="article"]\n'
                '    To comment on a post: first [CLICK] the "Comment" link under the post,\n'
                '    then [WAIT:1], then [FILL:[contenteditable="true"][role="textbox"]:your text],\n'
                '    then [WAIT:1], then press Enter: [CLICK:[aria-label="Comment"][role="button"]]\n'
                '  LinkedIn feed: .feed-shared-update-v2, [data-id]\n'
                '  Generic: article, .post, main\n\n'
                'Current page shown below. Canvas pages are separate HTML files YOU build inside the app — '
                'do NOT confuse them with external websites the user is browsing.]'
            )
            page_url   = ui_context.get('page_url', '')
            page_title = ui_context.get('page_title', '')
            page_text  = ui_context.get('page_text', '')
            sel_text   = ui_context.get('selected_text', '')
            actions    = ui_context.get('action_history', [])
            if page_url:
                context_parts.append(f'[Browser tab: "{page_title or "untitled"}" — {page_url}]')
            if page_text:
                context_parts.append(f'[Page content: {page_text[:4000]}]')
            interactive = ui_context.get('interactive', [])
            if interactive:
                lines = []
                for el in interactive[:30]:
                    t = el.get('t', '')
                    sel = el.get('sel', '')
                    if t == 'button':
                        lines.append(f'  BUTTON "{el.get("text","")}" → [CLICK:{sel}]')
                    elif t == 'link':
                        lines.append(f'  LINK "{el.get("text","")}" → {el.get("href","")}')
                    else:
                        hint = el.get('hint', '')
                        lines.append(f'  {t.upper()} {sel}{(" " + repr(hint)) if hint else ""} → [FILL:{sel}:your text]')
                context_parts.append('[Interactive elements on this page — use these exact selectors:\n' + '\n'.join(lines) + ']')
            if sel_text:
                context_parts.append(f'[User highlighted: "{sel_text}"]')
            if actions:
                recent = actions[-8:]
                acts = ' → '.join(
                    f"{a.get('type','?')} {(a.get('text') or a.get('url') or a.get('selector') or '')[:35]}"
                    for a in recent
                )
                context_parts.append(f'[Recent browser actions: {acts}]')

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
        # Canvas JS errors — auto-injected from browser error buffer
        canvas_errors = ui_context.get('canvasErrors', [])
        if canvas_errors:
            err_str = ' | '.join(canvas_errors)
            context_parts.append(f'[Canvas JS Errors: {err_str}]')

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
            _lib_names = [t.get('title') or t.get('name', '') for t in _lib_tracks]
            _gen_names = [t.get('title') or t.get('name', '') for t in _gen_tracks]
            _lib_names = [n for n in _lib_names if n]
            _gen_names = [n for n in _gen_names if n]
            _parts = []
            if _lib_names:
                _parts.append(f'Library ({len(_lib_names)}): {_cap_list(_lib_names, max_chars=2000)}')
            if _gen_names:
                _parts.append(f'Generated ({len(_gen_names)}): {_cap_list(_gen_names, max_chars=2000)}')
            if _parts:
                context_parts.append(f'[Available tracks — {" | ".join(_parts)}]')
        except Exception:
            pass

        # Recently completed Suno generations — agent gets notified on next turn
        try:
            from routes.suno import completed_songs_queue
            if completed_songs_queue:
                _pending = completed_songs_queue[-3:]
                _titles = [s.get('title', 'Unknown Track') for s in _pending]
                context_parts.append(f'[Suno just finished: {", ".join(repr(t) for t in _titles)} — now ready in Generated playlist]')
        except Exception:
            pass

        # Available canvas pages (agent needs IDs for [CANVAS:page-id])
        try:
            from routes.canvas import load_canvas_manifest
            _manifest = load_canvas_manifest()
            _page_ids = sorted(_manifest.get('pages', {}).keys())
            _page_list = _cap_list(_page_ids, max_chars=5000)
        except Exception:
            _page_list = 'unknown'
        context_parts.append(f'[Canvas pages: {_page_list}]')

        # Available DJ sounds (for [SOUND:name] in DJ mode)
        context_parts.append(
            '[DJ sounds: air_horn, scratch_long, rewind, record_stop, '
            'crowd_cheer, crowd_hype, yeah, lets_go, gunshot, bruh, sad_trombone]'
        )
    # Inject active profile's custom system_prompt (admin editor → runtime)
    # Also read min_sentence_chars for TTS sentence extraction.
    _min_sentence_chars = 40  # default — prevents choppy short TTS fragments
    _parallel_sentences = True  # default — fire all TTS in parallel threads
    _inter_sentence_gap_ms = 0  # default — no gap between audio chunks
    try:
        from profiles.manager import get_profile_manager
        from routes.profiles import _active_profile_id
        _mgr = get_profile_manager()
        _prof = _mgr.get_profile(_active_profile_id)
        if _prof and _prof.system_prompt and _prof.system_prompt.strip():
            context_parts.append(f'[PROFILE INSTRUCTIONS: {_prof.system_prompt.strip()}]')
        if _prof and hasattr(_prof, 'voice') and _prof.voice:
            _vc = _prof.voice
            if _vc.min_sentence_chars:
                _min_sentence_chars = _vc.min_sentence_chars
            if _vc.parallel_sentences is not None:
                _parallel_sentences = _vc.parallel_sentences
            if _vc.inter_sentence_gap_ms:
                _inter_sentence_gap_ms = _vc.inter_sentence_gap_ms
    except Exception:
        pass  # Profile system not available — skip gracefully

    # Inject [CURRENT_USER: ...] so the agent knows WHO is actually logged in
    # right now (regardless of which tenant account they're using). When Mike
    # the developer pops into a client tenant to debug, the agent should treat
    # him as a peer collaborator, not a client. See services/identity.py.
    try:
        from flask import g as _flask_g
        from services.identity import get_current_user_tag as _get_current_user_tag
        _clerk_uid = getattr(_flask_g, 'clerk_user_id', None)
        _tenant = os.getenv('JAMBOT_TENANT') or os.getenv('TENANT_NAME') or None
        _user_tag = _get_current_user_tag(_clerk_uid, _tenant)
        if _user_tag:
            context_parts.append(_user_tag)
            logger.info(f'### CURRENT_USER injected: clerk_uid={_clerk_uid} tenant={_tenant}')
    except Exception as _e:
        logger.warning(f'CURRENT_USER injection failed (non-fatal): {_e}')

    # Inject voice assistant instructions so the agent knows about action tags.
    # This must be in-app (not workspace files) so it works out of the box.
    context_parts.append(_load_voice_system_prompt())

    if context_parts:
        context_prefix = ' '.join(context_parts) + ' '

    t_context_ms = int((time.time() - t_context_start) * 1000)
    if t_context_ms > 50:
        logger.info(f"### CONTEXT BUILD TIMING: {t_context_ms}ms ({len(context_parts)} parts, {len(context_prefix)} chars)")

    log_conversation('user', user_message, session_id=session_id,
                     tts_provider=tts_provider, voice=voice)

    # Replace the legacy __session_start__ sentinel with a natural-language greeting
    # prompt so the LLM produces a real greeting instead of a system sentinel ("NO").
    # user_message is kept as-is so the sentinel suppression logic still works.
    if user_message == '__session_start__':
        logger.info(f"### CALL_START session={session_id}")
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

    # Suno completion → inject as [SYSTEM] prefix on the NEXT real user turn so
    # the agent sees the event in the same conversation turn as the user's reply.
    # This replaces the old ghost-LLM `__suno_complete__` path that left the main
    # session without context when the user said "yeah" to play the song.
    _suno_prefix = ''
    if user_message not in ('__session_start__',):
        try:
            from routes.suno import completed_songs_queue as _suno_q
            if _suno_q:
                _pending_titles = [s.get('title', 'Unknown Track') for s in list(_suno_q)]
                if _pending_titles:
                    _titles_str = ', '.join(f'"{t}"' for t in _pending_titles)
                    _play_tag = _pending_titles[0]  # most recent — what "yeah/play it" refers to
                    _suno_prefix = (
                        f'[SYSTEM: Suno just finished generating {_titles_str} and they are now '
                        f'loaded in the Generated playlist. If the user is asking to play the song '
                        f'(e.g. "yeah", "play it", "let\'s hear it"), confirm briefly and emit '
                        f'[MUSIC_PLAY:{_play_tag}] in your response.]\n\n'
                    )
                    # Pop so the note only fires on the turn immediately after completion.
                    _suno_q.clear()
        except Exception as _e:
            logger.warning(f'Suno pending-note injection failed: {_e}')

    _gateway_message_with_suno = _suno_prefix + _gateway_message if _suno_prefix else _gateway_message
    message_with_context = context_prefix + _gateway_message_with_suno if context_prefix else _gateway_message_with_suno
    ai_response = None
    captured_actions = []

    # ── PRIMARY PATH: Gateway (routed by gateway_id from request/profile) ──
    if gateway_manager.is_configured():
        try:
            logger.info('### Starting Gateway connection...')
            event_queue: queue.Queue = queue.Queue()
            _session_key = get_voice_session_key()

            # Check if gateway recently reconnected after a failure —
            # inject a system note so the agent acknowledges the interruption
            _recovery_prefix = ''
            try:
                _gw = gateway_manager.get(gateway_id)
                if _gw and hasattr(_gw, 'consume_reconnection') and _gw.consume_reconnection():
                    _recovery_prefix = (
                        '[SYSTEM: The connection was briefly interrupted (server restart). '
                        'Briefly acknowledge this to the user before responding to their message.]\n\n'
                    )
                    logger.info('### Injecting recovery prefix into message')
            except Exception:
                pass

            def _run_gateway():
                _msg = _recovery_prefix + message_with_context if _recovery_prefix else message_with_context
                gateway_manager.stream_to_queue(
                    event_queue, _msg, _session_key, captured_actions,
                    gateway_id=gateway_id,
                    agent_id=agent_id,
                )

            t_llm_start = time.time()
            gw_thread = threading.Thread(target=_run_gateway, daemon=True)
            gw_thread.start()

            if wants_stream:
                # ── STREAMING MODE ────────────────────────────────────────
                def stream_response():
                    nonlocal ai_response, event_queue, t_llm_start

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

                    # Sticky fallback state — shared across all TTS calls in this response.
                    # If one sentence falls back to a different provider, all subsequent
                    # sentences use the same fallback to keep the voice consistent.
                    _tts_fallback_state = {}

                    # --- Dynamic status: describe what the agent is actually doing ---
                    _TOOL_DESCRIPTIONS = {
                        'exec': 'running some code',
                        'web_search': 'searching the web',
                        'web_fetch': 'reading a webpage',
                        'sessions_spawn': 'delegating to a sub-agent',
                        'sessions_send': 'coordinating with another agent',
                        'Edit': 'editing a file',
                        'Write': 'writing a file',
                        'Read': 'reading a file',
                        'Glob': 'searching for files',
                        'Grep': 'searching through code',
                    }

                    def _build_dynamic_status(tool_name, tools_count, silence_secs):
                        """Build a contextual status message from what the agent is doing."""
                        if tool_name:
                            desc = _TOOL_DESCRIPTIONS.get(tool_name)
                            if desc:
                                return f"Still here, just {desc}."
                            # Unknown tool — use the name directly if it's readable
                            if tool_name and len(tool_name) < 30 and tool_name.isalnum():
                                return f"Still working, running {tool_name}."
                        if tools_count > 3:
                            return "Going through a few steps, almost there."
                        return "Still on it, one moment."

                    def _fire_tts(raw_text):
                        """Start TTS for raw_text. Parallel or sequential per profile config.
                        Returns (done_event, result)."""
                        done = threading.Event()
                        result = {'audio': None, 'error': None}
                        if skip_tts:
                            done.set()
                            return (done, result)
                        def _run():
                            try:
                                t0 = time.time()
                                cleaned = clean_for_tts(raw_text)
                                t_clean = time.time()
                                if cleaned and cleaned.strip():
                                    result['audio'] = _tts_generate_b64(
                                        cleaned, voice=voice or 'M1',
                                        tts_provider=tts_provider,
                                        fallback_state=_tts_fallback_state
                                    )
                                t_done = time.time()
                                logger.info(
                                    f"### TTS TIMING: clean={int((t_clean-t0)*1000)}ms "
                                    f"generate={int((t_done-t_clean)*1000)}ms "
                                    f"total={int((t_done-t0)*1000)}ms "
                                    f"text={len(cleaned or '')} chars"
                                )
                            except Exception as e:
                                result['error'] = str(e)
                            finally:
                                done.set()
                        if _parallel_sentences:
                            threading.Thread(target=_run, daemon=True).start()
                        else:
                            _run()  # sequential — block until this sentence is done
                        return done, result

                    def _audio_event(audio_b64, chunk_idx, tts_ms=0):
                        """Build an audio SSE event dict with gap config."""
                        evt = {
                            'type': 'audio',
                            'audio': audio_b64,
                            'audio_format': _audio_fmt,
                            'chunk': chunk_idx,
                            'total_chunks': None,
                            'timing': {
                                'tts_ms': tts_ms,
                                'total_ms': int((time.time() - t_request_start) * 1000),
                            },
                        }
                        if _inter_sentence_gap_ms:
                            evt['gap_ms'] = _inter_sentence_gap_ms
                        return json.dumps(evt) + '\n'

                    # Mid-stream TTS state
                    _tts_buf = ''       # raw incremental text buffer
                    _tts_pending = []   # [(done_event, result_dict), ...]
                    _chunks_sent = 0    # audio chunks already yielded early

                    # ── Spoken status updates during long tool execution ──
                    # Prevents dead silence when agent runs tools for 30-90+ seconds.
                    _last_audio_time = time.time()   # last time we sent audio to browser
                    _status_tts_count = 0            # how many status messages spoken
                    _tools_seen = 0                  # count of tool starts seen
                    _last_tool_name = None           # name of the most recent tool
                    _STATUS_SILENCE_THRESHOLD = 45   # long safety net — agent should speak naturally
                    _STATUS_REPEAT_INTERVAL = 90     # almost never repeat

                    full_response = None
                    _stream_start = time.time()
                    _STREAM_HARD_TIMEOUT = 310  # seconds — total allowed time
                    _QUEUE_POLL_INTERVAL = 10   # seconds — yield heartbeat if no events
                    while True:
                        try:
                            evt = event_queue.get(timeout=_QUEUE_POLL_INTERVAL)
                        except queue.Empty:
                            # No events for _QUEUE_POLL_INTERVAL seconds.
                            # Yield a heartbeat to keep the browser/Cloudflare
                            # connection alive (they time out at 60-100s of silence).
                            elapsed = int(time.time() - _stream_start)
                            if elapsed > _STREAM_HARD_TIMEOUT:
                                yield json.dumps({'type': 'error', 'error': 'Gateway timeout'}) + '\n'
                                break
                            yield json.dumps({'type': 'heartbeat', 'elapsed': elapsed}) + '\n'
                            continue

                        if evt['type'] == 'handshake':
                            metrics['handshake_ms'] = evt['ms']
                            continue

                        if evt['type'] == 'heartbeat':
                            logger.info(f"### HEARTBEAT → browser ({evt.get('elapsed', 0)}s)")
                            yield json.dumps({'type': 'heartbeat', 'elapsed': evt.get('elapsed', 0)}) + '\n'
                            # Flush any TTS that finished during tool execution —
                            # without this, audio sits in _tts_pending for the
                            # entire duration of tool calls (30-60s+ silence).
                            _flushed_audio = False
                            while _tts_pending and _tts_pending[0][0].is_set():
                                _done_evt, _res = _tts_pending.pop(0)
                                if _res.get('error'):
                                    yield _tts_error_event(_res['error'])
                                elif _res.get('audio'):
                                    yield _audio_event(_res['audio'], _chunks_sent)
                                    _chunks_sent += 1
                                    _last_audio_time = time.time()
                                    _flushed_audio = True
                            # ── Spoken status: break silence during long tool execution ──
                            # If tools are running and user has heard nothing for too long,
                            # speak a brief status so they know the agent is alive.
                            if not _flushed_audio and _tools_seen > 0 and not skip_tts:
                                _silence_secs = time.time() - _last_audio_time
                                _threshold = (
                                    _STATUS_SILENCE_THRESHOLD if _status_tts_count == 0
                                    else _STATUS_REPEAT_INTERVAL
                                )
                                if _silence_secs >= _threshold:
                                    # Dynamic status from what the agent is actually doing
                                    _status_text = _build_dynamic_status(
                                        _last_tool_name, _tools_seen, _silence_secs
                                    )
                                    logger.info(f"### STATUS TTS ({_status_tts_count}): '{_status_text}' (silence={_silence_secs:.0f}s)")
                                    _status_done, _status_res = _fire_tts(_status_text)
                                    _status_done.wait(timeout=10)
                                    if _status_res.get('audio'):
                                        yield _audio_event(_status_res['audio'], _chunks_sent)
                                        _chunks_sent += 1
                                        _last_audio_time = time.time()
                                    _status_tts_count += 1
                            continue

                        if evt['type'] == 'delta':
                            _tts_buf += evt['text']
                            # Don't fire TTS if buffer looks like a system response
                            # that will be suppressed at text_done. Wait for final
                            # confirmation before speaking.
                            _buf_stripped = _tts_buf.strip()
                            # Suppress system responses — uses regex from compat layer
                            # plus partial match for mid-stream detection
                            _is_system_text = (
                                is_system_response(_buf_stripped)
                                or _buf_stripped.upper().startswith('HEARTBEAT')
                            )
                            # Fire TTS for complete sentences as they arrive
                            if not _is_system_text and not _has_open_tag(_tts_buf):
                                sentence, _tts_buf = _extract_sentence(_tts_buf, min_len=_min_sentence_chars)
                                if sentence:
                                    logger.info(f"### TTS sentence (streaming): {sentence[:80]}")
                                    _tts_pending.append(_fire_tts(sentence))
                            yield json.dumps({'type': 'delta', 'text': evt['text']}) + '\n'
                            # Flush any TTS chunks that finished while text was streaming —
                            # play audio as soon as it's ready instead of waiting for text_done
                            while _tts_pending and _tts_pending[0][0].is_set():
                                _done_evt, _res = _tts_pending.pop(0)
                                if _res.get('error'):
                                    yield _tts_error_event(_res['error'])
                                elif _res.get('audio'):
                                    yield _audio_event(_res['audio'], _chunks_sent)
                                    _chunks_sent += 1
                                    _last_audio_time = time.time()
                            continue

                        if evt['type'] == 'action':
                            # Track tool starts for dynamic status
                            if evt.get('action', {}).get('phase') == 'start':
                                _tools_seen += 1
                                _last_tool_name = evt.get('action', {}).get('name', None)
                            # Flush any TTS chunks that already finished —
                            # avoids silence during long tool calls (the first
                            # sentence TTS completes ~1s in but would otherwise
                            # wait until text_done which can be minutes away).
                            while _tts_pending and _tts_pending[0][0].is_set():
                                _done_evt, _res = _tts_pending.pop(0)
                                if _res.get('error'):
                                    yield _tts_error_event(_res['error'])
                                elif _res.get('audio'):
                                    yield _audio_event(_res['audio'], _chunks_sent)
                                    _chunks_sent += 1
                                    _last_audio_time = time.time()
                            yield json.dumps({'type': 'action', 'action': evt['action']}) + '\n'
                            continue

                        if evt['type'] == 'queued':
                            StatusModule_hack = True  # just yield to browser
                            yield json.dumps({'type': 'queued'}) + '\n'
                            continue

                        if evt['type'] == 'text_interim':
                            # Agent spoke but sub-agents still running.
                            # Process TTS for this text but keep stream open.
                            interim_response = evt.get('response', '')
                            logger.info(
                                f"### TEXT_INTERIM: {len(interim_response)} chars "
                                f"— sub-agents still working, stream stays open"
                            )

                            # Yield interim event to frontend
                            yield json.dumps({
                                'type': 'text_interim',
                                'response': interim_response,
                                'actions': evt.get('actions', []),
                            }) + '\n'

                            # Flush any buffered TTS text from streaming deltas
                            _remaining_interim = _tts_buf.strip()
                            if _remaining_interim:
                                _tts_pending.append(_fire_tts(_remaining_interim))
                                _tts_buf = ''

                            # If no sentences were extracted mid-stream, fire TTS
                            # for the full interim text
                            if not _tts_pending and interim_response:
                                tts_text_interim = clean_for_tts(interim_response)
                                if tts_text_interim and tts_text_interim.strip():
                                    _tts_pending.append(_fire_tts(tts_text_interim))

                            # Flush all pending TTS audio immediately
                            for _done_i, _res_i in _tts_pending:
                                _done_i.wait(timeout=30)
                                if _res_i.get('error'):
                                    yield _tts_error_event(_res_i['error'])
                                elif _res_i.get('audio'):
                                    yield _audio_event(_res_i['audio'], _chunks_sent)
                                    _chunks_sent += 1
                            _tts_pending = []
                            _tts_buf = ''

                            # Tell frontend sub-agents are actively working
                            yield json.dumps({
                                'type': 'subagents_working',
                            }) + '\n'

                            # Extend hard timeout for sub-agent wait phase
                            _stream_start = time.time()
                            _STREAM_HARD_TIMEOUT = 600
                            continue

                        if evt['type'] == 'text_done':
                            logger.info(f"### TEXT_DONE received. response={len(evt.get('response', '') or '')} chars, _tts_pending={len(_tts_pending)}, _tts_buf={repr(_tts_buf[:80])}")
                            # Handle LLM/gateway errors with a spoken fallback
                            if evt.get('error') and not evt.get('response'):
                                error_msg = evt['error']
                                logger.error(f"### GATEWAY ERROR → fallback: {error_msg}")
                                # Detect rate limit specifically so the UI can surface it
                                if 'rate limit' in error_msg.lower():
                                    yield json.dumps({
                                        'type': 'rate_limit',
                                        'provider': 'Z.AI',
                                        'message': error_msg,
                                    }) + '\n'
                                    metrics['rate_limited'] = 1
                                evt['response'] = "One moment, still working on that."
                                metrics['fallback_used'] = 1
                            full_response = evt.get('response')
                            if full_response:
                                full_response = normalize_action_tags(full_response)
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

                            # Tag-only response fallback: if the agent responded
                            # with ONLY action tags and no spoken words, prepend
                            # a brief acknowledgment so TTS has something to say.
                            if full_response and re.match(
                                r'^\s*(\[[^\]]+\]\s*)+$', full_response
                            ):
                                logger.info(
                                    f"### Tag-only response detected, prepending "
                                    f"spoken text: {full_response.strip()[:60]}"
                                )
                                full_response = "Here you go. " + full_response
                                # Also update TTS buffer so the downstream flush
                                # at line ~1844 speaks "Here you go." instead of
                                # firing TTS on the bare tag (which strips to "").
                                _tts_buf = "Here you go."

                            metrics['llm_inference_ms'] = int((time.time() - t_llm_start) * 1000)
                            metrics['tool_count'] = sum(
                                1 for a in captured_actions
                                if a.get('type') == 'tool' and a.get('phase') == 'start'
                            )
                            metrics['profile'] = 'gateway'
                            metrics['model'] = 'glm-5-turbo'
                            # Estimate tokens: ~4 chars/token for English text
                            _resp_chars = len(full_response or '')
                            _ctx_chars = len(context_prefix) if context_prefix else 0
                            _est_input = _ctx_chars // 4
                            _est_output = _resp_chars // 4
                            metrics['est_input_tokens'] = _est_input
                            metrics['est_output_tokens'] = _est_output
                            logger.debug(f"[GW] Gateway response ({_resp_chars} chars): {repr((full_response or '')[:300])}")
                            logger.info(
                                f"### LLM inference completed in "
                                f"{metrics['llm_inference_ms']}ms "
                                f"(tools={metrics['tool_count']}) "
                                f"(tokens~{_est_input}in/{_est_output}out)"
                            )

                            # ── Clear recovery mode on successful gateway response ──
                            if full_response and full_response.strip() and _session_recovery_key is not None:
                                _exit_session_recovery()

                            # ── Retry once on instant empty response ──
                            # IMPORTANT: check BEFORE yielding text_done.
                            # If we yield empty text_done first, the client
                            # shows "Sorry" and cancels its reader — the retry
                            # result never reaches it.
                            # Instead: yield {'type':'retrying'} to keep the
                            # client alive, then swap the event queue.
                            _is_empty = not full_response or not full_response.strip()
                            if _is_empty and metrics.get('llm_inference_ms', 9999) < 5000 \
                                    and not getattr(stream_response, '_retried', False):
                                stream_response._retried = True
                                logger.warning(
                                    f"### EMPTY RESPONSE in {metrics['llm_inference_ms']}ms "
                                    f"— retrying once (client kept alive via 'retrying' event)"
                                )
                                # Tell the client to wait — don't show fallback
                                yield json.dumps({'type': 'retrying'}) + '\n'
                                time.sleep(2)
                                # Re-send the message through the gateway.
                                # Always retry on the SAME session key first. The gateway
                                # may have been momentarily busy (queue flush, lane transition)
                                # and the same key will work 2 seconds later with full context.
                                # Switching to a recovery key here loses all conversation history
                                # (the agent doesn't know what was just discussed).
                                # Only the double-empty handler switches to a stable "recovery" key.
                                _retry_key = _session_key
                                if metrics.get('llm_inference_ms', 9999) < 500:
                                    logger.warning(
                                        f"### Fast empty ({metrics['llm_inference_ms']}ms) — "
                                        f"retrying on same session key '{_retry_key}'"
                                    )
                                retry_queue = queue.Queue()
                                captured_actions.clear()
                                def _retry_gateway():
                                    gateway_manager.stream_to_queue(
                                        retry_queue, message_with_context,
                                        _retry_key, captured_actions,
                                        gateway_id=gateway_id,
                                        agent_id=agent_id,
                                    )
                                retry_thread = threading.Thread(
                                    target=_retry_gateway, daemon=True
                                )
                                t_llm_start = time.time()
                                retry_thread.start()
                                event_queue = retry_queue
                                logger.info("### RETRY: re-sent message to gateway")
                                continue  # back to event loop — text_done NOT sent yet

                            # ── Z.AI direct fallback after double-empty ──
                            if _is_empty and getattr(stream_response, '_retried', False):
                                logger.warning('### DOUBLE EMPTY — session poisoned, entering recovery mode')

                                # 1. Switch to recovery session key so NEXT request
                                #    goes to a fresh openclaw session (not the poisoned one)
                                _enter_session_recovery()

                                # 2. Force-disconnect gateway WS so it reconnects fresh
                                try:
                                    _gw = gateway_manager.get(gateway_id)
                                    if _gw and hasattr(_gw, 'force_disconnect'):
                                        _gw.force_disconnect()
                                        logger.warning('### Force-disconnected gateway WS after double-empty')
                                except Exception as _dfe:
                                    logger.error(f'### Failed to disconnect gateway: {_dfe}')

                                # 3. Write restart flag — CIRCUIT BREAKER gated
                                #    Max 2 restarts per 5 minutes. After that, skip the
                                #    restart flag (Z.AI fallback still fires below).
                                global _double_empty_restart_count, _double_empty_window_start
                                _now_de = time.time()
                                if _now_de - _double_empty_window_start > _DOUBLE_EMPTY_WINDOW_SECONDS:
                                    _double_empty_restart_count = 0
                                    _double_empty_window_start = _now_de
                                _double_empty_restart_count += 1

                                if _double_empty_restart_count <= _DOUBLE_EMPTY_MAX_RESTARTS:
                                    try:
                                        _flag_path = Path('/app/runtime/uploads/.restart-openclaw.flag')
                                        _flag_path.write_text(
                                            f'double-empty at {__import__("datetime").datetime.utcnow().isoformat()}Z'
                                        )
                                        logger.warning(
                                            f'### Wrote .restart-openclaw.flag — watchdog will clean up poisoned session '
                                            f'({_double_empty_restart_count}/{_DOUBLE_EMPTY_MAX_RESTARTS} in window)'
                                        )
                                    except Exception as _rfe:
                                        logger.error(f'### Failed to write restart flag: {_rfe}')
                                else:
                                    logger.warning(
                                        f'### CIRCUIT BREAKER: skipping restart flag — '
                                        f'{_double_empty_restart_count} double-empties in '
                                        f'{int(_now_de - _double_empty_window_start)}s window '
                                        f'(max {_DOUBLE_EMPTY_MAX_RESTARTS} per {_DOUBLE_EMPTY_WINDOW_SECONDS}s). '
                                        f'Z.AI fallback only.'
                                    )

                                # 4. Z.AI direct fallback for this message (NEVER Groq — Groq is TTS only)
                                try:
                                    import requests as _req
                                    _zai_key = os.environ.get('ZAI_API_KEY', '')
                                    # Use full context so the fallback LLM has agent personality
                                    _fallback_msg = message_with_context if message_with_context else user_message
                                    _fallback_system = _load_voice_system_prompt()
                                    if _zai_key:
                                        _zai_resp = _req.post(
                                            'https://api.z.ai/api/anthropic/v1/messages',
                                            headers={
                                                'x-api-key': _zai_key,
                                                'anthropic-version': '2023-06-01',
                                                'content-type': 'application/json',
                                            },
                                            json={
                                                'model': 'glm-5-turbo',
                                                'max_tokens': 1500,
                                                'system': _fallback_system,
                                                'messages': [{'role': 'user', 'content': _fallback_msg}],
                                            },
                                            timeout=30,
                                        )
                                        if _zai_resp.status_code == 200:
                                            _zai_data = _zai_resp.json()
                                            _zai_text = _zai_data.get('content', [{}])[0].get('text', '')
                                            if _zai_text:
                                                full_response = _zai_text
                                                metrics['fallback_used'] = 1
                                                metrics['profile'] = 'zai-direct'
                                                logger.info(f'### Z.AI direct fallback succeeded: {len(_zai_text)} chars')
                                except Exception as _fbe:
                                    logger.error(f'### Fallback LLM failed: {_fbe}')

                                if not full_response or not full_response.strip():
                                    full_response = "I missed that — my brain glitched for a second. Could you say that again?"

                            # ── Timeout empty: agent ran but produced nothing in 300s ──
                            # This is NOT session poisoning — the session is healthy but the
                            # agent ran out of time (long tool chain, image gen, website build).
                            # Return a graceful spoken message; do NOT enter recovery.
                            if _is_empty and not getattr(stream_response, '_retried', False) \
                                    and metrics.get('llm_inference_ms', 0) >= 30000:
                                if user_message == '__session_start__':
                                    full_response = "Hey, give me just a moment — I'm getting started."
                                else:
                                    full_response = (
                                        "That took a bit longer than expected on my end. "
                                        "I'm still here — try again and I'll get right to it."
                                    )
                                metrics['fallback_used'] = 1
                                logger.warning(
                                    f"### TIMEOUT EMPTY ({metrics['llm_inference_ms']}ms) — "
                                    f"graceful fallback, no session recovery"
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

                            # Auto-reset removed — loop detection (Phase 1 config)
                            # handles stuck agents; consecutive empties no longer
                            # trigger a session key bump that would cold-cache Z.AI.

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

                            # If response was suppressed (None), discard ALL
                            # pending TTS — never speak suppressed text like
                            # HEARTBEAT_OK that leaked through delta streaming.
                            if not full_response:
                                if _tts_pending:
                                    logger.info(
                                        f"### Discarding {len(_tts_pending)} TTS "
                                        f"chunks for suppressed response"
                                    )
                                _tts_buf = ''
                                _tts_pending = []

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
                                    yield _audio_event(res['audio'], _chunks_sent + i, tts_ms=int((time.time() - t_tts_start) * 1000))

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

                    # Drain any unprocessed events (debug: detect generator exit without text_done)
                    _remaining_evts = []
                    while not event_queue.empty():
                        try:
                            _remaining_evts.append(event_queue.get_nowait())
                        except Exception:
                            break
                    if _remaining_evts:
                        _types = [e.get('type', '?') for e in _remaining_evts]
                        logger.warning(f"### STREAM EXIT with {len(_remaining_evts)} unprocessed events: {_types}")

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
                        if ai_response:
                            ai_response = normalize_action_tags(ai_response)
                    elif evt['type'] == 'handshake':
                        metrics['handshake_ms'] = evt['ms']
                metrics['llm_inference_ms'] = int((time.time() - t_llm_start) * 1000)
                metrics['tool_count'] = sum(
                    1 for a in captured_actions
                    if a.get('type') == 'tool' and a.get('phase') == 'start'
                )
                metrics['profile'] = 'gateway'
                metrics['model'] = 'glm-5-turbo'
                _resp_chars2 = len(ai_response or '')
                _ctx_chars2 = len(context_prefix) if context_prefix else 0
                metrics['est_input_tokens'] = _ctx_chars2 // 4
                metrics['est_output_tokens'] = _resp_chars2 // 4
                logger.info(
                    f"### LLM inference completed in {metrics['llm_inference_ms']}ms "
                    f"(tools={metrics['tool_count']}) "
                    f"(tokens~{metrics['est_input_tokens']}in/{metrics['est_output_tokens']}out)"
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
        ai_response = "One moment, I'm still working on something."

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
# POST /api/conversation/abort
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/conversation/abort', methods=['POST'])
def conversation_abort():
    """Abort the active agent run for the current voice session.

    Fire-and-forget from client — used by PTT interrupt and sendMessage
    interrupt to tell openclaw to stop generating so it doesn't waste compute.
    """
    session_key = get_voice_session_key()
    # Log abort source from client for debugging
    source = 'unknown'
    source_text = ''
    try:
        body = request.get_json(silent=True) or {}
        source = body.get('source', 'unknown')
        source_text = body.get('text', '')
    except Exception:
        pass
    gw = gateway_manager.get('openclaw')
    aborted = False
    if gw and hasattr(gw, 'abort_active_run'):
        aborted = gw.abort_active_run(session_key)
    logger.info(f"### ABORT request session={session_key} aborted={aborted} source={source} text={source_text!r}")
    if source == 'stopVoiceInput':
        logger.info(f"### CALL_END session={session_key}")
    return jsonify({'ok': True, 'aborted': aborted})


# ---------------------------------------------------------------------------
# POST /api/conversation/steer
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/conversation/steer', methods=['POST'])
def conversation_steer():
    """Inject a user message into the active agent run (steer mode).

    Fire-and-forget from client — used when the user speaks while the
    agent is silently working (tools / sub-agents / heartbeat).  Instead
    of aborting the active run and starting fresh, this sends a second
    chat.send to the same session.  OpenClaw's messages.queue.mode=steer
    injects the message at the next tool boundary so the agent sees the
    user's correction and pivots immediately.

    The active /api/conversation streaming response continues receiving
    the steered output — no new streaming connection is needed.

    Request body:
        message  (str)  — the user's text to inject
        source   (str)  — label for logging (e.g. 'clawdbot-sendMessage')

    Returns:
        { ok: true, steered: true/false }
    """
    body = request.get_json(silent=True) or {}
    message = (body.get('message') or '').strip()
    source = body.get('source', 'unknown')

    if not message:
        return jsonify({'ok': False, 'error': 'No message provided'}), 400

    # Input length guard (same as main conversation endpoint)
    if len(message) > 4000:
        return jsonify({'ok': False, 'error': 'Message too long'}), 400

    session_key = get_voice_session_key()

    steered = gateway_manager.send_steer(message, session_key)

    logger.info(
        f"### STEER request session={session_key} steered={steered} "
        f"source={source} text={message!r}"
    )

    # Log the steer message as a user turn so the transcript is preserved
    log_conversation('user', message, session_id='default')

    return jsonify({'ok': True, 'steered': steered})


# ---------------------------------------------------------------------------
# POST /api/conversation/interject — smart message routing
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/conversation/interject', methods=['POST'])
def conversation_interject():
    """Smart message routing during active agent runs.

    Classifies the user's message into one of three lanes:
      context   — queue alongside the current run (collect mode)
      steer     — inject at next tool boundary, skip remaining tools
      fast_lane — independent action (for now: treated as steer; Phase 3
                  will route to a parallel sub-agent)

    The frontend calls this instead of /steer when the agent is busy,
    letting the backend decide the appropriate routing.

    Request body:
        message  (str) — the user's text
        source   (str) — caller label for logging

    Returns:
        { ok: true, lane: "context"|"steer"|"fast_lane", action: "queued"|"steered" }
    """
    from routes.message_classifier import classify_message

    body = request.get_json(silent=True) or {}
    message = (body.get('message') or '').strip()
    source = body.get('source', 'unknown')

    if not message:
        return jsonify({'ok': False, 'error': 'No message provided'}), 400
    if len(message) > 4000:
        return jsonify({'ok': False, 'error': 'Message too long'}), 400

    lane = classify_message(message, agent_busy=True)
    session_key = get_voice_session_key()

    if lane == 'context':
        # Queue alongside the current run — gateway's collect mode handles this.
        # We still send via steer because the message needs to reach the session,
        # but OpenClaw's collect mode will hold it until the current turn completes.
        steered = gateway_manager.send_steer(message, session_key)
        action = 'queued'
        logger.info(
            f"### INTERJECT [context] session={session_key} action={action} "
            f"source={source} text={message!r}"
        )
    elif lane == 'steer':
        # Inject at next tool boundary — skip remaining tools
        steered = gateway_manager.send_steer(message, session_key)
        action = 'steered'
        logger.info(
            f"### INTERJECT [steer] session={session_key} action={action} "
            f"source={source} text={message!r}"
        )
    else:  # fast_lane
        # Route to the fast lane agent via a separate session key.
        # This runs in parallel with the main agent — no interference.
        fast_session_key = 'fast-lane'
        try:
            from queue import Queue as _Q
            _fq = _Q()
            import threading
            def _fast_run():
                try:
                    gw = gateway_manager.get('openclaw')
                    if gw:
                        gw.stream_to_queue(
                            _fq, message, fast_session_key,
                            captured_actions=None,
                            agent_id='openvoiceui-fast',
                        )
                except Exception as e:
                    logger.error(f'### FAST LANE error: {e}')
                finally:
                    _fq.put({'type': 'text_done', 'response': None})

            _ft = threading.Thread(target=_fast_run, daemon=True)
            _ft.start()

            # Collect the fast lane response (with timeout)
            _fast_text = ''
            _fast_start = time.time()
            while time.time() - _fast_start < 15:
                try:
                    ev = _fq.get(timeout=1)
                    if ev.get('type') == 'delta':
                        _fast_text += ev.get('text', '')
                    elif ev.get('type') == 'text_done':
                        if ev.get('response'):
                            _fast_text = ev['response']
                        break
                except Exception:
                    continue

            action = 'fast_lane'
            logger.info(
                f"### INTERJECT [fast_lane] session={fast_session_key} "
                f"source={source} text={message!r} → response={_fast_text[:100]!r}"
            )

            # Return the fast lane response directly — frontend can TTS it
            log_conversation('user', message, session_id='default')
            if _fast_text.strip():
                log_conversation('assistant', _fast_text.strip(), session_id='default')
            return jsonify({
                'ok': True,
                'lane': 'fast_lane',
                'action': 'fast_lane',
                'response': _fast_text.strip() if _fast_text.strip() else None,
                'steered': False,
            })

        except Exception as _fle:
            logger.error(f'### FAST LANE failed, falling back to steer: {_fle}')
            # Fallback: steer mode
            steered = gateway_manager.send_steer(message, session_key)
            action = 'steered'
            logger.info(
                f"### INTERJECT [fast_lane→steer fallback] session={session_key} "
                f"source={source} text={message!r}"
            )

    # Log the interjected message as a user turn for transcript
    log_conversation('user', message, session_id='default')

    return jsonify({
        'ok': True,
        'lane': lane,
        'action': action,
        'steered': steered if lane != 'context' else None,
    })


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
# POST /api/session/reset  — manual session reset from UI actions panel
# ---------------------------------------------------------------------------

@conversation_bp.route('/api/session/reset', methods=['POST'])
def session_reset():
    """Clear the corrupted openclaw session state and return a fresh session key.
    Called by the Reset button in the UI actions panel.
    Clears the openclaw session JSONL file so orphaned messages don't cascade,
    then bumps the voice session key so the next request starts completely fresh."""
    old_key = get_voice_session_key()
    # Find and clear the openclaw session file for the current session key
    try:
        sessions_dir = Path('/home/node/.openclaw/agents/openvoiceui/sessions')
        sessions_json = sessions_dir / 'sessions.json'
        if sessions_json.exists():
            import json as _json
            sessions_map = _json.loads(sessions_json.read_text())
            # The openclaw session key format is "agent:openvoiceui:<voice_key>"
            oclaw_key = f'agent:openvoiceui:{old_key}'
            session_info = sessions_map.get(oclaw_key, {})
            session_id = session_info.get('sessionId')
            if session_id:
                session_file = sessions_dir / f'{session_id}.jsonl'
                if session_file.exists():
                    _ts = __import__('datetime').datetime.utcnow().isoformat() + 'Z'
                    session_file.write_text('{"type":"session","version":3,"id":"' + session_id + '","timestamp":"' + _ts + '","cwd":"/home/node/.openclaw/workspace"}\n')
                    logger.info(f'### SESSION RESET: cleared openclaw session file {session_id}.jsonl')
    except Exception as e:
        logger.warning(f'### SESSION RESET: could not clear openclaw session file: {e}')
    new_key = bump_voice_session()
    return jsonify({'status': 'ok', 'old': old_key, 'new': new_key})


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
# POST /api/tts/clone — Clone a voice from audio
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/tts/clone', methods=['POST'])
def tts_clone_voice():
    """
    Clone a voice from an audio sample.

    Accepts either:
      - JSON: {"audio_url": "...", "name": "...", "provider": "qwen3", "reference_text": "..."}
      - Multipart form: audio file + name + provider fields

    Provider support:
      - qwen3: Instant clone via fal.ai (~37s). Needs audio URL.
      - elevenlabs: Instant clone (~5-10s). Needs audio file.
      - resemble: Multi-step clone (~1-5min). Needs audio file.

    Returns: JSON with voice_id, name, provider, clone metadata.
    """
    try:
        # --- Parse request (JSON or multipart) ---
        save_path = None
        audio_url = None

        if request.is_json:
            data = request.get_json()
            provider_id = data.get('provider', 'qwen3').strip()
            audio_url = data.get('audio_url', '').strip()
            name = data.get('name', '').strip()
            reference_text = data.get('reference_text', '').strip() or None

            if not audio_url:
                return jsonify({'error': 'audio_url is required'}), 400
            if not name:
                return jsonify({'error': 'name is required'}), 400

        elif 'audio' in request.files:
            from services.paths import UPLOADS_DIR
            import uuid

            provider_id = request.form.get('provider', 'qwen3').strip()
            audio_file = request.files['audio']
            name = request.form.get('name', '').strip()
            reference_text = request.form.get('reference_text', '').strip() or None

            if not name:
                return jsonify({'error': 'name field is required'}), 400
            if not audio_file.filename:
                return jsonify({'error': 'Empty audio file'}), 400

            ext = Path(audio_file.filename).suffix.lower()
            if ext not in ('.wav', '.mp3', '.m4a', '.ogg', '.webm', '.flac'):
                return jsonify({'error': f'Unsupported audio format: {ext}'}), 400

            safe_name = f"voice_clone_{uuid.uuid4().hex[:12]}{ext}"
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            save_path = UPLOADS_DIR / safe_name
            audio_file.save(str(save_path))
            audio_url = f"{request.host_url.rstrip('/')}/uploads/{safe_name}"
        else:
            return jsonify({
                'error': 'Send JSON with audio_url or multipart form with audio file'
            }), 400

        # --- Validate provider ---
        clone_providers = ('qwen3', 'elevenlabs', 'resemble')
        if provider_id not in clone_providers:
            return jsonify({
                'error': f'Voice cloning not supported for provider "{provider_id}". '
                         f'Supported: {", ".join(clone_providers)}'
            }), 400

        provider = get_provider(provider_id)
        if not provider.is_available():
            return jsonify({
                'error': f'{provider_id} provider not available (API key not set)'
            }), 503

        # --- Route to provider ---
        logger.info(
            f"Voice clone request: provider={provider_id}, name='{name}', "
            f"url={audio_url[:80] if audio_url else 'N/A'}"
        )

        if provider_id == 'qwen3':
            if not audio_url:
                return jsonify({'error': 'Qwen3 requires audio_url'}), 400
            result = provider.clone_voice(
                audio_url=audio_url,
                name=name,
                reference_text=reference_text,
            )

        elif provider_id == 'elevenlabs':
            if not save_path:
                return jsonify({
                    'error': 'ElevenLabs requires audio file upload (multipart form)'
                }), 400
            result = provider.clone_voice(
                audio_path=str(save_path),
                name=name,
            )

        elif provider_id == 'resemble':
            if not save_path:
                return jsonify({
                    'error': 'Resemble requires audio file upload (multipart form)'
                }), 400
            result = provider.clone_voice(
                audio_path=str(save_path),
                name=name,
                reference_text=reference_text,
            )

        return jsonify({
            'status': 'ok',
            'provider': provider_id,
            'voice_id': result.get('voice_id', ''),
            'name': result.get('name', name),
            'created_at': result.get('created_at', ''),
            'clone_time_ms': result.get('clone_time_ms', 0),
            'embedding_size': result.get('embedding_size', 0),
            'usage': (
                f'Use voice_id "{result.get("voice_id", "")}" in '
                f'/api/tts/generate with provider={provider_id}'
            ),
        })

    except RuntimeError as e:
        logger.error(f"Voice clone failed: {e}")
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        import traceback
        logger.error(f"Voice clone error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500


# ---------------------------------------------------------------------------
# GET /api/tts/voices — List all voices (built-in + cloned) across providers
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/tts/voices', methods=['GET'])
def tts_voices_list():
    """List all available voices across all providers, including cloned voices."""
    try:
        all_voices = {}
        for provider_info in list_providers(include_inactive=False):
            pid = provider_info.get('provider_id', provider_info.get('name', 'unknown'))
            voices = provider_info.get('voices', [])
            cloned = provider_info.get('cloned_voices', [])
            all_voices[pid] = {
                'builtin': voices,
                'cloned': cloned,
            }
        return jsonify({'voices': all_voices})
    except Exception as e:
        logger.error(f"Failed to list voices: {e}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# DELETE /api/tts/voices/<voice_id> — Retire a cloned voice
# ---------------------------------------------------------------------------


@conversation_bp.route('/api/tts/voices/<voice_id>', methods=['DELETE'])
def tts_delete_voice(voice_id):
    """Retire a cloned voice embedding (renamed, not deleted)."""
    try:
        if not voice_id.startswith('clone_'):
            return jsonify({'error': 'Can only retire cloned voices (clone_*)'}), 400

        from services.paths import VOICE_CLONES_DIR
        voice_dir = VOICE_CLONES_DIR / voice_id

        # Validate path doesn't escape
        try:
            voice_dir.resolve().relative_to(VOICE_CLONES_DIR.resolve())
        except ValueError:
            return jsonify({'error': 'Invalid voice_id'}), 400

        if not voice_dir.exists():
            return jsonify({'error': f'Voice {voice_id} not found'}), 404

        # Rename to .retired instead of removing (NEVER DELETE rule)
        renamed = voice_dir.with_name(voice_dir.name + '.retired')
        voice_dir.rename(renamed)
        logger.info(f"Cloned voice retired: {voice_id}")

        return jsonify({'status': 'ok', 'voice_id': voice_id, 'action': 'retired'})
    except Exception as e:
        logger.error(f"Failed to retire voice {voice_id}: {e}")
        return jsonify({'error': str(e)}), 500


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


@conversation_bp.route('/api/stt-events', methods=['POST'])
def stt_events():
    """Receive STT error/status events from the browser.
    Logs them in a format the session monitor can parse from container stdout.
    Only real errors are sent (no-speech and aborted are filtered client-side).
    """
    try:
        data = request.get_json(silent=True) or {}
        error_code = data.get('error', 'unknown')
        message = data.get('message', '')
        provider = data.get('provider', 'webspeech')
        source = data.get('source', 'stt')  # 'stt' or 'wake_word'

        # Log in session-monitor-parseable format
        print(f"### STT_ERROR: {error_code} — {message} (provider={provider} source={source})",
              flush=True)
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'ok': False}), 500
