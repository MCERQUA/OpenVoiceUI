"""
routes/vision.py — Camera / Vision / Facial Recognition Blueprint

Endpoints:
  POST /api/vision              — analyze camera frame with vision LLM
  POST /api/frame               — receive live frame (stored as latest_frame)
  POST /api/identify            — identify person from camera frame (DeepFace)
  GET  /api/faces               — list registered faces
  POST /api/faces/<name>        — register a face photo
  DELETE /api/faces/<name>      — delete a registered face

Face recognition: DeepFace (local, free, runs on-server — no API calls).
Vision analysis ("look at"): configurable vision LLM (default: glm-4.6v).
"""

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

vision_bp = Blueprint('vision', __name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

from services.paths import KNOWN_FACES_DIR as FACES_DIR

FACES_DIR.mkdir(parents=True, exist_ok=True)

# Latest frame received from browser (in-memory, ephemeral)
_latest_frame: dict = {'image': None, 'ts': 0}

# ---------------------------------------------------------------------------
# DeepFace — lazy load (heavy import, downloads models on first use)
# Serialize all face recognition calls — concurrent TF/h5py calls crash the process.
# ---------------------------------------------------------------------------

_deepface = None
_deepface_lock = threading.Lock()

def _get_deepface():
    global _deepface
    if _deepface is None:
        try:
            from deepface import DeepFace
            _deepface = DeepFace
        except ImportError:
            raise ImportError(
                "Face recognition requires deepface. Install it with: "
                "pip install deepface tf-keras"
            )
    return _deepface


def _clear_deepface_cache():
    """Delete DeepFace's cached face index so newly registered/deleted faces are picked up."""
    for pkl in FACES_DIR.glob('*.pkl'):
        try:
            pkl.unlink()
        except OSError:
            pass

# ---------------------------------------------------------------------------
# Vision model config
# ---------------------------------------------------------------------------

# Known vision-capable models (shown in admin UI dropdown)
VISION_MODELS = [
    {'id': 'glm-4.6v',      'label': 'GLM-4.6V (128K · Paid)',          'provider': 'zai'},
    {'id': 'glm-4v-plus',   'label': 'GLM-4V Plus (Legacy · Paid)',     'provider': 'zai'},
]

# Gemini is THE vision provider for this stack (Mike, 2026-09-06), matching the openclaw
# media chain in openclaw.json which is confirmed working on the same test images.
#
# ⚠️ DO NOT point this at Z.AI/GLM. GLM models ARE vision-capable, but the endpoint we are
# mandated to use (api.z.ai/api/anthropic) SILENTLY STRIPS IMAGE CONTENT — measured 2026-05-05
# against one screenshot on glm-5.1 / glm-5 / glm-5-turbo / glm-4.5v: every one confidently
# described a desktop that does not exist. The same glm-4.5v on the native paas/v4 endpoint was
# correct. A stripped image does not error; the model confabulates, which is worse than a failure.
# Until openclaw's zaiProvider routes image requests via paas/v4, Z.AI is text-only here.
DEFAULT_VISION_MODEL    = os.environ.get('VISION_MODEL', 'gemini-flash-latest')
DEFAULT_VISION_FALLBACK = os.environ.get('VISION_MODEL_FALLBACK', 'gemini-pro-latest')
DEFAULT_VISION_PROVIDER = 'google'


def _get_vision_model() -> tuple[str, str]:
    """Return (model_id, provider) from active profile or env defaults."""
    try:
        from profiles.manager import get_profile_manager
        mgr = get_profile_manager()
        p   = mgr.get_active_profile()
        if p:
            d = p.to_dict()
            model    = d.get('vision', {}).get('model')    or DEFAULT_VISION_MODEL
            provider = d.get('vision', {}).get('provider') or DEFAULT_VISION_PROVIDER
            return model, provider
    except Exception as exc:
        logger.debug('Could not read vision config from profile: %s', exc)
    return DEFAULT_VISION_MODEL, DEFAULT_VISION_PROVIDER


def _call_vision_via_claude(container_img_path: str, prompt: str) -> str:
    """
    Analyze an uploaded image file using headless Claude Code (subscription, no marginal cost).

    Runs `docker run jambot/openclaw:latest` — the openclaw image has the claude binary
    and sources CLAUDE_CODE_OAUTH_TOKEN from the mounted platform-keys env. Same
    subscription path as the image-intel cron sweep; no Groq/Gemini/OpenAI.

    container_img_path: path to image inside the OVU container (/app/runtime/uploads/<file>).
    Converts to the host filesystem path for the docker-run -v bind.
    """
    import subprocess

    tenant = (os.environ.get('CLIENT_NAME') or '').strip().lower()
    if not tenant:
        raise ValueError('CLIENT_NAME env not set — cannot derive host image path')

    filename = Path(container_img_path).name
    host_img_path = f'/mnt/clients/{tenant}/openvoiceui/uploads/{filename}'

    vision_prompt = f'Read the image file at {host_img_path} and then: {prompt}'

    # Source platform keys to get CLAUDE_CODE_OAUTH_TOKEN, then run claude with full path
    bash_cmd = (
        'set -a; source /mnt/system/base/.platform-keys.env 2>/dev/null; set +a; '
        'PATH="/home/node/.local/bin:$PATH" '
        'claude -p --model claude-sonnet-5 '
        '--allowedTools Read --output-format text "$VISION_PROMPT"'
    )

    proc = subprocess.run(
        ['docker', 'run', '--rm',
         '-v', '/mnt:/mnt:ro',
         '-e', f'VISION_PROMPT={vision_prompt}',
         'jambot/openclaw:latest',
         '/bin/bash', '-c', bash_cmd],
        capture_output=True,
        text=True,
        timeout=90,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f'claude vision rc={proc.returncode}: {(proc.stderr or "")[:300]}'
        )

    result = proc.stdout.strip()
    if not result:
        raise RuntimeError('claude vision returned empty response')

    return result


def _gemini_vision(image_b64: str, mime: str, prompt: str, model: str) -> str:
    """One Gemini generateContent call. Raises on non-2xx so the caller can fall back."""
    key = os.environ.get('GEMINI_API_KEY', '')
    if not key:
        raise ValueError('GEMINI_API_KEY is not set — cannot call vision model')
    # KEY GOES IN A HEADER, NEVER THE URL. Gemini accepts ?key= as a query param, but requests'
    # HTTPError message embeds the full URL — so a single 404 on a bad model name writes the live
    # GEMINI_API_KEY into the log, the exception, and any transcript that captures it. Measured
    # 2026-09-06: a fallback test did exactly that. x-goog-api-key keeps it out of every error path.
    resp = requests.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        headers={'x-goog-api-key': key},
        json={'contents': [{'parts': [
            {'text': prompt},
            {'inline_data': {'mime_type': mime, 'data': image_b64}},
        ]}]},
        timeout=60,
    )
    resp.raise_for_status()
    cands = resp.json().get('candidates') or []
    if not cands:
        raise RuntimeError('gemini returned no candidates')
    return (cands[0]['content']['parts'][0].get('text') or '').strip()


def _call_vision(image_b64: str, prompt: str, model: str | None = None, file_path: str | None = None) -> str:
    """
    Send an image + prompt to Gemini and return the text response.

    ONE path for uploads and camera frames alike (2026-09-06). What this replaced, and why:

      * uploaded files spawned a whole container per image —
        `docker run --rm -v /mnt:/mnt:ro jambot/openclaw:latest claude -p --model claude-sonnet-5`
        — a container launch, a 90s timeout, and the entire /mnt tree (every tenant's data and
        .platform-keys.env) bind-mounted in, to look at one picture. Retained below as
        _call_vision_via_claude() for reference; no longer called.
      * camera frames were hardcoded to Groq qwen3.6-27b, bypassing the configured provider
        entirely — while this stack's standing rule is that Groq is STT/TTS only.
      * _get_vision_model() was read and its result DISCARDED: the `model` argument existed and
        was never used, so the configured vision model had no effect on anything.

    image_b64 may be raw base64 or a data-URI. file_path (a path inside this container) is read
    from disk when no base64 is supplied.
    """
    mime = 'image/png'

    if not image_b64 and file_path:
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f'vision: no such image {file_path}')
        image_b64 = base64.b64encode(p.read_bytes()).decode()
        ext = p.suffix.lower()
        mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
                '.gif': 'image/gif'}.get(ext, 'image/png')

    if image_b64.startswith('data:'):
        head, image_b64 = image_b64.split(',', 1)
        if ';' in head and ':' in head:
            mime = head.split(':', 1)[1].split(';', 1)[0] or mime

    if not image_b64:
        raise ValueError('vision: no image supplied (need image_b64 or file_path)')

    primary = model or _get_vision_model()[0]
    chain = [m for m in (primary, DEFAULT_VISION_FALLBACK) if m]
    last = None
    for m in chain:
        try:
            return _gemini_vision(image_b64, mime, prompt, m)
        except Exception as exc:            # try the next model in the chain
            last = exc
            # Scrub any key= / api-key that a library put in the message before it is logged.
            msg = re.sub(r'(key=|api[-_]?key["\':\s]+)[A-Za-z0-9._\-]{8,}', r'\1<redacted>',
                         str(exc), flags=re.I)
            logger.warning('vision: %s failed (%s) — trying next in chain', m, msg[:200])
    raise RuntimeError(f'all vision models failed; last error: {last}')


# ---------------------------------------------------------------------------
# POST /api/vision  — agent "look at" tool
# ---------------------------------------------------------------------------

@vision_bp.route('/api/vision', methods=['POST'])
def vision_analyze():
    """Analyze a camera frame with the configured vision model."""
    data   = request.get_json(silent=True) or {}
    image  = data.get('image', '')
    prompt = data.get('prompt', 'Describe what you see in this image in detail.')
    model  = data.get('model')  # optional override

    if not image:
        return jsonify({'error': 'No image provided'}), 400

    try:
        description = _call_vision(image, prompt, model)
        return jsonify({'description': description, 'model': model or _get_vision_model()[0]})
    except Exception as exc:
        logger.error('Vision analysis failed: %s', exc)
        return jsonify({'error': 'Internal server error'}), 500


# ---------------------------------------------------------------------------
# POST /api/frame  — receive live frame stream from browser
# ---------------------------------------------------------------------------

_FRAME_MAX_BYTES = 5 * 1024 * 1024  # 5 MB max per frame

@vision_bp.route('/api/frame', methods=['POST'])
def receive_frame():
    """Store the latest camera frame in memory for use by other endpoints."""
    if request.content_length and request.content_length > _FRAME_MAX_BYTES:
        return jsonify({'ok': False, 'error': 'Frame too large'}), 413
    data  = request.get_json(silent=True) or {}
    image = data.get('image', '')
    if image:
        if len(image) > _FRAME_MAX_BYTES:
            return jsonify({'ok': False, 'error': 'Frame too large'}), 413
        _latest_frame['image'] = image
        _latest_frame['ts']    = time.time()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# POST /api/identify  — facial recognition
# ---------------------------------------------------------------------------

@vision_bp.route('/api/identify', methods=['POST'])
def identify_face():
    """
    Identify who is in the camera frame using DeepFace (local, free, no API calls).

    Uses the SFace model — fast on CPU, ~100ms after first load.
    Face database: known_faces/<PersonName>/*.jpg
    """
    data  = request.get_json(silent=True) or {}
    image = data.get('image', '')
    if not image:
        image = _latest_frame.get('image', '')
    if not image:
        return jsonify({'name': 'unknown', 'confidence': 0, 'message': 'No image'}), 200

    # Check if any faces are registered
    known_people = [d.name for d in FACES_DIR.iterdir()
                    if d.is_dir() and any(d.iterdir())]
    if not known_people:
        return jsonify({'name': 'unknown', 'confidence': 0,
                        'message': 'No faces registered yet'}), 200

    # Decode and save to temp file (DeepFace needs a file path)
    # Malformed/truncated data-URIs happen with flaky camera capture — return the
    # same graceful "unknown" payload as every other failure path, never a 500.
    try:
        image_data = image
        if ',' in image_data:
            image_data = image_data.split(',', 1)[1]
        image_bytes = base64.b64decode(image_data)
    except Exception:
        return jsonify({'name': 'unknown', 'confidence': 0,
                        'message': 'Invalid image data'}), 200

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        DeepFace = _get_deepface()
        with _deepface_lock:
            results = DeepFace.find(
                img_path=tmp_path,
                db_path=str(FACES_DIR),
                model_name='SFace',
                enforce_detection=False,
                silent=True,
            )

        if results and len(results) > 0 and len(results[0]) > 0:
            df           = results[0]
            best         = df.iloc[0]
            identity_path = best['identity']
            distance     = float(best['distance'])
            person_name  = Path(identity_path).parent.name

            # SFace cosine distance threshold ~0.5; convert to confidence %
            confidence = max(0, round((1 - distance / 0.7) * 100, 1))

            if distance < 0.5:
                return jsonify({'name': person_name, 'confidence': confidence})
            else:
                return jsonify({'name': 'unknown', 'confidence': confidence,
                                'message': 'Face detected but not recognized'})
        else:
            return jsonify({'name': 'unknown', 'confidence': 0,
                            'message': 'No face detected in frame'})

    except Exception as exc:
        logger.error('Face identification failed: %s', exc)
        return jsonify({'name': 'unknown', 'confidence': 0, 'message': 'Face identification failed'}), 200
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# GET /api/faces  — list registered faces
# ---------------------------------------------------------------------------

def _list_faces_data():
    entries = []
    for face_dir in sorted(FACES_DIR.iterdir()):
        if not face_dir.is_dir():
            continue
        photos = list(face_dir.glob('*.jpg')) + list(face_dir.glob('*.jpeg')) + \
                 list(face_dir.glob('*.png'))
        entries.append({'name': face_dir.name, 'photo_count': len(photos)})
    return entries


@vision_bp.route('/api/faces', methods=['GET'])
def list_faces():
    return jsonify({'faces': _list_faces_data()})


# ---------------------------------------------------------------------------
# POST /api/faces/<name>  — register a face photo
# ---------------------------------------------------------------------------

@vision_bp.route('/api/faces/<name>', methods=['POST'])
def register_face(name):
    """Save a face photo for a named person."""
    # Sanitize name
    safe_name = re.sub(r'[^a-zA-Z0-9_\- ]', '', name).strip()
    if not safe_name:
        return jsonify({'error': 'Invalid name'}), 400

    data       = request.get_json(silent=True) or {}
    image_data = data.get('image', '')
    if not image_data:
        return jsonify({'error': 'No image provided'}), 400

    face_dir = FACES_DIR / safe_name
    face_dir.mkdir(exist_ok=True)

    # Strip data-URI prefix
    if image_data.startswith('data:'):
        image_data = image_data.split(',', 1)[1]

    # Save with incrementing filename
    idx      = len(list(face_dir.glob('*.jpg'))) + 1
    out_path = face_dir / f'photo_{idx:03d}.jpg'
    out_path.write_bytes(base64.b64decode(image_data))

    logger.info('Registered face photo: %s (%s)', safe_name, out_path.name)

    # Clear DeepFace's cached index so the new face is picked up immediately
    with _deepface_lock:
        _clear_deepface_cache()

    return jsonify({'ok': True, 'name': safe_name, 'file': out_path.name})


# ---------------------------------------------------------------------------
# DELETE /api/faces/<name>  — remove a registered face
# ---------------------------------------------------------------------------

@vision_bp.route('/api/faces/<name>', methods=['DELETE'])
def delete_face(name):
    safe_name = re.sub(r'[^a-zA-Z0-9_\- ]', '', name).strip()
    face_dir  = FACES_DIR / safe_name
    if not face_dir.exists():
        return jsonify({'error': 'Face not found'}), 404

    import shutil
    shutil.rmtree(face_dir)
    with _deepface_lock:
        _clear_deepface_cache()
    return jsonify({'ok': True, 'deleted': safe_name})


# ---------------------------------------------------------------------------
# GET /api/vision/models  — list available vision models (for admin UI)
# ---------------------------------------------------------------------------

@vision_bp.route('/api/vision/models', methods=['GET'])
def list_vision_models():
    active_model, _ = _get_vision_model()
    return jsonify({'models': VISION_MODELS, 'active': active_model})
