"""
Audio Clips routes — generate, save, list, and email TTS audio clips as files.

Distinct from /api/tts/generate (used by the live voice agent for ephemeral
streaming): this blueprint persists every generated clip to disk under
uploads/audio-clips/, applies a 1-second leading-silence pad by default
(Groq Orpheus clips the first ~1-2s — locked-in rule 2026-05-07), and
maintains a manifest for the canvas-page clip history.

Endpoints:
  POST   /api/audio-clips/generate    create + save MP3 clip
  GET    /api/audio-clips/list        list manifest entries
  POST   /api/audio-clips/email       send a saved clip via AgentMail
  DELETE /api/audio-clips/<clip_id>   soft-delete (manifest flag only — files
                                      are NEVER deleted, per CLAUDE.md)
"""

import base64
import json
import logging
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify, request

from tts_providers import get_provider
from services.paths import UPLOADS_DIR

logger = logging.getLogger(__name__)
audio_clips_bp = Blueprint('audio_clips', __name__)

AUDIO_CLIPS_DIR = UPLOADS_DIR / "audio-clips"
AUDIO_CLIPS_MANIFEST = AUDIO_CLIPS_DIR / "manifest.json"
AUDIO_CLIPS_DIR.mkdir(parents=True, exist_ok=True)

MAX_TEXT_LEN = 8000
DEFAULT_LEADING_SILENCE_MS = 1000
SAFE_SLUG = re.compile(r'[^a-zA-Z0-9_-]+')


def _load_manifest():
    if not AUDIO_CLIPS_MANIFEST.exists():
        return {'clips': []}
    try:
        return json.loads(AUDIO_CLIPS_MANIFEST.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning('audio-clips manifest unreadable, starting fresh')
        return {'clips': []}


def _save_manifest(m):
    tmp = AUDIO_CLIPS_MANIFEST.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(AUDIO_CLIPS_MANIFEST)


def _make_id(label: str) -> str:
    slug = SAFE_SLUG.sub('-', label.strip())[:40].strip('-').lower() or 'clip'
    ts = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return f"{ts}-{slug}-{uuid.uuid4().hex[:6]}"


def _encode_mp3_with_silence(input_path, mp3_path, leading_silence_ms):
    if leading_silence_ms <= 0:
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(input_path),
               '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '96k', str(mp3_path)]
    else:
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', str(input_path),
               '-af', f'adelay={leading_silence_ms}:all=1',
               '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '96k', str(mp3_path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def _mp3_duration_ms(path):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=nk=1:nw=1', str(path)],
            capture_output=True, text=True, check=True, timeout=10
        )
        return int(float(result.stdout.strip()) * 1000)
    except Exception:
        return None


@audio_clips_bp.route('/api/audio-clips/generate', methods=['POST'])
def generate_clip():
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text is required'}), 400
    if len(text) > MAX_TEXT_LEN:
        return jsonify({'error': f'text too long (max {MAX_TEXT_LEN} chars)'}), 400

    provider_id = (data.get('provider') or 'groq').strip()
    voice = (data.get('voice') or '').strip() or None
    label = (data.get('label') or 'clip').strip() or 'clip'

    try:
        leading_silence_ms = int(data.get('leading_silence_ms', DEFAULT_LEADING_SILENCE_MS))
    except (ValueError, TypeError):
        leading_silence_ms = DEFAULT_LEADING_SILENCE_MS
    leading_silence_ms = max(0, min(5000, leading_silence_ms))

    try:
        provider = get_provider(provider_id)
    except ValueError as e:
        return jsonify({'error': f'invalid provider: {e}'}), 400

    gen_params = {'text': text}
    if voice:
        gen_params['voice'] = voice

    try:
        audio_bytes = provider.generate_speech(**gen_params)
    except Exception as e:
        logger.exception('TTS generation failed')
        return jsonify({'error': f'TTS generation failed: {e}'}), 500

    clip_id = _make_id(label)
    raw_path = AUDIO_CLIPS_DIR / f"{clip_id}.raw"
    raw_path.write_bytes(audio_bytes)

    final_mp3 = AUDIO_CLIPS_DIR / f"{clip_id}.mp3"
    try:
        _encode_mp3_with_silence(raw_path, final_mp3, leading_silence_ms)
    except subprocess.CalledProcessError as e:
        logger.error('ffmpeg failed: %s', e.stderr.decode(errors='replace')[:500])
        return jsonify({'error': 'audio post-processing failed'}), 500
    finally:
        raw_path.unlink(missing_ok=True)

    clip_entry = {
        'id': clip_id,
        'label': label,
        'provider': provider_id,
        'voice': voice,
        'text_preview': text[:200],
        'text_full': text,
        'leading_silence_ms': leading_silence_ms,
        'duration_ms': _mp3_duration_ms(final_mp3),
        'size_bytes': final_mp3.stat().st_size,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'url': f'/uploads/audio-clips/{clip_id}.mp3',
        'filename': f'{clip_id}.mp3',
        'soft_deleted': False,
        'emails_sent': [],
    }

    manifest = _load_manifest()
    manifest['clips'].insert(0, clip_entry)
    _save_manifest(manifest)

    logger.info('audio-clip generated: %s (%s/%s, %d bytes)',
                clip_id, provider_id, voice, clip_entry['size_bytes'])
    return jsonify(clip_entry), 200


@audio_clips_bp.route('/api/audio-clips/list', methods=['GET'])
def list_clips():
    include_deleted = request.args.get('include_deleted', '0') == '1'
    manifest = _load_manifest()
    clips = manifest['clips']
    if not include_deleted:
        clips = [c for c in clips if not c.get('soft_deleted')]
    return jsonify({'clips': clips}), 200


@audio_clips_bp.route('/api/audio-clips/<clip_id>', methods=['DELETE'])
def soft_delete_clip(clip_id):
    manifest = _load_manifest()
    target = None
    for c in manifest['clips']:
        if c['id'] == clip_id:
            c['soft_deleted'] = True
            target = c
            break
    if not target:
        return jsonify({'error': 'clip not found'}), 404
    _save_manifest(manifest)
    return jsonify({'ok': True, 'id': clip_id}), 200


@audio_clips_bp.route('/api/audio-clips/email', methods=['POST'])
def email_clip():
    data = request.get_json(silent=True) or {}
    clip_id = (data.get('clip_id') or '').strip()
    to_addrs = data.get('to') or []
    cc_addrs = data.get('cc') or []
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()

    if not clip_id:
        return jsonify({'error': 'clip_id is required'}), 400
    if not isinstance(to_addrs, list) or not to_addrs:
        return jsonify({'error': 'to (non-empty list) is required'}), 400
    if not subject:
        return jsonify({'error': 'subject is required'}), 400

    api_key = os.getenv('AGENTMAIL_API_KEY', '').strip()
    if not api_key:
        return jsonify({'error': 'AgentMail not configured (AGENTMAIL_API_KEY missing)'}), 503

    inbox = os.getenv('AGENTMAIL_INBOX', '').strip()
    if not inbox:
        try:
            r = requests.get(
                'https://api.agentmail.to/v0/inboxes',
                headers={'Authorization': f'Bearer {api_key}'},
                timeout=10,
            )
            r.raise_for_status()
            inboxes = r.json().get('inboxes', [])
            if not inboxes:
                return jsonify({'error': 'no AgentMail inboxes available'}), 503
            inbox = inboxes[0]['inbox_id']
        except requests.RequestException as e:
            return jsonify({'error': f'AgentMail inbox lookup failed: {e}'}), 503

    manifest = _load_manifest()
    clip = next((c for c in manifest['clips'] if c['id'] == clip_id), None)
    if not clip:
        return jsonify({'error': 'clip not found'}), 404

    file_path = AUDIO_CLIPS_DIR / clip['filename']
    if not file_path.exists():
        return jsonify({'error': 'clip file missing on disk'}), 404

    encoded = base64.b64encode(file_path.read_bytes()).decode('ascii')

    text_body = body or f"Audio clip attached: {clip['label']}"
    html_body = '<p>' + text_body.replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'

    payload = {
        'to': to_addrs,
        'subject': subject,
        'text': text_body,
        'html': html_body,
        'attachments': [{
            'content': encoded,
            'filename': clip['filename'],
            'content_type': 'audio/mpeg',
        }],
    }
    if cc_addrs:
        payload['cc'] = cc_addrs

    try:
        r = requests.post(
            f'https://api.agentmail.to/v0/inboxes/{inbox}/messages/send',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        result = r.json()
    except requests.RequestException as e:
        body_text = ''
        if hasattr(e, 'response') and e.response is not None:
            body_text = e.response.text[:500]
        logger.error('AgentMail send failed: %s | %s', e, body_text)
        return jsonify({'error': f'AgentMail send failed: {e}', 'detail': body_text}), 502

    clip.setdefault('emails_sent', []).append({
        'sent_at': datetime.now(timezone.utc).isoformat(),
        'to': to_addrs,
        'cc': cc_addrs,
        'subject': subject,
        'message_id': result.get('message_id'),
        'thread_id': result.get('thread_id'),
    })
    _save_manifest(manifest)

    logger.info('audio-clip emailed: %s → %s (msg=%s)',
                clip_id, to_addrs, result.get('message_id'))
    return jsonify({
        'ok': True,
        'message_id': result.get('message_id'),
        'thread_id': result.get('thread_id'),
        'inbox': inbox,
    }), 200
