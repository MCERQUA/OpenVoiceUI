"""Business-data importer (services/biz/importer.py, POST /api/biz/v1/import).

Cases come from the design's migration rules (section 10) and the importer brief, not from the code:
  additive + idempotent (a re-run adds 0 rows and 0 activity; an archived import is never re-created);
  a dry run writes nothing and predicts exactly what a real run adds; a run for another tenant is refused;
  never invent data (page seed stock with only a voided count is not imported; a cost sheet whose lines
  do not reconcile becomes ONE confirmed-total line, never a guessed split); running totals are recomputed,
  not copied; the card-number wall still applies to imported text; the route is owner-only and a dry run
  unless dry_run=0. All data here is synthetic.
"""
import copy

import pytest

from services.biz import importer
from services.biz.store import BizStore

JOBS = 'openclaw/workspace/business/jobs.json'
FIELD = 'openclaw/workspace/field/field-data.json'
INV = 'openvoiceui/canvas-pages/_data/field-dock/inventory.json'
EXT = 'openvoiceui/canvas-pages/_data/field-dock/extractions.json'

SOURCES = {
    JOBS: {'jobs': [
        {'id': 'job-001', 'customer': 'Test Customer One', 'customer_phone': '(555) 010-0001', 'status': 'completed',
         'completed_date': '2026-09-24', 'revenue_billed': 1000.0, 'service_type': 'attic blow-in',
         'costs': {'diesel': 50.0, 'material': 200.0, 'labor_cost': 150.0, 'labor_hours': 3, 'labor_rate': 50,
                   'known_subtotal_excl_labor': 250.0},
         'profit_final': {'total_costs': 400.0, 'net_profit': 600.0}, 'notes': ['note a'],
         'photo_url': '/uploads/test-1.jpg', 'invoice': '9001'},
        {'id': 'job-002', 'customer': 'Test Customer Two', 'status': 'completed', 'completed_date': '2026-10-01',
         'revenue_billed': 2000.0,
         # sub-lines as read (300) do not match the confirmed written total (500): one roll-up line, no split
         'costs': {'gas': 100.0, 'manpower': {'hours': 4, 'rate': 50, 'cost': 200.0}, 'written_total': 500.0},
         'profit': {'net_profit': 1500.0}},
        {'id': 'job-003', 'customer': '', 'status': 'completed', 'revenue_billed': 5.0, 'costs': {}},
    ], 'running_totals': {'jobs': 2, 'revenue': 3000.0, 'net_profit': 2100.0}},
    FIELD: {'roster': [{'name': 'Crew Person', 'phone': '+15550100002', 'role': 'Sprayer'}],
            'material': [{'id': 1}], 'submissions': []},
    INV: {'items': [{'id': 1, 'name': 'Seed Kit', 'unit': 'kits', 'qty': 6, 'cost': 10},
                    {'id': 2, 'name': 'Counted Kit', 'cat': 'Foam', 'unit': 'kits', 'qty': 3, 'cost': 20}],
          'log': [{'id': 'L1', 'ts': '2026-11-18T18:29:00Z', 'who': 'Office', 'action': 'count', 'item': 'Seed Kit', 'qty': 6},
                  {'id': 'L2', 'ts': '2026-11-18T18:38:00Z', 'action': 'void', 'item': 'Seed Kit', 'voids': 'L1'},
                  {'id': 'L3', 'ts': '2026-11-19T09:00:00Z', 'who': 'Office', 'action': 'count', 'item': 'Counted Kit', 'qty': 3}]},
    EXT: {'images': {
        '/uploads/fd/a.jpg': {'image': '/uploads/fd/a.jpg', 'doc_type': 'work order', 'title': 'WO 1',
                              'extracted_by': 'reader-a', 'fields': [{'label': 'Invoice #', 'value': '9001'}],
                              'line_items': [], 'comparison': {'fields': {'0': 'agree'}, 'counts': {'agree': 1}},
                              'second_reader': {'model': 'reader-b', 'status': 'done'}},
        '/uploads/fd/b.jpg': {'image': '/uploads/fd/b.jpg', 'doc_type': 'receipt', 'title': 'Receipt',
                              'transcription': 'CARD 4111 1111 1111 1111 APPROVED'},
    }, 'failed': {}},
}


@pytest.fixture()
def store(tmp_path):
    s = BizStore(tmp_path / 'tenant-a' / 'db', 'tenant-a')
    yield s
    s.close()


def _activity(s):
    with s._read() as c:
        return c.execute('SELECT COUNT(*) FROM activity').fetchone()[0]


def _live(s, coll):
    return s.collection_counts().get(coll, {}).get('live', 0)


def test_import_maps_only_what_exists_and_rerun_adds_nothing(store):
    rep = importer.run(store, copy.deepcopy(SOURCES), dry_run=False, expect_tenant='tenant-a')
    created = {k: v['created'] for k, v in rep['collections'].items()}
    assert created == {'contact': 2, 'job': 2, 'cost_entry': 4, 'document': 2, 'invoice': 1, 'person': 1,
                       'product': 1, 'stock_move': 1}
    # job-003 has no customer: reported, never guessed
    assert any('without id or customer' in u['what'] for u in rep['unmapped'])
    # the seed item whose only count was voided is not business data
    assert any(u['what'] == 'item Seed Kit' for u in rep['unmapped'])
    # the card number in the receipt is refused by the backend's wall, the run continues
    assert [r['code'] for r in rep['rejects']] == ['card_number_refused']
    # running totals recomputed from records, not copied
    chk = rep['checks'][0]
    assert chk['recomputed']['revenue'] == 3000.0 and chk['recomputed']['net_profit'] == 2100.0
    assert chk['delta'] == {'jobs': 0, 'revenue': 0.0, 'net_profit': 0.0}

    before_act, before = _activity(store), store.collection_counts()
    rep2 = importer.run(store, copy.deepcopy(SOURCES), dry_run=False, expect_tenant='tenant-a')
    assert sum(v['created'] for v in rep2['collections'].values()) == 0
    assert _activity(store) == before_act and store.collection_counts() == before


def test_unreconciled_cost_sheet_is_one_confirmed_total_line(store):
    importer.run(store, copy.deepcopy(SOURCES), dry_run=False, expect_tenant='tenant-a')
    rows = store.list('cost_entry', limit=50)['records']
    by_desc = sorted((r['data']['kind'], r['data']['amount']) for r in rows)
    # job-001 reconciles (50+200+150 == 400): itemised; job-002 does not: one 500 line
    assert by_desc == sorted([('fuel', 50.0), ('material', 200.0), ('labor', 150.0), ('other', 500.0)])


def test_archived_import_is_never_recreated(store):
    importer.run(store, copy.deepcopy(SOURCES), dry_run=False, expect_tenant='tenant-a')
    person = store.list('person', limit=5)['records'][0]
    store.set_archived('person', person['id'], True, by='owner')
    importer.run(store, copy.deepcopy(SOURCES), dry_run=False, expect_tenant='tenant-a')
    assert _live(store, 'person') == 0


def test_dry_run_writes_nothing_and_predicts_the_real_run(store, tmp_path):
    before_act, before = _activity(store), store.collection_counts()
    dry = importer.run(store, copy.deepcopy(SOURCES), dry_run=True, expect_tenant='tenant-a')
    assert _activity(store) == before_act and store.collection_counts() == before
    assert not (store.db_dir / 'schema' / 'custom' / 'job.json').exists()
    real = importer.run(store, copy.deepcopy(SOURCES), dry_run=False, expect_tenant='tenant-a')
    assert {k: v['created'] for k, v in dry['collections'].items()} == \
           {k: v['created'] for k, v in real['collections'].items()}


def test_wrong_tenant_is_refused_and_writes_nothing(store):
    with pytest.raises(importer.ImportRefused) as e:
        importer.run(store, copy.deepcopy(SOURCES), dry_run=False, expect_tenant='tenant-b')
    assert e.value.code == 'tenant_mismatch'
    assert _activity(store) == 0 or store.collection_counts() == {}


def test_unknown_source_is_reported_not_read(store):
    rep = importer.run(store, {'some/other.json': {'x': 1}}, dry_run=True)
    assert rep['unmapped'] == [{'source': 'some/other.json', 'what': 'whole file', 'count': 1,
                                'why': 'no importer adapter for this source'}]


# ── review fixes (PR #616 review): each of these failed on 54dc07b ────────────
def test_same_key_twice_in_one_run_creates_one_record(store):
    """A repeat customer (same phone on two jobs) is one contact, both jobs link to it."""
    src = {JOBS: {'jobs': [
        {'id': 'job-a', 'customer': 'Repeat Customer', 'customer_phone': '555-010-0009', 'status': 'completed',
         'revenue_billed': 100.0, 'costs': {'lump_sum_cost': 10.0}},
        {'id': 'job-b', 'customer': 'Repeat Customer', 'customer_phone': '(555) 010-0009', 'status': 'completed',
         'revenue_billed': 200.0, 'costs': {'lump_sum_cost': 20.0}},
    ]}}
    rep = importer.run(store, src, dry_run=False, expect_tenant='tenant-a')
    assert rep['collections']['contact']['created'] == 1
    assert rep['collections']['contact']['merged_in_run'] == 1
    assert _live(store, 'contact') == 1 and _live(store, 'job') == 2
    contact_id = store.list('contact', limit=5)['records'][0]['id']
    assert {j['data']['contact_id'] for j in store.list('job', limit=5)['records']} == {contact_id}


def test_long_key_is_stable_across_runs(store):
    """A 600-character image path must map to the same stored key every run (2 runs -> 1 document)."""
    long_url = '/uploads/fd/' + 'x' * 600 + '.jpg'
    src = {EXT: {'images': {long_url: {'image': long_url, 'doc_type': 'photo', 'title': 'Long'}}}}
    importer.run(store, copy.deepcopy(src), dry_run=False, expect_tenant='tenant-a')
    rep2 = importer.run(store, copy.deepcopy(src), dry_run=False, expect_tenant='tenant-a')
    assert _live(store, 'document') == 1
    assert rep2['collections']['document'] == {'planned': 1, 'created': 0, 'already_imported': 1,
                                               'merged_in_run': 0, 'rejected': 0}
    with store._read() as c:
        refs = [r[0] for r in c.execute("SELECT ref FROM activity WHERE type = 'created'").fetchall()]
    assert refs and all(len(r) <= 500 for r in refs)


def test_forged_import_ref_from_the_agent_is_not_trusted(store):
    """The tenant agent writes a contact whose ref looks like an import key; the real contact still imports."""
    src = {FIELD: {'roster': [{'name': 'Real Person', 'phone': '+15550100777', 'role': 'Office'}]}}
    key = importer.build_plan(src).items[0][0]
    store.create('person', {'name': 'Planted'}, by='agent:tenant-a', agent=True,
                 source={'channel': 'sms', 'ref': key, 'words': 'forged'})
    rep = importer.run(store, src, dry_run=False, expect_tenant='tenant-a')
    assert rep['collections']['person']['created'] == 1
    assert rep['collections']['person']['already_imported'] == 0
    assert sorted(p['data']['name'] for p in store.list('person', limit=5)['records']) == ['Planted', 'Real Person']


# ── the route ───────────────────────────────────────────────────────────────
OWNER = 'user_owner_a'
KEY_A = 'agent-key-tenant-a'
B = '/api/biz/v1'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_DB_DIR', str(tmp_path / 'clients' / 'tenant-a' / 'db'))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.delenv('CANVAS_REQUIRE_AUTH', raising=False)
    monkeypatch.delenv('BIZ_SANDBOXES', raising=False)
    from app import create_app
    from routes.biz import biz_bp, jb_sdk_bp
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    app.register_blueprint(biz_bp)
    app.register_blueprint(jb_sdk_bp)
    yield app.test_client()
    for s in list(app.extensions.get('biz', {}).values()):
        if hasattr(s, 'close'):
            s.close()


def _as(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: 'tok' if user else None)
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: user)


def _counts(client):
    return client.get(f'{B}/health').get_json().get('counts', {})


def test_route_owner_only_dry_run_by_default(client, monkeypatch):
    _as(monkeypatch, None)
    assert client.post(f'{B}/import', json={'sources': SOURCES}).status_code == 401
    r = client.post(f'{B}/import', json={'sources': SOURCES}, headers={'X-Agent-Key': KEY_A})
    assert r.status_code == 403
    _as(monkeypatch, OWNER)
    r = client.post(f'{B}/import', json={'sources': SOURCES, 'tenant': 'tenant-b'})
    assert r.status_code == 400
    before = _counts(client)
    r = client.post(f'{B}/import', json={'sources': SOURCES})
    assert r.status_code == 200 and r.get_json()['dry_run'] is True
    assert r.get_json()['collections']['job']['created'] == 2
    assert _counts(client) == before
    r = client.post(f'{B}/import?dry_run=0', json={'sources': SOURCES})
    assert r.status_code == 200 and r.get_json()['dry_run'] is False
    r = client.post(f'{B}/import?dry_run=0', json={'sources': SOURCES})
    assert sum(v['created'] for v in r.get_json()['collections'].values()) == 0
