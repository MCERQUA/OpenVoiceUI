"""
Image generation proxy — Google Gemini / Imagen APIs.
Keeps GEMINI_API_KEY server-side.

POST /api/image-gen
  body: { prompt, images?: [{mime_type, data: base64}], model? }
  returns: { images: [{mime_type, data, url}], text }
  NOTE: every generated image is saved to UPLOADS_DIR on the server immediately
        before the response is returned. `url` is the permanent server path.

GET  /api/image-gen/saved       — load AI designs manifest (server-side, cross-device)
POST /api/image-gen/saved       — append or replace entry in manifest
DELETE /api/image-gen/saved     — remove an entry by url
"""
import base64
import json
import logging
import os
import time
import requests as http
from flask import Blueprint, jsonify, request
from services.paths import UPLOADS_DIR

logger = logging.getLogger(__name__)
image_gen_bp = Blueprint('image_gen', __name__)

GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'

DEFAULT_MODEL = 'nano-banana-pro-preview'

AI_DESIGNS_MANIFEST = UPLOADS_DIR / 'ai-designs-manifest.json'


def _load_manifest():
    try:
        if AI_DESIGNS_MANIFEST.exists():
            return json.loads(AI_DESIGNS_MANIFEST.read_text())
    except Exception:
        pass
    return []


def _save_manifest(entries):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    AI_DESIGNS_MANIFEST.write_text(json.dumps(entries, indent=2))


def _save_generated_image(mime_type: str, b64_data: str) -> str:
    """Save a base64-encoded generated image to UPLOADS_DIR. Returns the /uploads/... URL."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ext = mime_type.split('/')[-1] if '/' in mime_type else 'png'
    filename = f'ai-gen-{int(time.time() * 1000)}.{ext}'
    path = UPLOADS_DIR / filename
    path.write_bytes(base64.b64decode(b64_data))
    logger.info('image_gen: saved generated image → %s (%d bytes)', path, path.stat().st_size)
    return f'/uploads/{filename}'


def _generate_gemini(model, prompt, images):
    """generateContent-based models (Gemini, nano-banana, etc.)"""
    parts = []
    for i, img in enumerate(images):
        data = img.get('data')
        if not data:
            logger.warning('image_gen: skipping ref image %d — missing data (client sent empty base64)', i)
            continue
        parts.append({'inline_data': {
            'mime_type': img.get('mime_type', 'image/png'),
            'data': data,
        }})
    parts.append({'text': prompt})
    logger.info('image_gen: model=%s images=%d prompt_len=%d', model, len([p for p in parts if 'inline_data' in p]), len(prompt))

    payload = {
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {'responseModalities': ['IMAGE', 'TEXT']},
    }
    url = f'{GEMINI_BASE}/{model}:generateContent?key={GEMINI_KEY}'
    resp = http.post(url, json=payload, timeout=90)
    resp.raise_for_status()
    result = resp.json()

    images_out, text_out = [], ''
    for candidate in result.get('candidates', []):
        for part in candidate.get('content', {}).get('parts', []):
            if 'inlineData' in part:
                images_out.append({
                    'mime_type': part['inlineData']['mimeType'],
                    'data': part['inlineData']['data'],
                })
            elif 'text' in part:
                text_out += part['text']
    return images_out, text_out


def _generate_imagen(model, prompt):
    """predict-based models (Imagen 4, etc.) — text-to-image only"""
    payload = {
        'instances': [{'prompt': prompt}],
        'parameters': {'sampleCount': 1},
    }
    url = f'{GEMINI_BASE}/{model}:predict?key={GEMINI_KEY}'
    resp = http.post(url, json=payload, timeout=90)
    resp.raise_for_status()
    result = resp.json()

    images_out = []
    for pred in result.get('predictions', []):
        if 'bytesBase64Encoded' in pred:
            images_out.append({
                'mime_type': pred.get('mimeType', 'image/png'),
                'data': pred['bytesBase64Encoded'],
            })
    return images_out, ''


@image_gen_bp.route('/api/image-gen', methods=['POST'])
def generate_image():
    if not GEMINI_KEY:
        return jsonify({'error': 'GEMINI_API_KEY not configured on server'}), 503

    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    images = data.get('images', [])
    model = data.get('model') or DEFAULT_MODEL

    if not prompt:
        return jsonify({'error': 'prompt is required'}), 400

    try:
        is_imagen = model.startswith('imagen-')
        if is_imagen:
            imgs_out, text_out = _generate_imagen(model, prompt)
        else:
            imgs_out, text_out = _generate_gemini(model, prompt, images)

        if not imgs_out:
            return jsonify({'error': 'Model returned no image', 'text': text_out}), 502

        # Save every generated image to disk immediately — before the response leaves the server.
        # The client receives a permanent /uploads/... URL; no client-side upload step needed.
        for img in imgs_out:
            try:
                img['url'] = _save_generated_image(img['mime_type'], img['data'])
            except Exception as save_err:
                logger.error('image_gen: failed to save image to disk: %s', save_err)
                img['url'] = None  # client must handle this case

        return jsonify({'images': imgs_out, 'text': text_out})

    except http.HTTPError as e:
        body = e.response.text[:400] if e.response else str(e)
        logger.error('Gemini image-gen HTTP error: %s', body)
        return jsonify({'error': body}), e.response.status_code if e.response else 502
    except Exception as e:
        logger.exception('Gemini image-gen error')
        return jsonify({'error': str(e)}), 500


@image_gen_bp.route('/api/image-gen/saved', methods=['GET'])
def get_saved_designs():
    """Return all saved AI designs — persisted on server, works across devices."""
    return jsonify(_load_manifest())


@image_gen_bp.route('/api/image-gen/saved', methods=['POST'])
def save_design():
    """Prepend a design entry to the server-side manifest."""
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    name = (data.get('name') or 'AI Generated').strip()[:80]
    ts = data.get('ts') or 0

    if not url:
        return jsonify({'error': 'url is required'}), 400

    entries = _load_manifest()
    # Avoid duplicates — remove existing entry for same URL first
    entries = [e for e in entries if e.get('url') != url]
    entries.insert(0, {'url': url, 'name': name, 'ts': ts})
    _save_manifest(entries)
    logger.info('ai-designs manifest: saved %d entries', len(entries))
    return jsonify({'ok': True, 'count': len(entries)})


@image_gen_bp.route('/api/image-gen/saved', methods=['DELETE'])
def delete_saved_design():
    """Remove a design entry from the manifest by URL."""
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'url is required'}), 400

    entries = _load_manifest()
    entries = [e for e in entries if e.get('url') != url]
    _save_manifest(entries)
    return jsonify({'ok': True, 'count': len(entries)})
