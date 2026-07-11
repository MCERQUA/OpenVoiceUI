"""
Icon library & AI icon generation.

Static icons:
  GET /api/icons/library                        → list all icon names
  GET /api/icons/library/search?q=<term>        → search icons by name
  GET /api/icons/library/<name>.svg             → serve a Lucide SVG icon

Generated icons:
  POST /api/icons/generate                      → generate icon via Gemini
  GET  /api/icons/generated                     → list user's generated icons
  GET  /api/icons/generated/<filename>          → serve a generated icon
"""

import os
import re
import io
import json
import base64
import hashlib
import time
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request, send_file, Response
from PIL import Image

from services.paths import RUNTIME_DIR

icons_bp = Blueprint('icons', __name__)

# ── Static icon library (Lucide SVGs, shared across all clients) ──
LUCIDE_DIR = Path('/mnt/system/base/icons/lucide')

# ── Per-user generated icons ──
GENERATED_DIR = RUNTIME_DIR / 'icons' / 'generated'

# ── Gemini config ──
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL = 'gemini-2.5-flash-image'
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'

# Cache icon list (rebuilt on first request)
_icon_list_cache = None


def _get_icon_list():
    """Get sorted list of all Lucide icon names."""
    global _icon_list_cache
    if _icon_list_cache is None:
        if LUCIDE_DIR.exists():
            _icon_list_cache = sorted(
                p.stem for p in LUCIDE_DIR.glob('*.svg')
            )
        else:
            _icon_list_cache = []
    return _icon_list_cache


def _ensure_generated_dir():
    """Create per-user generated icons directory."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    return GENERATED_DIR


def _remove_background(image_bytes: bytes, tolerance: int = 60) -> bytes:
    """
    Remove solid-color background from a generated icon.

    Samples the entire border perimeter to find the dominant background color,
    then walks every pixel — any pixel within `tolerance` Euclidean distance
    (in RGB space) of the background color becomes fully transparent.
    Anti-aliased edge pixels get partial alpha.

    Returns PNG bytes with real alpha channel.
    """
    from collections import Counter

    img = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
    pixels = img.load()
    w, h = img.size

    # Sample the entire border perimeter (inset 5px to avoid edge artifacts)
    inset = min(5, w // 20, h // 20)
    border_pixels = []
    # Top and bottom rows
    for x in range(inset, w - inset, max(1, w // 50)):
        border_pixels.append(pixels[x, inset])
        border_pixels.append(pixels[x, h - 1 - inset])
    # Left and right columns
    for y in range(inset, h - inset, max(1, h // 50)):
        border_pixels.append(pixels[inset, y])
        border_pixels.append(pixels[w - 1 - inset, y])

    # Bucket colors (round to nearest 20) and find the most common
    def _bucket(c):
        return (c[0] // 20 * 20, c[1] // 20 * 20, c[2] // 20 * 20)

    counts = Counter(_bucket(c) for c in border_pixels)
    bg_bucket = counts.most_common(1)[0][0]

    # Average the actual border values that fall in the winning bucket
    matching = [c for c in border_pixels if _bucket(c) == bg_bucket]
    bg_r = sum(c[0] for c in matching) // len(matching)
    bg_g = sum(c[1] for c in matching) // len(matching)
    bg_b = sum(c[2] for c in matching) // len(matching)

    # Walk every pixel and adjust alpha
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            dist = ((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) ** 0.5
            if dist < tolerance * 0.5:
                # Clearly background — fully transparent
                pixels[x, y] = (r, g, b, 0)
            elif dist < tolerance:
                # Anti-alias zone — partial transparency
                alpha_ratio = (dist - tolerance * 0.5) / (tolerance * 0.5)
                new_alpha = int(a * alpha_ratio)
                pixels[x, y] = (r, g, b, new_alpha)
            # else: keep pixel as-is (part of the icon)

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════
#  STATIC ICON LIBRARY
# ══════════════════════════════════════════════════════════════

@icons_bp.route('/api/icons/library')
def list_icons():
    """List all available icon names."""
    icons = _get_icon_list()
    return jsonify({
        'count': len(icons),
        'icons': icons,
    })


@icons_bp.route('/api/icons/library/search')
def search_icons():
    """Search icons by name. ?q=folder&limit=20"""
    q = request.args.get('q', '').lower().strip()
    limit = min(int(request.args.get('limit', 50)), 200)

    if not q:
        return jsonify({'error': 'Missing ?q= parameter'}), 400

    icons = _get_icon_list()
    # Exact prefix matches first, then contains
    prefix = [n for n in icons if n.startswith(q)]
    contains = [n for n in icons if q in n and n not in prefix]
    results = (prefix + contains)[:limit]

    return jsonify({
        'query': q,
        'count': len(results),
        'icons': results,
    })


@icons_bp.route('/api/icons/library/<name>.svg')
def serve_icon(name):
    """Serve a Lucide SVG icon by name.

    NO-CACHE: see docs/jambot/no-cache-policy.md. Icons are user-visible
    surfaces that agents may swap or redirect. No browser cache anywhere on
    icons in this system, even for the "static" Lucide set.
    """
    # Sanitize name
    safe = re.sub(r'[^a-z0-9\-]', '', name.lower())
    path = LUCIDE_DIR / f'{safe}.svg'

    if not path.exists():
        return Response('<!-- icon not found -->', status=404, mimetype='image/svg+xml')

    resp = send_file(str(path), mimetype='image/svg+xml')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ══════════════════════════════════════════════════════════════
#  AI ICON GENERATION (Gemini)
# ══════════════════════════════════════════════════════════════

# Default style for generated icons — modern glassmorphic 3D, matching the
# rest of the desktop's move away from flat/retro toward an Apple-style look.
# Override per-call with the `style` param (e.g. retro/pixel-art for games).
DEFAULT_ICON_STYLE = (
    'modern 3D glassmorphic app icon, frosted translucent glass material, '
    'soft rounded squircle shape, subtle depth and drop shadow, vibrant '
    'gradient accent color, macOS Big Sur / visionOS app icon style'
)


class IconGenerationError(Exception):
    """Raised by generate_icon_image() on any failure — callers (HTTP route
    or internal auto-gen hook) decide how to surface it."""
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def generate_icon_image(prompt, name=None, style=None):
    """
    Core icon-generation logic — callable directly (used by the auto-gen
    hook in routes/canvas.py) or via the /api/icons/generate HTTP route.

    Returns a dict: {url, name, filename, prompt, size}
    Raises IconGenerationError on any failure.
    """
    if not GEMINI_API_KEY:
        raise IconGenerationError('GEMINI_API_KEY not configured', status=500)

    user_prompt = (prompt or '').strip()
    if not user_prompt:
        raise IconGenerationError('Missing prompt', status=400)

    name_slug = (name or '').strip()
    style = (style or '').strip()

    # Build the generation prompt
    # NOTE: NEVER say "transparent background" — AI models render checkerboard patterns
    # instead of real alpha. Use a solid chroma-key color that we remove in post-processing.
    # The green bg is ALWAYS appended regardless of custom style — background removal depends on it.
    base_style = style or DEFAULT_ICON_STYLE
    # Strip any "transparent" from custom styles — it causes checkerboard
    base_style = base_style.replace('transparent background', 'solid background')
    style_instruction = f'{base_style}, solid bright green (#00FF00) background'

    full_prompt = (
        f'Generate a single app icon: {user_prompt}. '
        f'Style: {style_instruction}. '
        f'The icon should be simple, recognizable at 48x48 pixels, centered on the canvas, '
        f'with no text or labels. Square aspect ratio. Professional quality. '
        f'The background MUST be a flat solid bright green (#00FF00) with no gradients or patterns.'
    )

    # Generate filename
    if not name_slug:
        # Derive from prompt
        name_slug = re.sub(r'[^a-z0-9]+', '-', user_prompt.lower())[:40].strip('-')
    safe_name = re.sub(r'[^a-z0-9\-]', '', name_slug)
    if not safe_name:
        safe_name = 'icon-' + hashlib.md5(user_prompt.encode()).hexdigest()[:8]

    # Call Gemini API
    try:
        resp = requests.post(
            f'{GEMINI_URL}?key={GEMINI_API_KEY}',
            json={
                'contents': [{'parts': [{'text': full_prompt}]}],
                'generationConfig': {
                    'responseModalities': ['IMAGE', 'TEXT'],
                },
            },
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()
    except requests.RequestException as e:
        raise IconGenerationError(f'Gemini API error: {str(e)}', status=502)

    # Extract image from response
    image_data = None
    mime_type = 'image/png'
    try:
        for candidate in result.get('candidates', []):
            for part in candidate.get('content', {}).get('parts', []):
                if 'inlineData' in part:
                    image_data = base64.b64decode(part['inlineData']['data'])
                    mime_type = part['inlineData'].get('mimeType', 'image/png')
                    break
            if image_data:
                break
    except (KeyError, TypeError):
        pass

    if not image_data:
        raise IconGenerationError('Gemini did not return an image', status=502)

    # Remove background → real PNG alpha transparency
    # Save original as backup first, then process
    try:
        image_data = _remove_background(image_data)
        mime_type = 'image/png'  # always PNG after background removal
    except Exception:
        pass  # if removal fails, keep the original image

    # Always PNG after processing (background removal outputs PNG)
    ext = '.png'

    # Save to server immediately (NEVER lose generated content)
    out_dir = _ensure_generated_dir()
    filename = f'{safe_name}{ext}'
    out_path = out_dir / filename

    # Don't overwrite — add timestamp suffix
    if out_path.exists():
        filename = f'{safe_name}-{int(time.time())}{ext}'
        out_path = out_dir / filename

    out_path.write_bytes(image_data)

    # Save metadata alongside
    meta_path = out_dir / f'{filename}.meta.json'
    meta_path.write_text(json.dumps({
        'prompt': user_prompt,
        'full_prompt': full_prompt,
        'style': style_instruction,
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'size': len(image_data),
        'mime': mime_type,
    }, indent=2))

    return {
        'url': f'/api/icons/generated/{filename}',
        'name': safe_name,
        'filename': filename,
        'prompt': user_prompt,
        'size': len(image_data),
    }


@icons_bp.route('/api/icons/generate', methods=['POST'])
def generate_icon():
    """
    Generate a custom icon via Gemini image generation.

    POST body:
      { "prompt": "description of icon",
        "name": "optional-filename-slug",
        "style": "optional style override" }

    Returns:
      { "url": "/api/icons/generated/my-icon.png",
        "name": "my-icon",
        "prompt": "..." }
    """
    data = request.get_json(silent=True) or {}
    try:
        result = generate_icon_image(
            data.get('prompt', ''), name=data.get('name', ''), style=data.get('style', ''),
        )
    except IconGenerationError as e:
        return jsonify({'error': str(e)}), e.status
    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  GENERATED ICONS — LIST & SERVE
# ══════════════════════════════════════════════════════════════

@icons_bp.route('/api/icons/generated')
def list_generated():
    """List user's generated icons."""
    out_dir = _ensure_generated_dir()
    icons = []
    for p in sorted(out_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.suffix in ('.png', '.jpg', '.jpeg', '.webp') and not p.name.endswith('.meta.json'):
            meta = {}
            meta_path = out_dir / f'{p.name}.meta.json'
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except Exception:
                    pass
            icons.append({
                'name': p.stem,
                'filename': p.name,
                'url': f'/api/icons/generated/{p.name}',
                'size': p.stat().st_size,
                'prompt': meta.get('prompt', ''),
                'generated_at': meta.get('generated_at', ''),
            })
    return jsonify({'count': len(icons), 'icons': icons})


@icons_bp.route('/api/icons/generated/<filename>')
def serve_generated(filename):
    """Serve a generated icon.

    NO-CACHE: see docs/jambot/no-cache-policy.md. Agents regenerate icons —
    a 1-hour cache here used to hide updates for an hour. Live updates win;
    optimize the source images for size instead.
    """
    safe = re.sub(r'[^\w.\-]', '', filename)
    path = _ensure_generated_dir() / safe
    if not path.exists():
        return jsonify({'error': 'Not found'}), 404
    resp = send_file(str(path))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp
