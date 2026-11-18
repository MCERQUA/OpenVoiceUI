"""JamBot Hub summary: GET /api/biz/v1/hub/summary (services/biz/hub.py).

Cases come from the Hub build brief (milestone a) and the design's section 8 walls, not from the code:
  happy path on a seeded db (exact counts, verdicts, last activity); unauthenticated -> 401; a wrong
  agent key -> 403; a db that belongs to another tenant -> refused; a request naming a tenant cannot
  change whose data comes back (the gate refuses it, and the platform service is never asked for
  that tenant); an unreachable read-through -> verdict unknown with no number; a dev sandbox never
  shows the dev server's real leads; no record content (names, phones) ever comes back.

The platform service (:6350) is a fake on loopback that behaves like the real caller binding:
it answers only for the tenant it is bound to and refuses any other with 403.
"""
import json
import shutil
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

OWNER = 'user_owner_a'
KEY_A = 'agent-key-tenant-a'
SBX_KEY = 'sbx-key-one-0123456789abcdef0123456789abcdef'
B = '/api/biz/v1'
SRC = {'channel': 'sms', 'ref': 'msg-1', 'confidence': 0.9, 'words': 'hub test'}
NOW = datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


# ── a fake platform service, bound to tenant-a like the real caller binding ─
class _Platform:
    def __init__(self, bound='tenant-a', mode='ok'):
        self.bound, self.mode, self.calls = bound, mode, []
        plat = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                asked = self.headers.get('X-Tenant')
                plat.calls.append((self.path, asked))
                if plat.mode == 'refuse' or (asked and asked != plat.bound):
                    return self._send(403, {'error': 'Tenant mismatch'})
                if plat.mode == 'garbage':
                    return self._send(200, {'nope': True})
                path = self.path.split('?')[0]
                body = {
                    '/api/leads/stats': {'stats': {'total': 9, 'new_count': 2, 'contacted': 3, 'quoted': 1,
                                                   'won': 1, 'lost': 1, 'spam': 1}},
                    '/api/leads': {'leads': [{'created_at': _iso(NOW - timedelta(hours=3)),
                                              'name': 'Lead Person Secret', 'phone': '555-0100'}]},
                    '/api/seo/projects': {'projects': [{'domain': 'example-a.com', 'label': 'A'}]},
                    '/api/seo/reports': {'reports': [{'domain': 'example-a.com',
                                                      'generatedAt': _iso(NOW - timedelta(days=10))}]},
                }.get(path)
                if body is None:
                    return self._send(404, {'error': 'nope'})
                self._send(200, body)

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _closed_port_url():
    import socket
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return f'http://127.0.0.1:{port}'


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture()
def dirs(tmp_path):
    return {'a': tmp_path / 'clients' / 'tenant-a' / 'db', 'b': tmp_path / 'clients' / 'tenant-b' / 'db'}


@pytest.fixture()
def platform():
    p = _Platform()
    yield p
    p.close()


@pytest.fixture()
def env(dirs, monkeypatch, platform):
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_DB_DIR', str(dirs['a']))
    monkeypatch.setenv('BIZ_PLATFORM_API_URL', platform.url)
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.delenv('CANVAS_REQUIRE_AUTH', raising=False)
    monkeypatch.delenv('BIZ_SANDBOXES', raising=False)
    from services.biz import hub
    hub.clear_cache()
    yield dirs
    hub.clear_cache()


def _make_client():
    from app import create_app
    from routes.biz import biz_bp, jb_sdk_bp
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    app.register_blueprint(biz_bp)
    app.register_blueprint(jb_sdk_bp)
    return app, app.test_client()


@pytest.fixture()
def app_client(env):
    app, client = _make_client()
    yield app, client
    for store in list(app.extensions.get('biz', {}).values()):
        if hasattr(store, 'close'):
            store.close()


@pytest.fixture()
def client(app_client):
    return app_client[1]


def _as(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: 'tok' if user else None)
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: user)


@pytest.fixture()
def owner(monkeypatch):
    _as(monkeypatch, OWNER)


@pytest.fixture()
def anon(monkeypatch):
    _as(monkeypatch, None)


def _c(client, coll, data, headers=None):
    r = client.post(f'{B}/{coll}', json={'data': data, 'source': SRC}, headers=headers or {})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _seed(client):
    today = NOW.date()
    a = _c(client, 'contact', {'name': 'Customer Secret Name', 'phone': '555-0199'})
    _c(client, 'contact', {'name': 'Second Customer'})
    _c(client, 'job', {'title': 'Late job', 'status': 'scheduled', 'scheduled_at': _iso(NOW - timedelta(days=2)),
                       'contact_id': a['id']})
    _c(client, 'job', {'title': 'Future job', 'status': 'scheduled', 'scheduled_at': _iso(NOW + timedelta(days=3))})
    _c(client, 'job', {'title': 'Running', 'status': 'in_progress'})
    _c(client, 'job', {'title': 'Done', 'status': 'completed'})
    _c(client, 'job', {'title': 'Paid', 'status': 'paid'})
    _c(client, 'estimate', {'title': 'Attic', 'status': 'sent', 'total': 1200.5})
    _c(client, 'estimate', {'title': 'Draft', 'status': 'draft', 'total': 99})
    _c(client, 'invoice', {'number': 'I-1', 'status': 'sent', 'amount': 500, 'due_on': (today - timedelta(days=5)).isoformat()})
    _c(client, 'invoice', {'number': 'I-2', 'status': 'sent', 'amount': 300, 'due_on': (today + timedelta(days=10)).isoformat()})
    _c(client, 'invoice', {'number': 'I-3', 'status': 'paid', 'amount': 800, 'due_on': (today - timedelta(days=50)).isoformat()})
    _c(client, 'payment', {'amount': 800, 'paid_at': _iso(NOW - timedelta(days=2))})
    _c(client, 'payment', {'amount': 50, 'paid_at': _iso(NOW - timedelta(days=60))})
    _c(client, 'person', {'name': 'Crew One'})
    _c(client, 'person', {'name': 'Crew Two', 'active': False})
    p = _c(client, 'product', {'name': 'Open cell set'})
    _c(client, 'stock_move', {'product_id': p['id'], 'qty': 4, 'type': 'receive', 'date': today.isoformat()})
    return a


# ── happy path ──────────────────────────────────────────────────────────────
def test_summary_on_a_seeded_db(client, owner, platform):
    _seed(client)
    r = client.get(f'{B}/hub/summary')
    assert r.status_code == 200, r.get_json()
    h = r.get_json()
    assert h['tenant'] == 'tenant-a' and h['sandbox'] is None and h['hub'] == 1
    assert h['order'] == ['customers', 'jobs', 'money', 'team', 'inventory', 'leads', 'seo', 'site']
    s = h['sections']
    assert s['customers']['counts'] == {'customers': 2, 'needs_check': 0}
    assert s['customers']['verdict'] == 'ok' and s['customers']['last_activity']
    j = s['jobs']['counts']
    assert (j['jobs'], j['open'], j['scheduled'], j['in_progress'], j['done'], j['late']) == (5, 3, 2, 1, 2, 1)
    assert s['jobs']['verdict'] == 'warn' and 'past its date' in s['jobs']['reason']
    m = s['money']['counts']
    assert (m['estimates'], m['estimates_open'], m['estimates_open_value']) == (2, 1, 1200.5)
    assert (m['invoices'], m['invoices_unpaid'], m['invoices_unpaid_value']) == (3, 2, 800.0)
    assert (m['invoices_overdue'], m['invoices_overdue_value']) == (1, 500.0)
    assert (m['payments'], m['payments_30d'], m['payments_30d_value']) == (2, 1, 800.0)
    assert s['money']['verdict'] == 'warn' and 'past due' in s['money']['reason']
    assert s['team']['counts'] == {'people': 2, 'active': 0, 'active_unknown': 1} and s['team']['verdict'] == 'ok'
    assert s['inventory']['counts']['products'] == 1 and s['inventory']['counts']['stock_moves_30d'] == 1 and s['inventory']['verdict'] == 'ok'
    # read-through: the platform's numbers, asked for THIS tenant only
    assert s['leads']['counts'] == {'total': 9, 'new': 2, 'contacted': 3, 'quoted': 1, 'won': 1, 'lost': 1, 'spam': 1}
    assert s['leads']['verdict'] == 'warn' and s['leads']['source'] == 'platform'
    assert s['leads']['last_activity']
    assert s['seo']['verdict'] == 'ok' and s['seo']['counts'] == {'projects': 1, 'reports': 1, 'domains': 1}
    assert s['site']['verdict'] == 'unknown' and s['site']['domains'] == ['example-a.com']
    assert platform.calls and {t for _, t in platform.calls} == {'tenant-a'}
    assert h['overall']['verdict'] == 'warn' and 'site' in h['overall']['unknown_sections']
    # counts only: no record content, and no lead content, ever comes back
    text = r.get_data(as_text=True)
    for leak in ('Customer Secret Name', '555-0199', 'Lead Person Secret', '555-0100', 'Crew One'):
        assert leak not in text


def test_unknown_active_is_never_counted_as_active(client, owner):
    # 3 products with NO `active` field + 1 explicitly inactive -> 0 active, 3 unknown (never counted as measured)
    for n in ('A', 'B', 'C'):
        _c(client, 'product', {'name': n})
    _c(client, 'product', {'name': 'D', 'active': False})
    _c(client, 'person', {'name': 'P1'})
    _c(client, 'person', {'name': 'P2', 'active': True})
    s = client.get(f'{B}/hub/summary').get_json()['sections']
    c = s['inventory']['counts']
    assert (c['products'], c['products_active'], c['products_unknown']) == (4, 0, 3)
    assert '0 active' in s['inventory']['reason'] and '3 not set' in s['inventory']['reason']
    t = s['team']['counts']
    assert (t['people'], t['active'], t['active_unknown']) == (2, 1, 1)


def test_active_true_is_counted(client, owner):
    _c(client, 'product', {'name': 'On', 'active': True})
    _c(client, 'product', {'name': 'Unset'})
    c = client.get(f'{B}/hub/summary').get_json()['sections']['inventory']['counts']
    assert (c['products_active'], c['products_unknown']) == (1, 1)


def test_badly_overdue_invoice_is_bad(client, owner):
    _c(client, 'invoice', {'number': 'I-9', 'status': 'overdue', 'amount': 100,
                           'due_on': (NOW.date() - timedelta(days=45)).isoformat()})
    s = client.get(f'{B}/hub/summary').get_json()['sections']['money']
    assert s['verdict'] == 'bad' and s['counts']['invoices_overdue'] == 1


def test_empty_business_is_unknown_not_zero_ok(client, owner, monkeypatch):
    monkeypatch.setenv('BIZ_PLATFORM_API_URL', 'off')
    h = client.get(f'{B}/hub/summary').get_json()
    for name in ('customers', 'jobs', 'money', 'team', 'inventory'):
        assert h['sections'][name]['verdict'] == 'unknown' and h['sections'][name]['state'] == 'empty'
    assert h['overall']['verdict'] == 'unknown'


def test_agent_key_reads_the_summary(client, anon):
    r = client.get(f'{B}/hub/summary', headers={'X-Agent-Key': KEY_A})
    assert r.status_code == 200 and r.get_json()['tenant'] == 'tenant-a'


def test_summary_is_read_only(client, owner):
    assert client.post(f'{B}/hub/summary', json={}).status_code in (404, 405)


# ── auth walls ──────────────────────────────────────────────────────────────
def test_unauthenticated_is_401(client, anon, platform):
    r = client.get(f'{B}/hub/summary')
    assert r.status_code == 401 and r.get_json()['code'] == 'auth_required'
    assert platform.calls == []


def test_wrong_agent_key_is_403(client, anon, platform):
    r = client.get(f'{B}/hub/summary', headers={'X-Agent-Key': 'agent-key-tenant-b'})
    assert r.status_code == 403 and r.get_json()['code'] == 'agent_key_invalid'
    assert platform.calls == []


# ── jail cell: whose data ───────────────────────────────────────────────────
def test_db_of_another_tenant_is_refused(env, owner, platform):
    from services.biz.store import BizStore
    other = BizStore(env['b'], 'tenant-b')
    other.create('contact', {'name': 'B customer'}, by='test')
    other.close()
    env['a'].mkdir(parents=True, exist_ok=True)
    shutil.copy(env['b'] / 'business.db', env['a'] / 'business.db')
    app, c = _make_client()
    r = c.get(f'{B}/hub/summary')
    assert r.status_code == 503 and r.get_json()['code'] == 'biz_tenant_mismatch'
    assert platform.calls == []


@pytest.mark.parametrize('kw', [
    {'query_string': {'tenant': 'tenant-b'}},
    {'query_string': {'tenant': 'tenant-a'}},
    {'headers': {'X-Tenant': 'tenant-b'}},
])
def test_a_tenant_parameter_never_changes_whose_data(client, owner, platform, kw):
    _seed(client)
    r = client.get(f'{B}/hub/summary', **kw)
    # The engine refuses any request that names a tenant (stricter than ignoring it), so no foreign data can
    # come back, and the platform service is never asked about the named tenant.
    assert r.status_code == 400 and r.get_json()['code'] == 'tenant_param_refused'
    assert all(t == 'tenant-a' for _, t in platform.calls)
    assert 'sections' not in (r.get_json() or {})


def test_platform_is_asked_only_for_the_containers_tenant(client, owner, platform, monkeypatch):
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    client.get(f'{B}/hub/summary')
    assert platform.calls and all(t == 'tenant-a' for _, t in platform.calls)


# ── read-throughs that cannot be reached ────────────────────────────────────
def _assert_unknown_no_number(sec, state='unreachable'):
    assert sec['verdict'] == 'unknown' and sec['state'] == state, sec
    assert sec['counts'] == {} and sec['last_activity'] is None
    assert sec['reason']


def test_unreachable_read_through_is_unknown(client, owner, monkeypatch):
    _seed(client)
    monkeypatch.setenv('BIZ_PLATFORM_API_URL', _closed_port_url())
    h = client.get(f'{B}/hub/summary').get_json()
    for name in ('leads', 'seo', 'site'):
        _assert_unknown_no_number(h['sections'][name])
        assert 'could not be reached' in h['sections'][name]['reason']
    assert h['sections']['customers']['counts']['customers'] == 2      # local sections still measured


def test_refusing_read_through_is_unknown(client, owner, platform):
    platform.mode = 'refuse'
    h = client.get(f'{B}/hub/summary').get_json()
    for name in ('leads', 'seo', 'site'):
        _assert_unknown_no_number(h['sections'][name])
        assert 'refused' in h['sections'][name]['reason']


def test_malformed_read_through_is_unknown(client, owner, platform):
    platform.mode = 'garbage'
    s = client.get(f'{B}/hub/summary').get_json()['sections']['leads']
    _assert_unknown_no_number(s)


def test_platform_switched_off_is_unknown(client, owner, monkeypatch, platform):
    monkeypatch.setenv('BIZ_PLATFORM_API_URL', 'off')
    h = client.get(f'{B}/hub/summary').get_json()
    for name in ('leads', 'seo', 'site'):
        _assert_unknown_no_number(h['sections'][name])
        assert 'not connected' in h['sections'][name]['reason']
    assert platform.calls == []


# ── dev sandbox: fake data, never the dev server's real leads ───────────────
def test_sandbox_summary_never_reads_the_dev_tenants_platform_data(env, anon, monkeypatch, platform):
    monkeypatch.setenv('BIZ_SANDBOXES', 'one')
    monkeypatch.setenv('BIZ_SANDBOX_KEY_ONE', SBX_KEY)
    app, c = _make_client()
    try:
        r = c.post(f'{B}/contact', json={'data': {'name': 'Sample A'}, 'source': SRC}, headers={'X-Agent-Key': SBX_KEY})
        assert r.status_code == 201
        h = c.get(f'{B}/hub/summary', headers={'X-Agent-Key': SBX_KEY}).get_json()
    finally:
        for store in list(app.extensions.get('biz', {}).values()):
            if hasattr(store, 'close'):
                store.close()
    assert h['sandbox'] == 'one' and h['tenant'] == 'tenant-a-sbx-one'
    assert h['sections']['customers']['counts']['customers'] == 1
    for name in ('leads', 'seo', 'site'):
        _assert_unknown_no_number(h['sections'][name], state='sandbox')
    assert platform.calls == []


def test_sdk_exposes_hub_summary(client, anon):
    js = client.get('/jb/v1/jb.js').get_data(as_text=True)
    assert "hub: {" in js and "'/hub/summary'" in js
    md = client.get('/jb/v1/sdk.md').get_data(as_text=True)
    assert 'jb.hub.summary()' in md and '/api/biz/v1/hub/summary' in md
