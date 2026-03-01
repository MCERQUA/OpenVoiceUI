"""
Canvas routes blueprint — extracted from server.py (P2-T5).

Provides all canvas-related HTTP endpoints plus the canvas context tracking
and manifest management helpers that other modules (e.g. server.py's
conversation handler) need via direct import.
"""

import html as html_module
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

import requests as http_requests
from flask import Blueprint, Response, jsonify, redirect, request, send_file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from services.paths import APP_ROOT as _APP_ROOT, CANVAS_MANIFEST_PATH, CANVAS_PAGES_DIR
CANVAS_SSE_PORT = int(os.getenv('CANVAS_SSE_PORT', '3030'))
CANVAS_SESSION_PORT = int(os.getenv('CANVAS_SESSION_PORT', '3002'))
BRAIN_EVENTS_PATH = Path('/tmp/openvoiceui-events.jsonl')
# Self-hosted installs: auth is disabled by default. Set CANVAS_REQUIRE_AUTH=true to enable Clerk JWT checks.
CANVAS_REQUIRE_AUTH = os.getenv('CANVAS_REQUIRE_AUTH', 'false').lower() == 'true'

CATEGORY_KEYWORDS = {
    'dashboards': ['dashboard', 'monitor', 'status', 'overview', 'control panel', 'panel'],
    'weather': ['weather', 'temperature', 'forecast', 'climate', 'rain', 'sunny', 'humidity'],
    'research': ['research', 'analysis', 'study', 'compare', 'investigate', 'explore'],
    'social': ['twitter', 'x.com', 'social', 'post', 'tweet', 'follower', 'engagement'],
    'finance': ['price', 'cost', 'budget', 'money', 'crypto', 'stock', 'market'],
    'tasks': ['todo', 'task', 'project', 'plan', 'roadmap', 'checklist'],
    'reference': ['guide', 'reference', 'documentation', 'help', 'how to', 'tutorial'],
    'entertainment': ['music', 'radio', 'playlist', 'dj', 'audio', 'song'],
}

CATEGORY_ICONS = {
    'dashboards': '📊',
    'weather': '🌤️',
    'research': '🔬',
    'social': '🐦',
    'finance': '💰',
    'tasks': '✅',
    'reference': '📖',
    'entertainment': '🎵',
    'uncategorized': '📁',
}

CATEGORY_COLORS = {
    'dashboards': '#4a9eff',
    'weather': '#ffb347',
    'research': '#9b59b6',
    'social': '#1da1f2',
    'finance': '#2ecc71',
    'tasks': '#e74c3c',
    'reference': '#95a5a6',
    'entertainment': '#e91e63',
    'uncategorized': '#6e7681',
}

# ---------------------------------------------------------------------------
# Canvas context state (module-level so other modules can import it)
# ---------------------------------------------------------------------------

_canvas_context_lock = threading.Lock()

canvas_context = {
    'current_page': None,    # filename of current page
    'current_title': None,   # title of current page
    'page_content': None,    # brief content summary
    'updated_at': None,      # when context was last updated
    'all_pages': [],         # list of all known canvas pages
}

# ---------------------------------------------------------------------------
# Manifest cache
# ---------------------------------------------------------------------------

_manifest_cache: dict = {'data': None, 'mtime': 0}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _notify_brain(event_type: str, **data) -> None:
    """Append a canvas event to the Brain event log (non-critical)."""
    try:
        event = {'type': event_type, 'timestamp': datetime.now().isoformat()}
        event.update(data)
        with open(BRAIN_EVENTS_PATH, 'a') as f:
            f.write(json.dumps(event) + '\n')
    except Exception as exc:
        logging.getLogger(__name__).debug(f'Brain notification failed (non-critical): {exc}')


# ---------------------------------------------------------------------------
# Canvas context helpers (imported by server.py conversation handler)
# ---------------------------------------------------------------------------

def update_canvas_context(page_path: str, title: str = None, content_summary: str = None) -> None:
    """Update the current canvas context (called by frontend)."""
    global canvas_context
    canvas_context['current_page'] = page_path
    canvas_context['current_title'] = title
    canvas_context['page_content'] = content_summary
    canvas_context['updated_at'] = datetime.now().isoformat()

    _notify_brain('canvas_display', page=page_path, title=title)

    try:
        if CANVAS_PAGES_DIR.exists():
            pages = sorted(
                CANVAS_PAGES_DIR.glob('*.html'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:30]
            canvas_context['all_pages'] = [
                {'name': p.name, 'title': p.stem.replace('-', ' '), 'mtime': p.stat().st_mtime}
                for p in pages
            ]
    except Exception:
        pass


def extract_canvas_page_content(page_path: str, max_chars: int = 1000) -> str:
    """Extract readable text content from a canvas HTML page."""
    try:
        if page_path.startswith('/pages/'):
            page_path = page_path[7:]
        full_path = CANVAS_PAGES_DIR / page_path
        if not full_path.exists():
            return ''
        html_raw = full_path.read_text(errors='ignore')
        html_raw = re.sub(r'<script[^>]*>.*?</script>', '', html_raw, flags=re.DOTALL | re.IGNORECASE)
        html_raw = re.sub(r'<style[^>]*>.*?</style>', '', html_raw, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', html_raw)
        text = re.sub(r'\s+', ' ', text).strip()
        text = html_module.unescape(text)
        return text[:max_chars]
    except Exception as exc:
        logging.getLogger(__name__).debug(f'Failed to extract canvas content: {exc}')
        return ''


def get_canvas_context() -> str:
    """Return canvas context string for the agent's system prompt with full page catalog."""
    manifest = load_canvas_manifest()
    parts = ['\n--- CANVAS CONTEXT ---']

    if canvas_context.get('current_page'):
        page_name = canvas_context['current_title'] or canvas_context['current_page']
        parts.append(f"Currently viewing: {page_name}")
        page_content = extract_canvas_page_content(canvas_context['current_page'], max_chars=800)
        if page_content:
            parts.append('\nPage content summary:')
            parts.append(page_content[:800])

    starred = [p for p in manifest.get('pages', {}).values() if p.get('starred')]
    if starred:
        parts.append('\nStarred pages (user favorites, say name to open):')
        for p in starred[:5]:
            aliases = p.get('voice_aliases', [])[:2]
            alias_str = f" (say: {', '.join(aliases)})" if aliases else ''
            parts.append(f"  - {p['display_name']}{alias_str}")

    categories = manifest.get('categories', {})
    if categories:
        parts.append('\nPage categories:')
        for cat_id, cat in categories.items():
            count = len(cat.get('pages', []))
            if count > 0:
                parts.append(f"  {cat.get('icon', '📄')} {cat['name']}: {count} pages")

    recent = manifest.get('recently_viewed', [])[:5]
    if recent:
        recent_names = []
        for pid in recent:
            if pid in manifest.get('pages', {}):
                recent_names.append(manifest['pages'][pid].get('display_name', pid))
        if recent_names:
            parts.append(f"\nRecently viewed: {', '.join(recent_names[:3])}")

    parts.append('\nVOICE COMMANDS:')
    parts.append('- "Show [page name]" - Open a specific canvas page')
    parts.append('- "Show [category] pages" - Show category overview')
    parts.append('- "What pages do we have?" - List available pages')
    parts.append('- "Update this page" - Modify the current page')
    parts.append('\nAGENT CANVAS CONTROL:')
    parts.append('- To open a canvas page, include: [CANVAS:page-name]')
    parts.append('- Example: [CANVAS:dashboard] or [CANVAS:weather]')
    parts.append('- To open the canvas menu, include: [CANVAS_MENU]')
    parts.append('- The canvas will open automatically when user sees your response')
    parts.append('\nAGENT SONG GENERATION (Suno AI):')
    parts.append('- To generate a new song, include: [SUNO_GENERATE:describe the song here]')
    parts.append('- Example: [SUNO_GENERATE:upbeat track about a sunny day]')
    parts.append('- The frontend will call /api/suno, poll for completion (~45s), then auto-play the new song')
    parts.append('- Songs are saved to generated_music/ and appear in the music player')
    parts.append('- Costs ~12 Suno credits per song (2 tracks generated per request)')
    parts.append('\nAGENT MUSIC CONTROL:')
    parts.append('- To play music/radio, include: [MUSIC_PLAY]')
    parts.append('- To play a specific track, include: [MUSIC_PLAY:track name]')
    parts.append('- To stop music, include: [MUSIC_STOP]')
    parts.append('- To skip to next track, include: [MUSIC_NEXT]')
    parts.append('- Available tracks are loaded dynamically from the music library')
    parts.append('- The music player will open/close automatically when user sees your response')
    parts.append('--- END CANVAS CONTEXT ---')

    return '\n'.join(parts)


def get_current_canvas_page_for_worker() -> str | None:
    """Return current canvas page filename for workers to update."""
    if canvas_context.get('current_page'):
        page = canvas_context['current_page']
        if page.startswith('/pages/'):
            page = page[7:]
        return page
    return None


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def load_canvas_manifest() -> dict:
    """Load manifest with mtime-based caching."""
    global _manifest_cache
    if CANVAS_MANIFEST_PATH.exists():
        try:
            mtime = CANVAS_MANIFEST_PATH.stat().st_mtime
            if mtime > _manifest_cache['mtime']:
                with open(CANVAS_MANIFEST_PATH, 'r') as f:
                    _manifest_cache['data'] = json.load(f)
                    _manifest_cache['mtime'] = mtime
            if _manifest_cache['data']:
                return _manifest_cache['data']
        except (json.JSONDecodeError, IOError) as exc:
            logging.getLogger(__name__).warning(f'Failed to load canvas manifest: {exc}')

    return {
        'version': 1,
        'last_updated': datetime.now().isoformat(),
        'categories': {},
        'pages': {},
        'uncategorized': [],
        'recently_viewed': [],
        'user_custom_order': None,
    }


def save_canvas_manifest(manifest: dict) -> None:
    """Save manifest atomically with backup."""
    manifest['last_updated'] = datetime.now().isoformat()
    try:
        temp_path = CANVAS_MANIFEST_PATH.with_suffix('.tmp')
        with open(temp_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        if CANVAS_MANIFEST_PATH.exists():
            shutil.copy(CANVAS_MANIFEST_PATH, CANVAS_MANIFEST_PATH.with_suffix('.bak'))
        shutil.move(temp_path, CANVAS_MANIFEST_PATH)
        _manifest_cache['mtime'] = 0  # invalidate cache
    except Exception as exc:
        logging.getLogger(__name__).error(f'Failed to save canvas manifest: {exc}')


def suggest_category(title: str, content: str = '') -> str:
    """Suggest category based on title and content keywords."""
    text = (title + ' ' + (content or '')[:500]).lower()
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(3 if kw in text else 0 for kw in keywords)
        if score > 0:
            scores[category] = score
    return max(scores, key=scores.get) if scores else 'uncategorized'


def generate_voice_aliases(title: str) -> list[str]:
    """Generate voice-friendly aliases for a page."""
    aliases = []
    name = title.lower()
    aliases.append(name)
    words = name.replace('-', ' ').split()
    if len(words) > 1:
        aliases.extend(words)
    if words:
        aliases.append(f'{words[0]} page')
    return list(set(aliases))[:5]


def sync_canvas_manifest() -> dict:
    """Full sync with pages directory."""
    manifest = load_canvas_manifest()
    logger = logging.getLogger(__name__)

    if not CANVAS_PAGES_DIR.exists():
        logger.warning(f'Canvas pages directory not found: {CANVAS_PAGES_DIR}')
        return manifest

    existing_files = {p.name for p in CANVAS_PAGES_DIR.glob('*.html')}
    manifest_files = {p.get('filename') for p in manifest['pages'].values()}

    for filename in existing_files - manifest_files:
        page_id = Path(filename).stem
        filepath = CANVAS_PAGES_DIR / filename
        title = page_id.replace('-', ' ').title()
        try:
            content = filepath.read_text()[:1000]
        except Exception:
            content = ''
        category = suggest_category(title, content)
        manifest['pages'][page_id] = {
            'filename': filename,
            'display_name': title,
            'description': '',
            'category': category,
            'tags': [],
            'created': datetime.fromtimestamp(filepath.stat().st_ctime).isoformat(),
            'modified': datetime.fromtimestamp(filepath.stat().st_mtime).isoformat(),
            'starred': False,
            'voice_aliases': generate_voice_aliases(title),
            'access_count': 0,
        }
        if category not in manifest['categories']:
            manifest['categories'][category] = {
                'name': category.title(),
                'icon': CATEGORY_ICONS.get(category, '📄'),
                'color': CATEGORY_COLORS.get(category, '#4a9eff'),
                'pages': [],
            }
        if page_id not in manifest['categories'][category]['pages']:
            manifest['categories'][category]['pages'].append(page_id)
        # Note: uncategorized pages are managed via manifest['categories']['uncategorized']['pages']

    # Reconcile: pages registered in pages{} but missing from their category list
    for page_id, page_data in manifest['pages'].items():
        cat = page_data.get('category', 'uncategorized')
        if cat not in manifest['categories']:
            manifest['categories'][cat] = {
                'name': cat.title(),
                'icon': CATEGORY_ICONS.get(cat, '📄'),
                'color': CATEGORY_COLORS.get(cat, '#4a9eff'),
                'pages': [],
            }
        if page_id not in manifest['categories'][cat]['pages']:
            manifest['categories'][cat]['pages'].append(page_id)
            logger.info(f'Reconciled missing category entry: {page_id} → {cat}')

    deleted_files = manifest_files - existing_files
    for filename in list(deleted_files):
        page_id = Path(filename).stem
        if page_id in manifest['pages']:
            old_cat = manifest['pages'][page_id].get('category')
            if old_cat and old_cat in manifest['categories']:
                if page_id in manifest['categories'][old_cat].get('pages', []):
                    manifest['categories'][old_cat]['pages'].remove(page_id)
            if page_id in manifest.get('uncategorized', []):
                manifest['uncategorized'].remove(page_id)
            del manifest['pages'][page_id]

    save_canvas_manifest(manifest)
    logger.info(f'Canvas manifest synced: {len(manifest["pages"])} pages')
    return manifest


def add_page_to_manifest(filename: str, title: str, description: str = '', content: str = '') -> dict:
    """Add a new page to the manifest (called after page creation)."""
    manifest = load_canvas_manifest()
    page_id = Path(filename).stem
    category = suggest_category(title, content)
    manifest['pages'][page_id] = {
        'filename': filename,
        'display_name': title,
        'description': description[:200] if description else '',
        'category': category,
        'tags': [],
        'created': datetime.now().isoformat(),
        'modified': datetime.now().isoformat(),
        'starred': False,
        'is_public': False,
        'voice_aliases': generate_voice_aliases(title),
        'access_count': 0,
    }
    if category not in manifest['categories']:
        manifest['categories'][category] = {
            'name': category.title(),
            'icon': CATEGORY_ICONS.get(category, '📄'),
            'color': CATEGORY_COLORS.get(category, '#4a9eff'),
            'pages': [],
        }
    if page_id not in manifest['categories'][category]['pages']:
        manifest['categories'][category]['pages'].append(page_id)
    if page_id in manifest.get('uncategorized', []):
        manifest['uncategorized'].remove(page_id)
    save_canvas_manifest(manifest)
    return manifest['pages'][page_id]


def track_page_access(page_id: str) -> None:
    """Track when a page is accessed (for recently viewed)."""
    manifest = load_canvas_manifest()
    if page_id in manifest['pages']:
        manifest['pages'][page_id]['access_count'] = manifest['pages'][page_id].get('access_count', 0) + 1
        recently = manifest.get('recently_viewed', [])
        if page_id in recently:
            recently.remove(page_id)
        recently.insert(0, page_id)
        manifest['recently_viewed'] = recently[:20]
        save_canvas_manifest(manifest)


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

canvas_bp = Blueprint('canvas', __name__)
logger = logging.getLogger(__name__)


@canvas_bp.route('/api/canvas/update', methods=['POST'])
def canvas_update():
    """
    Canvas Display Proxy — forward display commands to Canvas SSE server.
    POST /api/canvas/update
    Body: {"displayOutput": {"type": "page|image|status", "path": "/pages/xyz.html", "title": "Title"}}
    """
    try:
        data = request.get_json()
        if not data or 'displayOutput' not in data:
            return jsonify({'error': 'Missing displayOutput'}), 400

        display_output = data['displayOutput']
        display_type = display_output.get('type')
        path = display_output.get('path', '')
        title = display_output.get('title', '')

        logger.info(f'Canvas update: {display_type} - {title}')

        if display_type == 'page' and path:
            update_canvas_context(path, title)
            logger.info(f'Canvas context updated: {path}')

        canvas_response = http_requests.post(
            f'http://localhost:{CANVAS_SSE_PORT}/update',
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=5,
        )

        if canvas_response.status_code != 200:
            logger.error(f'Canvas server error: {canvas_response.status_code}')
            return jsonify({'error': f'Canvas update failed: {canvas_response.status_code}'}), canvas_response.status_code

        return jsonify({'success': True, 'message': 'Canvas updated successfully'})

    except Exception as exc:
        logger.error(f'Canvas update error: {exc}')
        return jsonify({'error': 'Canvas update failed'}), 500


@canvas_bp.route('/api/canvas/show', methods=['POST'])
def canvas_show_page():
    """
    Quick helper to show a page on canvas.
    POST /api/canvas/show
    Body: {"type": "page", "path": "/pages/test.html", "title": "My Page"}
    """
    try:
        data = request.get_json()
        path = data.get('path', '')
        if not path:
            return jsonify({'error': 'Missing path'}), 400
        # Delegate to canvas_update (same logic, wraps displayOutput format)
        return canvas_update()
    except Exception as exc:
        logger.error(f'Canvas show error: {exc}')
        return jsonify({'error': 'Canvas operation failed'}), 500


@canvas_bp.route('/canvas-proxy')
def canvas_proxy():
    """Proxy Canvas live.html to serve over HTTPS; rewrites SSE/session URLs."""
    try:
        canvas_path = '/var/www/canvas-display/canvas/live.html'
        with open(canvas_path, 'r') as f:
            html_content = f.read()
        html_content = html_content.replace(f'http://localhost:{CANVAS_SSE_PORT}/events', '/canvas-sse/events')
        html_content = html_content.replace('http://localhost:3030/events', '/canvas-sse/events')
        html_content = html_content.replace('/sse/events', '/canvas-sse/events')
        html_content = html_content.replace('/api/session/', '/canvas-session/')
        return Response(html_content, mimetype='text/html')
    except Exception as exc:
        logger.error(f'Canvas proxy error: {exc}')
        return '<html><body><h1>Canvas Error</h1><p>Internal server error</p></body></html>', 500


@canvas_bp.route('/canvas-sse/<path:path>')
def canvas_sse_proxy(path):
    """Proxy SSE events from Canvas server."""
    try:
        resp = http_requests.get(
            f'http://localhost:{CANVAS_SSE_PORT}/{path}',
            stream=True,
            headers={'Accept': 'text/event-stream'},
        )

        def generate():
            for chunk in resp.iter_content(chunk_size=1024):
                if chunk:
                    yield chunk

        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )
    except Exception as exc:
        logger.error(f'Canvas SSE proxy error: {exc}')
        return jsonify({'error': 'Canvas proxy error'}), 500


def _safe_canvas_path(base: str, user_path: str) -> Path | None:
    """Resolve user_path inside base, rejecting path traversal."""
    try:
        base_p = Path(base).resolve()
        resolved = (base_p / user_path).resolve()
        if base_p == resolved or base_p in resolved.parents:
            return resolved
    except Exception:
        pass
    return None


@canvas_bp.route('/pages/<path:path>')
def canvas_pages_proxy(path):
    """Serve files from Canvas pages directory.

    Access control:
    - If CANVAS_REQUIRE_AUTH=true: pages with is_public=False require a valid Clerk session token.
    - Default (self-hosted): all pages served without auth.
    """
    try:
        # Auth check — only when explicitly enabled (opt-in for self-hosted deployments)
        if CANVAS_REQUIRE_AUTH:
            page_id = Path(path).stem
            manifest = load_canvas_manifest()
            page_meta = manifest.get('pages', {}).get(page_id, {})
            is_public = page_meta.get('is_public', False)
            if not is_public:
                from services.auth import get_token_from_request, verify_clerk_token
                token = get_token_from_request()
                user_id = verify_clerk_token(token) if token else None
                if not user_id:
                    if request.headers.get('Accept', '').startswith('text/html'):
                        return redirect('/?redirect=/pages/' + path)
                    return 'Unauthorized', 401

        # P7-T3 security: prevent path traversal
        resolved = _safe_canvas_path(str(CANVAS_PAGES_DIR), path)
        if resolved is None:
            return 'Invalid path', 400
        if resolved.exists():
            with open(resolved, 'rb') as f:
                content = f.read()
            if path.endswith('.html'):
                # Inject padding to clear UI chrome (side buttons: 44px, top tab: 24px)
                _padding_css = (
                    b'<style id="canvas-ui-clearance">'
                    b'html,body{'
                    b'padding-top:15px!important;'
                    b'padding-left:15px!important;'
                    b'padding-right:15px!important;'
                    b'box-sizing:border-box!important;}'
                    b'</style>'
                )
                if b'</head>' in content:
                    content = content.replace(b'</head>', _padding_css + b'</head>', 1)
                else:
                    content = _padding_css + content
                content_type = 'text/html'
            elif path.endswith('.css'):
                content_type = 'text/css'
            elif path.endswith('.js'):
                content_type = 'application/javascript'
            else:
                content_type = 'application/octet-stream'
            return Response(content, mimetype=content_type)
        return 'Page not found', 404
    except Exception as exc:
        logger.error(f'Canvas pages proxy error: {exc}')
        return 'Internal server error', 500


@canvas_bp.route('/images/<path:path>')
def canvas_images_proxy(path):
    """Serve files from Canvas images directory."""
    try:
        # P7-T3 security: prevent path traversal
        resolved = _safe_canvas_path('/var/www/canvas-display/images', path)
        if resolved is None:
            return 'Invalid path', 400
        if resolved.exists():
            return send_file(resolved)
        return 'Image not found', 404
    except Exception as exc:
        logger.error(f'Canvas images proxy error: {exc}')
        return 'Internal server error', 500


# Dev server proxy for website preview in canvas
WEBSITE_DEV_PORT = int(os.getenv('WEBSITE_DEV_PORT', '15050'))

@canvas_bp.route('/website-dev', methods=['GET', 'POST', 'PUT', 'DELETE'], strict_slashes=False)
@canvas_bp.route('/website-dev/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def website_dev_proxy(path=''):
    """Proxy requests to the local website dev server (for HTTPS canvas compatibility)."""
    import re as re_module
    try:
        dev_url = f'http://localhost:{WEBSITE_DEV_PORT}/{path}'
        if request.method == 'GET':
            resp = http_requests.get(dev_url, params=request.args, timeout=30, stream=True)
        elif request.method == 'POST':
            resp = http_requests.post(dev_url, json=request.get_json(silent=True), data=request.get_data(), timeout=30, stream=True)
        elif request.method == 'PUT':
            resp = http_requests.put(dev_url, json=request.get_json(silent=True), data=request.get_data(), timeout=30, stream=True)
        elif request.method == 'DELETE':
            resp = http_requests.delete(dev_url, timeout=30, stream=True)
        else:
            return 'Method not allowed', 405

        content_type = resp.headers.get('content-type', '')

        # For HTML responses, rewrite absolute URLs to go through proxy
        if 'text/html' in content_type:
            content = resp.content.decode('utf-8', errors='replace')
            # Rewrite absolute URLs: src="/..." -> src="/website-dev/..."
            content = re_module.sub(r'(src|href|action)=("|\')/(?!website-dev)', r'\1=\2/website-dev/', content)
            return Response(content.encode('utf-8'), status=resp.status_code, content_type=content_type)

        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        # Forward content type and other relevant headers
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded_headers]

        return Response(generate(), status=resp.status_code, headers=headers)
    except Exception as exc:
        logger.error(f'Website dev proxy error: {exc}')
        return 'Dev server unavailable', 503


# Dashboard API proxy for canvas plugin
DASHBOARD_API_PORT = int(os.getenv('DASHBOARD_API_PORT', '16300'))

@canvas_bp.route('/dashboard-api', methods=['GET', 'POST', 'PUT', 'DELETE'], strict_slashes=False)
@canvas_bp.route('/dashboard-api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def dashboard_api_proxy(path=''):
    """Proxy requests to the Dashboard API server (canvas plugin)."""
    try:
        api_url = f'http://localhost:{DASHBOARD_API_PORT}/{path}'
        headers = {k: v for k, v in request.headers if k.lower() not in ['host', 'content-length']}

        if request.method == 'GET':
            resp = http_requests.get(api_url, params=request.args, headers=headers, timeout=30, stream=True)
        elif request.method == 'POST':
            resp = http_requests.post(api_url, json=request.get_json(silent=True), data=request.get_data(), headers=headers, timeout=30, stream=True)
        elif request.method == 'PUT':
            resp = http_requests.put(api_url, json=request.get_json(silent=True), data=request.get_data(), headers=headers, timeout=30, stream=True)
        elif request.method == 'DELETE':
            resp = http_requests.delete(api_url, headers=headers, timeout=30, stream=True)
        else:
            return 'Method not allowed', 405

        content_type = resp.headers.get('content-type', '')

        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        # Forward content type and other relevant headers
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded_headers]

        return Response(generate(), status=resp.status_code, headers=response_headers)
    except Exception as exc:
        logger.error(f'Dashboard API proxy error: {exc}')
        return jsonify({'error': 'Dashboard API unavailable', 'message': str(exc)}), 503


@canvas_bp.route('/canvas-session/<path:path>', methods=['GET', 'POST'])
def canvas_session_proxy(path):
    """Proxy Canvas session API requests."""
    _default_session = {
        'id': 'default',
        'stats': {'imageCount': 0, 'pageCount': 0, 'dataCount': 0, 'commandCount': 0},
        'outputs': {'images': [], 'pages': [], 'data': [], 'commands': []},
        'timestamp': '',
    }
    try:
        if request.method == 'GET':
            resp = http_requests.get(f'http://localhost:{CANVAS_SESSION_PORT}/api/session/{path}', timeout=5)
        else:
            resp = http_requests.post(
                f'http://localhost:{CANVAS_SESSION_PORT}/api/session/{path}',
                json=request.get_json(),
                headers={'Content-Type': 'application/json'},
                timeout=5,
            )
        try:
            return jsonify(resp.json()), resp.status_code
        except Exception:
            return jsonify(_default_session), 200
    except Exception as exc:
        logger.error(f'Canvas session proxy error: {exc}')
        return jsonify(_default_session), 200


@canvas_bp.route('/api/canvas/context', methods=['POST'])
def update_canvas_route():
    """Receive canvas context from frontend — what page is being displayed."""
    data = request.get_json() or {}
    page_path = data.get('page', '')
    title = data.get('title', '')
    content_summary = data.get('content_summary', '')
    update_canvas_context(page_path, title, content_summary)
    return jsonify({'status': 'ok', 'current_page': page_path})


@canvas_bp.route('/api/canvas/context', methods=['GET'])
def get_canvas_route():
    """Get current canvas context."""
    return jsonify(canvas_context)


@canvas_bp.route('/api/canvas/manifest', methods=['GET'])
def get_canvas_manifest():
    """Get full canvas manifest with all pages and categories."""
    manifest = load_canvas_manifest()
    response = jsonify(manifest)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@canvas_bp.route('/api/canvas/manifest/sync', methods=['POST'])
def sync_manifest():
    """Sync manifest with pages directory — adds new pages, removes deleted."""
    manifest = sync_canvas_manifest()
    return jsonify({
        'status': 'ok',
        'pages_count': len(manifest['pages']),
        'categories_count': len(manifest['categories']),
    })


@canvas_bp.route('/api/canvas/manifest/page/<page_id>', methods=['GET', 'PATCH', 'DELETE'])
def handle_page_metadata(page_id):
    """Get, update, or delete page metadata."""
    manifest = load_canvas_manifest()

    if page_id not in manifest['pages']:
        return jsonify({'error': 'Page not found'}), 404

    if request.method == 'GET':
        return jsonify(manifest['pages'][page_id])

    if request.method == 'DELETE':
        page = manifest['pages'][page_id]
        filename = page.get('filename')
        page_title = page.get('display_name', page_id)
        logger.info(f'Deleting canvas page: {page_title} ({filename})')

        old_category = page.get('category')
        if old_category and old_category in manifest['categories']:
            if page_id in manifest['categories'][old_category].get('pages', []):
                manifest['categories'][old_category]['pages'].remove(page_id)
        if page_id in manifest.get('uncategorized', []):
            manifest['uncategorized'].remove(page_id)
        if page_id in manifest.get('recently_viewed', []):
            manifest['recently_viewed'].remove(page_id)

        del manifest['pages'][page_id]

        # Clear canvas_context if this was the current page
        global canvas_context
        current_page = canvas_context.get('current_page') or ''
        if filename and current_page.endswith(filename):
            canvas_context['current_page'] = None
            canvas_context['current_title'] = None
            canvas_context['page_content'] = None
            logger.info('Cleared canvas context (deleted page was current)')

        # Refresh all_pages list
        try:
            if CANVAS_PAGES_DIR.exists():
                pages = sorted(CANVAS_PAGES_DIR.glob('*.html'), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
                canvas_context['all_pages'] = [
                    {'name': p.name, 'title': p.stem.replace('-', ' '), 'mtime': p.stat().st_mtime}
                    for p in pages
                ]
        except Exception as exc:
            logger.warning(f'Failed to refresh all_pages: {exc}')

        # Archive the file (rename to .bak)
        if filename:
            filepath = CANVAS_PAGES_DIR / filename
            try:
                if filepath.exists():
                    bak_path = filepath.with_suffix('.bak')
                    counter = 1
                    while bak_path.exists():
                        bak_path = filepath.with_name(f'{filepath.stem}.bak.{counter}')
                        counter += 1
                    filepath.rename(bak_path)
                    logger.info(f'Archived canvas page: {filename} -> {bak_path.name}')
            except Exception as exc:
                logger.warning(f'Failed to archive file {filename}: {exc}')

        save_canvas_manifest(manifest)
        _notify_brain('canvas_page_deleted', page_id=page_id, title=page_title, filename=filename)

        try:
            http_requests.post(
                f'http://localhost:{CANVAS_SSE_PORT}/clear-display',
                json={'path': f'/pages/{filename}'},
                timeout=2,
            )
        except Exception as exc:
            logger.debug(f'Could not clear canvas display: {exc}')

        return jsonify({'status': 'ok', 'message': 'Page archived', 'page_id': page_id, 'title': page_title})

    # PATCH — update metadata
    data = request.get_json() or {}
    page = manifest['pages'][page_id]

    for field in ['display_name', 'description', 'category', 'tags', 'starred', 'is_public']:
        if field in data:
            old_category = page.get('category')
            page[field] = data[field]

            if field == 'category' and old_category != data[field]:
                if old_category and old_category in manifest['categories']:
                    if page_id in manifest['categories'][old_category].get('pages', []):
                        manifest['categories'][old_category]['pages'].remove(page_id)
                if old_category == 'uncategorized' and page_id in manifest.get('uncategorized', []):
                    manifest['uncategorized'].remove(page_id)

                new_cat = data[field]
                if new_cat not in manifest['categories']:
                    manifest['categories'][new_cat] = {
                        'name': new_cat.title(),
                        'icon': CATEGORY_ICONS.get(new_cat, '📄'),
                        'color': CATEGORY_COLORS.get(new_cat, '#4a9eff'),
                        'pages': [],
                    }
                if page_id not in manifest['categories'][new_cat]['pages']:
                    manifest['categories'][new_cat]['pages'].append(page_id)

    save_canvas_manifest(manifest)
    return jsonify({'status': 'ok', 'page': page})


@canvas_bp.route('/api/canvas/manifest/category', methods=['GET', 'POST', 'PATCH'])
def handle_category():
    """List, create, or update categories."""
    manifest = load_canvas_manifest()

    if request.method == 'GET':
        return jsonify(manifest.get('categories', {}))

    if request.method == 'POST':
        data = request.get_json() or {}
        cat_id = data.get('id', '').lower().replace(' ', '-')
        if not cat_id:
            return jsonify({'error': 'Category ID required'}), 400
        manifest['categories'][cat_id] = {
            'name': data.get('name', cat_id.title()),
            'icon': data.get('icon', '📄'),
            'color': data.get('color', '#4a9eff'),
            'pages': [],
        }
        save_canvas_manifest(manifest)
        return jsonify({'status': 'ok', 'category': manifest['categories'][cat_id]})

    # PATCH
    data = request.get_json() or {}
    cat_id = data.get('id')
    if not cat_id or cat_id not in manifest['categories']:
        return jsonify({'error': 'Category not found'}), 404
    for field in ['name', 'icon', 'color']:
        if field in data:
            manifest['categories'][cat_id][field] = data[field]
    save_canvas_manifest(manifest)
    return jsonify({'status': 'ok', 'category': manifest['categories'][cat_id]})


@canvas_bp.route('/api/canvas/manifest/access/<page_id>', methods=['POST'])
def track_access(page_id):
    """Track page access (for recently viewed and access count)."""
    track_page_access(page_id)
    return jsonify({'status': 'ok'})


@canvas_bp.route('/api/canvas/pages', methods=['POST'])
def create_canvas_page():
    """
    Save a new canvas page from HTML content.
    POST /api/canvas/pages
    Body: {"filename": "my-page.html", "html": "<html>...</html>", "title": "My Page"}
    Returns: {"filename": "my-page.html", "page_id": "my-page", "url": "/pages/my-page.html"}
    """
    try:
        data = request.get_json()
        if not data or 'html' not in data:
            return jsonify({'error': 'Missing html content'}), 400

        html_content = data['html']
        title = data.get('title', 'Canvas Page')

        # Derive filename from title if not provided
        raw_filename = data.get('filename', '')
        if not raw_filename:
            slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
            raw_filename = f'{slug}.html'

        # Sanitize: strip directory traversal, ensure .html
        filename = Path(raw_filename).name
        if not filename.endswith('.html'):
            filename += '.html'

        CANVAS_PAGES_DIR.mkdir(parents=True, exist_ok=True)
        filepath = CANVAS_PAGES_DIR / filename

        filepath.write_text(html_content, encoding='utf-8')
        logger.info(f'Canvas page saved: {filename} ({len(html_content)} bytes)')

        page_meta = add_page_to_manifest(filename, title, content=html_content[:500])
        _notify_brain('canvas_page_created', filename=filename, title=title)

        return jsonify({
            'filename': filename,
            'page_id': Path(filename).stem,
            'url': f'/pages/{filename}',
            'title': title,
            'category': page_meta.get('category', 'uncategorized'),
        })
    except Exception as exc:
        logger.error(f'Canvas page create error: {exc}')
        return jsonify({'error': 'Canvas page creation failed'}), 500


@canvas_bp.route('/api/canvas/mtime/<path:filename>', methods=['GET'])
def canvas_mtime(filename):
    """Return last modified time of a canvas page (frontend uses to detect changes)."""
    resolved = _safe_canvas_path(str(CANVAS_PAGES_DIR), filename)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return jsonify({'error': 'not found'}), 404
    mtime = resolved.stat().st_mtime
    return jsonify({'mtime': mtime, 'filename': filename})
