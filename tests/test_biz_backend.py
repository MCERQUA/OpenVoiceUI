"""JamBot client backend, phase 1 (services/biz + routes/biz.py).

Cases come from the design's phase-1 acceptance list and section 8 controls
(docs/jambot/client-backend-design.md), not from the implementation:
  CRUD round trip under an owner session; unauthenticated -> 401; wrong agent
  key -> 403; agent write without source -> 422; stale If-Match -> 409;
  foreign-tenant insert -> trigger abort; mismatched meta.tenant -> refused;
  backup restores with equal counts; additive-only schema; path traversal and
  cross-tenant ids refused.
"""
import json
import shutil
import sqlite3
import threading
import uuid

import pytest

OWNER = 'user_owner_a'
KEY_A = 'agent-key-tenant-a'
SRC = {'channel': 'sms', 'ref': 'msg-1', 'confidence': 0.9, 'words': 'new job for the attic'}


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture()
def dirs(tmp_path):
    return {'a': tmp_path / 'clients' / 'tenant-a' / 'db', 'b': tmp_path / 'clients' / 'tenant-b' / 'db'}


@pytest.fixture()
def env(dirs, monkeypatch):
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_DB_DIR', str(dirs['a']))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.delenv('CANVAS_REQUIRE_AUTH', raising=False)
    return dirs


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
    store = app.extensions.get('biz', {}).get('store')
    if store:
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


AG = {'X-Agent-Key': KEY_A}
B = '/api/biz/v1'


def _create(client, coll, data, headers=None, source=None):
    return client.post(f'{B}/{coll}', json={'data': data, 'source': source}, headers=headers or {})


# ── auth: negative controls ─────────────────────────────────────────────────
def test_unauthenticated_read_is_401(client, anon):
    r = client.get(f'{B}/job')
    assert r.status_code == 401
    assert r.get_json()['code'] == 'auth_required'


def test_unauthenticated_write_is_401(client, anon):
    assert _create(client, 'job', {'title': 'x'}).status_code == 401


def test_wrong_agent_key_is_403(client, anon):
    r = client.get(f'{B}/job', headers={'X-Agent-Key': 'agent-key-tenant-b'})
    assert r.status_code == 403
    assert r.get_json()['code'] == 'agent_key_invalid'


def test_wrong_agent_key_with_valid_session_still_403(client, owner):
    r = client.get(f'{B}/job', headers={'X-Agent-Key': 'not-this-tenant'})
    assert r.status_code == 403


def test_agent_write_without_source_is_422(client, anon):
    r = _create(client, 'job', {'title': 'Attic re-blow'}, headers=AG)
    assert r.status_code == 422
    assert r.get_json()['code'] == 'source_required'


def test_agent_write_with_bad_channel_is_422(client, anon):
    r = _create(client, 'job', {'title': 'x'}, headers=AG, source={'channel': 'carrier-pigeon'})
    assert r.status_code == 422 and r.get_json()['code'] == 'bad_source'


def test_agent_write_with_source_is_stamped(client, anon):
    r = _create(client, 'job', {'title': 'Attic re-blow'}, headers=AG, source=SRC)
    assert r.status_code == 201, r.get_json()
    rec = r.get_json()
    assert rec['created_by'] == 'agent:tenant-a' and rec['source']['channel'] == 'sms'


def test_export_is_owner_only(client, anon):
    assert client.get(f'{B}/export', headers=AG).status_code == 403


def test_hosted_without_clerk_fails_closed(env, monkeypatch, anon):
    monkeypatch.delenv('CLERK_PUBLISHABLE_KEY')
    monkeypatch.setenv('CANVAS_REQUIRE_AUTH', 'true')
    _, c = _make_client()
    assert c.get(f'{B}/job').status_code == 503      # app.py fail_closed_unconfigured


def test_sdk_is_a_public_asset_without_storage(client, anon):
    r = client.get('/jb/v1/jb.js')
    assert r.status_code == 200 and 'javascript' in r.content_type
    body = r.get_data(as_text=True)
    assert "contract: 'v1'" in body and '/api/biz/v1' in body
    code = '\n'.join(l for l in body.splitlines() if not l.strip().startswith('*'))
    for banned in ('localStorage', 'sessionStorage', 'indexedDB'):
        assert banned not in code


# ── CRUD round trip (owner) ─────────────────────────────────────────────────
def test_owner_crud_round_trip(client, owner):
    c = _create(client, 'contact', {'name': 'Pat Example', 'phone': '602-555-0100', 'type': 'residential'})
    assert c.status_code == 201
    contact = c.get_json()
    assert contact['created_by'] == f'clerk:{OWNER}' and contact['version'] == 1
    j = _create(client, 'job', {'title': 'Attic re-blow', 'contact_id': contact['id'], 'status': 'scheduled'})
    assert j.status_code == 201
    job = j.get_json()

    got = client.get(f"{B}/job/{job['id']}").get_json()
    assert got['data']['title'] == 'Attic re-blow'

    lst = client.get(f'{B}/job', query_string={'where': json.dumps({'status': 'scheduled'}),
                                               'include': 'contact'}).get_json()
    assert [r['id'] for r in lst['records']] == [job['id']]
    assert lst['records'][0]['included']['contact_id']['data']['name'] == 'Pat Example'

    p = client.patch(f"{B}/job/{job['id']}", json={'data': {'crew': ['Sam']}}, headers={'If-Match': '1'})
    assert p.status_code == 200 and p.get_json()['version'] == 2

    tl = client.get(f"{B}/job/{job['id']}/timeline").get_json()['events']
    assert [e['type'] for e in tl] == ['created', 'updated']
    assert tl[1]['diff']['changed']['crew'] == [None, ['Sam']]

    assert client.post(f"{B}/job/{job['id']}/archive", json={}).status_code == 200
    assert client.get(f'{B}/job').get_json()['records'] == []
    assert len(client.get(f'{B}/job?archived=1').get_json()['records']) == 1
    un = client.post(f"{B}/job/{job['id']}/unarchive", json={}).get_json()
    assert un['archived_at'] is None

    exp = client.get(f'{B}/export').get_json()
    assert exp['tenant'] == 'tenant-a' and len(exp['records']) == 2


def test_no_delete_verb(client, owner):
    j = _create(client, 'job', {'title': 'x'}).get_json()
    assert client.delete(f"{B}/job/{j['id']}").status_code == 405


def test_stale_if_match_is_409_with_current(client, owner):
    j = _create(client, 'job', {'title': 'x'}).get_json()
    assert client.patch(f"{B}/job/{j['id']}", json={'data': {'title': 'y'}}, headers={'If-Match': '1'}).status_code == 200
    r = client.patch(f"{B}/job/{j['id']}", json={'data': {'title': 'z'}}, headers={'If-Match': '1'})
    assert r.status_code == 409
    body = r.get_json()
    assert body['code'] == 'version_conflict' and body['current']['data']['title'] == 'y'


def test_patch_without_if_match_is_428(client, owner):
    j = _create(client, 'job', {'title': 'x'}).get_json()
    r = client.patch(f"{B}/job/{j['id']}", json={'data': {'title': 'y'}})
    assert r.status_code == 428


def test_patch_never_blanks_a_populated_field(client, owner):
    j = _create(client, 'job', {'title': 'x', 'notes': 'gate code 4411'}).get_json()
    r = client.patch(f"{B}/job/{j['id']}", json={'data': {'notes': ''}}, headers={'If-Match': '1'}).get_json()
    assert r['data']['notes'] == 'gate code 4411' and r['preserved'] == ['notes'] and r['version'] == 1
    r = client.patch(f"{B}/job/{j['id']}", json={'clear': ['notes']}, headers={'If-Match': '1'}).get_json()
    assert 'notes' not in r['data'] and r['version'] == 2


@pytest.mark.parametrize('data,problem', [
    ({'title': 'x', 'colour': 'red'}, 'unknown field'),
    ({'title': 'x', 'status': 'bogus'}, 'not one of the allowed values'),
    ({'title': 'x', 'scheduled_at': 'next tuesday'}, 'must be an ISO date-time'),
    ({'status': 'scheduled'}, 'required'),
    ({'title': 'x', 'tenant': 'tenant-b'}, 'reserved name'),
])
def test_bad_records_are_refused(client, owner, data, problem):
    r = _create(client, 'job', data)
    assert r.status_code == 422, r.get_json()
    assert problem in json.dumps(r.get_json())


def test_card_number_refused_phone_allowed(client, owner):
    r = _create(client, 'payment', {'amount': 120.0, 'reference': 'card 4111 1111 1111 1111'})
    assert r.status_code == 422 and r.get_json()['code'] == 'card_number_refused'
    assert _create(client, 'payment', {'amount': 120.0, 'reference': 'check 1042, call 602-555-0100'}).status_code == 201


def test_stock_moves_are_append_only(client, owner):
    p = _create(client, 'product', {'name': 'R-38 batt'}).get_json()
    m = _create(client, 'stock_move', {'product_id': p['id'], 'qty': 10, 'type': 'receive'}).get_json()
    r = client.patch(f"{B}/stock_move/{m['id']}", json={'data': {'qty': 9}}, headers={'If-Match': '1'})
    assert r.status_code == 409 and r.get_json()['code'] == 'append_only'


def test_leads_are_read_through_not_copied(client, owner):
    assert client.get(f'{B}/lead').status_code == 501
    assert _create(client, 'lead', {'description': 'x'}).status_code == 501


def test_company_is_a_singleton(client, owner):
    assert _create(client, 'company', {'name': 'Acme Insulation'}).status_code == 201
    r = _create(client, 'company', {'name': 'Second'})
    assert r.status_code == 409 and r.get_json()['code'] == 'singleton_exists'


def test_counter_is_atomic_and_formatted(app_client, owner):
    app, client = app_client
    first = client.post(f'{B}/counters/job_number/next', json={'prefix': '26', 'pad': 3}).get_json()
    assert first['formatted'] == '26001'
    store = app.extensions['biz']['store']
    got, errs = [], []

    def worker():
        try:
            for _ in range(10):
                got.append(store.counter_next('job_number')['value'])
        except Exception as e:      # pragma: no cover - a failure shows up in errs
            errs.append(e)
    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(got) == 40 and len(set(got)) == 40 and max(got) == 41


def test_events_feed(client, owner):
    latest = client.get(f'{B}/events?since=-1').get_json()['last_id']
    _create(client, 'job', {'title': 'x'})
    ev = client.get(f'{B}/events?since={latest}').get_json()
    assert [e['type'] for e in ev['events']] == ['created'] and ev['last_id'] > latest


def test_health_reports_versions(client, owner):
    h = client.get(f'{B}/health').get_json()
    assert h['tenant'] == 'tenant-a' and h['api'] == 'v1' and h['schema'] == h['schema_latest'] >= 1
    assert h['mode'] == 'read-write' and h['library_version'] == 1


# ── additive-only schema ────────────────────────────────────────────────────
def _schema(client, payload, headers=None):
    return client.post(f'{B}/schema/custom', json=payload, headers=headers or {})


def test_custom_field_on_core_collection(client, owner):
    r = _schema(client, {'collection': 'stock_move', 'fields': {
        'x_foam_source': {'type': 'enum', 'label': 'Foam source', 'values': ['ours', 'gc']}}})
    assert r.status_code == 200, r.get_json()
    p = _create(client, 'product', {'name': 'Closed cell set'}).get_json()
    m = _create(client, 'stock_move', {'product_id': p['id'], 'qty': -1, 'type': 'use', 'x_foam_source': 'gc'})
    assert m.status_code == 201


@pytest.mark.parametrize('payload,status,code', [
    ({'collection': 'job', 'fields': {'gate_code': {'type': 'string'}}}, 422, 'bad_field_name'),
    ({'collection': 'job', 'fields': {'title': {'type': 'number'}}}, 409, 'additive_violation'),
    ({'collection': 'job', 'fields': {'x_a': {'type': 'string', 'required': True}}}, 409, 'additive_violation'),
    ({'collection': 'job', 'fields': {'x_a': {'type': 'blob'}}}, 422, 'bad_field_spec'),
    ({'collection': 'job', 'fields': {'x_a': {'type': 'string', 'default': 'Mr Smith'}}}, 422, 'bad_field_spec'),
    ({'collection': 'truck_checklist', 'fields': {'item': {'type': 'string'}}}, 422, 'bad_collection_name'),
    ({'collection': 'job', 'fields': {'sf_set_yield': {'type': 'number'}}}, 422, 'library_key_reserved'),
    ({'collection': '__probe__', 'fields': {'x_a': {'type': 'string'}}}, 409, 'system_collection'),
])
def test_schema_violations_refused(client, owner, payload, status, code):
    r = _schema(client, payload)
    assert r.status_code == status and r.get_json()['code'] == code, r.get_json()


def test_custom_field_type_never_changes(client, owner):
    assert _schema(client, {'collection': 'job', 'fields': {'x_gate': {'type': 'string'}}}).status_code == 200
    r = _schema(client, {'collection': 'job', 'fields': {'x_gate': {'type': 'integer'}}})
    assert r.status_code == 409 and r.get_json()['code'] == 'additive_violation'


def test_enum_values_never_removed(client, owner):
    _schema(client, {'collection': 'job', 'fields': {'x_tier': {'type': 'enum', 'values': ['a', 'b']}}})
    r = _schema(client, {'collection': 'job', 'fields': {'x_tier': {'type': 'enum', 'values': ['a']}}})
    assert r.status_code == 409
    ok = _schema(client, {'collection': 'job', 'fields': {'x_tier': {'type': 'enum', 'values': ['a', 'b', 'c']}}})
    assert ok.status_code == 200


def test_custom_collection_round_trip(client, owner, env):
    r = _schema(client, {'collection': 'c_truck_checklist', 'label': 'Truck checklist', 'title_field': 'item',
                         'fields': {'item': {'type': 'string'}, 'done': {'type': 'boolean'}}})
    assert r.status_code == 200, r.get_json()
    assert _create(client, 'c_truck_checklist', {'item': 'Hoses coiled', 'done': True}).status_code == 201
    overlay = env['a'] / 'schema' / 'custom' / 'c_truck_checklist.json'
    assert json.loads(overlay.read_text())['fields']['done']['type'] == 'boolean'


def test_agent_schema_change_needs_source(client, anon):
    r = _schema(client, {'collection': 'job', 'fields': {'x_a': {'type': 'string'}}}, headers=AG)
    assert r.status_code == 422 and r.get_json()['code'] == 'source_required'


# ── shared field library + candidates ───────────────────────────────────────
def test_library_is_served_and_filterable(client, owner):
    lib = client.get(f'{B}/fields/library').get_json()
    assert lib['library_version'] == 1 and len(lib['fields']) >= 100
    sf = client.get(f'{B}/fields/library?trade=spray-foam').get_json()['fields']
    assert sf and all(f['trade'] == 'spray-foam' for f in sf.values())
    for key in ('sf_stroke_count', 'sf_board_feet', 'sf_humidity', 'sf_a_batch_number', 'sf_foam_type'):
        assert key in sf


def test_adopt_library_field_then_range_enforced(client, owner):
    r = client.post(f'{B}/schema/adopt', json={'collection': 'job', 'key': 'sf_humidity'})
    assert r.status_code == 200, r.get_json()
    spec = r.get_json()['collection']['fields']['sf_humidity']
    assert spec['library'] == 'sf_humidity' and spec['unit'] == '%'
    assert client.post(f'{B}/schema/adopt', json={'collection': 'job', 'key': 'sf_humidity'}).status_code == 200
    assert _create(client, 'job', {'title': 'x', 'sf_humidity': 55}).status_code == 201
    bad = _create(client, 'job', {'title': 'x', 'sf_humidity': 140})
    assert bad.status_code == 422 and 'above the maximum' in json.dumps(bad.get_json())


def test_adopt_unknown_library_field_404(client, owner):
    r = client.post(f'{B}/schema/adopt', json={'collection': 'job', 'key': 'sf_made_up'})
    assert r.status_code == 404


def test_new_custom_field_files_a_candidate_with_gate(client, owner):
    _create(client, 'contact', {'name': 'Bartholomew Quist'})
    _create(client, 'company', {'name': 'Acme Insulation LLC', 'website': 'https://acmeinsul.com'})
    _schema(client, {'collection': 'job', 'fields': {
        'x_gate_code': {'type': 'string', 'label': 'Gate code'},
        'x_quist_notes': {'type': 'text', 'label': 'Notes for Quist'},
        'x_acme_rating': {'type': 'integer', 'label': 'Acme rating'}}})
    props = {p['field']: p for p in client.get(f'{B}/schema/proposals').get_json()['proposals']}
    assert props['x_gate_code']['gate']['verdict'] == 'pass'
    assert props['x_gate_code']['definition'] == {'key': 'gate_code', 'collection': 'job', 'type': 'string',
                                                  'label': 'Gate code'}
    assert props['x_quist_notes']['gate']['reasons'] == ['contains a customer name']
    assert props['x_acme_rating']['gate']['reasons'] == ["contains the tenant's own brand term"]


# ── isolation: jail cells ───────────────────────────────────────────────────
@pytest.mark.parametrize('kw', [
    {'query_string': {'tenant': 'tenant-b'}},
    {'headers': {'X-Tenant': 'tenant-b'}},
])
def test_request_naming_a_tenant_is_refused(client, owner, kw):
    r = client.get(f'{B}/job', **kw)
    assert r.status_code == 400 and r.get_json()['code'] == 'tenant_param_refused'


def test_body_naming_a_tenant_is_refused(client, owner):
    r = client.post(f'{B}/job', json={'tenant': 'tenant-b', 'data': {'title': 'x'}})
    assert r.status_code == 400 and r.get_json()['code'] == 'tenant_param_refused'


@pytest.mark.parametrize('path', [
    '/api/biz/v1/..%2F..%2Ftenant-b%2Fdb%2Fbusiness',
    '/api/biz/v1/job/..%2F..%2F..%2Ftenant-b%2Fdb%2Fbusiness.db',
    '/api/biz/v1/%2e%2e/%2e%2e/tenant-b/db/business.db',
    '/api/biz/v1/schema/..%2F..%2Fschema%2Fcustom%2Fjob',
])
def test_path_traversal_is_refused(client, owner, env, path):
    r = client.get(path)
    assert r.status_code in (400, 404), (path, r.status_code)
    assert not env['b'].exists()      # tenant B's directory was never created or touched


def test_traversal_in_custom_collection_name_refused(client, owner, env):
    r = _schema(client, {'collection': '../../tenant-b/db/schema/custom/c_x', 'fields': {'a': {'type': 'string'}}})
    assert r.status_code == 422
    assert not env['b'].exists()


def test_cross_tenant_id_is_refused(client, owner, env):
    from services.biz.store import BizStore
    other = BizStore(env['b'], 'tenant-b')
    try:
        b_contact = other.create('contact', {'name': 'Tenant B customer'}, by='test')
        b_job = other.create('job', {'title': 'B job'}, by='test')
    finally:
        other.close()
    assert client.get(f"{B}/job/{b_job['id']}").status_code == 404
    assert client.get(f"{B}/contact/{b_contact['id']}/timeline").status_code == 404
    r = _create(client, 'job', {'title': 'x', 'contact_id': b_contact['id']})
    assert r.status_code == 422 and r.get_json()['code'] == 'ref_not_found'
    r = client.patch(f"{B}/job/{b_job['id']}", json={'data': {'title': 'hijack'}}, headers={'If-Match': '1'})
    assert r.status_code == 404


def test_id_of_another_collection_is_404(client, owner):
    c = _create(client, 'contact', {'name': 'x'}).get_json()
    assert client.get(f"{B}/job/{c['id']}").status_code == 404


def test_copied_db_from_another_tenant_is_refused(env, owner):
    from services.biz.store import BizStore
    other = BizStore(env['b'], 'tenant-b')
    other.create('job', {'title': 'B job'}, by='test')
    other.close()
    env['a'].mkdir(parents=True, exist_ok=True)
    shutil.copy(env['b'] / 'business.db', env['a'] / 'business.db')
    app, c = _make_client()
    r = c.get(f'{B}/job')
    assert r.status_code == 503 and r.get_json()['code'] == 'biz_tenant_mismatch'


@pytest.mark.parametrize('envset,code', [
    ({'JAMBOT_TENANT': None}, 'biz_tenant_unset'),
    ({'JAMBOT_TENANT': '../tenant-b'}, 'biz_tenant_invalid'),
    ({'BIZ_DB_DIR': None}, 'biz_storage_not_durable'),
])
def test_unsafe_location_refused(env, owner, monkeypatch, envset, code):
    for k, v in envset.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    _, c = _make_client()
    r = c.get(f'{B}/job')
    assert r.status_code == 503 and r.get_json()['code'] == code


def test_db_dir_follows_ovui_db_path(env, owner, monkeypatch, tmp_path):
    monkeypatch.delenv('BIZ_DB_DIR')
    monkeypatch.setenv('OVUI_DB_PATH', str(tmp_path / 'ovu-db' / 'usage.db'))
    app, c = _make_client()
    assert c.get(f'{B}/health').status_code == 200
    assert (tmp_path / 'ovu-db' / 'business.db').exists()
    app.extensions['biz']['store'].close()


# ── row wall: triggers ──────────────────────────────────────────────────────
@pytest.fixture()
def store_a(env):
    from services.biz.store import BizStore
    s = BizStore(env['a'], 'tenant-a')
    yield s
    s.close()


def test_foreign_tenant_row_insert_aborts(store_a):
    conn = sqlite3.connect(str(store_a.path))
    try:
        with pytest.raises(sqlite3.DatabaseError, match='tenant does not match'):
            conn.execute("INSERT INTO records(id, tenant, collection, data, created_at, created_by, updated_at, "
                         "updated_by) VALUES (?, 'tenant-b', 'job', '{}', 'now', 'x', 'now', 'x')",
                         (str(uuid.uuid4()),))
        conn.execute("INSERT INTO records(id, tenant, collection, data, created_at, created_by, updated_at, "
                     "updated_by) VALUES (?, 'tenant-a', 'job', '{}', 'now', 'x', 'now', 'x')", (str(uuid.uuid4()),))
        with pytest.raises(sqlite3.DatabaseError, match='tenant does not match'):
            conn.execute("UPDATE records SET tenant = 'tenant-b'")
        with pytest.raises(sqlite3.DatabaseError, match='never deleted'):
            conn.execute('DELETE FROM records')
        with pytest.raises(sqlite3.DatabaseError, match='tenant does not match'):
            conn.execute("INSERT INTO counters(name, tenant) VALUES ('job_number', 'tenant-b')")
    finally:
        conn.close()


def test_activity_is_append_only_and_tenant_immutable(store_a):
    store_a.create('job', {'title': 'x'}, by='test')
    conn = sqlite3.connect(str(store_a.path))
    try:
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            conn.execute("UPDATE activity SET by = 'someone else'")
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            conn.execute('DELETE FROM activity')
        with pytest.raises(sqlite3.DatabaseError, match='immutable'):
            conn.execute("UPDATE meta SET value = 'tenant-b' WHERE key = 'tenant'")
        with pytest.raises(sqlite3.DatabaseError, match='immutable'):
            conn.execute("DELETE FROM meta WHERE key = 'tenant'")
    finally:
        conn.close()


# ── backup + restore ────────────────────────────────────────────────────────
def test_daily_backup_restores_with_equal_counts(client, owner, env, tmp_path):
    for i in range(5):
        _create(client, 'job', {'title': f'job {i}'})
    _create(client, 'contact', {'name': 'x'})
    backups = list((env['a'] / 'backups').glob('business-*.db'))
    assert len(backups) == 1                      # first request of the day made it
    from services.biz.store import BizStore, verify_backup
    live = client.get(f'{B}/health').get_json()['last_backup']
    assert live['verified'] is True and live['live_counts'] == live['backup_counts']

    # a fresh backup now (after writes), restored into a new dir, must match the live db exactly
    app_store = client.application.extensions['biz']['store']
    res = app_store.backup_to(tmp_path / 'restore-src' / 'business.db')
    assert res['verified']
    restored_dir = tmp_path / 'restored' / 'db'
    restored_dir.mkdir(parents=True)
    shutil.copy(tmp_path / 'restore-src' / 'business.db', restored_dir / 'business.db')
    restored = BizStore(restored_dir, 'tenant-a')
    try:
        assert restored.collection_counts() == app_store.collection_counts()
        assert verify_backup(restored_dir / 'business.db')['counts'] == res['live_counts']
    finally:
        restored.close()
    with pytest.raises(Exception) as exc:        # a restore under the wrong tenant is refused
        BizStore(restored_dir, 'tenant-b')
    assert getattr(exc.value, 'code', '') == 'biz_tenant_mismatch'


def test_newer_db_is_read_forward_compatible(store_a, env):
    store_a.create('job', {'title': 'x'}, by='test')
    store_a.close()
    conn = sqlite3.connect(str(env['a'] / 'business.db'))
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    from services.biz.store import BizStore
    s = BizStore(env['a'], 'tenant-a')
    try:
        assert s.engine_too_old and len(s.list('job')['records']) == 1
        with pytest.raises(Exception) as exc:
            s.create('job', {'title': 'x', 'x_from_the_future': 1}, by='test')
        assert exc.value.code == 'engine_too_old'
    finally:
        s.close()
