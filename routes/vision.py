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

DEFAULT_VISION_MODEL    = os.environ.get('VISION_MODEL', 'glm-4.6v')
DEFAULT_VISION_PROVIDER = 'zai'


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

    container_img_path: path to image inside the OVU container, anywhere under
    /app/runtime/ (uploads/, captures/albums/<id>/media/, ...). Converts to the
    host filesystem path for the docker-run -v bind.
    """
    import subprocess

    tenant = (os.environ.get('CLIENT_NAME') or '').strip().lower()
    if not tenant:
        raise ValueError('CLIENT_NAME env not set — cannot derive host image path')

    # Map the whole container subtree, not just uploads/. The previous version
    # took only the basename and forced .../openvoiceui/uploads/<name>, so any
    # caller outside uploads/ (capture album media, for one) resolved to a path
    # that does not exist -- and the failure surfaced as an unhelpful "claude
    # vision returned empty" rather than "wrong path". Prefix-swap keeps the
    # subdirectories intact and is byte-identical for the uploads/ case.
    tenant_root = f'/mnt/clients/{tenant}/openvoiceui'
    _CONTAINER_RUNTIME = '/app/runtime/'
    cpath = str(container_img_path)
    if cpath.startswith(_CONTAINER_RUNTIME):
        host_img_path = f'{tenant_root}/{cpath[len(_CONTAINER_RUNTIME):]}'
    else:
        # Bare filename or unrecognised root — preserve the historical behaviour.
        host_img_path = f'{tenant_root}/uploads/{Path(cpath).name}'

    # Bind the ONE image in at a fixed path instead of relying on /mnt traversal.
    #
    # Why: the container runs as uid 1000 with groups=1000(node) only. Host
    # supplementary group membership (mike in openvoiceui) does NOT carry into a
    # container, so uid 1000 cannot traverse /mnt/clients (0750 mike:openvoiceui)
    # or /mnt/clients/<tenant> (0770 <tenant>:openvoiceui) -- verified 2026-07-30:
    # reading BOTH a captures/ file and an uploads/ file through -v /mnt:/mnt:ro
    # returned EACCES, so this arm could never read any tenant file for any
    # tenant. The docker daemon resolves a bind SOURCE as root, so mounting the
    # file directly sidesteps parent-directory traversal entirely; only the
    # file's own mode matters inside (media is 0644). This is also tighter than
    # exposing all of /mnt to the ephemeral container.
    src = Path(host_img_path)
    if not src.is_file():
        raise RuntimeError(f'vision source image not found on host: {host_img_path}')

    ext = src.suffix.lower() or '.jpg'
    in_container = f'/tmp/vision-input{ext}'
    vision_prompt = f'Read the image file at {in_container} and then: {prompt}'

    # Source platform keys to get CLAUDE_CODE_OAUTH_TOKEN, then run claude with full path
    bash_cmd = (
        'set -a; source /mnt/system/base/.platform-keys.env 2>/dev/null; set +a; '
        'PATH="/home/node/.local/bin:$PATH" '
        'claude -p --model claude-sonnet-5 '
        '--allowedTools Read --output-format text "$VISION_PROMPT"'
    )

    proc = subprocess.run(
        ['docker', 'run', '--rm',
         # /mnt stays mounted for .platform-keys.env (under /mnt/system, which IS
         # traversable by uid 1000). The image comes in as its own bind.
         '-v', '/mnt:/mnt:ro',
         '-v', f'{host_img_path}:{in_container}:ro',
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

    # A refusal or read-failure is a FAILURE, not a description. Without this the
    # EACCES above surfaced as the caption "I wasn't able to access that file --
    # the read failed with a permission error", which callers then stored as a
    # photo's ai_caption. Storing an apology as data is worse than erroring: it
    # looks like a successful describe forever after.
    _low = result.lower()
    _read_failed = (
        'eacces' in _low
        or 'permission denied' in _low
        or ("wasn't able to" in _low and 'file' in _low)
        or ('unable to' in _low and ('read' in _low or 'access' in _low))
        or ('no such file' in _low)
    )
    if _read_failed:
        raise RuntimeError(f'claude vision could not read the image: {result[:200]}')

    return result


def _call_vision(image_b64: str, prompt: str, model: str | None = None, file_path: str | None = None) -> str:
    """
    Send an image + prompt to the configured vision model and return the text response.

    For uploaded image files (file_path provided): routes through headless Claude Code
    subscription via docker run — no Groq/Gemini/OpenAI, no marginal cost.
    For camera frames (no file_path): uses Groq qwen3.6-27b as before.

    image_b64 may be a raw base64 string or a data-URI (data:image/jpeg;base64,...).
    """
    # Uploaded files: use Claude subscription (image-intel system pattern)
    if file_path:
        return _call_vision_via_claude(file_path, prompt)

    # Camera frames: Groq path (in-memory frames have no on-disk path)
    # Strip data-URI prefix if present
    if image_b64.startswith('data:'):
        image_b64 = image_b64.split(',', 1)[1]

    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        raise ValueError('GROQ_API_KEY is not set — cannot call vision model for camera frame')

    vision_model = os.environ.get('GROQ_VISION_MODEL', 'qwen/qwen3.6-27b')

    payload = {
        'model': vision_model,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image_url',
                 'image_url': {'url': f'data:image/png;base64,{image_b64}'}},
                {'type': 'text', 'text': prompt},
            ],
        }],
        'max_tokens': 1500,
        # qwen3 is a reasoning model — without this its <think> tokens eat the
        # budget and can leak into the returned description
        'reasoning_format': 'hidden',
    }

    resp = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    text = (resp.json()['choices'][0]['message']['content'] or '').strip()
    # Defense in depth: strip any <think> block if reasoning_format was ignored
    if '</think>' in text:
        text = text.rsplit('</think>', 1)[1].strip()
    return text


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
