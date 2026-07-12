"""
fal.ai image generation proxy — keeps FAL_KEY server-side and instruments every call
so the JamFlow Watch board lights the prov:fal node live (Mike 2026-06-20).

POST /api/fal/image
  body: { prompt, model?, image_size?, num_images?, num_inference_steps? }
  returns: { images: [{url, width, height, content_type}], seed, timings }
  NOTE: every generated image is downloaded and saved to UPLOADS_DIR on the server
        immediately before the response is returned (CLAUDE.md: AI output is saved the
        moment it is produced). The returned `url` is the permanent server path; the
        upstream fal.media URL is ALSO included as `fal_url` for reference.

Mirrors routes/suno.py for telemetry: a per-call provider receipt is written two ways
(file-drop receipt + books queue) so the prov:fal node fires per call and books.db gets
the api_call row. Best-effort — telemetry NEVER fails a generation.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests as http
from flask import Blueprint, jsonify, request

from services.paths import UPLOADS_DIR

logger = logging.getLogger(__name__)
fal_bp = Blueprint('fal', __name__)

FAL_KEY = os.getenv('FAL_KEY', '')
FAL_BASE = 'https://fal.run'
DEFAULT_MODEL = 'fal-ai/flux/schnell'

# Allow-list of supported fal image models (prefix is the fal.run path).
FAL_MODELS = {
    'fal-ai/flux/schnell',
    'fal-ai/flux/dev',
    'fal-ai/flux-pro/v1.1',
    'fal-ai/flux-pro/v1.1-ultra',
    'fal-ai/recraft-v3',
}

# ---------------------------------------------------------------------------
# Per-call provider receipt (JamFlow Watch live pulse) — same pattern as suno.py.
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
                          'provider': 'fal', 'tenant': _RECEIPT_TENANT,
                          'op': op, 'units': units})
        with open(PROVIDER_RECEIPTS_DIR / f"fal-{now.strftime('%Y-%m-%d')}.jsonl", 'a') as f:
            f.write(row + '\n')
    except Exception:  # never let telemetry touch the generation path
        pass
    # JamBot Books: also record as an api_call to the host-tailed books queue
    # (the receipt path above is unmounted on most containers; this one isn't).
    try:
        from services.jambot_books_hook import record_provider_call
        record_provider_call('fal', endpoint='/' + DEFAULT_MODEL, op=op, units=units)
    except Exception:
        pass


def _save_generated_image(url: str, ext_hint: str = 'jpg') -> str:
    """Download a generated image from a fal.media URL into UPLOADS_DIR.
    Returns the permanent /uploads/... server URL. Raises on download failure."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ext = url.rsplit('.', 1)[-1].split('?')[0][:4] if '.' in url.rsplit('/', 1)[-1] else ext_hint
    filename = f'ai-gen-{int(time.time() * 1000)}.{ext}'
    path = UPLOADS_DIR / filename
    r = http.get(url, timeout=60)
    r.raise_for_status()
    path.write_bytes(r.content)
    logger.info('fal: saved generated image → %s (%d bytes)', path, path.stat().st_size)
    return f'/uploads/{filename}'


@fal_bp.route('/api/fal/image', methods=['POST'])
def fal_image():
    if not FAL_KEY:
        return jsonify({'error': 'FAL_KEY not configured on server'}), 500

    body = request.get_json(silent=True) or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'error': 'prompt is required'}), 400

    model = body.get('model') or DEFAULT_MODEL
    if model not in FAL_MODELS:
        return jsonify({'error': f'unsupported model {model}',
                        'supported': sorted(FAL_MODELS)}), 400

    num_images = int(body.get('num_images') or 1)
    payload = {
        'prompt': prompt,
        'image_size': body.get('image_size') or 'square',
        'num_images': num_images,
    }
    # schnell/dev accept num_inference_steps; pro models ignore it harmlessly.
    if body.get('num_inference_steps'):
        payload['num_inference_steps'] = int(body['num_inference_steps'])

    logger.info('fal: model=%s prompt_len=%d num=%d', model, len(prompt), num_images)
    try:
        resp = http.post(f'{FAL_BASE}/{model}',
                         headers={'Authorization': f'Key {FAL_KEY}',
                                  'Content-Type': 'application/json'},
                         json=payload, timeout=120)
    except Exception as e:  # noqa: BLE001
        logger.exception('fal: request failed')
        return jsonify({'error': f'fal request failed: {e}'}), 502

    if resp.status_code != 200:
        logger.warning('fal: upstream %d — %s', resp.status_code, resp.text[:300])
        return jsonify({'error': 'fal upstream error', 'status': resp.status_code,
                        'detail': resp.text[:500]}), 502

    result = resp.json()

    # Save every returned image to the server immediately (permanent URLs).
    out_images = []
    for img in (result.get('images') or []):
        u = img.get('url')
        if not u:
            continue
        ext = (img.get('content_type') or 'image/jpeg').split('/')[-1]
        try:
            server_url = _save_generated_image(u, ext_hint=ext)
        except Exception:  # noqa: BLE001
            logger.exception('fal: failed to persist %s', u)
            server_url = None
        out_images.append({'url': server_url, 'fal_url': u,
                           'width': img.get('width'), 'height': img.get('height'),
                           'content_type': img.get('content_type')})

    # Telemetry: one receipt per successful call (units = images saved).
    _provider_receipt('image', units=str(len(out_images) or num_images))

    return jsonify({'images': out_images, 'seed': result.get('seed'),
                    'timings': result.get('timings'), 'model': model})
