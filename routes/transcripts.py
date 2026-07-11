"""
Transcript storage — saves listen-mode transcriptions to disk.

Files are organized as:
  transcripts/
    YYYY-MM-DD/
      HH-MM-SS_<slug>.txt

POST /api/transcripts/save   — save a transcript
GET  /api/transcripts        — list saved transcripts (newest first)
GET  /api/transcripts/<date>/<filename>  — read one transcript
"""

import os
import re
import json
from datetime import datetime
from pathlib import Path
from flask import Blueprint, jsonify, request

transcripts_bp = Blueprint('transcripts', __name__)

from services.paths import TRANSCRIPTS_DIR as _TRANSCRIPTS_DIR_PATH, RUNTIME_DIR as _RUNTIME_DIR

TRANSCRIPTS_DIR = str(_TRANSCRIPTS_DIR_PATH)

# SMS ledger lives in the openclaw agent workspace, mounted read-only into the
# OVU container at /app/runtime/workspace/Agent. Each message is one markdown
# file under ledger/sms/<YYYY-MM-DD>/<HH-MM-SS>-<in|out>-<sid>.md.
SMS_LEDGER_DIR = str(_RUNTIME_DIR / 'workspace' / 'Agent' / 'ledger' / 'sms')


def _int_arg(name: str, default: int, cap: int) -> int:
    """Parse an int query param, falling back to default on garbage (no 500s)."""
    try:
        return min(int(request.args.get(name, default)), cap)
    except (TypeError, ValueError):
        return default


def _slug(title: str) -> str:
    """Turn a title into a safe filename slug."""
    s = title.strip().lower()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    s = s.strip('-')
    return s[:60] or 'untitled'


@transcripts_bp.route('/api/transcripts/save', methods=['POST'])
def save_transcript():
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get('title') or 'Untitled').strip()
    text  = (data.get('text') or '').strip()

    if not text:
        return jsonify({'error': 'No transcript text provided'}), 400

    now = datetime.now()
    date_dir  = now.strftime('%Y-%m-%d')
    time_part = now.strftime('%H-%M-%S')
    slug      = _slug(title)
    filename  = f'{time_part}_{slug}.txt'

    save_dir = os.path.join(TRANSCRIPTS_DIR, date_dir)
    os.makedirs(save_dir, exist_ok=True)

    filepath = os.path.join(save_dir, filename)

    word_count = len(text.split())
    content = (
        f'Title: {title}\n'
        f'Date:  {now.strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'Words: {word_count}\n'
        f'\n---\n\n'
        f'{text}\n'
    )

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    return jsonify({
        'saved': True,
        'path':  f'transcripts/{date_dir}/{filename}',
        'date':  date_dir,
        'filename': filename,
        'words': word_count,
    })


@transcripts_bp.route('/api/transcripts', methods=['GET'])
def list_transcripts():
    entries = []
    if not os.path.isdir(TRANSCRIPTS_DIR):
        return jsonify([])

    for date_dir in sorted(os.listdir(TRANSCRIPTS_DIR), reverse=True):
        day_path = os.path.join(TRANSCRIPTS_DIR, date_dir)
        if not os.path.isdir(day_path):
            continue
        for fname in sorted(os.listdir(day_path), reverse=True):
            if not fname.endswith('.txt'):
                continue
            fpath = os.path.join(day_path, fname)
            # Read first few lines for metadata
            meta = {'title': fname, 'date': date_dir, 'filename': fname, 'words': 0}
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith('Title:'):
                            meta['title'] = line[6:].strip()
                        elif line.startswith('Date:'):
                            meta['timestamp'] = line[5:].strip()
                        elif line.startswith('Words:'):
                            meta['words'] = int(line[6:].strip())
                        elif line.strip() == '---':
                            break
            except Exception:
                pass
            entries.append(meta)

    return jsonify(entries)


@transcripts_bp.route('/api/history', methods=['GET'])
def conversation_history():
    """Return real conversation turns from this client's transcripts directory.

    Query params:
      days   — how many days back (default 30)
      limit  — max turns to return (default 200)
      date   — filter to a specific date YYYY-MM-DD
    """
    days  = _int_arg('days', 30, 365)
    limit = _int_arg('limit', 200, 1000)
    date_filter = request.args.get('date', '')

    from datetime import timedelta as _td
    cutoff = (datetime.now() - _td(days=days)).strftime('%Y-%m-%d')

    turns = []
    if not os.path.isdir(TRANSCRIPTS_DIR):
        return jsonify({'turns': [], 'total': 0})

    for date_dir in sorted(os.listdir(TRANSCRIPTS_DIR), reverse=True):
        if date_dir < cutoff:
            break
        if date_filter and date_dir != date_filter:
            continue
        day_path = os.path.join(TRANSCRIPTS_DIR, date_dir)
        if not os.path.isdir(day_path):
            continue
        for fname in sorted(os.listdir(day_path), reverse=True):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(day_path, fname)
            try:
                with open(fpath, encoding='utf-8') as f:
                    data = json.load(f)
                user = (data.get('user') or '').strip()
                if user == '__session_start__':
                    continue
                if not user and not (data.get('assistant') or '').strip():
                    continue
                turns.append({
                    'date':          data.get('date', date_dir),
                    'time':          data.get('time', '00:00:00'),
                    'timestamp':     data.get('timestamp', ''),
                    'user':          user[:300],
                    'assistant':     (data.get('assistant') or '')[:500],
                    'tools':         data.get('tools', []),
                    'session_id':    data.get('session_id', ''),
                    'session_key':   data.get('session_key', 'main'),
                    'duration_ms':   data.get('duration_ms'),
                    'word_count':    data.get('word_count', {}),
                    'clerk_user_id': data.get('clerk_user_id', ''),
                })
                if len(turns) >= limit:
                    break
            except Exception:
                pass
        if len(turns) >= limit:
            break

    return jsonify({'turns': turns, 'total': len(turns), 'days': days})


def _parse_sms_file(text: str) -> dict:
    """Parse one SMS ledger markdown file into a dict.

    Format (EXAMPLE only — fictitious E.164s for docs; not real personal numbers):
        # SMS — in — 2026-05-18T18:28:49.793999+00:00
        - tenant: test-dev
        - from: +15555550100   # EXAMPLE / fictitious
        - to: +15555550199     # EXAMPLE / fictitious
        - twilio_sid: (local)

        ## Body

        <message text>

    Do not put real personal phone numbers in this docstring or committed fixtures.
    """
    direction = ''
    timestamp = ''
    fields = {}
    body_lines = []
    in_body = False
    for raw in text.splitlines():
        line = raw.rstrip('\n')
        if not in_body and line.startswith('# SMS'):
            # Split on em-dash (—) or hyphen surrounded by spaces.
            parts = re.split(r'\s+[—-]\s+', line)
            if len(parts) >= 3:
                direction = parts[1].strip().lower()
                timestamp = parts[2].strip()
            continue
        if not in_body and line.strip() == '## Body':
            in_body = True
            continue
        if not in_body and line.startswith('- '):
            kv = line[2:].split(':', 1)
            if len(kv) == 2:
                fields[kv[0].strip().lower()] = kv[1].strip()
            continue
        if in_body:
            body_lines.append(line)
    body = '\n'.join(body_lines).strip()
    return {
        'direction': direction or 'out',
        'timestamp': timestamp,
        'from': fields.get('from', ''),
        'to': fields.get('to', ''),
        'name': fields.get('name', ''),
        'body': body,
    }


@transcripts_bp.route('/api/sms-history', methods=['GET'])
def sms_history():
    """Return this client's SMS messages (to and from the agent).

    Reads the openclaw agent's SMS ledger. Each returned message carries its
    direction ('in' = from the user's phone, 'out' = from the agent), the phone
    numbers, body, and timestamp — shaped to merge into the conversation view.

    Query params:
      days   — how many days back (default 30, max 365)
      limit  — max messages to return (default 500, max 2000)
      date   — filter to a specific date YYYY-MM-DD
    """
    days  = _int_arg('days', 30, 365)
    limit = _int_arg('limit', 500, 2000)
    date_filter = request.args.get('date', '')

    from datetime import timedelta as _td
    cutoff = (datetime.now() - _td(days=days)).strftime('%Y-%m-%d')

    messages = []
    if not os.path.isdir(SMS_LEDGER_DIR):
        return jsonify({'messages': [], 'total': 0, 'days': days})

    for date_dir in sorted(os.listdir(SMS_LEDGER_DIR), reverse=True):
        if date_dir < cutoff:
            break
        if date_filter and date_dir != date_filter:
            continue
        day_path = os.path.join(SMS_LEDGER_DIR, date_dir)
        if not os.path.isdir(day_path):
            continue
        for fname in sorted(os.listdir(day_path)):
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(day_path, fname)
            try:
                parsed = _parse_sms_file(Path(fpath).read_text(encoding='utf-8'))
                if not parsed['body']:
                    continue
                # Derive a HH:MM:SS from the filename prefix (HH-MM-SS-...) as a fallback.
                time_str = '00:00:00'
                m = re.match(r'(\d{2})-(\d{2})-(\d{2})', fname)
                if m:
                    time_str = f'{m.group(1)}:{m.group(2)}:{m.group(3)}'
                messages.append({
                    'type':      'sms',
                    'date':      date_dir,
                    'time':      time_str,
                    'timestamp': parsed['timestamp'],
                    'direction': parsed['direction'],
                    'from':      parsed['from'],
                    'to':        parsed['to'],
                    'name':      parsed['name'],
                    'body':      parsed['body'][:1000],
                })
                if len(messages) >= limit:
                    break
            except Exception:
                pass
        if len(messages) >= limit:
            break

    return jsonify({'messages': messages, 'total': len(messages), 'days': days})


@transcripts_bp.route('/api/transcripts/<date_dir>/<filename>', methods=['GET'])
def get_transcript(date_dir, filename):
    # Resolve and verify path stays within TRANSCRIPTS_DIR
    base = Path(TRANSCRIPTS_DIR).resolve()
    try:
        resolved = (base / date_dir / filename).resolve()
    except (ValueError, OSError):
        return jsonify({'error': 'Invalid path'}), 400
    if base not in resolved.parents and resolved != base:
        return jsonify({'error': 'Invalid path'}), 400
    if not resolved.is_file():
        return jsonify({'error': 'Not found'}), 404
    return resolved.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/plain; charset=utf-8'}


import logging as _transcript_logger

def save_conversation_turn(
    user_msg: str,
    ai_response: str,
    session_id: str = 'default',
    session_key: str = None,
    tts_provider: str = None,
    voice: str = None,
    duration_ms: int = None,
    actions: list = None,
    identified_person: dict = None,
    clerk_user_id: str = None,
) -> 'str | None':
    """Save one conversation turn as a JSON transcript file.

    Organized as: transcripts/YYYY-MM-DD/HH-MM-SS_<session_key>_<session_id>.json

    Returns the relative file path on success, or None on failure.
    Never raises — errors are logged at debug level so callers are never broken.
    """
    try:
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        time_str = now.strftime('%H-%M-%S')
        ms_str = f'{now.microsecond // 1000:03d}'
        ts_iso = f'{now.strftime("%Y-%m-%dT%H:%M:%S")}.{ms_str}Z'

        # Extract brief tool summaries from captured actions (phase=result only)
        tools = []
        if actions:
            for action in actions:
                if action.get('type') == 'tool' and action.get('phase') == 'result':
                    name = action.get('name', 'unknown')
                    result = action.get('result', '')
                    summary = str(result)[:120] if result else ''
                    tools.append({'name': name, 'phase': 'result', 'summary': summary})

        user_words = len(user_msg.split()) if user_msg else 0
        ai_words = len(ai_response.split()) if ai_response else 0
        key = session_key or 'unknown'

        # Resolve the registered user's NAME from clerk_user_id so every transcript carries
        # WHO it was — not just an opaque id (Mike 2026-06-12: "names via clerk id EVERYWHERE,
        # no unknown user"). identity-registry is the complete Clerk roster. Fail-open.
        user_name = None
        if clerk_user_id:
            try:
                from services.identity import resolve as _resolve_identity
                _ident = _resolve_identity(clerk_user_id)
                if _ident:
                    user_name = _ident.get('name')
            except Exception:
                pass

        payload = {
            'schema': 'v1',
            'session_id': session_id,
            'session_key': key,
            'timestamp': ts_iso,
            'date': date_str,
            'time': now.strftime('%H:%M:%S'),
            'tts_provider': tts_provider,
            'voice': voice,
            'duration_ms': duration_ms,
            'user': user_msg,
            'assistant': ai_response,
            'tools': tools,
            'identified_person': identified_person,
            'clerk_user_id': clerk_user_id,
            'user_name': user_name,
            'word_count': {'user': user_words, 'assistant': ai_words},
        }

        save_dir = os.path.join(TRANSCRIPTS_DIR, date_str)
        os.makedirs(save_dir, exist_ok=True)
        filename = f'{time_str}_{key}_{session_id}.json'
        filepath = os.path.join(save_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return f'transcripts/{date_str}/{filename}'

    except Exception as exc:
        _transcript_logger.getLogger(__name__).debug(
            f'save_conversation_turn failed (non-critical): {exc}'
        )
        return None
