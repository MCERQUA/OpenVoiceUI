"""
SEC-069 — /api/vault/platform-setup is PLATFORM-admin only.

The platform-setup routes write .platform-oauth.env: the PLATFORM's shared
OAuth app client_id/client_secret, consumed by every tenant's OpenVoiceUI.
The app-level admin gate admits anyone in the TENANT's ADMIN_USER_IDS (the
tenant owner), so before this fix any tenant owner could rewrite
platform-wide OAuth credentials from their own admin UI.

Every file path is redirected into tmp_path. Nothing here touches
/mnt/system, a real vault, Clerk, or the network.
"""
import os
import stat

import pytest

PLATFORM_ADMIN = 'user_platform_operator'
TENANT_ADMIN = 'user_tenant_owner'
VOICE_USER = 'user_plain_voice'
AGENT_KEY = 'agent-key-for-tests'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Auth ON (Clerk key present), tenant admin list = {TENANT_ADMIN,
    PLATFORM_ADMIN}, platform admin list = {PLATFORM_ADMIN} only. The env file
    is a writable tmp file seeded with one provider's keys."""
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_placeholder')
    monkeypatch.setenv('AGENT_API_KEY', AGENT_KEY)
    import services.auth as auth
    import services.vault as vault
    import routes.vault as rv
    monkeypatch.setattr(auth, '_ADMIN_USER_IDS', {TENANT_ADMIN, PLATFORM_ADMIN})
    monkeypatch.setattr(auth, '_PLATFORM_ADMIN_USER_IDS', {PLATFORM_ADMIN})

    env_file = tmp_path / '.platform-oauth.env'
    env_file.write_text('# platform oauth\nGOOGLE_OAUTH_CLIENT_ID=old-id\nGOOGLE_OAUTH_CLIENT_SECRET=old-secret-value\n')
    monkeypatch.setattr(vault, '_PLATFORM_OAUTH_ENV', env_file)
    # Keep the tenant vault (for the regression test) in tmp too — same
    # redirects as test_vault_connectors.py so nothing reads /mnt.
    clients = tmp_path / 'clients'
    (clients / 'alice' / 'compose').mkdir(parents=True)
    (clients / 'alice' / 'compose' / '.openclaw.env').write_text('# tenant env\n')
    platform = tmp_path / 'platform'
    platform.mkdir()
    (platform / 'platform-credentials.json').write_text('{"version": 1, "credentials": []}')
    monkeypatch.setattr(vault, '_CLIENTS_DIR', clients)
    monkeypatch.setattr(vault, '_RUNTIME_VAULT_DIR', tmp_path / 'no-runtime-vault')
    monkeypatch.setattr(vault, '_PLATFORM_CATALOG_PATH', platform / 'platform-credentials.json')
    monkeypatch.setattr(vault, '_PLATFORM_OAUTH_PATH', platform / 'platform-oauth.json')
    monkeypatch.setattr(vault, '_PLUGINS_DIR', tmp_path / 'no-plugins')
    monkeypatch.setattr(vault, '_catalog_cache', None)
    monkeypatch.setenv('DOMAIN', 'alice.jam-bot.com')
    vault.ensure_vault('alice')

    # Whatever the first provider is, drive the test off its real env var names.
    prov = rv._PLATFORM_PROVIDERS[0]
    env_file.write_text('# platform oauth\n' + ''.join(
        f'{v}=old-{k}\n' for k, v in prov['env_vars'].items()))
    for k in prov['env_vars'].values():
        monkeypatch.delenv(k, raising=False)

    from app import create_app
    from routes.vault import vault_bp
    app, _ = create_app(config_override={'TESTING': True})
    if 'vault' not in app.blueprints:  # server.py registers it in production
        app.register_blueprint(vault_bp)

    class Env:
        pass
    e = Env()
    e.client = app.test_client()
    e.file = env_file
    e.provider = prov
    e.monkeypatch = monkeypatch
    e.auth = auth
    return e


def _as(env, user):
    env.monkeypatch.setattr(env.auth, 'get_token_from_request', lambda: 'tok' if user else None)
    env.monkeypatch.setattr(env.auth, 'verify_clerk_token', lambda t: user)


def _put(env, cid='new-id', secret='new-secret-value'):
    return env.client.put(f"/api/vault/platform-setup/{env.provider['id']}",
                          json={'client_id': cid, 'client_secret': secret})


def _delete(env):
    return env.client.delete(f"/api/vault/platform-setup/{env.provider['id']}")


# ---------------------------------------------------------------------------
# A. the identity rule itself
# ---------------------------------------------------------------------------
class TestIdentityRule:
    def test_platform_admin_is_never_derived_from_tenant_admin_list(self, env):
        assert env.auth.is_admin_user(TENANT_ADMIN) is True
        assert env.auth.is_platform_admin(TENANT_ADMIN) is False
        assert env.auth.is_platform_admin(PLATFORM_ADMIN) is True
        assert env.auth.is_platform_admin(None) is False
        assert env.auth.is_platform_admin('') is False

    def test_platform_admin_ids_come_only_from_the_env_var(self):
        import services.auth as auth
        assert auth._parse_platform_admin_ids(' user_a , user_b,, ') == {'user_a', 'user_b'}
        assert auth._parse_platform_admin_ids('') == set()
        assert auth._parse_platform_admin_ids(None) == set()


# ---------------------------------------------------------------------------
# A2. module-level defaults, measured in a fresh interpreter per case so the
#     env the module reads at import time is exactly the one under test.
#     No operator id is built into the source: unset = NO platform admin.
# ---------------------------------------------------------------------------
def _auth_sets(**env):
    import json
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    clean = {k: v for k, v in os.environ.items()
             if k not in ('ADMIN_USER_IDS', 'PLATFORM_ADMIN_USER_IDS', 'CLERK_PUBLISHABLE_KEY')}
    clean.update(env)
    code = ('import json, services.auth as a; print(json.dumps({'
            '"platform": sorted(a._PLATFORM_ADMIN_USER_IDS), "admin": sorted(a._ADMIN_USER_IDS), '
            '"is_platform_x": a.is_platform_admin("user_x")}))')
    out = subprocess.run([sys.executable, '-c', code], cwd=repo, env=clean,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestModuleDefaults:
    def test_unset_means_no_platform_admin_and_no_admin(self):
        got = _auth_sets()
        assert got == {'platform': [], 'admin': [], 'is_platform_x': False}

    def test_platform_admins_from_env_and_admin_defaults_to_them(self):
        got = _auth_sets(PLATFORM_ADMIN_USER_IDS='user_op, user_op2')
        assert got['platform'] == ['user_op', 'user_op2']
        assert got['admin'] == ['user_op', 'user_op2']

    def test_tenant_admin_list_never_widens_platform_admins(self):
        got = _auth_sets(PLATFORM_ADMIN_USER_IDS='user_op', ADMIN_USER_IDS='user_owner,user_op')
        assert got['platform'] == ['user_op']
        assert got['admin'] == ['user_op', 'user_owner']


# ---------------------------------------------------------------------------
# B. the routes
# ---------------------------------------------------------------------------
class TestTenantAdminIsRefused:
    @pytest.mark.parametrize('method', ['GET', 'PUT', 'DELETE'])
    def test_tenant_admin_gets_403_platform_admin_only(self, env, method):
        _as(env, TENANT_ADMIN)
        before = env.file.read_text()
        if method == 'GET':
            r = env.client.get('/api/vault/platform-setup')
        elif method == 'PUT':
            r = _put(env)
        else:
            r = _delete(env)
        assert r.status_code == 403, r.data
        body = r.get_json()
        assert body['code'] == 'platform_admin_only'
        assert body['error'] and '\n' not in body['error']
        # The guard is the only thing between the owner and the platform file.
        assert env.file.read_text() == before
        for k in env.provider['env_vars'].values():
            assert k not in os.environ


class TestPlatformAdminIsAllowed:
    def test_get_lists_providers(self, env):
        _as(env, PLATFORM_ADMIN)
        r = env.client.get('/api/vault/platform-setup')
        assert r.status_code == 200
        d = r.get_json()
        assert any(p['id'] == env.provider['id'] for p in d['providers'])
        assert d['env_file'] == str(env.file)

    def test_put_writes_and_is_verified_from_the_file(self, env):
        _as(env, PLATFORM_ADMIN)
        r = _put(env, 'new-id', 'new-secret-value')
        assert r.status_code == 200, r.data
        assert r.get_json()['ok'] is True
        text = env.file.read_text()
        assert f"{env.provider['env_vars']['client_id']}=new-id" in text
        assert f"{env.provider['env_vars']['client_secret']}=new-secret-value" in text
        assert '# platform oauth' in text  # comments preserved
        assert os.environ[env.provider['env_vars']['client_id']] == 'new-id'

    def test_put_same_values_twice_is_still_ok(self, env):
        """A no-op re-save is not a failure (the writer returns False for
        'nothing changed'; the file is the evidence, not the return value)."""
        _as(env, PLATFORM_ADMIN)
        assert _put(env, 'same', 'same-secret-value').status_code == 200
        assert _put(env, 'same', 'same-secret-value').status_code == 200

    def test_delete_clears(self, env):
        _as(env, PLATFORM_ADMIN)
        r = _delete(env)
        assert r.status_code == 200, r.data
        text = env.file.read_text()
        assert f"{env.provider['env_vars']['client_id']}=\n" in text
        assert f"{env.provider['env_vars']['client_secret']}=\n" in text


class TestOtherCallersUnchanged:
    def test_anonymous_gets_401(self, env):
        _as(env, None)
        assert env.client.get('/api/vault/platform-setup').status_code == 401
        assert _put(env).status_code == 401

    def test_agent_key_is_still_refused_on_admin_surface(self, env):
        """The internal agent key never reaches an admin path (unchanged)."""
        _as(env, None)
        r = env.client.put(f"/api/vault/platform-setup/{env.provider['id']}",
                           json={'client_id': 'x', 'client_secret': 'y'},
                           headers={'X-Agent-Key': AGENT_KEY})
        assert r.status_code == 401
        assert r.get_json()['code'] == 'auth_required'
        r = env.client.get('/api/vault/platform-setup', headers={'X-Agent-Key': AGENT_KEY})
        assert r.status_code == 401

    def test_plain_voice_user_still_gets_admin_required(self, env):
        _as(env, VOICE_USER)
        r = env.client.get('/api/vault/platform-setup')
        assert r.status_code == 403
        assert r.get_json()['code'] == 'admin_required'

    def test_tenant_admin_still_reaches_own_vault(self, env):
        """Regression: the stricter gate is scoped to platform-setup. The
        owner's own vault routes keep admitting the tenant admin."""
        _as(env, TENANT_ADMIN)
        r = env.client.get('/api/vault/credentials')
        assert r.status_code == 200, r.data
        assert 'groups' in r.get_json()


# ---------------------------------------------------------------------------
# C. loud failure when the file cannot be written (SEC-069 `:ro` mount)
# ---------------------------------------------------------------------------
class TestReadOnlyEnvFailsLoudly:
    def test_read_only_file_is_500_with_code_and_nothing_goes_live(self, env):
        if os.geteuid() == 0:
            pytest.skip('root ignores mode bits; os.access would say writable')
        _as(env, PLATFORM_ADMIN)
        env.file.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        before = env.file.read_text()
        try:
            r = _put(env, 'ro-id', 'ro-secret-value')
            assert r.status_code == 500, r.data
            body = r.get_json()
            assert body['code'] == 'platform_env_read_only'
            assert 'read-only' in body['error']
            assert env.file.read_text() == before
            # Not "live in this process only" either.
            assert os.environ.get(env.provider['env_vars']['client_id']) != 'ro-id'
            r = _delete(env)
            assert r.status_code == 500 and r.get_json()['code'] == 'platform_env_read_only'
        finally:
            env.file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_read_only_mount_simulated_via_os_access(self, env):
        """A `:ro` bind mount reports EROFS through os.access even when the
        mode bits say rw and the process is root — simulate that path."""
        import routes.vault as rv
        _as(env, PLATFORM_ADMIN)
        env.monkeypatch.setattr(rv, '_platform_env_writable', lambda p: False)
        r = _put(env)
        assert r.status_code == 500 and r.get_json()['code'] == 'platform_env_read_only'

    def test_write_that_does_not_land_is_500_write_failed(self, env):
        """Writer claims nothing / swallows the error → the file is re-read
        and the mismatch is reported, not 'saved'."""
        import routes.vault as rv
        _as(env, PLATFORM_ADMIN)
        env.monkeypatch.setattr(rv, '_update_env_file', lambda p, u: False, raising=False)
        import services.vault as sv
        env.monkeypatch.setattr(sv, '_update_env_file', lambda p, u: False)
        r = _put(env, 'never-written', 'never-written-secret')
        assert r.status_code == 500, r.data
        assert r.get_json()['code'] == 'platform_env_write_failed'
        assert 'never-written' not in env.file.read_text()

    def test_missing_file_in_read_only_dir_is_500_read_only(self, env, tmp_path):
        if os.geteuid() == 0:
            pytest.skip('root ignores mode bits')
        import services.vault as sv
        _as(env, PLATFORM_ADMIN)
        ro_dir = tmp_path / 'ro'
        ro_dir.mkdir()
        ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        env.monkeypatch.setattr(sv, '_PLATFORM_OAUTH_ENV', ro_dir / '.platform-oauth.env')
        try:
            r = _put(env)
            assert r.status_code == 500 and r.get_json()['code'] == 'platform_env_read_only'
        finally:
            ro_dir.chmod(stat.S_IRWXU)
