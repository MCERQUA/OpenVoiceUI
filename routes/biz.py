"""routes/biz.py — the JamBot client backend API (/api/biz/v1) and the page SDK (/jb/v1/jb.js).

Every client page, dashboard or company app reads and writes business data through
this one API (Rule Book mikes-decisions/client-apps-use-the-common-backend). The
store is services/biz/store.py: one business.db per tenant, in the tenant's own db
dir. The tenant and the db dir come from the container env ONLY; no route takes a
tenant or a path, and a request that names a tenant is refused.

Auth (in addition to app.py require_auth):
  owner  Clerk session (__session cookie or Bearer)        by = clerk:<user id>
  agent  this tenant's X-Agent-Key (AGENT_API_KEY)          by = agent:<tenant>
         agent writes must carry source = {channel, ref, confidence, words}
  A presented but wrong X-Agent-Key is 403, never a fall-through.
  staff surface (host ruling H4): where the risk-transfer pack is on, a staff session changes the
         records its verdict reads (crt_*, contact, document, company, /crt/...) ONLY with the
         per-render token of a platform staff page (X-JB-Staff-Surface): the page is served from the
         image by GET /api/biz/v1/crt/staff/<page>, never from canvas-pages, which the agent can write.

GET    /api/biz/v1/health
GET    /api/biz/v1/schema                  merged registry (core + library adoptions + tenant overlay)
GET    /api/biz/v1/schema/<collection>
POST   /api/biz/v1/schema/custom           additive only; new fields filed as library candidates
POST   /api/biz/v1/schema/adopt            {collection, key} adopt a shared-library field
GET    /api/biz/v1/schema/proposals        this tenant's candidate field definitions
GET    /api/biz/v1/fields/library          the shared field library (definitions only)
GET    /api/biz/v1/<collection>            ?where=<json>&sort=-field&limit=&cursor=&include=a,b&archived=1
POST   /api/biz/v1/<collection>            {data, source}
GET    /api/biz/v1/<collection>/<id>
PATCH  /api/biz/v1/<collection>/<id>       If-Match: <version>; {data, clear, source}
GET    /api/biz/v1/<collection>/<id>/timeline
POST   /api/biz/v1/<collection>/<id>/archive | /unarchive
POST   /api/biz/v1/counters/<name>/next    {prefix, pad} (used on first call only)
GET    /api/biz/v1/events?since=<id>       change feed (poll)
GET    /api/biz/v1/export                  whole tenant as JSON (owner only)
POST   /api/biz/v1/files                   multipart "file" (+ "source" JSON, "name"): backend-private store
GET    /api/biz/v1/files/<id>              the bytes, as an attachment (a person's session only)
GET    /api/biz/v1/files/<id>/meta         {id, sha256, url: biz-file:<id>, content_type, size, name}
GET    /api/biz/v1/packs                   the packs this engine ships + the ones this business turned on
POST   /api/biz/v1/packs/<name>/enable     turn a pack on (additive, permanent; agent writes need a source)
       /api/biz/v1/<prefix>/...            a turned-on pack's own routes (contract: services/biz/packs)

Company system (phase 2, design section 9):
GET    /api/biz/v1/registry/pages          ?archived=1   the pages of this company system
POST   /api/biz/v1/registry/pages          {file, label, section, ...} -> 201 (+ first_of_system)
GET    /api/biz/v1/registry/pages/<id>
PATCH  /api/biz/v1/registry/pages/<id>     If-Match: <version>
POST   /api/biz/v1/registry/pages/<id>/archive | /unarchive
GET    /api/biz/v1/registry/shell          ?page=<id>   what the menu shell draws (sections, tabs, theme)
GET    /api/biz/v1/registry/resolve        ?collection=&id= | &job_number=   the deep link that opens a record
GET    /api/biz/v1/<collection>/aggregate  ?where=&sum=a,b&group_by=f&flagged=1   server-side count / sums
POST   /api/biz/v1/<collection>/<id>/confirm {fields:[..], note}   a person clears "needs check" flags (owner only)

Dev sandboxes (only where BIZ_SANDBOXES is set; see services/biz/sandbox.py):
GET    /api/biz/v1/dev/pages               the sandbox's page files
PUT    /api/biz/v1/dev/pages/<file>        {html} publish a page into the sandbox (sandbox key only)
GET    /api/biz/v1/dev/pages/<file>        the file + the shell tags the server would inject

Static, served from the image (public assets; the data behind them is gated):
GET    /jb/v1/jb.js  /jb/v1/shell.js  /jb/v1/shell.css  /jb/v1/sdk.md (also /jb/v1/)
"""
from __future__ import annotations

import hashlib
import hmac
import html as _html
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, current_app, g, jsonify, redirect, request, send_file

from services.biz import sandbox as _sandbox
from services.biz.pages import FILE_RE, RECORD_PARAM, build_theme, check_file, page_for_record, shell_model
from services.biz.schema import SchemaError
from services.biz.store import FILE_ID_RE, FILE_MAX_BYTES, BizStore, resolve_location
from services.paths import JB_SDK_DIR

logger = logging.getLogger(__name__)

API = '/api/biz/v1'
MAX_BODY_BYTES = 1024 * 1024
_RETRY_OPEN_AFTER_S = 60

biz_bp = Blueprint('biz', __name__)
jb_sdk_bp = Blueprint('jb_sdk', __name__)
_open_lock = threading.Lock()


def _hosted() -> bool:
    return bool(current_app.config.get('CLERK_AUTH_ENABLED')) or \
        os.getenv('CANVAS_REQUIRE_AUTH', '').strip().lower() == 'true'


def _open_main():
    tenant, db_dir = resolve_location(hosted=_hosted())
    return BizStore(db_dir, tenant)


def _open_sandbox(name):
    tenant, db_dir = resolve_location(hosted=_hosted())
    return BizStore(Path(db_dir) / 'sandboxes' / name, _sandbox.tenant_id(tenant, name))


def get_store(sandbox: str | None = None) -> BizStore:
    """The tenant's store (or one dev sandbox's), opened once per app.

    A refusal is cached for a minute, then retried.
    """
    ext = current_app.extensions.setdefault('biz', {})
    slot = 'store' if sandbox is None else 'sandbox:' + sandbox
    store = ext.get(slot)
    if store is not None:
        return store
    with _open_lock:
        store = ext.get(slot)
        if store is not None:
            return store
        failed = ext.get('open_error:' + slot)
        if failed and time.monotonic() - failed[0] < _RETRY_OPEN_AFTER_S:
            raise failed[1]
        try:
            store = _open_main() if sandbox is None else _open_sandbox(sandbox)
        except SchemaError as exc:
            ext['open_error:' + slot] = (time.monotonic(), exc)
            logger.error('biz: store %s refused to open: %s (%s)', slot, exc.code, exc.message)
            raise
        ext.pop('open_error:' + slot, None)
        ext[slot] = store
        return store


def _canvas_dir() -> Path:
    import routes.canvas as canvas          # the same (patchable) dir the page server reads
    return Path(canvas.CANVAS_PAGES_DIR)


def _pages_dir(sandbox: str | None) -> Path:
    base = _canvas_dir()
    return base if sandbox is None else base / 'sandbox' / sandbox


def _fail(status, code, message, **extra):
    return jsonify({'error': message, 'code': code, **extra}), status


@biz_bp.errorhandler(SchemaError)
def _schema_error(exc: SchemaError):
    return jsonify(exc.to_dict()), exc.status


# ── the platform staff surface (host ruling H4, 2026-11-17) ──────────────────
# A page the tenant's agent can write (canvas-pages) runs in the staff browser with the staff session.
# So a staff session alone never makes a person-write where a verdict reads person-writes: the request
# must also carry the token a PLATFORM staff page (served from this image, read-only to the agent) got
# when it was rendered. The token is minted only for a top-level navigation (never for fetch, an iframe
# or an embed), the page is not frameable and opens in its own browsing-context group (COOP), so a
# script on another same-origin page can neither fetch it nor reach into it to read the token.
STAFF_SURFACE_HEADER = 'X-JB-Staff-Surface'
STAFF_SURFACE_TTL_S = 8 * 3600
SURFACE_GUARDED_CORE = ('contact', 'document', 'company')
SURFACE_PACK = 'risk-transfer'
_SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')
# Per process: a restart (or a second worker) only means "reload the staff page" -- it fails closed.
_SURFACE_SECRET = secrets.token_bytes(32)


def _surface_mac(by, sandbox, exp, rnd) -> str:
    msg = f'v1|{by}|{sandbox or ""}|{exp}|{rnd}'.encode()
    return hmac.new(_SURFACE_SECRET, msg, hashlib.sha256).hexdigest()


def mint_staff_surface(by: str, sandbox: str | None) -> str:
    exp = int(time.time()) + STAFF_SURFACE_TTL_S
    rnd = secrets.token_hex(12)
    return f'v1.{exp}.{rnd}.{_surface_mac(by, sandbox, exp, rnd)}'


def staff_surface_token_ok(token, by, sandbox) -> bool:
    """The token was minted by this process for THIS staff session (user) and THIS sandbox, and is unexpired."""
    try:
        v, exp, rnd, mac = str(token).split('.')
        exp = int(exp)
    except (ValueError, TypeError):
        return False
    if v != 'v1' or exp < time.time() or not re.fullmatch(r'[0-9a-f]{24}', rnd):
        return False
    return hmac.compare_digest(mac, _surface_mac(by, sandbox, exp, rnd))


def _surface_refusal(token):
    if not staff_surface_token_ok(token, g.biz_by, g.biz_sandbox):
        return 'This staff-page token is not valid for this session; reload the staff page'
    site = request.headers.get('Sec-Fetch-Site')
    if site is not None and site != 'same-origin':
        return 'Staff changes come only from this site\'s own staff pages (Sec-Fetch-Site)'
    origin = request.headers.get('Origin')
    if origin and origin != 'null' and urlparse(origin).netloc.lower() != (request.host or '').lower():
        return 'Staff changes come only from this site\'s own staff pages (Origin)'
    if origin == 'null':
        return 'Staff changes come only from this site\'s own staff pages (opaque origin)'
    return None


def _surface_guarded(store) -> bool:
    """A write whose author the risk-transfer verdict reads (its records, or the contacts, documents and
    company it names), in a business with that pack on."""
    rel = request.path[len(API):].lstrip('/')
    first = rel.split('/', 1)[0]
    if not (first == 'crt' or first.startswith('crt_') or first in SURFACE_GUARDED_CORE):
        return False
    try:
        return store.pack_enabled(SURFACE_PACK)
    except Exception:
        return True                     # cannot tell: refuse (fail closed)


def platform_page_names() -> set:
    """Page file names a pack ships in the image: the platform serves these, an agent never writes them."""
    from services.biz.schema import PACKS_DIR
    return {p.name for p in Path(PACKS_DIR).glob('*/pages/*.html')}


def staff_surface_page(pages_dir: Path, page: str):
    if not FILE_RE.match(page or '') or not (Path(pages_dir) / page).is_file():
        raise SchemaError(404, 'not_found', 'No such staff page')
    if request.headers.get('Sec-Fetch-Mode') != 'navigate' or request.headers.get('Sec-Fetch-Dest') != 'document':
        raise SchemaError(403, 'staff_surface_navigation_only',
                          'Staff pages open only as a top-level page in the browser; they are never fetched, '
                          'framed or embedded by another page')
    token = mint_staff_surface(g.biz_by, g.biz_sandbox)
    nonce = secrets.token_urlsafe(18)
    html = (Path(pages_dir) / page).read_text(encoding='utf-8')
    html = re.sub(r'<script(?=[\s>])', f'<script nonce="{nonce}"', html)
    boot = json.dumps({'token': token, 'sandbox': g.biz_sandbox, 'base': f'{API}/crt/staff/'}).replace('</', '<\\/')
    at = html.find('<script')
    tag = f'<script nonce="{nonce}">window.__JB_SURFACE__ = {boot};</script>\n'
    html = html[:at] + tag + html[at:] if at >= 0 else html + tag
    resp = Response(html, mimetype='text/html')
    resp.headers['Content-Security-Policy'] = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    resp.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@biz_bp.after_request
def _no_store(resp):
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['CDN-Cache-Control'] = 'no-store'
    return resp


@biz_bp.before_request
def _authenticate():
    if not request.path.startswith(API + '/') and request.path != API:
        return None
    # The tenant is fixed by the container. A request that tries to name one is refused outright.
    if 'tenant' in request.args or request.headers.get('X-Tenant') or request.headers.get('X-Jambot-Tenant'):
        return _fail(400, 'tenant_param_refused',
                     'The business backend serves only this container\'s tenant; it never takes a tenant parameter')
    is_upload = request.method == 'POST' and request.path == API + '/files'
    cap = FILE_MAX_BYTES + 64 * 1024 if is_upload else MAX_BODY_BYTES
    if request.content_length and request.content_length > cap:
        return _fail(413, 'body_too_large', 'Request body is larger than '
                     + ('the file store limit' if is_upload else '1 MB'))

    presented = request.headers.get('X-Agent-Key')
    expected = os.getenv('AGENT_API_KEY', '').strip()
    asked_sandbox = (request.headers.get('X-Biz-Sandbox') or '').strip() or None
    if asked_sandbox is None and request.method == 'GET' and request.path.startswith(API + '/crt/staff/'):
        asked_sandbox = (request.args.get('sandbox') or '').strip() or None   # a navigation sends no headers
    sandboxes = _sandbox.configured()
    g.biz_sandbox = None
    if presented is not None:
        sbx = _sandbox.for_key(presented)
        if expected and hmac.compare_digest(presented.encode(), expected.encode()):
            g.biz_role = 'agent'
            if asked_sandbox:
                return _fail(400, 'tenant_param_refused',
                             'This agent key serves its own business only; it never takes a sandbox')
        elif sbx:
            g.biz_role = 'agent'
            g.biz_sandbox = sbx
            if asked_sandbox and asked_sandbox != sbx:
                logger.warning('biz: sandbox key for %s asked for sandbox %s (refused)', sbx, asked_sandbox[:40])
                return _fail(403, 'sandbox_refused', 'This key belongs to one sandbox and cannot reach another')
        else:
            logger.warning('biz: wrong X-Agent-Key refused (path=%s)', request.path)
            return _fail(403, 'agent_key_invalid', 'This agent key is not valid for this business')
    elif current_app.config.get('CLERK_AUTH_ENABLED'):
        uid = getattr(g, 'clerk_user_id', None)
        if not uid:
            from services import auth
            token = auth.get_token_from_request()
            uid = auth.verify_clerk_token(token) if token else None
        if not uid:
            return _fail(401, 'auth_required', 'Unauthorized')
        g.biz_role = 'owner'
        g.biz_by = f'clerk:{uid}'
    else:
        if os.getenv('CANVAS_REQUIRE_AUTH', '').strip().lower() == 'true':
            return _fail(401, 'auth_required', 'Unauthorized')
        g.biz_role = 'owner'          # single-user local install (no Clerk configured)
        g.biz_by = 'local:owner'

    if g.biz_role == 'owner' and asked_sandbox:
        # The dev server's own owner may open a sandbox to look at it (jb.js sends this header
        # on /pages/sandbox/<name>/ pages). Anywhere sandboxes are off, naming one is refused
        # exactly like naming a tenant.
        if asked_sandbox not in sandboxes:
            return _fail(400, 'tenant_param_refused',
                         'The business backend serves only this container\'s business; it never takes a sandbox')
        g.biz_sandbox = asked_sandbox

    try:
        store = get_store(g.biz_sandbox)
    except SchemaError as exc:
        return jsonify(exc.to_dict()), exc.status
    if g.biz_role == 'agent':
        g.biz_by = f'agent:{store.tenant}'
    g.biz_store = store
    g.biz_surface = False
    if g.biz_role == 'owner':
        token = request.headers.get(STAFF_SURFACE_HEADER)
        if token is not None:
            why = _surface_refusal(token)
            if why:
                logger.warning('biz: staff-surface token refused (path=%s): %s', request.path, why)
                return _fail(403, 'staff_surface_invalid', why)
            g.biz_surface = True
        if request.method not in _SAFE_METHODS and not g.biz_surface and _surface_guarded(store):
            return _fail(403, 'staff_surface_required',
                         'This business runs the risk-transfer pack: staff change its records only from the '
                         f'platform staff pages ({API}/crt/staff/...), never from a canvas page (host ruling H4)')
    store.maybe_daily_backup()
    if is_upload:
        if 'tenant' in request.form:
            return _fail(400, 'tenant_param_refused',
                         'The business backend serves only this container\'s tenant; never send a tenant')
    elif request.method in ('POST', 'PATCH', 'PUT'):
        body = request.get_json(silent=True)
        if request.content_length and body is None:
            return _fail(400, 'bad_json', 'Body must be JSON')
        if isinstance(body, dict) and 'tenant' in body:
            return _fail(400, 'tenant_param_refused',
                         'The business backend serves only this container\'s tenant; never send a tenant')
    return None


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _agent() -> bool:
    return g.biz_role == 'agent'


def _w() -> dict:
    return {'by': g.biz_by, 'agent': _agent()}


# ── health + schema + library ───────────────────────────────────────────────
@biz_bp.get(f'{API}/health')
def health():
    out = g.biz_store.health()
    if g.biz_sandbox:
        out['sandbox'] = g.biz_sandbox
    return jsonify(out)


@biz_bp.get(f'{API}/schema')
def schema_all():
    reg = g.biz_store.registry
    return jsonify({'collections': reg.public(), 'library_version': reg.library.get('library_version')})


@biz_bp.get(f'{API}/schema/proposals')
def schema_proposals():
    return jsonify({'proposals': g.biz_store.proposals()})


@biz_bp.get(f'{API}/schema/<collection>')
def schema_one(collection):
    return jsonify(g.biz_store.registry.public(collection))


@biz_bp.post(f'{API}/schema/custom')
def schema_custom():
    body = _body()
    spec, proposals = g.biz_store.add_custom_schema(
        {k: v for k, v in body.items() if k != 'source'}, source=body.get('source'), **_w())
    return jsonify({'collection': spec, 'proposals': proposals}), 200


@biz_bp.post(f'{API}/schema/adopt')
def schema_adopt():
    body = _body()
    spec = g.biz_store.adopt_library_field(body.get('collection'), body.get('key'),
                                           source=body.get('source'), **_w())
    return jsonify({'collection': spec})


@biz_bp.get(f'{API}/fields/library')
def fields_library():
    lib = g.biz_store.registry.library
    trade = request.args.get('trade')
    fields = {k: v for k, v in lib['fields'].items() if not trade or v.get('trade') == trade}
    return jsonify({'library_version': lib.get('library_version'), 'sources': lib.get('sources'),
                    'fields': fields})


# ── change feed, counters, export ──────────────────────────────────────────
@biz_bp.get(f'{API}/events')
def events():
    return jsonify(g.biz_store.events(request.args.get('since', 0), request.args.get('limit', 200)))


@biz_bp.post(f'{API}/counters/<name>/next')
def counter_next(name):
    body = _body()
    return jsonify(g.biz_store.counter_next(name, prefix=body.get('prefix', ''), pad=body.get('pad', 0),
                                            by=g.biz_by))


@biz_bp.get(f'{API}/export')
def export():
    if _agent():
        return _fail(403, 'owner_only', 'Export is for the business owner only')
    return jsonify(g.biz_store.export())


# ── backend-private file store (design amendment 2026-11-17) ──────────────
@biz_bp.post(f'{API}/files')
def file_upload():
    up = request.files.get('file')
    if up is None:
        raise SchemaError(422, 'file_empty', 'Send the file as multipart form field "file"')
    raw_source = request.form.get('source')
    source = None
    if raw_source:
        try:
            source = json.loads(raw_source)
        except ValueError:
            raise SchemaError(422, 'bad_source', '"source" must be JSON text')
    blob = up.read(FILE_MAX_BYTES + 1)
    meta = g.biz_store.put_file(blob, name=request.form.get('name') or up.filename, source=source, **_w())
    return jsonify(meta), 200 if meta['existed'] else 201


def _owner_file(fid):
    if _agent():
        raise SchemaError(403, 'owner_only', 'Stored files are opened by a person\'s session only')
    if not FILE_ID_RE.match(fid or ''):
        raise SchemaError(404, 'not_found', 'No such file')


@biz_bp.get(f'{API}/files/<fid>')
def file_get(fid):
    _owner_file(fid)
    meta, path = g.biz_store.file_open(fid)
    ext = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg'}.get(meta['content_type'], '')
    resp = send_file(path, mimetype=meta['content_type'], as_attachment=True,
                     download_name=fid[:16] + ext, max_age=0, conditional=False, etag=False)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return resp


@biz_bp.get(f'{API}/files/<fid>/meta')
def file_get_meta(fid):
    if not FILE_ID_RE.match(fid or ''):
        raise SchemaError(404, 'not_found', 'No such file')
    return jsonify(g.biz_store.file_meta(fid))


# ── packs (design section 6; contract in services/biz/packs/__init__.py) ──────
@biz_bp.get(f'{API}/packs')
def packs_list():
    reg = g.biz_store.registry
    return jsonify({'enabled': list(reg.enabled_packs), 'routes_loaded': list(PACK_ROUTES), 'available': [
        {'name': p['name'], 'prefix': p['prefix'], 'version': p['version'], 'label': p['label'],
         'description': p['description'], 'collections': sorted(p['collections']),
         'extends': sorted(p['extends'])} for p in reg.available_packs.values()]})


@biz_bp.post(f'{API}/packs/<name>/enable')
def packs_enable(name):
    return jsonify(g.biz_store.enable_pack(name, source=_body().get('source'), **_w()))


class PackContext:
    """What a pack's routes may use (and nothing else): the caller's store, role and body."""
    api = API
    SchemaError = SchemaError

    @staticmethod
    def store():
        return g.biz_store

    @staticmethod
    def is_agent() -> bool:
        return _agent()

    @staticmethod
    def by() -> str:
        return g.biz_by

    @staticmethod
    def body() -> dict:
        return _body()

    @staticmethod
    def require_pack(name):
        if not g.biz_store.pack_enabled(name):
            raise SchemaError(404, 'pack_not_enabled', f'The {name} pack is not turned on for this business')

    @staticmethod
    def require_person(what='do this'):
        if _agent():
            raise SchemaError(403, 'staff_only', f'Only a person (staff session) may {what}; an agent key cannot')

    @staticmethod
    def require_staff_surface():
        if _agent() or not getattr(g, 'biz_surface', False):
            raise SchemaError(403, 'staff_surface_required', 'Only the platform staff pages make this change '
                                                             '(host ruling H4)')

    @staticmethod
    def staff_surface_page(pages_dir, page):
        return staff_surface_page(pages_dir, page)

    @staticmethod
    def core_names():
        from services.biz.schema import CORE_DIR, _load_json_dir
        return _load_json_dir(CORE_DIR).keys()


# ── company system: page registry + shell model ───────────────────────────
@biz_bp.get(f'{API}/registry/pages')
def registry_list():
    pages = g.biz_store.pages_list(archived=request.args.get('archived') == '1')
    pdir = _pages_dir(g.biz_sandbox)
    for p in pages:
        p['exists'] = (pdir / p['file']).is_file()
    return jsonify({'pages': pages, 'count': len(pages)})


@biz_bp.post(f'{API}/registry/pages')
def registry_register():
    body = _body()
    page = g.biz_store.page_register({k: v for k, v in body.items() if k != 'source'},
                                     source=body.get('source'), pages_dir=_pages_dir(g.biz_sandbox), **_w())
    page['url'] = _page_url(page['file'])
    return jsonify(page), 201


@biz_bp.get(f'{API}/registry/pages/<pid>')
def registry_get(pid):
    page = g.biz_store.page_get(pid)
    page['exists'] = (_pages_dir(g.biz_sandbox) / page['file']).is_file()
    return jsonify(page)


@biz_bp.patch(f'{API}/registry/pages/<pid>')
def registry_patch(pid):
    body = _body()
    return jsonify(g.biz_store.page_update(pid, {k: v for k, v in body.items() if k != 'source'},
                                           expected_version=_if_match(), source=body.get('source'), **_w()))


@biz_bp.post(f'{API}/registry/pages/<pid>/archive')
def registry_archive(pid):
    return jsonify(g.biz_store.page_set_archived(pid, True, source=_body().get('source'), **_w()))


@biz_bp.post(f'{API}/registry/pages/<pid>/unarchive')
def registry_unarchive(pid):
    return jsonify(g.biz_store.page_set_archived(pid, False, source=_body().get('source'), **_w()))


def _style_tokens(page_id=None) -> dict:
    """The page's own canvas style, else the tenant's active style (tokens only)."""
    try:
        from services import canvas_styles
        style_id = None
        if page_id and not g.biz_sandbox:
            from routes.canvas import load_canvas_manifest
            style_id = (load_canvas_manifest().get('pages', {}).get(page_id) or {}).get('style')
        style_id = style_id or canvas_styles.get_active_style_id()
        style = canvas_styles.get_style(style_id) if style_id else None
        return dict((style or {}).get('tokens') or {})
    except Exception:
        logger.debug('biz: no style tokens for the shell', exc_info=True)
        return {}


@biz_bp.get(f'{API}/registry/shell')
def registry_shell():
    store = g.biz_store
    page_id = request.args.get('page')
    if page_id is not None and not FILE_RE.match(page_id + '.html'):
        page_id = None
    pdir = _pages_dir(g.biz_sandbox)
    brand = store.company_brand()
    model = shell_model(store.pages_list(), company=brand, theme=build_theme(_style_tokens(page_id), brand),
                        page_exists=lambda f: (pdir / f).is_file())
    model['base'] = _page_url('')
    model['page'] = page_id
    if g.biz_sandbox:
        model['sandbox'] = g.biz_sandbox
    return jsonify(model)


@biz_bp.get(f'{API}/registry/resolve')
def registry_resolve():
    """Deep link for a record: /pages/<file>?record=<collection>:<id> on the page that opens it."""
    store = g.biz_store
    collection = request.args.get('collection') or ''
    store.registry.require(collection)
    rid = request.args.get('id')
    if not rid and request.args.get('job_number') and collection == 'job':
        hits = store.list('job', where={'job_number': request.args['job_number']}, limit=2)['records']
        if not hits:
            raise SchemaError(404, 'not_found', 'No job with that number')
        rid = hits[0]['id']
    if not rid:
        raise SchemaError(400, 'bad_query', 'Send id=<record id> (or job_number= for a job)')
    rec = store.get(collection, rid)
    page = page_for_record(store.pages_list(), collection)
    if page is None:
        raise SchemaError(404, 'no_page_for_collection',
                          f'No registered page lists "{collection}" in its collections yet', record_id=rec['id'])
    return jsonify({'url': f"{_page_url(page['file'])}?{RECORD_PARAM}={collection}:{rec['id']}",
                    'page_id': page['id'], 'collection': collection, 'record_id': rec['id']})


def _page_url(file_name: str) -> str:
    return '/pages/' + (f'sandbox/{g.biz_sandbox}/' if g.biz_sandbox else '') + file_name


# ── dev sandboxes: page publish (sandbox keys only) ──────────────────────────
def _sandbox_only():
    if not g.biz_sandbox:
        raise SchemaError(404, 'not_a_sandbox',
                          'Dev page publishing exists only for sandbox keys on the dev server. A business\'s own '
                          'agent writes pages the normal way (canvas pages) and registers them here.')
    if g.biz_role != 'agent':
        raise SchemaError(403, 'sandbox_key_required', 'Only the sandbox\'s own key publishes its dev pages')


@biz_bp.get(f'{API}/dev/pages')
def dev_pages_list():
    if not g.biz_sandbox:
        raise SchemaError(404, 'not_a_sandbox', 'This is not a dev sandbox')
    pdir = _pages_dir(g.biz_sandbox)
    registered = {p['file'] for p in g.biz_store.pages_list()}
    files = sorted(f.name for f in pdir.glob('*.html')) if pdir.is_dir() else []
    return jsonify({'sandbox': g.biz_sandbox, 'pages': [
        {'file': f, 'url': _page_url(f), 'registered': f in registered} for f in files]})


@biz_bp.put(f'{API}/dev/pages/<file_name>')
def dev_page_put(file_name):
    _sandbox_only()
    check_file(file_name)
    if file_name in platform_page_names():
        raise SchemaError(409, 'platform_page_reserved',
                          'This page name belongs to a platform staff page, served from the image at '
                          f'{API}/crt/staff/<page>; publish your own view under another name (host ruling H4)',
                          file=file_name)
    body = _body()
    page_html = body.get('html')
    if not isinstance(page_html, str) or '<' not in page_html:
        raise SchemaError(422, 'validation_failed', 'html must be the page\'s full HTML text', field='html')
    g.biz_store.check_source(body.get('source'), agent=True)
    pdir = _pages_dir(g.biz_sandbox)
    pdir.mkdir(parents=True, exist_ok=True)
    dest = pdir / file_name
    kept = None
    if dest.is_file():
        hist = pdir / '.history'
        hist.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        kept = hist / f'{file_name}.{stamp}'
        shutil.copy2(dest, kept)          # the previous version is kept, never removed
    tmp = dest.with_name('.' + file_name + '.partial')
    tmp.write_text(page_html, encoding='utf-8')
    os.replace(tmp, dest)
    return jsonify({'file': file_name, 'url': _page_url(file_name), 'bytes': len(page_html.encode('utf-8')),
                    'previous_kept_as': kept.name if kept else None,
                    'next': 'Register it: POST /api/biz/v1/registry/pages {file, label, section, ...}'}), 200


@biz_bp.get(f'{API}/dev/pages/<file_name>')
def dev_page_get(file_name):
    if not g.biz_sandbox:
        raise SchemaError(404, 'not_a_sandbox', 'This is not a dev sandbox')
    check_file(file_name)
    path = _pages_dir(g.biz_sandbox) / file_name
    if not path.is_file():
        raise SchemaError(404, 'not_found', 'No such page in this sandbox')
    page = g.biz_store.page_for_serve(file_name)
    return jsonify({'file': file_name, 'url': _page_url(file_name), 'html': path.read_text(encoding='utf-8'),
                    'registered': page is not None,
                    'shell_injection': _shell_tags(page, g.biz_sandbox).decode('utf-8') if page else None})


# ── records ────────────────────────────────────────────────────────────────
def _where_arg():
    where = request.args.get('where')
    if not where:
        return None
    try:
        where = json.loads(where)
    except ValueError:
        raise SchemaError(400, 'bad_query', 'where must be JSON, e.g. {"status":"open"}')
    if not isinstance(where, dict):
        raise SchemaError(400, 'bad_query', 'where must be a JSON object')
    return where or None


@biz_bp.get(f'{API}/<collection>/aggregate')
def aggregate_records(collection):
    sums = [f.strip() for f in request.args.get('sum', '').split(',') if f.strip()]
    return jsonify(g.biz_store.aggregate(collection, where=_where_arg(), sum_fields=sums,
                                         group_by=request.args.get('group_by') or None,
                                         archived=request.args.get('archived') == '1',
                                         flagged=request.args.get('flagged') == '1'))


@biz_bp.post(f'{API}/<collection>/<rid>/confirm')
def confirm_record_flags(collection, rid):
    if _agent():
        return _fail(403, 'owner_only', 'A person confirms a flagged value; an agent cannot confirm its own reading')
    body = _body()
    return jsonify(g.biz_store.confirm_flags(collection, rid, body.get('fields'), by=g.biz_by, note=body.get('note')))


@biz_bp.get(f'{API}/<collection>')
def list_records(collection):
    where = request.args.get('where')
    if where:
        try:
            where = json.loads(where)
        except ValueError:
            return _fail(400, 'bad_query', 'where must be JSON, e.g. {"status":"open"}')
        if not isinstance(where, dict):
            return _fail(400, 'bad_query', 'where must be a JSON object')
    include = [c.strip() for c in request.args.get('include', '').split(',') if c.strip()]
    return jsonify(g.biz_store.list(collection, where=where or None, sort=request.args.get('sort'),
                                    limit=request.args.get('limit', 50), cursor=request.args.get('cursor'),
                                    include=include, archived=request.args.get('archived') == '1',
                                    flagged=request.args.get('flagged') == '1'))


@biz_bp.post(f'{API}/<collection>')
def create_record(collection):
    body = _body()
    rec = g.biz_store.create(collection, body.get('data'), source=body.get('source'), **_w())
    return jsonify(rec), 201


@biz_bp.get(f'{API}/<collection>/<rid>')
def get_record(collection, rid):
    return jsonify(g.biz_store.get(collection, rid))


def _if_match():
    raw = (request.headers.get('If-Match') or '').strip()
    if raw.startswith('W/'):
        raw = raw[2:]
    raw = raw.strip('"')
    try:
        return int(raw)
    except ValueError:
        return None


@biz_bp.patch(f'{API}/<collection>/<rid>')
def patch_record(collection, rid):
    body = _body()
    rec = g.biz_store.update(collection, rid, body.get('data'), expected_version=_if_match(),
                             clear=body.get('clear'), source=body.get('source'), **_w())
    return jsonify(rec)


@biz_bp.get(f'{API}/<collection>/<rid>/timeline')
def record_timeline(collection, rid):
    return jsonify({'events': g.biz_store.timeline(collection, rid)})


@biz_bp.post(f'{API}/<collection>/<rid>/archive')
def archive_record(collection, rid):
    return jsonify(g.biz_store.set_archived(collection, rid, True, source=_body().get('source'), **_w()))


@biz_bp.post(f'{API}/<collection>/<rid>/unarchive')
def unarchive_record(collection, rid):
    return jsonify(g.biz_store.set_archived(collection, rid, False, source=_body().get('source'), **_w()))


# ── the SDK, the menu shell and the SDK doc: fixed files, served from the image ──
_JB_ASSETS = {
    'jb.js': 'application/javascript',
    'shell.js': 'application/javascript',
    'shell.css': 'text/css',
    'sdk.md': 'text/markdown; charset=utf-8',
}


@jb_sdk_bp.get('/jb/v1/<name>')
def jb_asset(name):
    mimetype = _JB_ASSETS.get(name)
    if mimetype is None:
        return jsonify({'error': 'Not found', 'code': 'not_found'}), 404
    resp = send_file(JB_SDK_DIR / 'v1' / name, mimetype=mimetype, max_age=0)
    resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
    return resp


@jb_sdk_bp.get('/jb/v1/')
def jb_index():
    return redirect('/jb/v1/sdk.md', code=302)


# ── serve-time shell injection (called by routes/canvas.py for every served page) ──
def _shell_tags(page: dict, sandbox: str | None) -> bytes:
    attrs = [('id', 'jb-shell-js'), ('src', '/jb/v1/shell.js'), ('data-page', page['id']),
             ('data-variant', page['shell'])]
    if page.get('legacy_nav'):
        attrs.append(('data-legacy-nav', page['legacy_nav']))
    if sandbox:
        attrs.append(('data-sandbox', sandbox))
    tag = '<script ' + ' '.join(f'{k}="{_html.escape(v, quote=True)}"' for k, v in attrs) + '></script>'
    # No <link> for shell.css: the canvas CSP has no 'self' in style-src; shell.js adds it inline.
    return ('<script id="jb-sdk-js" src="/jb/v1/jb.js"></script>' + tag).encode('utf-8')


def shell_injection(rel_path: str, content: bytes) -> bytes:
    """Tags that put the shared menu on a served canvas page, or b''.

    Only for pages registered in this business's company system with shell != off.
    Never creates a database (a business that has none has no registered pages), never
    raises (a broken registry must not take the page down), and leaves the page file
    on disk untouched: removing the registry row gives the page back exactly as it was.
    """
    try:
        if b'/jb/v1/shell.js' in content:          # the page includes the shell itself
            return b''
        sandbox = None
        file_name = rel_path
        if '/' in rel_path:
            hit = _sandbox.split_page_path(rel_path)
            if not hit:
                return b''
            sandbox, file_name = hit
        if not FILE_RE.match(file_name):
            return b''
        ext = current_app.extensions.setdefault('biz', {})
        slot = 'store' if sandbox is None else 'sandbox:' + sandbox
        if ext.get(slot) is None:
            tenant, db_dir = resolve_location(hosted=_hosted())
            db_path = (Path(db_dir) if sandbox is None else Path(db_dir) / 'sandboxes' / sandbox) / 'business.db'
            if not db_path.is_file():
                return b''
        page = get_store(sandbox).page_for_serve(file_name)
        if not page or page['shell'] == 'off':
            return b''
        return _shell_tags(page, sandbox)
    except Exception:
        logger.warning('biz: shell injection skipped for %s', rel_path, exc_info=True)
        return b''


# ── pack routes: added once at import, before the blueprint is registered ──
def _register_pack_routes():
    from services.biz import packs as _packs
    from services.biz.schema import PACKS_DIR
    try:
        return _packs.register_routes(biz_bp, API, PackContext, PACKS_DIR)
    except Exception:
        logger.exception('biz: pack routes not registered')
        return []


PACK_ROUTES = _register_pack_routes()
