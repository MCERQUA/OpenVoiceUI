"""Canvas publish is self-service for the tenant agent (#477).

Two measured cases drove this:

* 2026-09-04 — a finished client deliverable could not be published by the
  agent; the refusal said "auth" when the real cause was an admin lock, and a
  duplicate '<id>.html' manifest key rendered a phantom tile with a 404 link.
* 2026-10-14 — a page registered with NO ``is_locked`` key was treated as
  locked (fail closed), so the agent's PATCH was refused; and the first PATCH
  after writing a file straight to canvas-pages/ returned "Page not found"
  until a throttled manifest GET happened to run discovery.

Every test here runs against a throwaway manifest + pages dir, not the
tenant's. Admin identity is patched at ``services.auth.is_admin_user``.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

AGENT_KEY = 'test-agent-key-477'
OLD = (datetime.now() - timedelta(hours=1)).isoformat()


@pytest.fixture
def canvas_env(tmp_path, monkeypatch):
    """Point routes.canvas at a scratch manifest + pages dir and reset its caches."""
    from routes import canvas
    pages_dir = tmp_path / 'canvas-pages'
    pages_dir.mkdir()
    manifest_path = tmp_path / 'canvas-manifest.json'
    monkeypatch.setattr(canvas, 'CANVAS_PAGES_DIR', pages_dir)
    monkeypatch.setattr(canvas, 'CANVAS_MANIFEST_PATH', manifest_path)
    monkeypatch.setattr(canvas, '_manifest_cache', {'data': None, 'mtime': 0})
    monkeypatch.setattr(canvas, '_last_sync_time', 0)
    monkeypatch.setenv('AGENT_API_KEY', AGENT_KEY)
    return canvas, pages_dir, manifest_path


@pytest.fixture(scope='module')
def app_client():
    """App with the auth gate ENFORCED (Clerk key set) and a known agent key.

    Hosted tenants always run this way; a test app with auth disabled would
    pass the agent requests through without ever exercising the X-Agent-Key
    carve-out that the real agent depends on. Both env vars are read by
    create_app() at construction time, so they are set here, not per test.
    """
    import os
    saved = {k: os.environ.get(k) for k in ('CLERK_PUBLISHABLE_KEY', 'AGENT_API_KEY')}
    os.environ['CLERK_PUBLISHABLE_KEY'] = 'pk_test_477'
    os.environ['AGENT_API_KEY'] = AGENT_KEY
    try:
        from app import create_app
        app, _ = create_app(config_override={'TESTING': True})
        from routes.canvas import canvas_bp   # server.py registers it, create_app() does not
        app.register_blueprint(canvas_bp)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return app.test_client()


def _as_admin():
    """Present a valid Clerk session whose user is an admin (all three seams)."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch('services.auth.get_token_from_request', return_value='tok'))
    stack.enter_context(patch('services.auth.verify_clerk_token', return_value='user_admin_477'))
    stack.enter_context(patch('services.auth.is_admin_user', return_value=True))
    return stack


def _write_manifest(manifest_path, pages):
    manifest_path.write_text(json.dumps({
        'version': 1, 'categories': {}, 'pages': pages,
        'uncategorized': [], 'recently_viewed': [], 'user_custom_order': None,
    }))


def _entry(**overrides):
    base = {'filename': 'deliverable.html', 'display_name': 'Deliverable',
            'category': 'uncategorized', 'created': OLD, 'is_public': False}
    base.update(overrides)
    return base


def _agent_patch(client, page_id, body):
    return client.patch(f'/api/canvas/manifest/page/{page_id}', json=body,
                        headers={'X-Agent-Key': AGENT_KEY})


# ---------------------------------------------------------------------------
# Creation paths write is_locked explicitly
# ---------------------------------------------------------------------------

class TestExplicitLockOnCreation:
    def test_discovered_file_gets_is_locked_false(self, canvas_env):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'hr-blog-search-tuneup.html').write_text('<h1>tuneup</h1>')
        manifest = canvas.sync_canvas_manifest()
        entry = manifest['pages']['hr-blog-search-tuneup']
        assert entry['is_locked'] is False
        assert entry['is_public'] is False
        on_disk = json.loads(manifest_path.read_text())['pages']['hr-blog-search-tuneup']
        assert 'is_locked' in on_disk and on_disk['is_locked'] is False

    def test_discovery_preserves_an_existing_explicit_lock(self, canvas_env):
        # A legacy entry keyed with 'file' instead of 'filename' is re-synced;
        # an admin lock it already carries must survive the normalisation.
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'legacy.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'legacy': {'file': 'legacy.html', 'is_locked': True}})
        manifest = canvas.sync_canvas_manifest()
        assert manifest['pages']['legacy']['is_locked'] is True

    def test_agent_created_page_gets_is_locked_false(self, canvas_env):
        canvas, pages_dir, _ = canvas_env
        (pages_dir / 'songs.html').write_text('<p>songs</p>')
        entry = canvas.add_page_to_manifest('songs.html', 'Songs', content='<p>songs</p>')
        assert entry['is_locked'] is False
        assert entry['is_public'] is False


# ---------------------------------------------------------------------------
# PATCH is_public — unlocked is self-service, locked names the lock
# ---------------------------------------------------------------------------

class TestAgentPublish:
    def test_agent_can_publish_an_unlocked_page(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>done</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=False)})
        resp = _agent_patch(app_client, 'deliverable', {'is_public': True})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()['page']['is_public'] is True
        assert json.loads(manifest_path.read_text())['pages']['deliverable']['is_public'] is True

    def test_agent_publish_on_locked_page_is_403_naming_the_lock(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>done</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=True)})
        resp = _agent_patch(app_client, 'deliverable', {'is_public': True})
        assert resp.status_code == 403
        body = resp.get_json()
        assert body['code'] == 'page_locked'
        assert body['is_locked'] is True
        assert body['lock_source'] == 'explicit'
        assert 'locked' in body['error'].lower()
        assert 'auth_required' not in json.dumps(body)
        # The page stayed private.
        assert json.loads(manifest_path.read_text())['pages']['deliverable']['is_public'] is False

    def test_agent_publish_on_entry_without_key_is_403_naming_absent_key(self, canvas_env, app_client):
        # Tonight's case: registered outside the app, no is_locked key at all.
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>done</p>')
        _write_manifest(manifest_path, {'deliverable': _entry()})
        resp = _agent_patch(app_client, 'deliverable', {'is_public': True})
        assert resp.status_code == 403
        body = resp.get_json()
        assert body['code'] == 'page_lock_unset'
        assert body['is_locked'] is None
        assert body['lock_source'] == 'absent_key'
        assert 'how_to_unlock' in body

    def test_agent_can_always_make_a_locked_page_private(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>done</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=True, is_public=True)})
        resp = _agent_patch(app_client, 'deliverable', {'is_public': False})
        assert resp.status_code == 200
        assert json.loads(manifest_path.read_text())['pages']['deliverable']['is_public'] is False

    def test_agent_cannot_change_the_lock(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>done</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=True)})
        resp = _agent_patch(app_client, 'deliverable', {'is_locked': False})
        assert resp.status_code == 403
        assert resp.get_json()['code'] == 'admin_only'

    def test_admin_can_publish_a_locked_page(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>done</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=True)})
        with _as_admin():
            resp = app_client.patch('/api/canvas/manifest/page/deliverable', json={'is_public': True})
        assert resp.status_code == 200
        assert json.loads(manifest_path.read_text())['pages']['deliverable']['is_public'] is True


# ---------------------------------------------------------------------------
# Discovery happens before the request is judged
# ---------------------------------------------------------------------------

class TestDiscoveryBeforePatch:
    def test_first_patch_after_writing_a_file_is_not_page_not_found(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'fresh.html').write_text('<h1>fresh</h1>')
        assert not manifest_path.exists()  # nothing has registered it yet
        resp = _agent_patch(app_client, 'fresh', {'description': 'just written'})
        assert resp.status_code == 200, resp.get_json()
        entry = json.loads(manifest_path.read_text())['pages']['fresh']
        assert entry['description'] == 'just written'
        assert entry['is_locked'] is False

    def test_first_publish_after_writing_a_file_hits_cooldown_not_404(self, canvas_env, app_client):
        # A just-discovered file has created == now, so the 30 s cooldown is the
        # honest answer — and it must say so, not "Page not found".
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'fresh.html').write_text('<h1>fresh</h1>')
        resp = _agent_patch(app_client, 'fresh', {'is_public': True})
        assert resp.status_code == 429
        body = resp.get_json()
        assert body['code'] == 'publish_cooldown'
        assert 0 < body['retry_after_seconds'] <= 30

    def test_get_metadata_discovers_a_new_file_too(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'fresh.html').write_text('<h1>fresh</h1>')
        resp = app_client.get('/api/canvas/manifest/page/fresh')
        assert resp.status_code == 200
        assert resp.get_json()['filename'] == 'fresh.html'

    def test_missing_file_is_404_that_names_the_cause(self, canvas_env, app_client):
        resp = _agent_patch(app_client, 'never-written', {'is_public': True})
        assert resp.status_code == 404
        body = resp.get_json()
        assert body['code'] == 'page_not_found'
        assert body['file_on_disk'] is False

    def test_html_suffixed_id_is_an_alias(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>done</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=False)})
        resp = _agent_patch(app_client, 'deliverable.html', {'is_public': True})
        assert resp.status_code == 200
        pages = json.loads(manifest_path.read_text())['pages']
        assert pages['deliverable']['is_public'] is True
        assert 'deliverable.html' not in pages


# ---------------------------------------------------------------------------
# Duplicate '<id>.html' keys are folded into '<id>'
# ---------------------------------------------------------------------------

class TestDuplicateKeyNormalisation:
    def test_sync_merges_html_keyed_duplicate_into_stem(self, canvas_env):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'client-songs.html').write_text('<p>songs</p>')
        manifest_path.write_text(json.dumps({
            'version': 1,
            'categories': {'music': {'name': 'Music', 'icon': '', 'color': '',
                                     'pages': ['client-songs', 'client-songs.html']}},
            'pages': {
                'client-songs': _entry(filename='client-songs.html', category='music', is_locked=False),
                'client-songs.html': {'filename': 'client-songs.html', 'category': 'music',
                                      'created': OLD, 'description': 'from the dup'},
            },
            'uncategorized': [], 'recently_viewed': ['client-songs.html'], 'user_custom_order': None,
        }))
        manifest = canvas.sync_canvas_manifest()
        assert 'client-songs.html' not in manifest['pages']
        merged = manifest['pages']['client-songs']
        assert merged['is_locked'] is False            # stem entry's field wins
        assert merged['description'] == 'from the dup'  # dup fills what stem lacked
        assert manifest['categories']['music']['pages'] == ['client-songs']
        assert manifest['recently_viewed'] == ['client-songs']

    def test_sync_renames_lone_html_key(self, canvas_env):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'lone.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'lone.html': _entry(filename='lone.html', is_locked=False)})
        manifest = canvas.sync_canvas_manifest()
        assert set(manifest['pages']) == {'lone'}
        assert manifest['pages']['lone']['is_locked'] is False


# ---------------------------------------------------------------------------
# PR #540 review findings — each was a way to publish a page that must stay private
# ---------------------------------------------------------------------------

def _unauth_pages_get(canvas, app_client, monkeypatch, filename):
    """What the /pages/ gate serves to an anonymous reader right now."""
    monkeypatch.setattr(canvas, 'CANVAS_REQUIRE_AUTH', True)
    monkeypatch.setattr(canvas, '_manifest_cache', {'data': None, 'mtime': 0})
    with patch('services.auth.get_token_from_request', return_value=None):
        return app_client.get(f'/pages/{filename}')


class TestNonBooleanFlagsAreRejected:
    # Pre-existing on main: guards tested `is True`, the field loop stored any
    # value, and the /pages/ gate reads it truthy — so each of these published a
    # LOCKED page inside the cooldown with the agent key.
    @pytest.mark.parametrize('val', [1, 'true', 'no', [1]])
    def test_non_bool_is_public_is_400_and_nothing_is_stored(self, canvas_env, app_client, monkeypatch, val):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>secret</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=True, created=datetime.now().isoformat())})
        resp = _agent_patch(app_client, 'deliverable', {'is_public': val})
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'invalid_type'
        assert json.loads(manifest_path.read_text())['pages']['deliverable']['is_public'] is False
        assert _unauth_pages_get(canvas, app_client, monkeypatch, 'deliverable.html').status_code != 200

    def test_non_bool_is_locked_is_400_even_for_admin(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=True)})
        with _as_admin():
            resp = app_client.patch('/api/canvas/manifest/page/deliverable', json={'is_locked': 'false'})
        assert resp.status_code == 400
        assert json.loads(manifest_path.read_text())['pages']['deliverable']['is_locked'] is True

    def test_non_object_body_is_400(self, canvas_env, app_client):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'deliverable.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'deliverable': _entry(is_locked=False)})
        resp = _agent_patch(app_client, 'deliverable', [{'is_public': True}])
        assert resp.status_code == 400


class TestAliasResolvesToTheGatedEntry:
    def test_alias_hits_stem_not_phantom_when_both_exist(self, canvas_env, app_client, monkeypatch):
        # Pre-normalisation manifest: 'foo' (locked, the entry the gate reads)
        # and a phantom 'foo.html' (unlocked). The alias must answer for 'foo'.
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'foo.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'foo': _entry(filename='foo.html', is_locked=True),
                                        'foo.html': _entry(filename='foo.html', is_locked=False)})
        resp = _agent_patch(app_client, 'foo.html', {'is_public': True})
        assert resp.status_code == 403
        assert resp.get_json()['code'] == 'page_locked'
        pages = json.loads(manifest_path.read_text())['pages']
        assert pages['foo']['is_public'] is False
        assert pages.get('foo.html', {}).get('is_public') is not True
        assert _unauth_pages_get(canvas, app_client, monkeypatch, 'foo.html').status_code != 200


class TestMergeKeepsTheMoreRestrictiveFlags:
    def test_lock_held_on_the_duplicate_survives_merge(self, canvas_env):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'foo.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'foo': _entry(filename='foo.html', is_locked=False),
                                        'foo.html': _entry(filename='foo.html', is_locked=True)})
        manifest = canvas.sync_canvas_manifest()
        assert manifest['pages']['foo']['is_locked'] is True

    def test_public_flag_on_the_duplicate_does_not_publish_the_stem(self, canvas_env):
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'foo.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'foo': _entry(filename='foo.html', is_locked=True, is_public=False),
                                        'foo.html': _entry(filename='foo.html', is_locked=False, is_public=True)})
        manifest = canvas.sync_canvas_manifest()
        assert manifest['pages']['foo']['is_public'] is False
        assert manifest['pages']['foo']['is_locked'] is True


class TestSyncNeverFlipsAnExistingEntry:
    def test_legacy_file_keyed_entry_keeps_its_absent_lock_across_an_unauth_manifest_get(self, canvas_env, app_client):
        # The sync runs on an UNAUTHENTICATED manifest GET. A pre-existing entry
        # with no is_locked key must come out of it still key-absent (locked),
        # not rewritten to is_locked:false — reviewer-measured 403 -> GET -> 200.
        canvas, pages_dir, manifest_path = canvas_env
        (pages_dir / 'legacy.html').write_text('<p>x</p>')
        _write_manifest(manifest_path, {'legacy': {'file': 'legacy.html', 'title': 'Legacy',
                                                   'created': OLD, 'is_public': False}})
        before = _agent_patch(app_client, 'legacy', {'is_public': True})
        assert before.status_code == 403 and before.get_json()['code'] == 'page_lock_unset'
        assert app_client.get('/api/canvas/manifest').status_code == 200   # anonymous, public-read prefix
        after = _agent_patch(app_client, 'legacy', {'is_public': True})
        assert after.status_code == 403 and after.get_json()['code'] == 'page_lock_unset'
        entry = json.loads(manifest_path.read_text())['pages']['legacy']
        assert 'is_locked' not in entry
        assert entry['filename'] == 'legacy.html'   # it WAS normalised, just not unlocked


# ---------------------------------------------------------------------------
# 401 names what it accepts — never confusable with a lock
# ---------------------------------------------------------------------------

class TestAuthRequiredNamesAcceptedCredentials:
    def test_401_lists_accepted_credentials(self, app_client):
        resp = app_client.patch('/api/canvas/manifest/page/anything', json={'is_public': True},
                                headers={'X-Gateway-Token': 'not-a-credential-this-gate-reads'})
        assert resp.status_code == 401
        body = resp.get_json()
        assert body['code'] == 'auth_required'
        assert any('X-Agent-Key' in c for c in body['accepted_credentials'])
        assert 'locked' not in json.dumps(body).lower()
