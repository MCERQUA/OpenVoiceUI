"""routes/hub.py — the JamBot Hub (JamDash) served on the business's own hostname at /hub/.

The Hub is a static app (MCERQUA/jamdash, built by Danielle's lane) that reads business data only
through the common backend (/api/biz/v1, routes/biz.py) and the existing per-tenant read-throughs.
It ships INSIDE this image, vendored from a pinned jamdash commit into hub/ (tools/hub/vendor-hub.sh),
so every business runs the same versioned Hub, exactly like the rest of OpenVoiceUI.

What this blueprint guarantees (tests: tests/test_hub.py):
  * Same people as /api/biz/v1: a Clerk session of an allowed user of THIS business (__session cookie or
    Bearer), or this business's own X-Agent-Key (read-only static files; the agent can check its own Hub).
    A presented but wrong X-Agent-Key is 403. A dev-sandbox key is not a Hub credential.
    No session: a page navigation goes to the sign-in shell and comes back (/?redirect=/hub/), anything else
    gets 401. Auth off (local install, no Clerk key): open, like the rest of the app.
  * Live mode only. The served index.html carries `globalThis.JAMDASH_MODE='live'`; the vendored tree never
    contains the fixture folder data/ (the vendor script drops it, and this route refuses data/ anyway), so a
    business never sees the demo's made-up numbers: a key with no live source renders "unknown".
  * No tenant from the request. The tenant shown is JAMBOT_TENANT (display only); the data API takes its
    tenant from the container, never from the page.
  * Only files that ship in the image are served: no symlink is followed, no dot-segment, no path outside hub/,
    and only the listed file types.

GET /hub                 -> 308 /hub/
GET /hub/                index.html + the live-mode boot block + /hub/_ovu/hub-auth.js
GET /hub/version.json    {hub: 'jamdash', repo, commit, vendored_at, files} (the vendored pin)
GET /hub/<path>          a static file of the vendored app
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, redirect, request

logger = logging.getLogger(__name__)

hub_bp = Blueprint('hub', __name__)

HUB_DIR = Path(os.getenv('HUB_DIR') or (Path(__file__).resolve().parent.parent / 'hub'))
PIN_FILE = 'VENDORED.json'

_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript',
    '.mjs': 'application/javascript',
    '.css': 'text/css',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.webmanifest': 'application/manifest+json',
}
_SEGMENT = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}$')
_REFUSED_TOP = ('data',)          # the fixture folder: never served on a business's host


def _auth_enabled() -> bool:
    return bool(current_app.config.get('CLERK_AUTH_ENABLED'))


def _who():
    """('clerk', user_id) | ('agent', None) | ('local', None) | ('refused', reason) | (None, None)."""
    if not _auth_enabled():
        return 'local', None
    presented = request.headers.get('X-Agent-Key')
    if presented is not None:
        own = (os.getenv('AGENT_API_KEY') or '').strip()
        if own and presented == own:
            return 'agent', None
        return 'refused', 'agent_key_invalid'
    try:
        from services.auth import get_token_from_request, verify_clerk_token
        token = get_token_from_request()
        uid = verify_clerk_token(token) if token else None
    except Exception as exc:                       # noqa: BLE001 -- fail closed
        logger.warning('hub: auth check failed closed: %s', exc)
        uid = None
    return ('clerk', uid) if uid else (None, None)


def _gate():
    """None when the caller may read the Hub, else the response that refuses it."""
    kind, detail = _who()
    if kind in ('clerk', 'agent', 'local'):
        return None
    if kind == 'refused':
        return jsonify({'error': 'This agent key is not valid for this business', 'code': detail}), 403
    accept = request.headers.get('Accept', '')
    if request.method == 'GET' and 'text/html' in accept and not request.headers.get('X-Requested-With'):
        return redirect('/?redirect=/hub/', code=302)
    return jsonify({'error': 'Unauthorized', 'code': 'auth_required',
                    'accepted_credentials': ['Authorization: Bearer <Clerk session JWT>', '__session cookie (Clerk)',
                                             'X-Agent-Key: <AGENT_API_KEY> (this business only)']}), 401


def _pin() -> dict:
    try:
        return json.loads((HUB_DIR / PIN_FILE).read_text())
    except (OSError, ValueError):
        return {}


def _tenant_label() -> str:
    t = (os.getenv('JAMBOT_TENANT') or '').strip()
    return t if re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', t) else ''


def _no_store(resp):
    # no-transform: Cloudflare must not rewrite the Hub (it injects its analytics beacon into HTML otherwise, which the
    # CSP blocks: a console error on every page view, and a script on the page that this image did not ship).
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, no-transform'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return resp


def _safe_file(rel: str) -> Path | None:
    """The vendored file at rel, or None. Every segment checked; no symlink anywhere on the way."""
    parts = [p for p in rel.split('/') if p != '']
    if not parts or parts[0] in _REFUSED_TOP:
        return None
    if any(not _SEGMENT.match(p) or p in ('.', '..') for p in parts):
        return None
    if Path(parts[-1]).suffix.lower() not in _TYPES:
        return None
    cur = HUB_DIR
    for p in parts:
        cur = cur / p
        if cur.is_symlink():
            return None
    return cur if cur.is_file() else None


def _boot_block() -> str:
    pin = _pin()
    cfg = {
        'tenant': _tenant_label(),
        'commit': str(pin.get('commit') or '')[:40],
        'auth': _auth_enabled(),
        'clerkPublishableKey': (os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip()
        if _auth_enabled() else '',
    }
    data = json.dumps(cfg).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return ('<meta name="jambot-hub" content="%s">\n'
            '<script id="jambot-hub-boot">globalThis.JAMDASH_MODE=\'live\';'
            'globalThis.JAMBOT_HUB=%s;globalThis.JAMDASH_TENANT=globalThis.JAMBOT_HUB.tenant||undefined;</script>\n'
            '<script src="/hub/_ovu/hub-auth.js"></script>\n') % (cfg['commit'] or 'unpinned', data)


@hub_bp.get('/hub')
def hub_bare():
    return redirect('/hub/', code=308)


@hub_bp.get('/hub/')
def hub_index():
    refused = _gate()
    if refused is not None:
        return refused
    index = _safe_file('index.html')
    if index is None:
        return jsonify({'error': 'The Hub is not installed in this image', 'code': 'hub_not_installed'}), 404
    html = index.read_text(encoding='utf-8')
    if '<head>' not in html:
        return jsonify({'error': 'The vendored Hub index.html has no <head>', 'code': 'hub_broken'}), 500
    html = html.replace('<head>', '<head>\n' + _boot_block(), 1)
    return _no_store(Response(html, mimetype='text/html'))


@hub_bp.get('/hub/version.json')
def hub_version():
    refused = _gate()
    if refused is not None:
        return refused
    pin = _pin()
    if not pin:
        return jsonify({'error': 'The Hub is not installed in this image', 'code': 'hub_not_installed'}), 404
    return _no_store(jsonify({'hub': 'jamdash', 'repo': pin.get('repo'), 'commit': pin.get('commit'),
                              'vendored_at': pin.get('vendored_at'), 'files': pin.get('file_count'),
                              'mode': 'live', 'tenant': _tenant_label()}))


@hub_bp.get('/hub/<path:rel>')
def hub_file(rel):
    refused = _gate()
    if refused is not None:
        return refused
    if rel == PIN_FILE or rel.rstrip('/') == 'index.html':
        return redirect('/hub/', code=302) if rel.rstrip('/') == 'index.html' else (
            jsonify({'error': 'Not found', 'code': 'not_found'}), 404)
    f = _safe_file(rel)
    if f is None:
        return jsonify({'error': 'Not found', 'code': 'not_found'}), 404
    resp = Response(f.read_bytes(), mimetype=_TYPES[f.suffix.lower()])
    return _no_store(resp)
