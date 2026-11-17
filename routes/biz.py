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
GET    /jb/v1/jb.js                        the SDK (public asset; the data behind it is gated)
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time

from flask import Blueprint, current_app, g, jsonify, request, send_file

from services.biz.schema import SchemaError
from services.biz.store import BizStore, resolve_location
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


def get_store() -> BizStore:
    """The tenant's store, opened once per app. A refusal is cached for a minute, then retried."""
    ext = current_app.extensions.setdefault('biz', {})
    store = ext.get('store')
    if store is not None:
        return store
    with _open_lock:
        store = ext.get('store')
        if store is not None:
            return store
        failed = ext.get('open_error')
        if failed and time.monotonic() - failed[0] < _RETRY_OPEN_AFTER_S:
            raise failed[1]
        try:
            tenant, db_dir = resolve_location(hosted=_hosted())
            store = BizStore(db_dir, tenant)
        except SchemaError as exc:
            ext['open_error'] = (time.monotonic(), exc)
            logger.error('biz: store refused to open: %s (%s)', exc.code, exc.message)
            raise
        ext.pop('open_error', None)
        ext['store'] = store
        return store


def _fail(status, code, message, **extra):
    return jsonify({'error': message, 'code': code, **extra}), status


@biz_bp.errorhandler(SchemaError)
def _schema_error(exc: SchemaError):
    return jsonify(exc.to_dict()), exc.status


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
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        return _fail(413, 'body_too_large', 'Request body is larger than 1 MB')

    presented = request.headers.get('X-Agent-Key')
    expected = os.getenv('AGENT_API_KEY', '').strip()
    if presented is not None:
        if expected and hmac.compare_digest(presented.encode(), expected.encode()):
            g.biz_role = 'agent'
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

    try:
        store = get_store()
    except SchemaError as exc:
        return jsonify(exc.to_dict()), exc.status
    if g.biz_role == 'agent':
        g.biz_by = f'agent:{store.tenant}'
    g.biz_store = store
    store.maybe_daily_backup()
    if request.method in ('POST', 'PATCH', 'PUT'):
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
    return jsonify(g.biz_store.health())


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


# ── records ────────────────────────────────────────────────────────────────
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
                                    include=include, archived=request.args.get('archived') == '1'))


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


# ── the SDK: one fixed file, served from the image, never copied into tenant folders ──
@jb_sdk_bp.get('/jb/v1/jb.js')
def jb_sdk():
    resp = send_file(JB_SDK_DIR / 'v1' / 'jb.js', mimetype='application/javascript', max_age=0)
    resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
    return resp
