"""routes/canvas_styles.py — Canvas Style System API.

GET    /api/canvas/styles                 — list styles (presets + customs) + active id
GET    /api/canvas/styles/<id>            — full definition
POST   /api/canvas/styles                 — create custom (optional clone_from)
PUT    /api/canvas/styles/<id>            — update custom (presets are read-only)
POST   /api/canvas/styles/<id>/archive    — archive custom (nothing is ever deleted)
POST   /api/canvas/styles/<id>/unarchive
POST   /api/canvas/styles/<id>/activate   — set tenant-active style, render agent files
GET    /api/canvas/styles/<id>/preview    — the style's template/demo page as HTML
POST   /api/canvas/styles/from-website    — spool a brand-review job (host worker)
GET    /api/canvas/styles/from-website/<job_id> — poll that job

Store + renderer: services/canvas_styles.py
Brand review pipeline: scripts/brand-style/ on the HOST (this container has no
browser and no coding agent), reached through the durable tool-job spool.
"""
import logging
import os
import re

from flask import Blueprint, Response, g, jsonify, request

from services import canvas_styles as store
from services import tool_jobs

logger = logging.getLogger(__name__)

canvas_styles_bp = Blueprint('canvas_styles', __name__)


@canvas_styles_bp.after_request
def _no_store(resp):
    # Canvas no-cache policy: style state must never be served stale — a cached
    # /api/canvas/styles response makes the picker show the OLD active style
    # after switching (looks like the choice didn't save).
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    resp.headers['CDN-Cache-Control'] = 'no-store'
    resp.headers['Cloudflare-CDN-Cache-Control'] = 'no-store'
    return resp


@canvas_styles_bp.get('/api/canvas/styles')
def list_styles():
    include_archived = request.args.get('archived') == '1'
    return jsonify({
        'styles': store.list_styles(include_archived=include_archived),
        'active': store.get_active(),
    })


@canvas_styles_bp.get('/api/canvas/styles/<style_id>')
def get_style(style_id):
    style = store.get_style(style_id)
    if style is None:
        return jsonify({'error': 'style not found'}), 404
    return jsonify(style)


@canvas_styles_bp.post('/api/canvas/styles')
def create_style():
    data = request.get_json(silent=True) or {}
    clone_from = data.pop('clone_from', None)
    if clone_from:
        source = store.get_style(clone_from)
        if source is None:
            return jsonify({'error': f'clone_from style not found: {clone_from}'}), 404
        merged = {k: v for k, v in source.items()
                  if k not in ('preset', 'created_at', 'updated_at', 'archived')}
        # Deep-merge tokens so a clone can override just a few colors.
        tokens = dict(merged.get('tokens', {}))
        tokens.update(data.get('tokens', {}) or {})
        merged.update({k: v for k, v in data.items() if k != 'tokens'})
        merged['tokens'] = tokens
        merged['cloned_from'] = clone_from
        data = merged

    err = store._validate(data)
    if err:
        return jsonify({'error': err}), 400
    if store.get_style(data['id']) is not None:
        return jsonify({'error': f"style id '{data['id']}' already exists"}), 409

    store.save_custom(data)
    store.clear_base_css_cache()
    return jsonify({'ok': True, 'id': data['id']}), 201


@canvas_styles_bp.put('/api/canvas/styles/<style_id>')
def update_style(style_id):
    existing = store.load_customs().get(style_id)
    if existing is None:
        if store.load_presets().get(style_id) is not None:
            return jsonify({'error': 'presets are read-only — clone it first'}), 403
        return jsonify({'error': 'style not found'}), 404

    data = request.get_json(silent=True) or {}
    data['id'] = style_id  # id is immutable
    merged = dict(existing)
    tokens = dict(existing.get('tokens', {}))
    tokens.update(data.get('tokens', {}) or {})
    merged.update({k: v for k, v in data.items() if k != 'tokens'})
    merged['tokens'] = tokens

    err = store._validate(merged)
    if err:
        return jsonify({'error': err}), 400

    store.save_custom(merged)
    store.clear_base_css_cache()
    # Keep the agent-facing files fresh if this is the active style.
    if store.get_active_style_id() == style_id:
        store.activate(style_id)
    return jsonify({'ok': True, 'id': style_id})


def _set_archived(style_id, archived):
    existing = store.load_customs().get(style_id)
    if existing is None:
        if store.load_presets().get(style_id) is not None:
            return jsonify({'error': 'presets cannot be archived'}), 403
        return jsonify({'error': 'style not found'}), 404
    if archived and store.get_active_style_id() == style_id:
        return jsonify({'error': 'cannot archive the active style — activate another first'}), 409
    existing['archived'] = archived
    store.save_custom(existing)
    return jsonify({'ok': True, 'id': style_id, 'archived': archived})


@canvas_styles_bp.post('/api/canvas/styles/<style_id>/archive')
def archive_style(style_id):
    return _set_archived(style_id, True)


@canvas_styles_bp.post('/api/canvas/styles/<style_id>/unarchive')
def unarchive_style(style_id):
    return _set_archived(style_id, False)


@canvas_styles_bp.post('/api/canvas/styles/<style_id>/activate')
def activate_style(style_id):
    style = store.activate(style_id)
    if style is None:
        return jsonify({'error': 'style not found'}), 404
    store.clear_base_css_cache()
    logger.info('[canvas-styles] activated %s', style_id)
    return jsonify({'ok': True, 'active': store.get_active(),
                    'agent_file': str(store.ACTIVE_STYLE_MD)})


@canvas_styles_bp.get('/api/canvas/lint/<page_id>')
def lint_page(page_id):
    """Deterministic page QA: emoji-as-icons, banned colors, CDN frameworks,
    missing viewport/page-icon, active-style mismatch. Agents run this after
    every page write and fix what it reports."""
    from services.paths import CANVAS_PAGES_DIR
    safe = ''.join(c for c in page_id if c.isalnum() or c in '-_')
    path = CANVAS_PAGES_DIR / f'{safe}.html'
    if not path.exists():
        return jsonify({'error': 'page not found'}), 404
    try:
        issues = store.lint_page_html(path.read_text(encoding='utf-8', errors='replace'))
    except Exception as exc:
        logger.error('lint failed for %s: %s', page_id, exc)
        return jsonify({'error': 'lint failed'}), 500
    return jsonify({'page_id': safe, 'ok': not issues, 'issues': issues})


_URL_SHAPE_RE = re.compile(
    r'^(?:https?://)?'
    r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}'
    r'(?::\d{2,5})?(?:[/?#]\S*)?$',
    re.I,
)


@canvas_styles_bp.post('/api/canvas/styles/from-website')
def style_from_website():
    """Ask the host to review a website and build a matching style.

    This container cannot do the work: it has no browser to render the site and
    no coding agent to judge what it sees. So the request is spooled to the same
    durable job bus the voice tools use, and the host worker
    (scripts/tool-job-worker.py -> handle_brand_style) executes it.

    The shape check here is a courtesy so the user gets an instant, readable
    error for a typo. It is NOT the security boundary — the host worker does the
    real SSRF check (public_url_or_reason) against every resolved address, since
    that is the process that will actually open the URL.
    """
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'a website URL is required'}), 400
    if len(url) > 500:
        return jsonify({'error': 'that URL is too long'}), 400
    if not _URL_SHAPE_RE.match(url):
        return jsonify({'error': "that doesn't look like a website address — "
                                 'try something like example.com'}), 400

    # Provenance only — the worker derives the real tenant from the job file's
    # own path (/mnt/clients/<tenant>/...), which is authoritative and cannot be
    # wrong even on tenants that never set these env vars.
    tenant = (os.getenv('JAMBOT_TENANT') or os.getenv('TENANT_NAME')
              or os.getenv('CLIENT_NAME') or None)
    job = tool_jobs.create(
        'brand_style',
        {'url': url},
        tenant=tenant,
        user_id=getattr(g, 'clerk_user_id', None),
    )
    logger.info('brand style requested: job=%s url=%s tenant=%s', job['id'], url, tenant)
    return jsonify({
        'ok': True,
        'job_id': job['id'],
        'status': job['status'],
        'message': 'Reviewing the site — this takes a few minutes.',
    }), 202


@canvas_styles_bp.get('/api/canvas/styles/from-website')
def style_from_website_latest():
    """The most recent brand-review job, so the picker can resume after a reload.

    The browser stores NOTHING (root CLAUDE.md: the VPS is the database). A user
    who refreshes mid-review must still see it running, and the only way to know
    that without client storage is to ask the server.
    """
    jobs = [j for j in tool_jobs.list_jobs() if j.get('tool') == 'brand_style']
    if not jobs:
        return jsonify({'job': None})
    return jsonify({'job': _brand_job_view(jobs[0])})


def _brand_job_view(job: dict) -> dict:
    out = {
        'job_id': job['id'],
        'status': job.get('status'),
        'url': (job.get('params') or {}).get('url'),
        'progress': job.get('progress'),
        'error': job.get('error'),
        'created_at': job.get('created_at'),
    }
    result = job.get('result') or {}
    style_id = job.get('style_id')
    if not style_id:
        for art in result.get('artifacts') or []:
            if art.get('type') == 'canvas_style':
                style_id = art.get('id')
                break
    if style_id:
        # Report the style only if it is REALLY in the store. A finished job
        # claiming a style that the picker cannot then show is worse than a
        # failure, because it looks like success.
        exists = store.get_style(style_id) is not None
        out['style_id'] = style_id if exists else None
        out['style_missing'] = not exists
        if exists:
            out['preview_url'] = f'/api/canvas/styles/{style_id}/preview'
    if result.get('summary'):
        out['summary'] = result['summary']
    return out


@canvas_styles_bp.get('/api/canvas/styles/from-website/<job_id>')
def style_from_website_status(job_id):
    job = tool_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'job not found'}), 404
    if job.get('tool') != 'brand_style':
        return jsonify({'error': 'not a brand-style job'}), 400
    return jsonify(_brand_job_view(job))


@canvas_styles_bp.get('/api/canvas/styles/<style_id>/preview')
def preview_style(style_id):
    style = store.get_style(style_id)
    if style is None:
        return Response('style not found', status=404)
    html = store.rendered_template(style)
    # Mirror the /pages/ serve environment: the canvas proxy injects 25px body
    # padding into every real page, and templates are authored assuming it.
    # Without this, spec-compliant templates (no own body padding) preview
    # edge-to-edge.
    _pad = ('<style id="canvas-preview-padding">'
            'html,body{padding:25px!important;box-sizing:border-box!important;}'
            '</style>')
    if '</head>' in html:
        html = html.replace('</head>', _pad + '</head>', 1)
    else:
        html = _pad + html
    resp = Response(html, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    # Same inline-friendly CSP as canvas pages so previews render fully.
    resp.headers['Content-Security-Policy'] = (
        "default-src 'none'; "
        "script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' https://fonts.gstatic.com;"
    )
    return resp
