"""
routes/intake.py — Signed one-time upload links for client file intake.

Problem this fixes (measured 2026-09-18): every /pages/*.html canvas page is
Clerk-gated by platform default. A tenant agent texted a client a link to an
intake canvas page so the client could upload a large plan set (too big for
MMS) and the client's browser got a bare `HTTP 401` — 12 bytes, no login UI,
because canvas-page auth requires a Clerk session the client will never have.
This blocked plan delivery twice.

The fix is NOT to make a page public (that makes it permanently browsable).
It's a signed, single-purpose token that gates ONE thing — upload — and
nothing else:

  1. An agent INSIDE the tenant's own container mints a token
     (POST /api/intake/mint — tenant-authenticated, gated by the app-wide
     require_auth() exactly like any other non-public API route: an
     X-Agent-Key or a Clerk session, never anonymous).
  2. The client opens `https://<tenant>.jam-bot.com/u/<token>` — no login,
     because the token itself is the credential. That page can ONLY render
     an upload form or an "expired" message; it can never list, read or
     browse anything.
  3. POST to the same path accepts exactly one file, validates it server
     side (size / content-type / traversal-safe filename), writes it to the
     tenant's existing UPLOADS_DIR immediately, and burns one use.

Security properties, and where each is enforced:
  - Entropy:  secrets.token_urlsafe(32) -> ~43 url-safe chars, ~256 bits.
  - Storage:  server-side JSON under RUNTIME_DIR (this tenant's own
    bind-mounted runtime dir — never a client-guessable path, never derived
    from anything the client supplies).
  - Scope:    the token is minted by (and only usable against) the process
    that minted it — this Flask app is one-tenant-per-container already, so
    there is no code path here that can reach another tenant's filesystem.
    The tenant identity (CLIENT_NAME) is stamped into the token record at
    mint time and re-checked at use time anyway, so a token file copied
    across containers by mistake still fails closed.
  - Upload-only: the token can only ever reach `_do_upload()` below. There
    is no read/list/browse verb on this blueprint at all.
  - No path traversal: the client's filename is kept ONLY as metadata
    (`original_filename` in the audit record). The stored filename is
    generated server-side (`uuid4().hex`), so nothing the client sends ever
    becomes a filesystem path component.
  - Expiry + use cap: both stored in the token record and enforced on every
    request — checked BEFORE the rate limiter and BEFORE touching disk.
  - Size cap: checked server-side before save via stream.seek/tell (same
    technique routes/static_files.py uses for /api/upload), independent of
    and tighter-or-equal to nginx's `client_max_body_size 100M` (see
    docs/jambot/... nginx template, verified 2026-09-18). Anything over
    100MB never reaches Flask at all — nginx returns its own 413 first.
  - Content-type allowlist: extension-based (same posture as the existing
    /api/upload blocklist) restricted to plan-ish formats.
  - Rate limiting: per-token AND per-IP sliding windows, in-process.
  - Audit: every attempt (accepted or refused) is appended to a tenant-
    scoped JSONL file, never only the successes.
  - Immediate persistence: FileStorage.save() streams straight to disk;
    nothing is ever held fully in memory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, Response

from services.paths import RUNTIME_DIR, UPLOADS_DIR
from routes.static_files import append_upload_index, _MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

intake_bp = Blueprint('intake', __name__)

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

TOKENS_PATH = RUNTIME_DIR / 'intake-tokens.json'
AUDIT_LOG_PATH = RUNTIME_DIR / 'intake-audit.jsonl'

_tokens_lock = threading.Lock()

DEFAULT_EXPIRY_HOURS = 72
DEFAULT_MAX_USES = 3

# Plans-ish formats only. Extension-based, same posture as the existing
# /api/upload blocklist (deny-by-default here instead — this endpoint is
# public-facing so we allowlist rather than blocklist).
_ALLOWED_EXTENSIONS = {
    '.pdf',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.heif', '.tiff', '.tif', '.bmp',
    '.dwg', '.dxf',
    '.zip',
}

# Rate limits — in-process sliding windows. Single-container-per-tenant, so
# this is per-tenant by construction (no cross-tenant state possible).
_RATE_WINDOW_SECONDS = 3600
_RATE_MAX_PER_TOKEN = 10
_RATE_MAX_PER_IP = 30

_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}  # key -> [timestamps]


def _rate_check(key: str, limit: int) -> bool:
    """Return True if `key` is still under `limit` hits in the current window,
    and records this hit. False means: reject, do not record twice."""
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits.get(key, []) if now - t < _RATE_WINDOW_SECONDS]
        if len(hits) >= limit:
            _rate_hits[key] = hits
            return False
        hits.append(now)
        _rate_hits[key] = hits
        return True


# ---------------------------------------------------------------------------
# Token store
# ---------------------------------------------------------------------------

def _load_tokens() -> dict:
    if not TOKENS_PATH.exists():
        return {}
    try:
        with open(TOKENS_PATH, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (json.JSONDecodeError, IOError) as exc:
        logger.error('intake: failed to load token store, treating as empty: %s', exc)
        return {}


def _save_tokens(tokens: dict) -> bool:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(TOKENS_PATH, 'w', encoding='utf-8') as fh:
            json.dump(tokens, fh, indent=2)
        return True
    except OSError as exc:
        logger.error('intake: failed to save token store: %s', exc)
        return False


def _tenant_id() -> str:
    return (os.getenv('CLIENT_NAME') or os.getenv('JAMBOT_TENANT') or os.getenv('TENANT_NAME') or 'unknown').strip()


def _append_audit(record: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(AUDIT_LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError as exc:  # noqa: BLE001 — audit failure must never break the request
        logger.warning('intake: audit append failed: %s', exc)


def _client_ip() -> str:
    # X-Forwarded-For is set by our own nginx layer; trust the first hop only.
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr or 'unknown'


# ---------------------------------------------------------------------------
# Mint — tenant-authenticated only. This route carries NO public exemption
# in app.py's require_auth(), so it is reachable only with a valid
# X-Agent-Key or an authenticated Clerk session, exactly like any other
# non-public API route in this app.
# ---------------------------------------------------------------------------

@intake_bp.route('/api/intake/mint', methods=['POST'])
def mint_intake_token():
    body = request.get_json(silent=True) or {}

    try:
        expiry_hours = float(body.get('expiry_hours', DEFAULT_EXPIRY_HOURS))
    except (TypeError, ValueError):
        return jsonify({'error': 'expiry_hours must be a number'}), 400
    if not (0 < expiry_hours <= 24 * 30):
        return jsonify({'error': 'expiry_hours must be between 0 and 720 (30 days)'}), 400

    try:
        max_uses = int(body.get('max_uses', DEFAULT_MAX_USES))
    except (TypeError, ValueError):
        return jsonify({'error': 'max_uses must be an integer'}), 400
    if not (1 <= max_uses <= 50):
        return jsonify({'error': 'max_uses must be between 1 and 50'}), 400

    label = str(body.get('label', ''))[:200]

    token = secrets.token_urlsafe(32)  # >=256 bits of entropy
    now = datetime.now(timezone.utc)
    record = {
        'token': token,
        'tenant': _tenant_id(),
        'label': label,
        'created_at': now.isoformat(),
        'expires_at': (now + timedelta(hours=expiry_hours)).isoformat(),
        'max_uses': max_uses,
        'use_count': 0,
        'disabled': False,
        'uploads': [],  # short summary list; full trail lives in the audit log
    }

    with _tokens_lock:
        tokens = _load_tokens()
        tokens[token] = record
        if not _save_tokens(tokens):
            return jsonify({'error': 'Failed to persist token'}), 500

    logger.info('intake: minted token for tenant=%s label=%r expiry_hours=%s max_uses=%s',
                record['tenant'], label, expiry_hours, max_uses)

    return jsonify({
        'token': token,
        'url_path': f'/u/{token}',
        'expires_at': record['expires_at'],
        'max_uses': max_uses,
    }), 201


# ---------------------------------------------------------------------------
# Validation shared by GET (render form) and POST (accept upload)
# ---------------------------------------------------------------------------

def _validate_token(token: str) -> tuple[dict | None, str | None]:
    """Returns (record, None) if usable, or (None, reason) if not.
    `reason` is for logs/audit only — never rendered to the client verbatim."""
    tokens = _load_tokens()
    record = tokens.get(token)
    if record is None:
        return None, 'not_found'
    if record.get('disabled'):
        return None, 'disabled'
    if record.get('tenant') != _tenant_id():
        # Defense in depth: this container can only ever mint tokens stamped
        # with its own tenant id, so this branch means the token store was
        # copied/shared across tenants by mistake. Fail closed either way.
        return None, 'tenant_mismatch'
    try:
        expires_at = datetime.fromisoformat(record['expires_at'])
    except (KeyError, ValueError):
        return None, 'bad_expiry'
    if datetime.now(timezone.utc) >= expires_at:
        return None, 'expired'
    if record.get('use_count', 0) >= record.get('max_uses', 0):
        return None, 'exhausted'
    return record, None


_EXPIRED_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Link expired</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;text-align:center}}
.card{{max-width:420px}}h1{{font-size:1.3rem;margin-bottom:.5rem}}p{{color:#94a3b8;line-height:1.5}}</style>
</head><body><div class="card"><h1>This link has expired</h1>
<p>This upload link is no longer active. Please text your contact and ask for a new link.</p></div>
</body></html>"""

_FORM_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Upload your file</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px}}
.card{{max-width:420px;width:100%;background:#1e293b;border-radius:12px;padding:24px}}
h1{{font-size:1.2rem;margin:0 0 4px}}p{{color:#94a3b8;font-size:.9rem;margin:0 0 16px}}
input[type=file]{{width:100%;margin-bottom:16px;color:#e2e8f0}}
button{{width:100%;padding:12px;border:0;border-radius:8px;background:#3b82f6;color:#fff;
font-size:1rem;font-weight:600}}
#msg{{margin-top:14px;font-size:.9rem}}</style></head>
<body><div class="card">
<h1>Upload your file</h1>
<p>{label_html}Accepted: PDF, images, DWG/DXF, ZIP. Up to 100MB.</p>
<form id="f"><input type="file" name="file" id="file" required><button type="submit">Upload</button></form>
<div id="msg"></div>
</div>
<script>
document.getElementById('f').addEventListener('submit', async function(e) {{
  e.preventDefault();
  var msg = document.getElementById('msg');
  var f = document.getElementById('file').files[0];
  if (!f) return;
  msg.textContent = 'Uploading...';
  var fd = new FormData();
  fd.append('file', f);
  try {{
    var resp = await fetch(window.location.pathname, {{ method: 'POST', body: fd }});
    var data = await resp.json().catch(function() {{ return {{}}; }});
    if (resp.ok) {{
      msg.style.color = '#4ade80';
      msg.textContent = 'Uploaded. Thank you!';
    }} else {{
      msg.style.color = '#f87171';
      msg.textContent = data.error || 'Upload failed. Please try again.';
    }}
  }} catch (err) {{
    msg.style.color = '#f87171';
    msg.textContent = 'Upload failed. Please try again.';
  }}
}});
</script>
</body></html>"""


@intake_bp.route('/u/<token>', methods=['GET'])
def intake_form(token: str):
    record, reason = _validate_token(token)
    if record is None:
        _append_audit({
            'at': datetime.now(timezone.utc).isoformat(),
            'token_suffix': token[-8:] if token else '',
            'ip': _client_ip(),
            'action': 'view',
            'accepted': False,
            'reason': reason,
        })
        return Response(_EXPIRED_PAGE, status=410, mimetype='text/html')

    label = record.get('label') or ''
    label_html = f'<strong>{_escape(label)}</strong><br>' if label else ''
    _append_audit({
        'at': datetime.now(timezone.utc).isoformat(),
        'token_suffix': token[-8:],
        'ip': _client_ip(),
        'action': 'view',
        'accepted': True,
    })
    return Response(_FORM_PAGE.format(label_html=label_html), status=200, mimetype='text/html')


def _escape(s: str) -> str:
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&#39;'))


@intake_bp.route('/u/<token>', methods=['POST'])
def intake_upload(token: str):
    ip = _client_ip()

    # 1. Token validity FIRST — before rate limiting or touching disk, so an
    #    expired/exhausted token never even burns a rate-limit slot from a
    #    legitimate client hammering refresh, and a bad actor probing dead
    #    tokens can't use this endpoint as a rate-limit-free oracle either
    #    (it still gets logged either way).
    record, reason = _validate_token(token)
    if record is None:
        _append_audit({
            'at': datetime.now(timezone.utc).isoformat(),
            'token_suffix': token[-8:] if token else '',
            'ip': ip,
            'action': 'upload',
            'accepted': False,
            'reason': reason,
        })
        return jsonify({'error': 'This link has expired. Please text your contact for a new link.'}), 410

    def _refuse(status: int, reason_code: str, message: str):
        _append_audit({
            'at': datetime.now(timezone.utc).isoformat(),
            'token_suffix': token[-8:],
            'ip': ip,
            'action': 'upload',
            'accepted': False,
            'reason': reason_code,
        })
        return jsonify({'error': message}), status

    # 2. Rate limiting — per token and per IP.
    if not _rate_check(f'token:{token}', _RATE_MAX_PER_TOKEN):
        return _refuse(429, 'rate_limited_token', 'Too many attempts. Please wait and try again.')
    if not _rate_check(f'ip:{ip}', _RATE_MAX_PER_IP):
        return _refuse(429, 'rate_limited_ip', 'Too many attempts. Please wait and try again.')

    # 3. File presence.
    if 'file' not in request.files:
        return _refuse(400, 'no_file', 'No file provided.')
    f = request.files['file']
    if not f.filename:
        return _refuse(400, 'no_filename', 'No file provided.')

    # 4. Extension allowlist (client-supplied name is metadata ONLY — never
    #    used to build a filesystem path; see step 6).
    original_name = Path(f.filename).name
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return _refuse(415, 'bad_extension', f'File type "{ext or "unknown"}" is not accepted here.')

    # 5. Size cap — same technique as /api/upload: seek to end, check, seek
    #    back, all before any disk write. Independent of nginx's own
    #    client_max_body_size 100M (anything bigger never reaches Flask).
    f.stream.seek(0, 2)
    file_size = f.stream.tell()
    f.stream.seek(0)
    max_bytes = min(record.get('max_bytes', _MAX_UPLOAD_BYTES), _MAX_UPLOAD_BYTES)
    if file_size == 0:
        return _refuse(400, 'empty_file', 'File is empty.')
    if file_size > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        limit_label = f'{max_mb:.0f} MB' if max_mb >= 1 else f'{max_bytes} bytes'
        return _refuse(413, 'too_large', f'File too large ({limit_label} max).')

    # 6. Save with a server-generated filename. Nothing from the client's
    #    filename reaches the filesystem as a path component — traversal
    #    strings like "../../etc/passwd" or embedded path separators are
    #    moot because we never join them into a path at all.
    stored_name = f"intake_{uuid.uuid4().hex}{ext}"
    dest = UPLOADS_DIR / stored_name
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        f.save(str(dest))  # streams to disk immediately — never held fully in memory
    except OSError as exc:
        logger.error('intake: save failed for token_suffix=%s: %s', token[-8:], exc)
        return _refuse(500, 'save_failed', 'Upload failed. Please try again.')

    # 7. Burn one use, atomically w.r.t. this process.
    with _tokens_lock:
        tokens = _load_tokens()
        live = tokens.get(token)
        if live is None or live.get('use_count', 0) >= live.get('max_uses', 0):
            # Token was exhausted/removed by a racing request between step 1
            # and here. The file is already saved (never discard received
            # content), but don't credit a use that no longer exists.
            logger.warning('intake: token exhausted mid-upload race, keeping saved file %s', stored_name)
        else:
            live['use_count'] = live.get('use_count', 0) + 1
            live.setdefault('uploads', []).append({
                'stored_name': stored_name,
                'original_filename': original_name,
                'size': file_size,
                'at': datetime.now(timezone.utc).isoformat(),
            })
            tokens[token] = live
            _save_tokens(tokens)

    mime = f.mimetype or ''
    append_upload_index({
        'filename': stored_name,
        'original_filename': original_name,
        'size': file_size,
        'mime': mime,
        'source': 'intake-link',
        'token_suffix': token[-8:],
        'ip': ip,
        'uploaded_at': datetime.now(timezone.utc).isoformat(),
    })

    _append_audit({
        'at': datetime.now(timezone.utc).isoformat(),
        'token_suffix': token[-8:],
        'ip': ip,
        'action': 'upload',
        'accepted': True,
        'stored_name': stored_name,
        'original_filename': original_name,
        'size': file_size,
    })

    logger.info('intake: upload accepted token_suffix=%s stored=%s size=%d', token[-8:], stored_name, file_size)
    return jsonify({'ok': True, 'filename': original_name}), 200
