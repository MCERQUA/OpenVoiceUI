"""POST /api/forms/<form_id>: anonymous canvas-page form capture into a private store (SEC-062).

The cases come from the brief's guard list, not from the implementation: unknown field,
oversize, honeypot, rate limit, bad origin, anonymous read 401, agent-key read 200, path
traversal in form_id, concurrent appends keep every row, the store is not reachable via
/uploads/. Every test runs the REAL login gate (Clerk key set, so app.require_auth is
registered) with the store in a tmp db dir laid out like a tenant container:
  <tmp>/runtime/uploads   (served at /uploads/)
  <tmp>/runtime/db        (BIZ_DB_DIR: business.db's dir; the forms store is <db>/forms/)
"""

import json
import logging
import multiprocessing
import os
import threading

import pytest

import routes.forms as forms

AGENT_KEY = 'agent-key-forms-test'
HOST = 'tenant.example.test'
ORIGIN = f'https://{HOST}'

REG_DEF = {
    'label': 'Group registration',
    'enabled': True,
    'fields': {
        'name': {'type': 'string', 'label': 'Full name', 'required': True, 'max_length': 120},
        'phone': {'type': 'phone', 'label': 'Mobile', 'required': True, 'max_length': 40},
        'email': {'type': 'email', 'label': 'Email', 'required': True, 'max_length': 254},
        'timezone': {'type': 'enum', 'label': 'Time zone', 'required': True,
                     'values': ['America/New_York', 'America/Chicago', 'America/Denver',
                                'America/Phoenix', 'America/Los_Angeles']},
    },
    'honeypot': 'company_website',
    'max_bytes': 4096,
}
GOOD = {'name': 'Pat Rivers', 'phone': '+1 (602) 555-0142', 'email': 'pat@example.com',
        'timezone': 'America/Phoenix'}


@pytest.fixture
def env(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    uploads = runtime / 'uploads'
    db = runtime / 'db'
    uploads.mkdir(parents=True)
    db.mkdir(parents=True)
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_forms')
    monkeypatch.setenv('AGENT_API_KEY', AGENT_KEY)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_DB_DIR', str(db))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    import routes.static_files as sf
    monkeypatch.setattr(sf, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(sf, 'UPLOADS_SHADOW_LOG', uploads / '.uploads-auth-shadow.jsonl')
    monkeypatch.setattr(sf, 'UPLOADS_REQUIRE_AUTH', True)
    monkeypatch.setattr(sf, 'UPLOADS_AUTH_MODE', 'shadow')     # the fleet default: served to anyone
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: None)   # anonymous unless a test says so
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: None)
    forms._reset_rate_limits_for_tests()
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    app.register_blueprint(forms.forms_bp)
    if 'static_files' not in app.blueprints:
        app.register_blueprint(sf.static_files_bp)
    c = app.test_client()
    yield {'client': c, 'db': db, 'uploads': uploads, 'runtime': runtime, 'app': app}
    forms._reset_rate_limits_for_tests()


def _agent():
    return {'X-Agent-Key': AGENT_KEY}


def _put_def(c, form_id='group-reg', body=None):
    r = c.put(f'/api/forms/{form_id}/def', json=body if body is not None else REG_DEF,
              headers=_agent(), base_url=ORIGIN)
    assert r.status_code in (200, 201), r.get_json()
    return r


def _post(c, form_id='group-reg', data=None, *, ip='203.0.113.7', origin=ORIGIN, referer=None,
          as_form=False, headers=None):
    h = {'CF-Connecting-IP': ip}
    if origin is not None:
        h['Origin'] = origin
    if referer is not None:
        h['Referer'] = referer
    h.update(headers or {})
    payload = GOOD if data is None else data
    if as_form:
        return c.post(f'/api/forms/{form_id}', data=payload, headers=h, base_url=ORIGIN)
    return c.post(f'/api/forms/{form_id}', json=payload, headers=h, base_url=ORIGIN)


def _rows(env, form_id='group-reg'):
    p = env['db'] / 'forms' / f'{form_id}.jsonl'
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# ── happy path ───────────────────────────────────────────────────────────────
def test_accepts_a_valid_submission_json_and_form_encoded(env):
    c = env['client']
    _put_def(c)
    r = _post(c)
    assert r.status_code == 201 and r.get_json() == {'ok': True}
    r = _post(c, data=dict(GOOD, name='Sam Lee'), as_form=True)
    assert r.status_code == 201
    rows = _rows(env)
    assert [x['data']['name'] for x in rows] == ['Pat Rivers', 'Sam Lee']
    assert rows[0]['tenant'] == 'tenant-a' and rows[0]['source'] == {'channel': 'web', 'ref': 'form:group-reg'}
    st = os.stat(env['db'] / 'forms' / 'group-reg.jsonl')
    assert oct(st.st_mode & 0o777) == '0o600'
    assert oct(os.stat(env['db'] / 'forms').st_mode & 0o777) == '0o700'


# ── 1. unknown field ─────────────────────────────────────────────────────────
def test_unknown_field_rejected(env):
    c = env['client']
    _put_def(c)
    r = _post(c, data=dict(GOOD, ssn='123-45-6789'))
    assert r.status_code == 400
    body = r.get_json()
    assert body['code'] == 'unknown_fields' and body['fields'] == ['ssn']
    assert '123-45-6789' not in r.get_data(as_text=True)          # names only, never values
    assert _rows(env) == []


def test_missing_required_and_bad_type_rejected(env):
    c = env['client']
    _put_def(c)
    r = _post(c, data={k: v for k, v in GOOD.items() if k != 'phone'})
    assert r.status_code == 400 and r.get_json()['fields'] == {'phone': 'required'}
    r = _post(c, data=dict(GOOD, email='not-an-email', timezone='Mars/Olympus'))
    assert r.status_code == 400
    assert set(r.get_json()['fields']) == {'email', 'timezone'}
    r = _post(c, data=dict(GOOD, name={'nested': 'x'}))
    assert r.status_code == 400 and r.get_json()['fields'] == {'name': 'must be text'}
    assert _rows(env) == []


# ── 2. oversize ──────────────────────────────────────────────────────────────
def test_oversize_rejected(env):
    c = env['client']
    _put_def(c, body=dict(REG_DEF, max_bytes=256))
    r = _post(c, data=dict(GOOD, name='x' * 100, email='a' * 150 + '@example.com'))
    assert r.status_code == 413 and r.get_json()['code'] == 'too_large'
    assert _rows(env) == []


def test_field_over_max_length_rejected(env):
    c = env['client']
    _put_def(c)
    r = _post(c, data=dict(GOOD, name='x' * 121))
    assert r.status_code == 400 and 'longer than 120' in r.get_json()['fields']['name']
    assert _rows(env) == []


# ── 3. honeypot ──────────────────────────────────────────────────────────────
def test_honeypot_dropped(env):
    c = env['client']
    _put_def(c)
    r = _post(c, data=dict(GOOD, company_website='http://spam.example'))
    assert r.status_code == 201 and r.get_json() == {'ok': True}     # the bot sees success
    assert _rows(env) == []                                          # nothing stored
    r = _post(c, data=dict(GOOD, company_website=''))                # a real browser sends it empty
    assert r.status_code == 201 and len(_rows(env)) == 1


# ── 4. rate limit ────────────────────────────────────────────────────────────
def test_rate_limit_per_ip_per_minute(env):
    c = env['client']
    _put_def(c)
    codes = [_post(c, ip='198.51.100.9').status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]
    assert _post(c, ip='198.51.100.10').status_code == 201          # another visitor is not blocked
    assert len(_rows(env)) == 6


def test_rate_limit_per_day_and_form_ceiling(env, monkeypatch):
    c = env['client']
    _put_def(c, body=dict(REG_DEF, rate_limit={'per_minute': 60, 'per_day': 3, 'form_per_day': 5}))
    assert [_post(c, ip='192.0.2.1').status_code for _ in range(4)] == [201, 201, 201, 429]
    # a spoofed-IP spray still hits the per-form ceiling (5 stored in total)
    codes = [_post(c, ip=f'192.0.2.{i}').status_code for i in range(10, 14)]
    assert codes == [201, 201, 429, 429]
    assert len(_rows(env)) == 5


# ── 5. bad origin ────────────────────────────────────────────────────────────
@pytest.mark.parametrize('origin,referer', [
    ('https://evil.example', None),          # cross-site browser POST (the app CSRF guard also refuses)
    (None, None),                            # no Origin, no Referer: the app guard lets it pass, ours does not
    (None, 'https://evil.example/page'),     # Referer from another site, no Origin
    ('null', None),                          # sandboxed iframe / file:// page
])
def test_bad_origin_rejected(env, origin, referer):
    c = env['client']
    _put_def(c)
    r = _post(c, origin=origin, referer=referer)
    assert r.status_code == 403
    assert _rows(env) == []


def test_same_host_referer_without_origin_is_accepted(env):
    c = env['client']
    _put_def(c)
    r = _post(c, origin=None, referer=f'{ORIGIN}/pages/group-registration.html')
    assert r.status_code == 201 and len(_rows(env)) == 1


# ── unknown / disabled form ──────────────────────────────────────────────────
def test_unknown_and_disabled_form_404(env):
    c = env['client']
    assert _post(c, form_id='nope').status_code == 404
    _put_def(c, body=dict(REG_DEF, enabled=False))
    r = _post(c)
    assert r.status_code == 404 and r.get_json()['code'] == 'form_not_found'
    assert _rows(env) == []


def test_definition_from_another_tenant_is_not_served(env, monkeypatch):
    c = env['client']
    _put_def(c)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-b')        # the store copied into another cell by mistake
    assert _post(c).status_code == 404
    assert c.get('/api/forms/group-reg', headers=_agent(), base_url=ORIGIN).status_code == 404


# ── 6. anonymous GET 401 (both walls) ────────────────────────────────────────
def test_anonymous_reads_and_definition_writes_401(env):
    c = env['client']
    _put_def(c)
    _post(c)
    for method, path in [('GET', '/api/forms/group-reg'), ('GET', '/api/forms/group-reg/def'),
                         ('GET', '/api/forms'), ('PUT', '/api/forms/group-reg/def')]:
        r = c.open(path, method=method, json=REG_DEF if method == 'PUT' else None, base_url=ORIGIN)
        assert r.status_code == 401, (method, path)
        assert 'Pat Rivers' not in r.get_data(as_text=True)
        assert r.get_json().get('accepted_credentials'), 'the app gate (wall 1) must refuse it'


def test_anonymous_read_401_even_without_the_app_gate(env, monkeypatch):
    """Wall 2: the handler re-checks the caller, so opening the gate by mistake never opens the rows."""
    from flask import Flask
    _put_def(env['client'])
    _post(env['client'])
    bare = Flask('bare')
    bare.register_blueprint(forms.forms_bp)
    c = bare.test_client()
    for path in ('/api/forms/group-reg', '/api/forms/group-reg/def', '/api/forms'):
        r = c.get(path, base_url=ORIGIN)
        assert r.status_code == 401, path
        assert 'Pat Rivers' not in r.get_data(as_text=True)


# ── 7. agent-key GET 200 ─────────────────────────────────────────────────────
def test_agent_key_reads_rows_and_definition(env):
    c = env['client']
    _put_def(c)
    _post(c)
    r = c.get('/api/forms/group-reg', headers=_agent(), base_url=ORIGIN)
    assert r.status_code == 200
    body = r.get_json()
    assert body['count'] == 1 and body['rows'][0]['data'] == GOOD
    r = c.get('/api/forms/group-reg/def', headers=_agent(), base_url=ORIGIN)
    assert r.status_code == 200 and r.get_json()['fields']['timezone']['type'] == 'enum'
    assert r.get_json()['updated_by'] == 'agent'
    r = c.get('/api/forms', headers=_agent(), base_url=ORIGIN)
    assert r.status_code == 200 and r.get_json()['forms'] == [
        {'form_id': 'group-reg', 'enabled': True, 'label': 'Group registration', 'count': 1}]


def test_wrong_agent_key_401(env):
    c = env['client']
    _put_def(c)
    r = c.get('/api/forms/group-reg', headers={'X-Agent-Key': 'wrong'}, base_url=ORIGIN)
    assert r.status_code == 401


def test_bad_definitions_refused(env):
    c = env['client']
    for bad in [
        dict(REG_DEF, fields={'name': {'type': 'file'}}),
        dict(REG_DEF, honeypot='name'),
        dict(REG_DEF, fields={'Name': {'type': 'string'}}),
        dict(REG_DEF, fields={'tz': {'type': 'enum'}}),
        dict(REG_DEF, max_bytes=10_000_000),
        dict(REG_DEF, surprise=True),
        dict(REG_DEF, fields={'name': {'type': 'string', 'max_length': 5000}}),
    ]:
        r = c.put('/api/forms/x/def', json=bad, headers=_agent(), base_url=ORIGIN)
        assert r.status_code == 422, bad
    assert not (env['db'] / 'forms' / 'x.def.json').exists()


def test_multipart_and_card_numbers_refused(env):
    c = env['client']
    _put_def(c)
    r = c.post('/api/forms/group-reg', data=dict(GOOD), content_type='multipart/form-data',
               headers={'Origin': ORIGIN}, base_url=ORIGIN)
    assert r.status_code == 415
    r = _post(c, data=dict(GOOD, name='4111 1111 1111 1111'))
    assert r.status_code == 422 and r.get_json()['code'] == 'card_number_refused'
    assert _rows(env) == []


# ── 8. path traversal in form_id ─────────────────────────────────────────────
@pytest.mark.parametrize('form_id', [
    '..', '%2e%2e', '.hidden', 'a.b', '..%2f..%2fetc%2fpasswd', '%2e%2e%2fdb', 'UPPER', 'a' * 65,
    'x%00y', 'group-reg.def', 'name.jsonl',
])
def test_path_traversal_in_form_id_rejected(env, form_id):
    c = env['client']
    _put_def(c)
    r = _post(c, form_id=form_id)
    assert r.status_code in (404, 401, 405), r.status_code
    assert r.status_code != 201
    r = c.put(f'/api/forms/{form_id}/def', json=REG_DEF, headers=_agent(), base_url=ORIGIN)
    assert r.status_code in (404, 405)
    assert sorted(p.name for p in (env['db'] / 'forms').iterdir()) == ['group-reg.def.json']
    assert sorted(p.name for p in env['db'].iterdir()) == ['forms']
    assert list(env['runtime'].iterdir()) and not any(p.name.endswith('.jsonl') for p in env['runtime'].iterdir())


@pytest.mark.parametrize('form_id', ['../db/x', '../../etc/passwd', 'forms/../x', '..', '.def'])
def test_traversal_refused_by_the_handler_itself(env, form_id):
    """Werkzeug's <form_id> converter stops '/', but the handlers must not rely on it (a future
    <path:form_id> converter, or a direct call): each view refuses the id as 'no such form'."""
    app = env['app']
    _put_def(env['client'])
    with app.test_request_context(f'/api/forms/x', method='POST', json=GOOD, base_url=ORIGIN,
                                  headers={'Origin': ORIGIN, 'X-Agent-Key': AGENT_KEY}):
        r = forms.submit_form(form_id)
        assert r[1] == 404 and r[0].get_json()['code'] == 'form_not_found'
    with app.test_request_context(f'/api/forms/x/def', method='PUT', json=REG_DEF, base_url=ORIGIN,
                                  headers={'X-Agent-Key': AGENT_KEY}):
        r = forms.put_definition(form_id)
        assert r[1] == 404
    with app.test_request_context(f'/api/forms/x', method='GET', base_url=ORIGIN,
                                  headers={'X-Agent-Key': AGENT_KEY}):
        assert forms.list_submissions(form_id)[1] == 404
    assert sorted(p.name for p in env['db'].iterdir()) == ['forms']


def test_form_id_regex_unit():
    for bad in ('..', '.', '', 'a/b', 'a\\b', 'a.b', '-lead', '_x', 'A', 'a b', 'a\x00', 'a' * 65, 'é'):
        assert not forms.FORM_ID_RE.match(bad), bad
    for good in ('group-reg', 'a', '0', 'waitlist_2026', 'a' * 64):
        assert forms.FORM_ID_RE.match(good), good


def test_symlinked_forms_dir_refused(env, tmp_path):
    """The db mount is world-writable on the host: a planted symlink must never be written through."""
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    os.symlink(elsewhere, env['db'] / 'forms')
    r = env['client'].put('/api/forms/group-reg/def', json=REG_DEF, headers=_agent(), base_url=ORIGIN)
    assert r.status_code == 503
    assert list(elsewhere.iterdir()) == []


# ── 9. concurrent appends keep every row ─────────────────────────────────────
def test_concurrent_http_appends_keep_every_row(env):
    c = env['client']
    _put_def(c, body=dict(REG_DEF, rate_limit={'per_minute': 60, 'per_day': 1000, 'form_per_day': 10000}))
    app = env['app']
    errors, n_threads, per_thread = [], 8, 25

    def worker(t):
        cl = app.test_client()
        for i in range(per_thread):
            r = _post(cl, data=dict(GOOD, name=f'T{t} N{i}'), ip=f'198.18.{t}.{i}')
            if r.status_code != 201:
                errors.append(r.status_code)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    rows = _rows(env)                                  # every line parses: no torn/interleaved rows
    assert len(rows) == n_threads * per_thread
    assert len({r['id'] for r in rows}) == len(rows)
    assert {r['data']['name'] for r in rows} == {f'T{t} N{i}' for t in range(n_threads) for i in range(per_thread)}


def _proc_append(db, n, tag):
    os.environ['BIZ_DB_DIR'] = db
    os.environ['JAMBOT_TENANT'] = 'tenant-a'
    for i in range(n):
        forms.append_row('group-reg', {'id': f'{tag}-{i}', 'tenant': 'tenant-a', 'data': {'pad': 'x' * 3000}})


def test_concurrent_process_appends_keep_every_row(env):
    _put_def(env['client'])
    ctx = multiprocessing.get_context('fork')
    procs = [ctx.Process(target=_proc_append, args=(str(env['db']), 100, f'p{k}')) for k in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert all(p.exitcode == 0 for p in procs)
    rows = _rows(env)
    assert len(rows) == 400 and len({r['id'] for r in rows}) == 400


# ── 10. the store is not reachable via /uploads/ ─────────────────────────────
def test_store_not_reachable_via_uploads(env):
    c = env['client']
    _put_def(c)
    marker = 'Marker Zebra-7731'
    assert _post(c, data=dict(GOOD, name=marker)).status_code == 201
    assert any(r['data']['name'] == marker for r in _rows(env))
    probes = [
        '/uploads/forms/group-reg.jsonl', '/uploads/group-reg.jsonl',
        '/uploads/../db/forms/group-reg.jsonl', '/uploads/%2e%2e/db/forms/group-reg.jsonl',
        '/uploads/..%2fdb%2fforms%2fgroup-reg.jsonl', '/uploads/%2e%2e%2fdb%2fforms%2fgroup-reg.jsonl',
        '/uploads/db/forms/group-reg.jsonl', '/uploads/forms/group-reg.def.json',
    ]
    for p in probes:
        r = c.get(p, base_url=ORIGIN)
        assert marker not in r.get_data(as_text=True), p
        assert r.status_code != 200 or b'Zebra' not in r.data, p
    r = c.get('/api/uploads', headers=_agent(), base_url=ORIGIN)
    assert 'group-reg' not in r.get_data(as_text=True)


def test_store_refused_inside_a_served_dir(env, monkeypatch):
    """Point the db dir at uploads/ (a misconfigured BIZ_DB_DIR): the store refuses, nothing lands there."""
    monkeypatch.setenv('BIZ_DB_DIR', str(env['uploads']))
    c = env['client']
    r = c.put('/api/forms/group-reg/def', json=REG_DEF, headers=_agent(), base_url=ORIGIN)
    assert r.status_code == 503 and r.get_json()['code'] == 'forms_storage_served'
    assert not (env['uploads'] / 'forms').exists()


def test_hosted_without_durable_db_dir_503(env, monkeypatch):
    monkeypatch.delenv('BIZ_DB_DIR')
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    r = env['client'].put('/api/forms/group-reg/def', json=REG_DEF, headers=_agent(), base_url=ORIGIN)
    assert r.status_code == 503 and r.get_json()['code'] == 'forms_storage_unavailable'


# ── logs carry no PII ────────────────────────────────────────────────────────
def test_logs_carry_no_values(env, caplog):
    c = env['client']
    _put_def(c)
    with caplog.at_level(logging.DEBUG):
        _post(c)
        _post(c, data=dict(GOOD, ssn='999-00-1234'))
        _post(c, data=dict(GOOD, company_website='spam'))
    text = caplog.text
    for v in list(GOOD.values()) + ['999-00-1234', '203.0.113.7', 'pat@example.com', '555-0142']:
        assert v not in text, v
    assert 'form_id=group-reg status=201' in text and 'status=400' in text
