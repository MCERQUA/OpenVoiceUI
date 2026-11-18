"""The JamBot Hub served at /hub/ (routes/hub.py).

Cases come from the Hub build plan (docs/jambot/jambot-hub-build-plan.md, milestone b, MIKE-AI repo) and the
backend design's jail-cell rules, not from the implementation:
  same people as /api/biz/v1 (this business's Clerk users or its own agent key); a wrong agent key is 403; a
  dev-sandbox key is no Hub credential; no session -> a page goes to /?redirect=/hub/, anything else 401;
  live mode is stamped into the page; the demo fixtures (data/) are never served; no path escapes hub/,
  no symlink is followed, only listed file types; the vendored pin is readable; the image's hub/ has no data/.
"""
import json
import os
from pathlib import Path

import pytest

OWNER = 'user_owner_a'
KEY_A = 'agent-key-tenant-a'
SBX_KEY = 'sbx-one-' + 'k' * 40
REPO_HUB = Path(__file__).resolve().parent.parent / 'hub'


@pytest.fixture()
def hubdir(tmp_path):
    h = tmp_path / 'hub'
    (h / 'assets' / 'js').mkdir(parents=True)
    (h / '_ovu').mkdir()
    (h / 'data').mkdir()
    (h / 'index.html').write_text('<!DOCTYPE html><html><head><title>JamDash</title></head><body>hub</body></html>')
    (h / 'assets' / 'js' / 'app.js').write_text('export const x = 1;')
    (h / '_ovu' / 'hub-auth.js').write_text('/* auth */')
    (h / 'data' / 'jobs.json').write_text('{"jobs":[{"customer":"Fixture Person","amount":12345}]}')
    (h / 'notes.txt').write_text('not a served type')
    (h / 'VENDORED.json').write_text(json.dumps({'repo': 'MCERQUA/jamdash', 'commit': 'a' * 40,
                                                 'vendored_at': '2026-11-18T17:00:00Z', 'file_count': 3}))
    outside = tmp_path / 'secret.js'
    outside.write_text('secret')
    (h / 'assets' / 'js' / 'link.js').symlink_to(outside)
    (h / 'linkdir').symlink_to(tmp_path)
    return h


def _make(monkeypatch, hubdir, clerk=True):
    import routes.hub as hub
    monkeypatch.setattr(hub, 'HUB_DIR', hubdir)
    if clerk:
        monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    else:
        monkeypatch.delenv('CLERK_PUBLISHABLE_KEY', raising=False)
        monkeypatch.delenv('VITE_CLERK_PUBLISHABLE_KEY', raising=False)
        monkeypatch.delenv('CANVAS_REQUIRE_AUTH', raising=False)
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
    monkeypatch.setenv('BIZ_SANDBOXES', 'one')
    monkeypatch.setenv('BIZ_SANDBOX_KEY_ONE', SBX_KEY)
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    if hub.hub_bp.name not in app.blueprints:
        app.register_blueprint(hub.hub_bp)
    return app.test_client()


def _as(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: 'tok' if user else None)
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: user)


@pytest.fixture()
def client(monkeypatch, hubdir):
    return _make(monkeypatch, hubdir)


HTML = {'Accept': 'text/html,application/xhtml+xml'}


# ── who may read it ─────────────────────────────────────────────────────────
def test_owner_session_gets_live_page(client, monkeypatch):
    _as(monkeypatch, OWNER)
    r = client.get('/hub/', headers=HTML)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "globalThis.JAMDASH_MODE='live'" in body
    assert '<meta name="jambot-hub" content="' + 'a' * 40 + '">' in body
    assert '/hub/_ovu/hub-auth.js' in body
    assert '"tenant": "tenant-a"' in body
    assert body.index('jambot-hub-boot') < body.index('<title>')      # boot block runs before the app loads
    assert 'no-store' in r.headers['Cache-Control'] and 'no-transform' in r.headers['Cache-Control']


def test_no_session_page_goes_to_sign_in_and_back(client, monkeypatch):
    _as(monkeypatch, None)
    r = client.get('/hub/', headers=HTML)
    assert r.status_code == 302 and r.headers['Location'].endswith('/?redirect=/hub/')


def test_no_session_non_page_is_401(client, monkeypatch):
    _as(monkeypatch, None)
    for path in ('/hub/', '/hub/assets/js/app.js', '/hub/version.json'):
        r = client.get(path, headers={'Accept': 'application/json'})
        assert r.status_code == 401, path
        assert r.get_json()['code'] == 'auth_required'


def test_own_agent_key_reads_static(client, monkeypatch):
    _as(monkeypatch, None)
    r = client.get('/hub/', headers={'X-Agent-Key': KEY_A})
    assert r.status_code == 200 and "JAMDASH_MODE='live'" in r.get_data(as_text=True)
    assert client.get('/hub/assets/js/app.js', headers={'X-Agent-Key': KEY_A}).status_code == 200


def test_wrong_agent_key_is_403_not_fallthrough(client, monkeypatch):
    _as(monkeypatch, OWNER)                       # even with a valid session in the same request
    r = client.get('/hub/', headers={'X-Agent-Key': 'agent-key-tenant-b'})
    assert r.status_code == 403 and r.get_json()['code'] == 'agent_key_invalid'


def test_sandbox_key_is_not_a_hub_credential(client, monkeypatch):
    _as(monkeypatch, None)
    r = client.get('/hub/', headers={'X-Agent-Key': SBX_KEY})
    assert r.status_code == 403


def test_local_install_without_clerk_is_open(monkeypatch, hubdir):
    c = _make(monkeypatch, hubdir, clerk=False)
    r = c.get('/hub/')
    assert r.status_code == 200
    assert '"auth": false' in r.get_data(as_text=True)


# ── what it serves ──────────────────────────────────────────────────────────
def test_fixture_folder_never_served(client, monkeypatch):
    _as(monkeypatch, OWNER)
    for path in ('/hub/data/jobs.json', '/hub/data/', '/hub/./data/jobs.json', '/hub/assets/../data/jobs.json'):
        r = client.get(path)
        assert r.status_code in (404, 308, 302), path
        assert b'Fixture Person' not in r.data, path


def test_no_escape_no_symlink_no_unlisted_type(client, monkeypatch):
    _as(monkeypatch, OWNER)
    for path in ('/hub/assets/js/link.js', '/hub/linkdir/secret.js', '/hub/..%2fsecret.js', '/hub/notes.txt',
                 '/hub/VENDORED.json', '/hub/.hidden.js', '/hub/assets/js/missing.js'):
        r = client.get(path)
        assert r.status_code == 404, (path, r.status_code)
        assert b'secret' not in r.data


def test_static_types_and_bare_redirect(client, monkeypatch):
    _as(monkeypatch, OWNER)
    r = client.get('/hub/assets/js/app.js')
    assert r.status_code == 200 and r.mimetype == 'application/javascript'
    assert client.get('/hub/_ovu/hub-auth.js').status_code == 200
    r = client.get('/hub')
    assert r.status_code == 308 and r.headers['Location'].endswith('/hub/')


def test_version_pin(client, monkeypatch):
    _as(monkeypatch, OWNER)
    v = client.get('/hub/version.json').get_json()
    assert v['commit'] == 'a' * 40 and v['repo'] == 'MCERQUA/jamdash' and v['mode'] == 'live'
    assert v['tenant'] == 'tenant-a'


def test_tenant_param_changes_nothing(client, monkeypatch):
    _as(monkeypatch, OWNER)
    r = client.get('/hub/?tenant=tenant-b', headers=HTML)
    body = r.get_data(as_text=True)
    assert '"tenant": "tenant-a"' in body and 'tenant-b' not in body


def test_not_installed_is_honest_404(monkeypatch, tmp_path):
    empty = tmp_path / 'nohub'
    empty.mkdir()
    c = _make(monkeypatch, empty)
    _as(monkeypatch, OWNER)
    r = c.get('/hub/')
    assert r.status_code == 404 and r.get_json()['code'] == 'hub_not_installed'


# ── the tree that ships in the image ────────────────────────────────────────
def test_shipped_hub_has_no_fixtures_and_is_pinned():
    if not (REPO_HUB / 'index.html').is_file():
        pytest.skip('hub/ not vendored in this checkout')
    assert not (REPO_HUB / 'data').exists()
    pin = json.loads((REPO_HUB / 'VENDORED.json').read_text())
    assert len(pin['commit']) == 40 and pin['fixtures_dropped'] is True
    assert (REPO_HUB / '_ovu' / 'hub-auth.js').is_file()
    links = [p for p in REPO_HUB.rglob('*') if p.is_symlink()]
    assert links == []
    import hashlib
    for rel, digest in pin['sha256'].items():
        assert hashlib.sha256((REPO_HUB / rel).read_bytes()).hexdigest() == digest, rel
