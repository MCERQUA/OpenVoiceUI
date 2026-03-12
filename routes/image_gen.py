"""
Image generation proxy — Google Gemini / Imagen / HuggingFace APIs.
Keeps API keys server-side.

POST /api/image-gen
  body: { prompt, images?: [{mime_type, data: base64}], model?, quality?, aspect? }
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
HF_TOKEN = os.getenv('HF_TOKEN', '')
HF_INFERENCE_BASE = 'https://router.huggingface.co/hf-inference/models'

DEFAULT_MODEL = 'nano-banana-pro-preview'

# HuggingFace model IDs — prefix with 'hf:' in the frontend
HF_MODELS = {
    'black-forest-labs/FLUX.1-schnell',
    'black-forest-labs/FLUX.1-dev',
    'stabilityai/stable-diffusion-xl-base-1.0',
    'stabilityai/stable-diffusion-3.5-large',
    'stabilityai/stable-diffusion-3.5-large-turbo',
}

# Resolution presets for HF models (actual pixel control)
HF_QUALITY_SIZES = {
    'standard': (1024, 1024),
    'high':     (1536, 1536),
    'ultra':    (2048, 2048),
}

# Aspect ratio → width/height multipliers (base is the quality size)
HF_ASPECT_RATIOS = {
    '1:1':  (1.0, 1.0),
    '16:9': (1.33, 0.75),
    '9:16': (0.75, 1.33),
    '4:3':  (1.15, 0.87),
    '3:4':  (0.87, 1.15),
    '3:2':  (1.22, 0.82),
    '2:3':  (0.82, 1.22),
}

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


def _enhance_prompt(idea: str, quality: str = 'standard', style: str = '') -> str:
    """Use Gemini Flash text to turn a rough idea into a detailed merch image prompt."""
    quality_context = {
        'standard': 'print-quality merch art',
        'high': 'high-resolution 2K print-quality merch art, sharp fine detail',
        'ultra': 'ultra-high-resolution 4K print-quality merch art, photorealistic detail, maximum sharpness',
    }.get(quality, 'print-quality merch art')

    style_instruction = (
        f"Art style: {style}. " if style else
        "Art style: vintage screen-print graphic tee art, bold ink outlines, flat cel-shading with 4-5 solid colors. "
    )

    system = (
        "You are an art director specializing in apparel graphics for trade and contractor merch. "
        "Turn rough ideas into image generation prompts that produce great t-shirt and hoodie designs.\n\n"
        "T-SHIRT DESIGN RULES — these are the most important:\n"
        "- Design must be ISOLATED: single bold graphic element on a solid black or transparent background. "
        "No scenic backgrounds, no landscapes, no environments behind the subject.\n"
        "- Use 3 to 5 solid bold colors maximum. High contrast. Prints cleanly on dark fabric.\n"
        "- Style should read like: vintage concert tee, old-school band shirt, Harley Davidson apparel, "
        "classic biker patch art, or retro work-wear graphic — NOT a photograph, NOT a painting, NOT a scene.\n"
        "- The subject (character, object, logo) must be large and centered. Readable at a glance.\n"
        "- Bold clean outlines on every element. No fine detail that disappears at shirt scale.\n"
        "- If there is a character, they should look like a mascot or graphic illustration — "
        "detailed face, gear, and posture — NOT a silhouette, NOT an outline-only figure.\n\n"
        "SPRAY FOAM EQUIPMENT — strictly enforced:\n"
        "- Professional spray foam guns ONLY: hose-connected industrial guns (Graco Fusion, PMC, Graco Reactor) "
        "with a thick heated hose trailing back to equipment. Substantial pistol-grip with hose at the rear.\n"
        "- NEVER a consumer canned foam gun (the orange/yellow Great Stuff gun from Home Depot). "
        "If a gun appears in the design, it is always the professional contractor type.\n\n"
        "PROMPT FORMAT — write the prompt to include:\n"
        "1. Subject description (character/object/logo with specific details)\n"
        f"2. {style_instruction}\n"
        "3. Color palette (name 3-5 specific colors, e.g. 'burnt orange, cream white, black, gold')\n"
        "4. Isolated on solid black background\n"
        "5. End with: 'apparel graphic, screen-print style, bold outlines, print-ready, no background'\n\n"
        f"Quality: {quality_context}\n\n"
        "Return ONLY the prompt. No explanation, no quotes, no intro."
    )

    payload = {
        'contents': [{'role': 'user', 'parts': [{'text': idea}]}],
        'systemInstruction': {'parts': [{'text': system}]},
        'generationConfig': {'maxOutputTokens': 512, 'temperature': 0.9},
    }
    url = f'{GEMINI_BASE}/gemini-2.0-flash:generateContent?key={GEMINI_KEY}'
    resp = http.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    text = ''
    for candidate in result.get('candidates', []):
        for part in candidate.get('content', {}).get('parts', []):
            if 'text' in part:
                text += part['text']
    return text.strip()


def _generate_huggingface(model_id, prompt, quality='standard', aspect='1:1'):
    """HuggingFace Inference API — FLUX, Stable Diffusion, etc."""
    if not HF_TOKEN:
        raise ValueError('HF_TOKEN not configured on server')

    base_w, base_h = HF_QUALITY_SIZES.get(quality, (1024, 1024))
    ar_w, ar_h = HF_ASPECT_RATIOS.get(aspect, (1.0, 1.0))
    # Round to nearest 8 (required by most diffusion models)
    width = round(base_w * ar_w / 8) * 8
    height = round(base_h * ar_h / 8) * 8

    logger.info('image_gen: HF model=%s quality=%s size=%dx%d prompt_len=%d',
                model_id, quality, width, height, len(prompt))

    payload = {
        'inputs': prompt,
        'parameters': {
            'width': width,
            'height': height,
        },
    }

    url = f'{HF_INFERENCE_BASE}/{model_id}'
    headers = {
        'Authorization': f'Bearer {HF_TOKEN}',
        'Content-Type': 'application/json',
    }
    resp = http.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()

    # HF Inference API returns raw image bytes
    content_type = resp.headers.get('Content-Type', 'image/png')
    mime = content_type.split(';')[0].strip()
    b64_data = base64.b64encode(resp.content).decode('ascii')

    images_out = [{'mime_type': mime, 'data': b64_data}]
    return images_out, ''


def _generate_imagen(model, prompt, aspect='1:1'):
    """predict-based models (Imagen 4, etc.) — text-to-image only"""
    payload = {
        'instances': [{'prompt': prompt}],
        'parameters': {'sampleCount': 1, 'aspectRatio': aspect},
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
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    images = data.get('images', [])
    model = data.get('model') or DEFAULT_MODEL
    quality = (data.get('quality') or 'standard').strip()
    aspect = (data.get('aspect') or '1:1').strip()

    if not prompt:
        return jsonify({'error': 'prompt is required'}), 400

    try:
        is_hf = model.startswith('hf:')
        is_imagen = model.startswith('imagen-')

        if is_hf:
            hf_model_id = model[3:]  # strip 'hf:' prefix
            if not HF_TOKEN:
                return jsonify({'error': 'HF_TOKEN not configured on server'}), 503
            imgs_out, text_out = _generate_huggingface(hf_model_id, prompt, quality, aspect)
        elif is_imagen:
            if not GEMINI_KEY:
                return jsonify({'error': 'GEMINI_API_KEY not configured on server'}), 503
            imgs_out, text_out = _generate_imagen(model, prompt, aspect)
        else:
            if not GEMINI_KEY:
                return jsonify({'error': 'GEMINI_API_KEY not configured on server'}), 503
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
        logger.error('image-gen HTTP error: %s', body)
        return jsonify({'error': body}), e.response.status_code if e.response else 502
    except Exception as e:
        logger.exception('image-gen error')
        return jsonify({'error': str(e)}), 500


@image_gen_bp.route('/api/image-gen/enhance', methods=['POST'])
def enhance_prompt_route():
    """Enhance a rough idea into a detailed sprayfoam merch image prompt using Gemini."""
    if not GEMINI_KEY:
        return jsonify({'error': 'GEMINI_API_KEY not configured on server'}), 503

    data = request.get_json(silent=True) or {}
    idea = (data.get('idea') or '').strip()
    quality = (data.get('quality') or 'standard').strip()
    style = (data.get('style') or '').strip()

    if not idea:
        return jsonify({'error': 'idea is required'}), 400

    try:
        enhanced = _enhance_prompt(idea, quality, style)
        if not enhanced:
            return jsonify({'error': 'LLM returned empty response'}), 502
        logger.info('enhance_prompt: idea_len=%d → prompt_len=%d quality=%s', len(idea), len(enhanced), quality)
        return jsonify({'prompt': enhanced})
    except http.HTTPError as e:
        body = e.response.text[:400] if e.response else str(e)
        logger.error('enhance_prompt HTTP error: %s', body)
        return jsonify({'error': body}), e.response.status_code if e.response else 502
    except Exception as e:
        logger.exception('enhance_prompt error')
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
