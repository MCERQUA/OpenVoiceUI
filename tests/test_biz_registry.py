"""JamBot client backend, phase 2: the company-system page registry, the menu shell model,
serve-time shell injection and dev sandboxes (services/biz/pages.py, services/biz/sandbox.py,
routes/biz.py, routes/canvas.py, jb/v1/shell.js).

Cases come from the design's phase-2 acceptance list and section 9 (docs/jambot/
client-backend-design.md), not from the implementation:
  registry is per tenant (server-side, the tenant's own business.db), first_of_system on the
  first registration only; stale If-Match -> 409; unknown keys / section / collection -> 422;
  the file must be a bare page file of THIS tenant (no paths, no URLs, no other tenant);
  only sections with a page render; a registered page is served with the shell, an
  unregistered or shell=off page is not, and the page file on disk is byte-identical;
  a sandbox key reaches only its own sandbox and nothing outside /api/biz/.
"""
import hashlib
import shutil

import pytest

OWNER = 'user_owner_a'
KEY_A = 'agent-key-tenant-a'
SBX_KEY_ONE = 'sbx-one-' + 'k' * 40
SBX_KEY_TWO = 'sbx-two-' + 'q' * 40
SRC = {'channel': 'voice', 'ref': 'owner asked for a quote tool'}
B = '/api/biz/v1'
AG = {'X-Agent-Key': KEY_A}


@pytest.fixture()
def dirs(tmp_path):
    d = {'a': tmp_path / 'clients' / 'tenant-a' / 'db', 'b': tmp_path / 'clients' / 'tenant-b' / 'db',
         'pages_a': tmp_path / 'clients' / 'tenant-a' / 'canvas-pages',
         'pages_b': tmp_path / 'clients' / 'tenant-b' / 'canvas-pages'}
    for k in ('pages_a', 'pages_b'):
        d[k].mkdir(parents=True)
    for name in ('quote-tool.html', 'job-board.html', 'home-dash.html', 'crew-log.html', 'costs.html',
                 'stock.html', 'reports.html'):
        (d['pages_a'] / name).write_text(f'<html><head><title>{name}</title></head><body><h1>{name}</h1>'
                                         '<nav id="old-nav">old</nav></body></html>')
    (d['pages_b'] / 'b-secret.html').write_text('<html><body>tenant b</body></html>')
    return d


def _env(monkeypatch, dirs, tenant='tenant-a', db='a', pages='pages_a'):
    import routes.canvas as canvas
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', tenant)
    monkeypatch.setenv('BIZ_DB_DIR', str(dirs[db]))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.delenv('BIZ_SANDBOXES', raising=False)
    monkeypatch.setattr(canvas, 'CANVAS_PAGES_DIR', dirs[pages])
    monkeypatch.setattr(canvas, 'CANVAS_REQUIRE_AUTH', True)
    monkeypatch.setattr(canvas, 'load_canvas_manifest', lambda: {'pages': {}})


def _make_client():
    from app import create_app
    from routes.biz import biz_bp, jb_sdk_bp
    import routes.canvas as canvas
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    for bp in (biz_bp, jb_sdk_bp, canvas.canvas_bp):
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)
    return app, app.test_client()


@pytest.fixture()
def app_client(dirs, monkeypatch):
    _env(monkeypatch, dirs)
    app, client = _make_client()
    yield app, client
    for k, store in list(app.extensions.get('biz', {}).items()):
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


def _reg(client, headers=None, **page):
    body = {'file': 'quote-tool.html', 'label': 'Quotes', 'section': 'sales'}
    body.update(page)
    return client.post(f'{B}/registry/pages', json=body, headers=headers or {})


# ── registry: positive ──────────────────────────────────────────────────────
def test_register_list_and_first_of_system(client, owner):
    r = _reg(client, kind='tool', collections=['estimate', 'contact'], verb='New quote',
             verb_href='quote-tool.html#new', tab=True)
    assert r.status_code == 201, r.get_json()
    p = r.get_json()
    assert p['id'] == 'quote-tool' and p['first_of_system'] is True and p['version'] == 1
    assert p['created_by'] == f'clerk:{OWNER}' and p['url'] == '/pages/quote-tool.html'
    r2 = _reg(client, file='job-board.html', label='Jobs', section='jobs')
    assert r2.status_code == 201 and r2.get_json()['first_of_system'] is False
    listed = client.get(f'{B}/registry/pages').get_json()
    assert [x['id'] for x in listed['pages']] == ['quote-tool', 'job-board']   # taxonomy order: sales < jobs
    assert all(x['exists'] for x in listed['pages'])


def test_registry_is_server_side_in_the_tenants_own_db(client, owner, dirs):
    import sqlite3
    assert _reg(client).status_code == 201
    conn = sqlite3.connect(str(dirs['a'] / 'business.db'))
    try:
        rows = conn.execute('SELECT id, tenant FROM app_pages').fetchall()
        meta = conn.execute("SELECT value FROM meta WHERE key = 'tenant'").fetchone()[0]
    finally:
        conn.close()
    assert rows == [('quote-tool', 'tenant-a')] and meta == 'tenant-a'


def test_update_with_if_match_and_stale_version_409(client, owner):
    p = _reg(client).get_json()
    r = client.patch(f'{B}/registry/pages/quote-tool', json={'label': 'Estimates'},
                     headers={'If-Match': str(p['version'])})
    assert r.status_code == 200 and r.get_json()['label'] == 'Estimates' and r.get_json()['version'] == 2
    stale = client.patch(f'{B}/registry/pages/quote-tool', json={'label': 'Bids'}, headers={'If-Match': '1'})
    assert stale.status_code == 409 and stale.get_json()['code'] == 'version_conflict'
    assert stale.get_json()['current']['label'] == 'Estimates'
    missing = client.patch(f'{B}/registry/pages/quote-tool', json={'label': 'Bids'})
    assert missing.status_code == 428


def test_archive_hides_from_menu_and_unarchive_restores(client, owner):
    _reg(client)
    _reg(client, file='job-board.html', label='Jobs', section='jobs')
    assert client.post(f'{B}/registry/pages/quote-tool/archive', json={}).status_code == 200
    shell = client.get(f'{B}/registry/shell').get_json()
    assert [s['id'] for s in shell['sections']] == ['jobs']
    assert len(client.get(f'{B}/registry/pages?archived=1').get_json()['pages']) == 2
    again = _reg(client)
    assert again.status_code == 409 and again.get_json()['code'] == 'page_registered'
    assert client.post(f'{B}/registry/pages/quote-tool/unarchive', json={}).status_code == 200
    assert [s['id'] for s in client.get(f'{B}/registry/shell').get_json()['sections']] == ['sales', 'jobs']


def test_shell_model_only_sections_with_pages_and_max_five_tabs(client, owner):
    pages = [('home-dash.html', 'Overview', 'home'), ('quote-tool.html', 'Quotes', 'sales'),
             ('job-board.html', 'Jobs', 'jobs'), ('crew-log.html', 'Crew log', 'jobs'),
             ('costs.html', 'Costs', 'money'), ('stock.html', 'Materials', 'stock'),
             ('reports.html', 'Reports', 'reports')]
    for f, label, sec in pages:
        assert _reg(client, file=f, label=label, section=sec, tab=True).status_code == 201
    m = client.get(f'{B}/registry/shell?page=quote-tool').get_json()
    assert [s['id'] for s in m['sections']] == ['home', 'sales', 'jobs', 'money', 'stock', 'reports']
    assert len(m['tabs']) <= 5 and m['tabs'][0] == 'home-dash' and m['more'] is True
    assert m['count'] == 7 and m['page'] == 'quote-tool'
    assert 'menu' in m['icons'] and 'clipboard' in m['icons'] and 'home' in m['icons']


def test_small_system_puts_every_page_on_the_bar(client, owner):
    _reg(client)
    m = client.get(f'{B}/registry/shell').get_json()
    assert m['tabs'] == ['quote-tool'] and m['more'] is False


def test_shell_theme_uses_company_colours_and_drops_unsafe_values(client, owner):
    r = client.post(f'{B}/company', json={'data': {'name': 'Example Insulation', 'brand': {
        'colors': {'primary': '#0f766e', 'background': 'red;}body{display:none', 'text': '#111111'},
        'fonts': {'body': 'Inter, sans-serif', 'display': 'x;}</style><script>'},
        'logo': 'https://evil.example/logo.png'}}})
    assert r.status_code == 201, r.get_json()
    _reg(client)
    m = client.get(f'{B}/registry/shell').get_json()
    assert m['theme']['brand'] == '#0f766e' and m['theme']['text'] == '#111111'
    assert 'bg' not in m['theme'] or m['theme']['bg'].startswith('#')
    assert m['theme'].get('font-body') == 'Inter, sans-serif' and 'font-display' not in m['theme']
    assert m['company'] == {'name': 'Example Insulation', 'logo': None}


def test_agent_registration_records_provenance(client, anon):
    r = _reg(client, headers=AG)
    assert r.status_code == 422 and r.get_json()['code'] == 'source_required'
    r = client.post(f'{B}/registry/pages', headers=AG, json={
        'file': 'quote-tool.html', 'label': 'Quotes', 'section': 'sales', 'source': SRC})
    assert r.status_code == 201
    p = r.get_json()
    assert p['created_by'] == 'agent:tenant-a' and p['source']['channel'] == 'voice'
    tl = client.get(f'{B}/events?since=0', headers=AG).get_json()
    assert any(e.get('type') == 'page_registered' for e in tl['events'])


# ── registry: negative ──────────────────────────────────────────────────────
def test_unauthenticated_registry_is_401(client, anon):
    assert client.get(f'{B}/registry/pages').status_code == 401
    assert _reg(client).status_code == 401
    assert client.get(f'{B}/registry/shell').status_code == 401


def test_wrong_agent_key_is_403(client, anon):
    r = _reg(client, headers={'X-Agent-Key': 'agent-key-tenant-b'})
    assert r.status_code == 403


@pytest.mark.parametrize('bad', [
    {'section': 'marketing'}, {'kind': 'game'}, {'shell': 'sidebar'}, {'label': ''}, {'label': '<b>x</b>'},
    {'label': 'x' * 61}, {'order': -1}, {'order': 'first'}, {'tab': 'yes'}, {'icon': 'rocket-emoji'},
    {'verb_href': 'https://evil.example/x.html'}, {'verb_href': '../tenant-b/x.html'},
    {'legacy_nav': '#nav{}'}, {'legacy_nav': '</style>'}, {'collections': 'job'},
    {'tenant_owner': 'x'},
])
def test_bad_fields_are_422(client, owner, bad):
    r = _reg(client, **bad)
    assert r.status_code in (400, 422), (bad, r.get_json())
    assert client.get(f'{B}/registry/pages').get_json()['count'] == 0


def test_unknown_collection_is_422(client, owner):
    r = _reg(client, collections=['job', 'spaceship'])
    assert r.status_code == 422 and r.get_json()['code'] == 'unknown_collection'


@pytest.mark.parametrize('f', ['../tenant-b/canvas-pages/b-secret.html', '/mnt/clients/tenant-b/b.html',
                               'sandbox/one/x.html', 'https://evil.example/x.html', 'C:\\x.html',
                               'x.htm', '.hidden.html', ''])
def test_page_file_must_be_a_bare_file_of_this_tenant(client, owner, f):
    r = _reg(client, file=f)
    assert r.status_code == 422, (f, r.get_json())


def test_page_file_must_exist(client, owner):
    r = _reg(client, file='not-built-yet.html')
    assert r.status_code == 422 and r.get_json()['code'] == 'page_not_found'


def test_tenant_param_refused_on_registry(client, owner):
    r = client.post(f'{B}/registry/pages?tenant=tenant-b', json={'file': 'quote-tool.html', 'label': 'Q',
                                                                 'section': 'sales'})
    assert r.status_code == 400 and r.get_json()['code'] == 'tenant_param_refused'
    r = client.post(f'{B}/registry/pages', json={'file': 'quote-tool.html', 'label': 'Q', 'section': 'sales',
                                                  'tenant': 'tenant-b'})
    assert r.status_code == 400


def test_registry_rows_cannot_be_deleted_or_cross_tenant(client, owner, dirs):
    import sqlite3
    _reg(client)
    conn = sqlite3.connect(str(dirs['a'] / 'business.db'))
    try:
        with pytest.raises(sqlite3.DatabaseError, match='never deleted'):
            conn.execute('DELETE FROM app_pages')
        with pytest.raises(sqlite3.DatabaseError, match='tenant does not match'):
            conn.execute("INSERT INTO app_pages(id, tenant, file, label, section, created_at, created_by, "
                         "updated_at, updated_by) VALUES ('x', 'tenant-b', 'x.html', 'X', 'home', 'n', 'b', 'n', 'b')")
    finally:
        conn.close()


def test_cross_tenant_registry_is_invisible(dirs, monkeypatch):
    """Tenant A registers; the same image running as tenant B sees an empty company system."""
    _as(monkeypatch, OWNER)
    _env(monkeypatch, dirs)
    app_a, ca = _make_client()
    assert _reg(ca).status_code == 201
    app_a.extensions['biz']['store'].close()
    _env(monkeypatch, dirs, tenant='tenant-b', db='b', pages='pages_b')
    app_b, cb = _make_client()
    try:
        assert cb.get(f'{B}/registry/pages').get_json()['count'] == 0
        assert cb.get(f'{B}/registry/pages/quote-tool').status_code == 404
        # B cannot register A's page file: it does not exist in B's own canvas folder
        assert _reg(cb).get_json()['code'] == 'page_not_found'
    finally:
        app_b.extensions['biz']['store'].close()


def test_copied_db_of_another_tenant_is_refused(dirs, monkeypatch):
    _as(monkeypatch, OWNER)
    _env(monkeypatch, dirs)
    app_a, ca = _make_client()
    assert _reg(ca).status_code == 201
    app_a.extensions['biz']['store'].close()
    dirs['b'].mkdir(parents=True, exist_ok=True)
    shutil.copy2(dirs['a'] / 'business.db', dirs['b'] / 'business.db')
    _env(monkeypatch, dirs, tenant='tenant-b', db='b', pages='pages_b')
    _, cb = _make_client()
    r = cb.get(f'{B}/registry/pages')
    assert r.status_code == 503 and r.get_json()['code'] == 'biz_tenant_mismatch'


# ── serve-time shell injection ──────────────────────────────────────────────
def _md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def test_registered_page_is_served_with_the_shell_and_file_is_unchanged(client, owner, dirs):
    before = _md5(dirs['pages_a'] / 'quote-tool.html')
    _reg(client, legacy_nav='#old-nav')
    html = client.get('/pages/quote-tool.html').get_data(as_text=True)
    assert 'src="/jb/v1/shell.js"' in html and 'data-page="quote-tool"' in html
    assert 'data-variant="office"' in html and 'data-legacy-nav="#old-nav"' in html
    assert 'src="/jb/v1/jb.js"' in html
    assert html.index('canvas-auth-bridge') < html.index('/jb/v1/shell.js') < html.index('</body>')
    assert _md5(dirs['pages_a'] / 'quote-tool.html') == before


def test_field_variant_is_injected(client, owner):
    _reg(client, file='crew-log.html', label='Crew log', section='jobs', shell='field')
    assert 'data-variant="field"' in client.get('/pages/crew-log.html').get_data(as_text=True)


def test_unregistered_off_and_archived_pages_get_no_shell(client, owner):
    _reg(client, file='job-board.html', label='Jobs', section='jobs', shell='off')
    _reg(client, file='costs.html', label='Costs', section='money')
    client.post(f'{B}/registry/pages/costs/archive', json={})
    for f in ('quote-tool.html', 'job-board.html', 'costs.html'):
        assert '/jb/v1/shell.js' not in client.get(f'/pages/{f}').get_data(as_text=True), f


def test_page_that_includes_the_shell_itself_is_not_doubled(client, owner, dirs):
    (dirs['pages_a'] / 'stock.html').write_text('<html><body><script src="/jb/v1/jb.js"></script>'
                                               '<script src="/jb/v1/shell.js"></script></body></html>')
    _reg(client, file='stock.html', label='Materials', section='stock')
    assert client.get('/pages/stock.html').get_data(as_text=True).count('/jb/v1/shell.js') == 1


def test_serving_a_page_never_creates_a_database(client, owner, dirs):
    assert client.get('/pages/quote-tool.html').status_code == 200
    assert not (dirs['a'] / 'business.db').exists()


def test_injection_escapes_selector_attribute(client, owner, dirs):
    _reg(client, legacy_nav='div[data-x="1"]')
    html = client.get('/pages/quote-tool.html').get_data(as_text=True)
    assert 'data-legacy-nav="div[data-x=&quot;1&quot;]"' in html


# ── static assets ───────────────────────────────────────────────────────────
@pytest.mark.parametrize('name,ctype', [('jb.js', 'javascript'), ('shell.js', 'javascript'),
                                        ('shell.css', 'text/css'), ('sdk.md', 'markdown')])
def test_assets_are_public_and_fixed(client, anon, name, ctype):
    r = client.get(f'/jb/v1/{name}')
    assert r.status_code == 200 and ctype in r.headers['Content-Type']


@pytest.mark.parametrize('name', ['../app.py', 'server.py', 'jb.js.bak', '%2e%2e%2fapp.py'])
def test_only_listed_assets_are_served(client, anon, name):
    assert client.get(f'/jb/v1/{name}').status_code == 404


def test_every_registry_icon_exists_and_sdk_documents_sections(client, anon):
    from services.biz.pages import SECTIONS, icon_paths
    icons = icon_paths()
    assert all(icon in icons for _, _, icon in SECTIONS)
    doc = client.get('/jb/v1/sdk.md').get_data(as_text=True)
    for sid, _, _ in SECTIONS:
        assert sid in doc


# ── dev sandboxes ───────────────────────────────────────────────────────────
@pytest.fixture()
def sbx(dirs, monkeypatch):
    _as(monkeypatch, None)
    _env(monkeypatch, dirs)
    monkeypatch.setenv('BIZ_SANDBOXES', 'one,two,weak')
    monkeypatch.setenv('BIZ_SANDBOX_KEY_ONE', SBX_KEY_ONE)
    monkeypatch.setenv('BIZ_SANDBOX_KEY_TWO', SBX_KEY_TWO)
    monkeypatch.setenv('BIZ_SANDBOX_KEY_WEAK', 'short')
    app, client = _make_client()
    yield client
    for store in list(app.extensions.get('biz', {}).values()):
        if hasattr(store, 'close'):
            store.close()


S1 = {'X-Agent-Key': SBX_KEY_ONE}
S2 = {'X-Agent-Key': SBX_KEY_TWO}
PAGE = '<html><head><title>t</title></head><body><h1>Quotes</h1></body></html>'


def test_sandbox_has_its_own_database(sbx, dirs):
    h = sbx.get(f'{B}/health', headers=S1).get_json()
    assert h['tenant'] == 'tenant-a-sbx-one' and h['sandbox'] == 'one'
    assert (dirs['a'] / 'sandboxes' / 'one' / 'business.db').is_file()
    r = sbx.post(f'{B}/contact', headers=S1, json={'data': {'name': 'Sample A'}, 'source': SRC})
    assert r.status_code == 201
    assert sbx.get(f'{B}/contact', headers=S2).get_json()['records'] == []
    assert sbx.get(f'{B}/contact', headers=AG).get_json()['records'] == []


def test_sandbox_key_cannot_reach_another_sandbox_or_the_tenant(sbx):
    r = sbx.get(f'{B}/contact', headers={**S1, 'X-Biz-Sandbox': 'two'})
    assert r.status_code == 403 and r.get_json()['code'] == 'sandbox_refused'
    r = sbx.get(f'{B}/contact', headers={**AG, 'X-Biz-Sandbox': 'one'})
    assert r.status_code == 400
    # outside /api/biz/ a sandbox key is no credential at all
    r = sbx.post('/api/canvas/pages', headers=S1, json={'filename': 'x.html', 'html': PAGE})
    assert r.status_code == 401 and r.get_json()['code'] == 'auth_required'


def test_weak_or_unlisted_sandbox_keys_are_refused(sbx):
    assert sbx.get(f'{B}/health', headers={'X-Agent-Key': 'short'}).status_code == 403


def test_owner_header_naming_a_sandbox_is_refused_when_sandboxes_are_off(client, owner):
    r = client.get(f'{B}/health', headers={'X-Biz-Sandbox': 'one'})
    assert r.status_code == 400 and r.get_json()['code'] == 'tenant_param_refused'


def test_sandbox_page_publish_register_and_serve(sbx, dirs, monkeypatch):
    r = sbx.put(f'{B}/dev/pages/quote-tool.html', headers=S1, json={'html': PAGE, 'source': SRC})
    assert r.status_code == 200 and r.get_json()['url'] == '/pages/sandbox/one/quote-tool.html'
    r = sbx.put(f'{B}/dev/pages/quote-tool.html', headers=S1, json={'html': PAGE + ' ', 'source': SRC})
    assert r.get_json()['previous_kept_as'].startswith('quote-tool.html.')
    assert len(list((dirs['pages_a'] / 'sandbox' / 'one' / '.history').iterdir())) == 1
    r = sbx.post(f'{B}/registry/pages', headers=S1, json={'file': 'quote-tool.html', 'label': 'Quotes',
                                                          'section': 'sales', 'source': SRC})
    assert r.status_code == 201 and r.get_json()['first_of_system'] is True
    assert r.get_json()['url'] == '/pages/sandbox/one/quote-tool.html'
    got = sbx.get(f'{B}/dev/pages/quote-tool.html', headers=S1).get_json()
    assert got['registered'] and 'data-sandbox="one"' in got['shell_injection']
    # the tenant's own registry is untouched, and a page of tenant A's own folder is not one of sandbox one's
    assert sbx.get(f'{B}/registry/pages', headers=AG).get_json()['count'] == 0
    r = sbx.post(f'{B}/registry/pages', headers=S1, json={'file': 'job-board.html', 'label': 'Jobs',
                                                          'section': 'jobs', 'source': SRC})
    assert r.get_json()['code'] == 'page_not_found'
    # served to the dev server's owner with the shell, bound to the sandbox
    _as(monkeypatch, OWNER)
    html = sbx.get('/pages/sandbox/one/quote-tool.html').get_data(as_text=True)
    assert 'data-sandbox="one"' in html and 'data-page="quote-tool"' in html
    m = sbx.get(f'{B}/registry/shell', headers={'X-Biz-Sandbox': 'one'}).get_json()
    assert m['sandbox'] == 'one' and m['base'] == '/pages/sandbox/one/'


def test_sandbox_publish_refusals(sbx, monkeypatch):
    assert sbx.put(f'{B}/dev/pages/x.html', headers=S1, json={'html': PAGE}).status_code == 422   # no source
    assert sbx.put(f'{B}/dev/pages/x.html', headers=AG, json={'html': PAGE, 'source': SRC}).status_code == 404
    for bad in ('..%2fx.html', 'x.js', '.x.html'):
        assert sbx.put(f'{B}/dev/pages/{bad}', headers=S1,
                       json={'html': PAGE, 'source': SRC}).status_code in (404, 422), bad
    _as(monkeypatch, OWNER)
    r = sbx.put(f'{B}/dev/pages/x.html', headers={'X-Biz-Sandbox': 'one'}, json={'html': PAGE, 'source': SRC})
    assert r.status_code == 403


def test_sandbox_pages_of_unknown_sandbox_get_no_shell(sbx, dirs, monkeypatch):
    other = dirs['pages_a'] / 'sandbox' / 'ghost'
    other.mkdir(parents=True)
    (other / 'quote-tool.html').write_text(PAGE)
    _as(monkeypatch, OWNER)
    assert '/jb/v1/shell.js' not in sbx.get('/pages/sandbox/ghost/quote-tool.html').get_data(as_text=True)


def test_phase1_database_migrates_to_the_registry_with_a_verified_backup(tmp_path, monkeypatch):
    """A tenant db written by the phase-1 engine (schema 1) gains app_pages; nothing is lost."""
    import services.biz.store as store_mod
    from services.biz.migrations import discover
    first_only = discover()[:1]
    monkeypatch.setattr(store_mod, 'discover', lambda: first_only)
    old = store_mod.BizStore(tmp_path / 'db', 'tenant-a')
    rec = old.create('contact', {'name': 'Sample Customer'}, by='local:owner')
    assert old.schema_version == 1
    old.close()
    monkeypatch.setattr(store_mod, 'discover', discover)
    new = store_mod.BizStore(tmp_path / 'db', 'tenant-a')
    try:
        assert new.schema_version == 2 and not new.read_only
        assert new.get('contact', rec['id'])['data']['name'] == 'Sample Customer'
        backups = list((tmp_path / 'db' / 'backups').glob('pre-migrate-1-2-*.db'))
        assert len(backups) == 1
        assert new.health()['registered_pages'] == 0
    finally:
        new.close()
