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
Vision analysis ("look at"): configurable vision LLM (default: glm-4v-flash).
"""

import base64
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

vision_bp = Blueprint('vision', __name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

from routes import DATA_DIR

# known_faces/ is the DeepFace database directory.
# Layout: known_faces/<PersonName>/photo_001.jpg  (one subdir per person)
FACES_DIR = DATA_DIR / 'known_faces'
FACES_DIR.mkdir(exist_ok=True)

# Latest frame received from browser (in-memory, ephemeral)
_latest_frame: dict = {'image': None, 'ts': 0}

# ---------------------------------------------------------------------------
# DeepFace — lazy load (heavy import, downloads models on first use)
# ---------------------------------------------------------------------------

_deepface = None

def _get_deepface():
    global _deepface
    if _deepface is None:
        from deepface import DeepFace
        _deepface = DeepFace
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
    {'id': 'glm-4v-flash',  'label': 'GLM-4V Flash (Free · Fast)',      'provider': 'zai'},
    {'id': 'glm-4v',        'label': 'GLM-4V (Better · Paid)',           'provider': 'zai'},
    {'id': 'glm-4v-plus',   'label': 'GLM-4V Plus (Best · Paid)',        'provider': 'zai'},
]

DEFAULT_VISION_MODEL    = os.environ.get('VISION_MODEL', 'glm-4v-flash')
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


def _call_vision(image_b64: str, prompt: str, model: str | None = None) -> str:
    """
    Send an image + prompt to the configured vision model and return the text response.

    image_b64 may be a raw base64 string or a data-URI (data:image/jpeg;base64,...).
    """
    if model is None:
        model, _ = _get_vision_model()

    # Strip data-URI prefix if present
    if image_b64.startswith('data:'):
        image_b64 = image_b64.split(',', 1)[1]

    api_key = os.environ.get('ZAI_API_KEY', '')
    if not api_key:
        raise ValueError('ZAI_API_KEY is not set — cannot call vision model')

    payload = {
        'model': model,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image_url',
                 'image_url': {'url': f'data:image/jpeg;base64,{image_b64}'}},
                {'type': 'text', 'text': prompt},
            ],
        }],
        'max_tokens': 600,
    }

    resp = requests.post(
        'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type':  'application/json',
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content'].strip()


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
    image_data = image
    if ',' in image_data:
        image_data = image_data.split(',', 1)[1]
    image_bytes = base64.b64decode(image_data)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        DeepFace = _get_deepface()
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
    _clear_deepface_cache()
    return jsonify({'ok': True, 'deleted': safe_name})


# ---------------------------------------------------------------------------
# GET /api/vision/models  — list available vision models (for admin UI)
# ---------------------------------------------------------------------------

@vision_bp.route('/api/vision/models', methods=['GET'])
def list_vision_models():
    active_model, _ = _get_vision_model()
    return jsonify({'models': VISION_MODELS, 'active': active_model})
