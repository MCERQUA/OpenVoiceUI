"""Shared backend: the backend-private file store (design amendment 2026-11-17, CCA plan B1 + review N2).

Bytes live at <db dir>/files/<sha256>, never under UPLOADS_DIR. They are served only by
GET /api/biz/v1/files/<id> to a person's session, as an attachment with nosniff. The id is
the content hash: id-only lookup, no path segments, traversal refused. Type is decided from
the bytes (PDF / PNG / JPEG only), size is capped, rows are immutable and never deleted,
and the daily backup mirrors the store with a hash check.
"""
import hashlib
import io
import json
import sqlite3

import pytest

OWNER = 'user_owner_a'
KEY_A = 'agent-key-tenant-a'
SBX_KEY_ONE = 'sbx-one-' + 'k' * 40
SBX_KEY_TWO = 'sbx-two-' + 'q' * 40
B = '/api/biz/v1'
AG = {'X-Agent-Key': KEY_A}
SRC = json.dumps({'channel': 'email', 'ref': 'fwd-1', 'confidence': 0.9, 'words': 'certificate forwarded by staff'})
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


@pytest.fixture()
def dirs(tmp_path):
    return {'a': tmp_path / 'clients' / 'tenant-a' / 'db', 'uploads': tmp_path / 'clients' / 'tenant-a' / 'uploads'}


def _as(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: 'tok' if user else None)
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: user)


@pytest.fixture()
def app_client(dirs, monkeypatch):
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_DB_DIR', str(dirs['a']))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.setenv('BIZ_SANDBOXES', 'one,two')
    monkeypatch.setenv('BIZ_SANDBOX_KEY_ONE', SBX_KEY_ONE)
    monkeypatch.setenv('BIZ_SANDBOX_KEY_TWO', SBX_KEY_TWO)
    from app import create_app
    from routes.biz import biz_bp
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    if biz_bp.name not in app.blueprints:
        app.register_blueprint(biz_bp)
    yield app, app.test_client()
    for store in list(app.extensions.get('biz', {}).values()):
        if hasattr(store, 'close'):
            store.close()


@pytest.fixture()
def client(app_client):
    return app_client[1]


def _up(client, blob, headers=None, source=None, name='coi.pdf', extra=None):
    data = {'file': (io.BytesIO(blob), name)}
    if source:
        data['source'] = source
    data.update(extra or {})
    return client.post(f'{B}/files', data=data, headers=headers or {}, content_type='multipart/form-data')


def test_anonymous_cannot_store_or_read(client, monkeypatch):
    _as(monkeypatch, OWNER)
    fid = _up(client, PDF).get_json()['id']
    _as(monkeypatch, None)
    assert _up(client, PDF).status_code == 401
    assert client.get(f'{B}/files/{fid}').status_code == 401
    assert client.get(f'{B}/files/{fid}/meta').status_code == 401


def test_owner_stores_and_reads_back_as_attachment(client, dirs, monkeypatch):
    _as(monkeypatch, OWNER)
    r = _up(client, PDF, name='Acme Roofing - Smith Job COI.pdf')
    assert r.status_code == 201
    meta = r.get_json()
    sha = hashlib.sha256(PDF).hexdigest()
    assert meta['id'] == sha and meta['url'] == 'biz-file:' + sha and meta['content_type'] == 'application/pdf'
    # stored by hash under the db dir, never under uploads, and no name in any path
    assert (dirs['a'] / 'files' / sha).read_bytes() == PDF
    assert not dirs['uploads'].exists()
    assert [p.name for p in (dirs['a'] / 'files').iterdir()] == [sha]
    got = client.get(f'{B}/files/{sha}')
    assert got.status_code == 200 and got.data == PDF
    assert got.headers['Content-Type'] == 'application/pdf'
    assert got.headers['X-Content-Type-Options'] == 'nosniff'
    assert got.headers['Content-Disposition'].startswith('attachment')
    assert 'Acme' not in got.headers['Content-Disposition']
    assert 'no-store' in got.headers['Cache-Control']


def test_agent_stores_with_source_but_cannot_open(client, monkeypatch):
    _as(monkeypatch, None)
    assert _up(client, PDF, AG).status_code == 422                 # agent writes carry a source
    r = _up(client, PDF, AG, SRC)
    assert r.status_code == 201
    fid = r.get_json()['id']
    assert client.get(f'{B}/files/{fid}/meta', headers=AG).status_code == 200
    r = client.get(f'{B}/files/{fid}', headers=AG)
    assert r.status_code == 403 and r.get_json()['code'] == 'owner_only'


def test_same_bytes_are_one_file(client, monkeypatch):
    _as(monkeypatch, OWNER)
    a = _up(client, PDF).get_json()
    b = _up(client, PDF, name='other.pdf')
    assert b.status_code == 200 and b.get_json()['id'] == a['id'] and b.get_json()['existed'] is True


@pytest.mark.parametrize('fid', ['../business.db', '..%2Fbusiness.db', 'A' * 64, 'a' * 63, 'g' * 64,
                                 '0' * 64 + '.pdf', '%2e%2e%2f' + '0' * 55])
def test_id_only_lookup_refuses_paths(client, monkeypatch, fid):
    _as(monkeypatch, OWNER)
    _up(client, PDF)
    r = client.get(f'{B}/files/{fid}')
    assert r.status_code == 404
    assert b'SQLite' not in r.data


def test_unknown_hash_is_404(client, monkeypatch):
    _as(monkeypatch, OWNER)
    assert client.get(f'{B}/files/{"0" * 64}').status_code == 404


@pytest.mark.parametrize('blob', [b'<html><script>alert(1)</script></html>', b'GIF89a....', b'PK\x03\x04zip',
                                  b'MZ\x90\x00exe'])
def test_type_is_decided_from_the_bytes(client, monkeypatch, blob):
    _as(monkeypatch, OWNER)
    r = _up(client, blob, name='looks-like.pdf')
    assert r.status_code == 415 and r.get_json()['code'] == 'file_type_refused'


def test_png_and_jpeg_accepted(client, monkeypatch):
    _as(monkeypatch, OWNER)
    assert _up(client, PNG, name='p.png').get_json()['content_type'] == 'image/png'
    assert _up(client, b'\xff\xd8\xff\xe0' + b'1' * 40, name='p.jpg').get_json()['content_type'] == 'image/jpeg'


def test_size_cap(client, monkeypatch):
    from services.biz.store import FILE_MAX_BYTES
    _as(monkeypatch, OWNER)
    r = _up(client, PDF + b'0' * (FILE_MAX_BYTES + 10))
    assert r.status_code == 413
    assert _up(client, b'').status_code == 422


def test_tenant_field_on_upload_refused(client, monkeypatch):
    _as(monkeypatch, OWNER)
    r = _up(client, PDF, extra={'tenant': 'tenant-b'})
    assert r.status_code == 400 and r.get_json()['code'] == 'tenant_param_refused'


def test_rows_are_immutable_and_never_deleted(client, dirs, monkeypatch):
    _as(monkeypatch, OWNER)
    fid = _up(client, PDF).get_json()['id']
    conn = sqlite3.connect(str(dirs['a'] / 'business.db'))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM files WHERE id = ?', (fid,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE files SET content_type = 'text/html' WHERE id = ?", (fid,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO files(id, tenant, content_type, size, created_at, created_by) "
                         "VALUES (?, 'tenant-b', 'application/pdf', 1, 'x', 'x')", ('1' * 64,))
    finally:
        conn.close()


def test_tampered_bytes_are_not_served(client, dirs, monkeypatch):
    _as(monkeypatch, OWNER)
    fid = _up(client, PDF).get_json()['id']
    (dirs['a'] / 'files' / fid).write_bytes(PDF + b'tampered')
    r = client.get(f'{B}/files/{fid}')
    assert r.status_code == 500 and r.get_json()['code'] == 'file_corrupt'


def test_daily_backup_mirrors_the_file_store(app_client, dirs, monkeypatch):
    app, client = app_client
    _as(monkeypatch, OWNER)
    a = _up(client, PDF).get_json()['id']
    b = _up(client, PNG, name='p.png').get_json()['id']
    store = app.extensions['biz']['store']
    res = store.backup_files()
    assert res == {'indexed': 2, 'mirrored': 2, 'copied_now': 2, 'problems': [], 'verified': True}
    assert (dirs['a'] / 'backups' / 'files' / a).read_bytes() == PDF
    assert (dirs['a'] / 'backups' / 'files' / b).read_bytes() == PNG
    again = store.backup_files()
    assert again['copied_now'] == 0 and again['verified'] is True
    # a store file that vanished is reported, never silently "verified"
    (dirs['a'] / 'backups' / 'files' / b).rename(dirs['a'] / 'backups' / 'files' / ('x' + b[1:]))
    (dirs['a'] / 'files' / b).rename(dirs['a'] / 'files' / ('y' + b[1:]))
    bad = store.backup_files()
    assert bad['verified'] is False and bad['problems'] == [b[:12]]


def test_export_lists_file_metadata_not_bytes(client, monkeypatch):
    _as(monkeypatch, OWNER)
    fid = _up(client, PDF).get_json()['id']
    ex = client.get(f'{B}/export').get_json()
    assert [f['id'] for f in ex['files']] == [fid]
    assert 'PDF' not in json.dumps(ex['files'])


def test_sandbox_files_stay_in_their_sandbox(client, dirs, monkeypatch):
    _as(monkeypatch, None)
    r = _up(client, PDF, {'X-Agent-Key': SBX_KEY_ONE}, SRC)
    assert r.status_code == 201
    fid = r.get_json()['id']
    assert (dirs['a'] / 'sandboxes' / 'one' / 'files' / fid).is_file()
    assert not (dirs['a'] / 'files').exists()
    # another sandbox's key: an agent role, so 403 on the bytes; and its own store has no such file
    assert client.get(f'{B}/files/{fid}', headers={'X-Agent-Key': SBX_KEY_TWO}).status_code == 403
    assert client.get(f'{B}/files/{fid}/meta', headers={'X-Agent-Key': SBX_KEY_TWO}).status_code == 404
    _as(monkeypatch, OWNER)
    assert client.get(f'{B}/files/{fid}').status_code == 404                       # the tenant's own store
    assert client.get(f'{B}/files/{fid}', headers={'X-Biz-Sandbox': 'two'}).status_code == 404
    assert client.get(f'{B}/files/{fid}', headers={'X-Biz-Sandbox': 'one'}).data == PDF


def test_phase2_database_migrates_to_the_file_store_with_a_verified_backup(tmp_path, monkeypatch):
    """A tenant db written by the phase-2 engine (schema 2) gains the files index; nothing is lost."""
    import services.biz.store as store_mod
    from services.biz.migrations import discover
    monkeypatch.setattr(store_mod, 'discover', lambda: discover()[:2])
    old = store_mod.BizStore(tmp_path / 'db', 'tenant-a')
    rec = old.create('contact', {'name': 'Sample Customer'}, by='local:owner')
    assert old.schema_version == 2
    old.close()
    monkeypatch.setattr(store_mod, 'discover', lambda: discover()[:3])
    new = store_mod.BizStore(tmp_path / 'db', 'tenant-a')
    try:
        assert new.schema_version == 3 and not new.read_only
        assert new.get('contact', rec['id'])['data']['name'] == 'Sample Customer'
        assert len(list((tmp_path / 'db' / 'backups').glob('pre-migrate-2-3-*.db'))) == 1
        assert new.put_file(PDF, by='local:owner')['existed'] is False
    finally:
        new.close()


def test_a_file_id_is_never_mistaken_for_a_card_number():
    """sha256 ids sometimes carry a Luhn-valid digit run; a record pointing at its file must not be refused."""
    from services.biz.schema import contains_card_number
    sha = 'ab' + '4111111111111111' + 'c' * 46                 # a hex id containing a Luhn-valid 16-digit run
    assert len(sha) == 64
    assert not contains_card_number('biz-file:' + sha)
    assert not contains_card_number({'source_sha256': sha.upper()})
    # a real card number is still refused, alone or next to an id
    assert contains_card_number('card 4111 1111 1111 1111')
    assert contains_card_number('biz-file:' + sha + ' and card 4111111111111111')


def test_document_pointing_at_a_luhn_looking_file_id_is_accepted(client, monkeypatch):
    import services.biz.store as store_mod
    _as(monkeypatch, OWNER)
    sha = 'ab' + '4111111111111111' + 'c' * 46
    real = store_mod.hashlib.sha256

    class Fixed:
        def __init__(self, *a):
            self.h = real(*a)

        def update(self, b):
            self.h.update(b)

        def hexdigest(self):
            return sha
    monkeypatch.setattr(store_mod.hashlib, 'sha256', Fixed)
    meta = _up(client, PDF).get_json()
    assert meta['id'] == sha
    r = client.post(f'{B}/document', json={'data': {'title': 'coi', 'file_url': meta['url']}})
    assert r.status_code == 201, r.get_json()
