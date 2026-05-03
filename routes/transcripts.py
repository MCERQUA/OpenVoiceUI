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

from services.paths import TRANSCRIPTS_DIR as _TRANSCRIPTS_DIR_PATH

TRANSCRIPTS_DIR = str(_TRANSCRIPTS_DIR_PATH)


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
