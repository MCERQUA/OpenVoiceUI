"""
Meshy 3D generation proxy — keeps MESHY_API_KEY server-side, handles generation,
polling, and GLB auto-download to CANVAS_PAGES_DIR so the model is immediately
servable at /pages/<name>.glb.

POST /api/meshy/text-to-3d
  body: { prompt, name?, mode?, ai_model?, topology?, target_polycount?, texture_prompt? }
  mode: "preview" (fast, untextured) or "refine" (PBR textured — needs preview_task_id)
  returns: { task_id, mode }

POST /api/meshy/image-to-3d
  body: { image_url OR image_b64, name?, ai_model?, enable_pbr? }
  returns: { task_id }

GET /api/meshy/task/<task_id>
  query: kind=text-to-3d|image-to-3d (default: text-to-3d), name=<filename-stem>
  When SUCCEEDED: downloads GLB + thumbnail to canvas-pages, returns local paths.
  returns: { status, progress, glb_url?, thumbnail_url?, local_path?, saved }

GET /api/meshy/balance
  returns: { balance }

GET /api/meshy/history
  returns: { models: [{name, glb_url, thumb_url, created}] }
"""
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests as http
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
meshy_bp = Blueprint('meshy', __name__)

MESHY_KEY = os.getenv('MESHY_API_KEY', '')
MESHY_BASE = 'https://api.meshy.ai/openapi'

CANVAS_DIR = Path(os.environ.get('CANVAS_PAGES_DIR', '/app/runtime/canvas-pages'))

# ── helpers ────────────────────────────────────────────────────────────────────

def _headers():
    return {'Authorization': f'Bearer {MESHY_KEY}', 'Content-Type': 'application/json'}


def _safe_name(raw: str) -> str:
    """Slugify a model name to a safe filename stem."""
    slug = re.sub(r'[^a-z0-9]+', '-', raw.lower().strip()).strip('-')
    return slug[:60] or 'model'


def _unique_name(stem: str, ext: str = '.glb') -> str:
    """Return stem (possibly suffixed with -N) so filename doesn't collide."""
    candidate = stem
    n = 1
    while (CANVAS_DIR / (candidate + ext)).exists():
        candidate = f'{stem}-{n}'
        n += 1
    return candidate


def _download_and_save(url: str, dest: Path) -> bool:
    try:
        r = http.get(url, timeout=60, stream=True)
        r.raise_for_status()
        CANVAS_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return True
    except Exception as exc:
        logger.warning('Meshy download failed %s: %s', url, exc)
        return False


# ── routes ─────────────────────────────────────────────────────────────────────

@meshy_bp.route('/api/meshy/text-to-3d', methods=['POST'])
def text_to_3d():
    if not MESHY_KEY:
        return jsonify(error='MESHY_API_KEY not configured'), 500

    body = request.get_json(force=True) or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return jsonify(error='prompt is required'), 400

    mode = body.get('mode', 'preview')
    ai_model = body.get('ai_model', 'meshy-6')

    payload = {'mode': mode, 'ai_model': ai_model, 'target_formats': ['glb']}

    if mode == 'preview':
        payload.update({
            'prompt': prompt,
            'topology': body.get('topology', 'triangle'),
            'target_polycount': int(body.get('target_polycount', 30000)),
            'symmetry_mode': body.get('symmetry_mode', 'auto'),
            'should_remesh': True,
            'auto_size': True,
            'origin_at': 'bottom',
        })
    elif mode == 'refine':
        preview_id = body.get('preview_task_id', '')
        if not preview_id:
            return jsonify(error='preview_task_id required for refine mode'), 400
        payload['preview_task_id'] = preview_id
        payload['enable_pbr'] = True
        if body.get('texture_prompt'):
            payload['texture_prompt'] = body['texture_prompt'][:600]

    try:
        resp = http.post(f'{MESHY_BASE}/v2/text-to-3d', json=payload,
                         headers=_headers(), timeout=30)
        resp.raise_for_status()
        task_id = resp.json().get('result', '')
        return jsonify(task_id=task_id, mode=mode)
    except http.exceptions.HTTPError as exc:
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text
        return jsonify(error=str(exc), detail=detail), exc.response.status_code
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@meshy_bp.route('/api/meshy/image-to-3d', methods=['POST'])
def image_to_3d():
    if not MESHY_KEY:
        return jsonify(error='MESHY_API_KEY not configured'), 500

    body = request.get_json(force=True) or {}
    image_url = body.get('image_url') or body.get('image_b64')
    if not image_url:
        return jsonify(error='image_url or image_b64 required'), 400

    ai_model = body.get('ai_model', 'meshy-6')
    payload = {
        'image_url': image_url,
        'ai_model': ai_model,
        'enable_pbr': bool(body.get('enable_pbr', True)),
        'should_texture': True,
        'topology': body.get('topology', 'triangle'),
        'target_polycount': int(body.get('target_polycount', 30000)),
        'symmetry_mode': body.get('symmetry_mode', 'auto'),
        'image_enhancement': True,
        'auto_size': True,
        'target_formats': ['glb'],
    }

    try:
        resp = http.post(f'{MESHY_BASE}/v1/image-to-3d', json=payload,
                         headers=_headers(), timeout=30)
        resp.raise_for_status()
        task_id = resp.json().get('result', '')
        return jsonify(task_id=task_id)
    except http.exceptions.HTTPError as exc:
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text
        return jsonify(error=str(exc), detail=detail), exc.response.status_code
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@meshy_bp.route('/api/meshy/task/<task_id>', methods=['GET'])
def poll_task(task_id):
    if not MESHY_KEY:
        return jsonify(error='MESHY_API_KEY not configured'), 500

    kind = request.args.get('kind', 'text-to-3d')
    name_hint = request.args.get('name', '')

    # API version depends on kind
    ver = 'v2' if kind == 'text-to-3d' else 'v1'
    endpoint = f'{MESHY_BASE}/{ver}/{kind}/{task_id}'

    try:
        resp = http.get(endpoint, headers=_headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return jsonify(error=str(exc)), 500

    status = data.get('status', 'UNKNOWN')
    progress = data.get('progress', 0)

    result = {'status': status, 'progress': progress, 'saved': False}

    if status == 'SUCCEEDED':
        model_urls = data.get('model_urls') or {}
        glb_remote = model_urls.get('glb') or data.get('model_url', '')
        thumb_remote = data.get('thumbnail_url', '')

        # Choose a filename stem
        stem = _safe_name(name_hint) if name_hint else f'mesh-{task_id[:8]}'
        stem = _unique_name(stem)

        glb_dest = CANVAS_DIR / f'{stem}.glb'
        thumb_dest = CANVAS_DIR / f'{stem}.png'

        saved_glb = False
        if glb_remote and not glb_dest.exists():
            saved_glb = _download_and_save(glb_remote, glb_dest)

        saved_thumb = False
        if thumb_remote and not thumb_dest.exists():
            saved_thumb = _download_and_save(thumb_remote, thumb_dest)

        result.update({
            'glb_url': f'/pages/{stem}.glb' if saved_glb or glb_dest.exists() else None,
            'thumbnail_url': f'/pages/{stem}.png' if saved_thumb or thumb_dest.exists() else None,
            'stem': stem,
            'saved': saved_glb,
            'remote_glb': glb_remote,
        })

    if status == 'FAILED':
        result['task_error'] = data.get('task_error', {})

    return jsonify(result)


@meshy_bp.route('/api/meshy/balance', methods=['GET'])
def balance():
    if not MESHY_KEY:
        return jsonify(error='MESHY_API_KEY not configured'), 500
    try:
        resp = http.get(f'{MESHY_BASE}/v1/balance', headers=_headers(), timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@meshy_bp.route('/api/meshy/history', methods=['GET'])
def history():
    CANVAS_DIR.mkdir(parents=True, exist_ok=True)
    models = []
    for glb in sorted(CANVAS_DIR.glob('*.glb'), key=lambda p: p.stat().st_mtime, reverse=True):
        stem = glb.stem
        thumb = CANVAS_DIR / f'{stem}.png'
        models.append({
            'name': stem,
            'glb_url': f'/pages/{stem}.glb',
            'thumb_url': f'/pages/{stem}.png' if thumb.exists() else None,
            'created': datetime.fromtimestamp(glb.stat().st_mtime, tz=timezone.utc).isoformat(),
            'size_kb': round(glb.stat().st_size / 1024),
        })
    return jsonify(models=models)
