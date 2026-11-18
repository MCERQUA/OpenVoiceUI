"""Shared backend: packs + the pack hooks contract (design section 6, amendment 2026-11-17).

Uses a throwaway fixture pack, not any real pack, so the engine rules are tested on their own:
a pack is invisible until turned on; turning it on is additive, logged and permanent; its
collections and its fields on core collections are named <prefix>_; enum_add only appends;
a pack that breaks a rule is not loaded; check_write runs before every write to a collection
the pack owns or extends (create, edit, archive, unarchive) and can refuse it; pack routes
answer 404 until the pack is on.
"""
import json
import shutil

import pytest

from services.biz.schema import CORE_DIR, SchemaError

OWNER = 'user_owner_a'
KEY_A = 'agent-key-tenant-a'
B = '/api/biz/v1'
AG = {'X-Agent-Key': KEY_A}
SRC = {'channel': 'email', 'ref': 'fwd-1', 'confidence': 0.9, 'words': 'x'}


def _write_pack(root, name='zz-demo', prefix='zz', collections=None, extends=None, hooks=None):
    d = root / name
    d.mkdir(parents=True)
    collections = collections if collections is not None else {
        'zz_item': {'collection': 'zz_item', 'label': 'Item', 'title_field': 'name', 'required': ['name'],
                    'fields': {'name': {'type': 'string'}, 'locked': {'type': 'boolean', 'staff_only': True},
                               'contact_id': {'type': 'ref', 'ref': 'contact'}}}}
    for c, spec in collections.items():
        (d / f'{c}.json').write_text(json.dumps(spec))
    (d / 'pack.json').write_text(json.dumps({
        'name': name, 'prefix': prefix, 'version': '0.1.0', 'label': 'Demo', 'hooks': hooks,
        'collections': list(collections),
        'extends': extends if extends is not None else {
            'contact': {'fields': {'zz_score': {'type': 'integer'}}, 'enum_add': {'type': ['zz_partner']}}}}))
    return d


class DemoHooks:
    calls = []

    @staticmethod
    def check_write(store, op, collection, data, current, *, by, agent, via_pack):
        DemoHooks.calls.append((op, collection, agent, via_pack))
        if collection == 'zz_item' and data.get('name') == 'forbidden':
            raise SchemaError(422, 'demo_refused', 'the demo pack refuses this name')
        if collection == 'contact' and (data.get('zz_score') or 0) > 100:
            raise SchemaError(422, 'demo_score', 'score above 100')
        if op == 'archive' and collection == 'zz_item' and not via_pack:
            raise SchemaError(409, 'demo_archive', 'demo items archive through the pack')


@pytest.fixture()
def packs_dir(tmp_path):
    root = tmp_path / 'packs'
    _write_pack(root)
    _write_pack(root, name='bad-prefix', prefix='bp', collections={
        'item': {'collection': 'item', 'fields': {'name': {'type': 'string'}}}}, extends={})
    _write_pack(root, name='bad-field', prefix='bf', collections={}, extends={
        'contact': {'fields': {'score': {'type': 'integer'}}}})
    _write_pack(root, name='bad-core', prefix='bc', collections={
        'bc_job': {'collection': 'bc_job', 'fields': {}}}, extends={'nosuch': {'fields': {}}})
    return root


@pytest.fixture()
def app_client(tmp_path, packs_dir, monkeypatch):
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_DB_DIR', str(tmp_path / 'db'))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.delenv('BIZ_SANDBOXES', raising=False)
    import routes.biz as rb
    import services.biz.packs as packs
    from services.biz.store import BizStore
    monkeypatch.setitem(packs._HOOKS, 'zz-demo', DemoHooks)
    DemoHooks.calls = []
    monkeypatch.setattr(rb, '_open_main', lambda: BizStore(tmp_path / 'db', 'tenant-a', packs_dir=packs_dir))
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: 'tok')
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: OWNER)
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    if rb.biz_bp.name not in app.blueprints:
        app.register_blueprint(rb.biz_bp)
    yield app, app.test_client()
    store = app.extensions.get('biz', {}).get('store')
    if store:
        store.close()


@pytest.fixture()
def client(app_client):
    return app_client[1]


def _mk(client, coll, data, headers=None, source=None):
    return client.post(f'{B}/{coll}', json={'data': data, 'source': source}, headers=headers or {})


def test_only_rule_abiding_packs_are_offered(client):
    out = client.get(f'{B}/packs').get_json()
    assert [p['name'] for p in out['available']] == ['zz-demo']
    assert out['enabled'] == []


def test_a_pack_is_invisible_until_turned_on(client):
    assert client.get(f'{B}/zz_item').status_code == 404
    assert _mk(client, 'zz_item', {'name': 'a'}).status_code == 404
    r = _mk(client, 'contact', {'name': 'Ann', 'zz_score': 5})
    assert r.status_code == 422                                      # unknown field until enabled
    assert _mk(client, 'contact', {'name': 'Ann', 'type': 'zz_partner'}).status_code == 422


def test_turning_a_pack_on_is_additive_logged_and_permanent(app_client, tmp_path, packs_dir):
    app, client = app_client
    r = client.post(f'{B}/packs/zz-demo/enable', json={})
    assert r.status_code == 200 and r.get_json()['enabled_packs'] == ['zz-demo']
    assert client.post(f'{B}/packs/zz-demo/enable', json={}).get_json()['already'] is True
    assert _mk(client, 'zz_item', {'name': 'a'}).status_code == 201
    assert _mk(client, 'contact', {'name': 'Ann', 'zz_score': 5, 'type': 'zz_partner'}).status_code == 201
    assert _mk(client, 'contact', {'name': 'Bob', 'type': 'gc'}).status_code == 201     # core values kept
    ev = client.get(f'{B}/events?since=0').get_json()['events']
    assert [e['diff']['pack'] for e in ev if e['type'] == 'pack_enabled'] == ['zz-demo']
    assert client.get(f'{B}/health').get_json()['packs'] == ['zz-demo']
    # reopened from disk: still on
    from services.biz.store import BizStore
    app.extensions['biz']['store'].close()
    again = BizStore(tmp_path / 'db', 'tenant-a', packs_dir=packs_dir)
    try:
        assert again.registry.enabled_packs == ['zz-demo'] and again.registry.get('zz_item')
    finally:
        again.close()
        app.extensions['biz'].pop('store')


def test_unknown_pack_and_agent_without_source(client):
    assert client.post(f'{B}/packs/bad-prefix/enable', json={}).status_code == 404
    assert client.post(f'{B}/packs/nope/enable', json={}).status_code == 404
    r = client.post(f'{B}/packs/zz-demo/enable', json={}, headers=AG)
    assert r.status_code == 422 and r.get_json()['code'] == 'source_required'
    assert client.post(f'{B}/packs/zz-demo/enable', json={'source': SRC}, headers=AG).status_code == 200


def test_staff_only_applies_to_pack_fields(client):
    client.post(f'{B}/packs/zz-demo/enable', json={})
    r = _mk(client, 'zz_item', {'name': 'a', 'locked': True}, AG, SRC)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'


def test_check_write_runs_on_every_verb_and_can_refuse(client):
    client.post(f'{B}/packs/zz-demo/enable', json={})
    r = _mk(client, 'zz_item', {'name': 'forbidden'}, AG, SRC)
    assert r.status_code == 422 and r.get_json()['code'] == 'demo_refused'
    rec = _mk(client, 'zz_item', {'name': 'fine'}).get_json()
    r = client.patch(f'{B}/zz_item/{rec["id"]}', json={'data': {'name': 'forbidden'}}, headers={'If-Match': '1'})
    assert r.status_code == 422 and r.get_json()['code'] == 'demo_refused'
    assert client.get(f'{B}/zz_item/{rec["id"]}').get_json()['data']['name'] == 'fine'
    r = client.post(f'{B}/zz_item/{rec["id"]}/archive', json={})
    assert r.status_code == 409 and r.get_json()['code'] == 'demo_archive'
    # an extended core collection runs the hook too
    r = _mk(client, 'contact', {'name': 'Ann', 'zz_score': 101})
    assert r.status_code == 422 and r.get_json()['code'] == 'demo_score'
    ops = {(op, coll) for op, coll, _, _ in DemoHooks.calls}
    assert {('create', 'zz_item'), ('edit', 'zz_item'), ('archive', 'zz_item'), ('create', 'contact')} <= ops
    assert all(via is False for *_, via in DemoHooks.calls)


def test_tenant_adds_only_x_fields_to_a_pack_collection(client):
    client.post(f'{B}/packs/zz-demo/enable', json={})
    r = client.post(f'{B}/schema/custom', json={'collection': 'zz_item', 'fields': {'note': {'type': 'string'}}})
    assert r.status_code == 422
    r = client.post(f'{B}/schema/custom', json={'collection': 'zz_item', 'fields': {'x_note': {'type': 'string'}}})
    assert r.status_code == 200
    assert _mk(client, 'zz_item', {'name': 'a', 'x_note': 'hi'}).status_code == 201


def test_shipped_packs_load_clean():
    """Every pack in the image passes the loader (a broken pack would silently vanish otherwise)."""
    from services.biz import packs
    from services.biz.schema import PACKS_DIR, _load_json_dir
    shipped = [d.name for d in PACKS_DIR.iterdir() if (d / 'pack.json').is_file()] if PACKS_DIR.is_dir() else []
    loaded = packs.load_packs(PACKS_DIR, _load_json_dir(CORE_DIR).keys())
    assert sorted(loaded) == sorted(shipped)


def test_pack_routes_404_until_the_pack_is_on():
    from flask import Blueprint, Flask, g, jsonify
    import routes.biz as rb

    class FakeStore:
        on = False

        def pack_enabled(self, name):
            return self.on

    store = FakeStore()
    app = Flask('t')
    bp = Blueprint('t', 't')

    @bp.before_request
    def _s():
        g.biz_store = store

    @bp.get('/x')
    def x():
        rb.PackContext.require_pack('zz-demo')
        return jsonify(ok=True)

    @bp.errorhandler(SchemaError)
    def err(exc):
        return jsonify(exc.to_dict()), exc.status

    app.register_blueprint(bp)
    c = app.test_client()
    r = c.get('/x')
    assert r.status_code == 404 and r.get_json()['code'] == 'pack_not_enabled'
    store.on = True
    assert c.get('/x').status_code == 200
