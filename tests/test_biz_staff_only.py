"""Shared backend: the "staff_only" schema flag (design amendment 2026-11-17, CCA review N1/N2).

A collection marked "staff_only": true is created, edited, archived and unarchived only by a
person's session; a field marked "staff_only": true is set or cleared only by a person. The
agent key gets 403 on every one of those verbs, through the real API. Nothing else changes:
the agent still reads those records, and still writes every other collection and field.
"""
import json
import shutil

import pytest

from services.biz.schema import CORE_DIR

OWNER = 'user_staff_a'
KEY_A = 'agent-key-tenant-a'
B = '/api/biz/v1'
AG = {'X-Agent-Key': KEY_A}
SRC = {'channel': 'email', 'ref': 'msg-1', 'confidence': 0.8, 'words': 'forwarded by the owner'}

STAFF_COLLECTION = {
    'collection': 'zz_signoff', 'label': 'Sign-off', 'title_field': 'note', 'staff_only': True,
    'required': ['note'], 'fields': {'note': {'type': 'string', 'label': 'Note'}},
}
MIXED_COLLECTION = {
    'collection': 'zz_case', 'label': 'Case', 'title_field': 'name', 'required': ['name'],
    'fields': {'name': {'type': 'string', 'label': 'Name'},
               'hold': {'type': 'boolean', 'label': 'Hold', 'staff_only': True},
               'hold_reason': {'type': 'text', 'label': 'Hold reason', 'staff_only': True}},
}


@pytest.fixture()
def core(tmp_path):
    d = tmp_path / 'core'
    shutil.copytree(CORE_DIR, d)
    (d / 'zz_signoff.json').write_text(json.dumps(STAFF_COLLECTION))
    (d / 'zz_case.json').write_text(json.dumps(MIXED_COLLECTION))
    return d


@pytest.fixture()
def client(tmp_path, core, monkeypatch):
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_DB_DIR', str(tmp_path / 'db'))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.delenv('BIZ_SANDBOXES', raising=False)
    import routes.biz as rb
    from services.biz.store import BizStore
    monkeypatch.setattr(rb, '_open_main', lambda: BizStore(tmp_path / 'db', 'tenant-a', core_dir=core))
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: 'tok')
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: OWNER)
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    if rb.biz_bp.name not in app.blueprints:
        app.register_blueprint(rb.biz_bp)
    c = app.test_client()
    yield c
    store = app.extensions.get('biz', {}).get('store')
    if store:
        store.close()


def _mk(client, coll, data, headers=None, source=None):
    return client.post(f'{B}/{coll}', json={'data': data, 'source': source}, headers=headers or {})


# ── staff-only collection ───────────────────────────────────────────────────
def test_agent_cannot_create_a_staff_only_record(client):
    r = _mk(client, 'zz_signoff', {'note': 'agent says ok'}, AG, SRC)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'


def test_person_creates_and_agent_still_reads(client):
    r = _mk(client, 'zz_signoff', {'note': 'reviewed by staff'})
    assert r.status_code == 201
    rid = r.get_json()['id']
    got = client.get(f'{B}/zz_signoff/{rid}', headers=AG)
    assert got.status_code == 200 and got.get_json()['data']['note'] == 'reviewed by staff'
    assert client.get(f'{B}/zz_signoff', headers=AG).get_json()['records'][0]['id'] == rid


def test_agent_cannot_edit_archive_or_unarchive_a_staff_only_record(client):
    rid = _mk(client, 'zz_signoff', {'note': 'reviewed'}).get_json()['id']
    r = client.patch(f'{B}/zz_signoff/{rid}', json={'data': {'note': 'x'}, 'source': SRC},
                     headers={**AG, 'If-Match': '1'})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'
    r = client.post(f'{B}/zz_signoff/{rid}/archive', json={'source': SRC}, headers=AG)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'
    assert client.get(f'{B}/zz_signoff/{rid}').get_json()['archived_at'] is None
    # a person archives it; the agent cannot bring it back either
    assert client.post(f'{B}/zz_signoff/{rid}/archive', json={}).status_code == 200
    r = client.post(f'{B}/zz_signoff/{rid}/unarchive', json={'source': SRC}, headers=AG)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'
    assert client.get(f'{B}/zz_signoff/{rid}').get_json()['archived_at'] is not None


def test_refusal_writes_nothing(client):
    _mk(client, 'zz_signoff', {'note': 'agent says ok'}, AG, SRC)
    assert client.get(f'{B}/zz_signoff').get_json()['records'] == []
    ev = client.get(f'{B}/events?since=0').get_json()['events']
    assert not [e for e in ev if e['collection'] == 'zz_signoff']


# ── staff-only fields ───────────────────────────────────────────────────────
def test_agent_cannot_set_a_staff_only_field_on_create(client):
    r = _mk(client, 'zz_case', {'name': 'job 1', 'hold': True}, AG, SRC)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'
    assert r.get_json()['fields'] == ['hold']


def test_agent_writes_the_other_fields_but_not_hold(client):
    r = _mk(client, 'zz_case', {'name': 'job 1'}, AG, SRC)
    assert r.status_code == 201
    rid, v = r.get_json()['id'], r.get_json()['version']
    r = client.patch(f'{B}/zz_case/{rid}', json={'data': {'name': 'job 1b'}, 'source': SRC},
                     headers={**AG, 'If-Match': str(v)})
    assert r.status_code == 200
    v = r.get_json()['version']
    r = client.patch(f'{B}/zz_case/{rid}', json={'data': {'hold': True}, 'source': SRC},
                     headers={**AG, 'If-Match': str(v)})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'
    # a person sets it
    r = client.patch(f'{B}/zz_case/{rid}', json={'data': {'hold': True, 'hold_reason': 'expired GL'}},
                     headers={'If-Match': str(v)})
    assert r.status_code == 200 and r.get_json()['data']['hold'] is True
    v = r.get_json()['version']
    # the agent cannot clear it, or blank it
    r = client.patch(f'{B}/zz_case/{rid}', json={'data': {}, 'clear': ['hold'], 'source': SRC},
                     headers={**AG, 'If-Match': str(v)})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'
    r = client.patch(f'{B}/zz_case/{rid}', json={'data': {'hold': False}, 'source': SRC},
                     headers={**AG, 'If-Match': str(v)})
    assert r.status_code == 403
    assert client.get(f'{B}/zz_case/{rid}').get_json()['data']['hold'] is True


def test_agent_may_archive_a_normal_record(client):
    rid = _mk(client, 'zz_case', {'name': 'job 2'}, AG, SRC).get_json()['id']
    assert client.post(f'{B}/zz_case/{rid}/archive', json={'source': SRC}, headers=AG).status_code == 200


def test_a_tenant_overlay_cannot_mark_or_unmark_staff_only(client):
    # custom field specs never carry the flag (it is a platform decision, not a tenant one)
    r = client.post(f'{B}/schema/custom', json={'collection': 'zz_case',
                                                 'fields': {'x_note': {'type': 'string', 'staff_only': True}}})
    assert r.status_code == 422
    spec = client.get(f'{B}/schema/zz_signoff').get_json()
    assert spec['staff_only'] is True and spec['fields']
