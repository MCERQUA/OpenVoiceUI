"""
One-click connectors + Supabase key validation (2026-09-27).

Every HTTP call is mocked (FakeHTTP); every vault path is redirected into
tmp_path. Nothing here touches /mnt/clients, a real vault, or the network.

Origin: a client spent a day pasting Supabase keys that were all "rejected".
They were valid keys, for his own project; his app ran on a different project.
The validator must say that in plain words, and the connector must make
pasting unnecessary.
"""
import base64
import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import requests as real_requests
from flask import Flask

REF_A = 'abcdefghijklmnopqrst'   # the project the URL points at
REF_B = 'zyxwvutsrqponmlkjihg'   # some other project


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip('=')


# Fixed ONCE at import. Two jwt() calls for the same ref/role must give the SAME token: tests compare
# a stored credential against an expected one built later, and an exp taken from time.time() per call
# made them differ whenever a clock second ticked between the two calls (flaked CI on PR #523).
_DEFAULT_EXP = int(time.time()) + 10 * 365 * 86400


def jwt(ref: str, role: str, exp: int = None) -> str:
    claims = {'iss': 'supabase', 'ref': ref, 'role': role, 'iat': 1700000000,
              'exp': exp if exp is not None else _DEFAULT_EXP}
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(claims)}.c2lnbmF0dXJl"


class FakeResp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else '')

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._body is None:
            raise ValueError('not json')
        return self._body


class FakeHTTP:
    """Routes (METHOD, url-prefix) -> FakeResp | callable(call)->FakeResp | Exception.
    First matching route wins; unmatched calls fail the test loudly."""
    Timeout = real_requests.Timeout
    RequestException = real_requests.RequestException
    ConnectionError = real_requests.ConnectionError
    HTTPError = real_requests.HTTPError

    def __init__(self):
        self.routes = []
        self.calls = []

    def on(self, method, prefix, resp):
        self.routes.insert(0, (method, prefix, resp))
        return self

    def _go(self, method, url, kw):
        call = SimpleNamespace(method=method, url=url, **{k: kw.get(k) for k in
                               ('headers', 'data', 'json', 'auth', 'params', 'timeout',
                                'allow_redirects')})
        self.calls.append(call)
        for m, prefix, resp in self.routes:
            if m == method and url.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp(call) if callable(resp) else resp
        raise AssertionError(f'unexpected HTTP {method} {url}')

    def get(self, url, **kw):
        return self._go('GET', url, kw)

    def post(self, url, **kw):
        return self._go('POST', url, kw)

    def delete(self, url, **kw):
        return self._go('DELETE', url, kw)

    def request(self, method, url, **kw):
        return self._go(method.upper(), url, kw)

    def made(self, method, prefix):
        return [c for c in self.calls if c.method == method and c.url.startswith(prefix)]


PUBLIC_IP = '104.18.38.10'   # stands in for a Supabase edge address


def _fake_resolver(table: dict):
    """host -> [addresses] | Exception. An IP literal resolves to itself (as
    getaddrinfo does); any other unlisted host resolves to PUBLIC_IP. Keeps
    every test off real DNS."""
    def resolve(host, port):
        hit = table.get(host)
        if isinstance(hit, Exception):
            raise hit
        if hit is not None:
            return list(hit)
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            return [PUBLIC_IP]
    return resolve


SUPABASE_ENTRY = {
    # mirrors the live platform catalog's supabase credential
    'id': 'supabase', 'name': 'Supabase', 'type': 'multi_field', 'group': 'Services',
    'fields': [
        {'id': 'url', 'name': 'Project URL', 'env_var': 'SUPABASE_URL', 'secret': False},
        {'id': 'anon_key', 'name': 'Anon / publishable key', 'env_var': 'SUPABASE_ANON_KEY'},
        {'id': 'service_role_key', 'name': 'Service role key', 'env_var': 'SUPABASE_SERVICE_ROLE_KEY'},
    ],
    'consumers': {'openclaw': {'type': 'env_var'}},
}
GOOGLE_ENTRY = {
    'id': 'google_analytics', 'name': 'Google Analytics', 'type': 'oauth2', 'group': 'Connections',
    'oauth': {'provider': 'google', 'scopes': ['https://www.googleapis.com/auth/analytics.readonly'],
              'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth',
              'token_url': 'https://oauth2.googleapis.com/token',
              'extra_params': {'access_type': 'offline', 'prompt': 'consent'}},
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import services.vault as vault
    import services.connectors as connectors
    clients = tmp_path / 'clients'
    (clients / 'alice' / 'compose').mkdir(parents=True)
    (clients / 'alice' / 'compose' / '.openclaw.env').write_text('# tenant env\n')
    platform = tmp_path / 'platform'
    platform.mkdir()
    catalog = platform / 'platform-credentials.json'
    catalog.write_text(json.dumps({'version': 1, 'credentials': [SUPABASE_ENTRY, GOOGLE_ENTRY]}))
    monkeypatch.setattr(vault, '_CLIENTS_DIR', clients)
    monkeypatch.setattr(vault, '_RUNTIME_VAULT_DIR', tmp_path / 'no-runtime-vault')
    monkeypatch.setattr(vault, '_PLATFORM_CATALOG_PATH', catalog)
    monkeypatch.setattr(vault, '_PLATFORM_OAUTH_PATH', platform / 'platform-oauth.json')
    monkeypatch.setattr(vault, '_PLUGINS_DIR', tmp_path / 'no-plugins')
    monkeypatch.setattr(vault, '_catalog_cache', None)
    restarts = []
    monkeypatch.setattr(vault, '_schedule_container_restart', restarts.append)
    for k in list(os.environ):
        if (k.endswith(('_OAUTH_CLIENT_ID', '_OAUTH_CLIENT_SECRET', '_OAUTH_APP_ID', '_OAUTH_APP_SECRET'))
                or k.startswith(('OAUTH_RELAY', 'SUPABASE_', 'CLERK_'))):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('DOMAIN', 'alice.jam-bot.com')
    http = FakeHTTP()
    monkeypatch.setattr(vault, 'requests', http)
    monkeypatch.setattr(connectors, 'requests', http)
    dns = {}
    real_resolve = getattr(connectors, '_resolve_addresses', None)
    monkeypatch.setattr(connectors, '_resolve_addresses', _fake_resolver(dns), raising=False)
    vault.ensure_vault('alice')
    return SimpleNamespace(vault=vault, connectors=connectors, http=http, restarts=restarts,
                           clients=clients, user='alice', monkeypatch=monkeypatch,
                           dns=dns, real_resolve=real_resolve)


@pytest.fixture()
def client(env):
    from routes.vault import vault_bp
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(vault_bp)
    return app.test_client()


def _creds(env):
    return {c['id']: c for c in env.vault.get_credentials_status(env.user)}


def _platform_on(env, provider):
    env.monkeypatch.setenv(f'{provider.upper()}_OAUTH_CLIENT_ID', f'{provider}-client-id')
    env.monkeypatch.setenv(f'{provider.upper()}_OAUTH_CLIENT_SECRET', f'{provider}-client-secret')


def _relay_on(env):
    env.monkeypatch.setenv('OAUTH_RELAY_BASE_URL', 'https://oauth.jam-bot.com')
    env.monkeypatch.setenv('OAUTH_RELAY_ALLOWED_DOMAINS', '.jam-bot.com')


def _probe_ok(env, ref=REF_A):
    env.http.on('GET', f'https://{ref}.supabase.co/rest/v1/', FakeResp(200, {'swagger': '2.0'}))
    env.http.on('GET', f'https://{ref}.supabase.co/auth/v1/settings', FakeResp(200, {'external': {}}))


def _probe_invalid(env, ref=REF_A):
    bad = FakeResp(401, {'message': 'Invalid API key', 'hint': 'Double check your Supabase `anon` or `service_role` API key.'})
    env.http.on('GET', f'https://{ref}.supabase.co/rest/v1/', bad)
    env.http.on('GET', f'https://{ref}.supabase.co/auth/v1/settings', bad)


# ---------------------------------------------------------------------------
# A. Validation — the known-bad inputs
# ---------------------------------------------------------------------------
class TestSupabaseValidation:
    def test_keys_from_another_project_fail_offline_and_name_both_projects(self, env):
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co',
            'anon_key': jwt(REF_B, 'anon'),
            'service_role_key': jwt(REF_B, 'service_role'),
        })
        assert v['verdict'] == 'fail'
        assert REF_A in v['message'] and REF_B in v['message']
        assert 'same project' in v['message']
        assert env.http.calls == [], 'a ref mismatch is definitive; no network needed'

    def test_service_role_key_in_anon_box_fails(self, env):
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'anon_key': jwt(REF_A, 'service_role')})
        assert v['verdict'] == 'fail' and 'secret key' in v['message']

    def test_publishable_key_in_service_box_fails(self, env):
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'service_role_key': 'sb_publishable_abc123'})
        assert v['verdict'] == 'fail' and 'public' in v['message']

    def test_invalid_api_key_401_is_a_plain_words_fail(self, env):
        _probe_invalid(env)
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'service_role_key': 'sb_secret_deleted_key_000'})
        assert v['verdict'] == 'fail'
        assert 'rejected' in v['message'] and 'different project' in v['message']
        call = env.http.made('GET', f'https://{REF_A}.supabase.co/rest/v1/')[0]
        assert call.headers['apikey'] == 'sb_secret_deleted_key_000'
        assert 'Authorization' not in call.headers, 'new-style keys are not JWTs; no Bearer'

    def test_right_project_but_rotated_key(self, env):
        _probe_invalid(env)
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'service_role_key': jwt(REF_A, 'service_role')})
        assert v['verdict'] == 'fail' and 'rotated' in v['message']

    def test_expired_jwt_fails(self, env):
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'anon_key': jwt(REF_A, 'anon', exp=1000)})
        assert v['verdict'] == 'fail' and 'expired' in v['message']

    def test_both_keys_accepted_pass(self, env):
        _probe_ok(env)
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co',
            'anon_key': jwt(REF_A, 'anon'), 'service_role_key': jwt(REF_A, 'service_role')})
        assert v['verdict'] == 'pass', v
        assert env.http.made('GET', f'https://{REF_A}.supabase.co/rest/v1/'), 'secret probed on /rest/v1/'
        assert env.http.made('GET', f'https://{REF_A}.supabase.co/auth/v1/settings'), 'anon probed on /auth/v1/settings'

    def test_unreachable_is_cannot_tell_never_pass(self, env):
        env.http.on('GET', f'https://{REF_A}.supabase.co', real_requests.ConnectionError('dns'))
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'service_role_key': jwt(REF_A, 'service_role')})
        assert v['verdict'] == 'cannot_tell'

    def test_paused_project_5xx_is_cannot_tell(self, env):
        env.http.on('GET', f'https://{REF_A}.supabase.co', FakeResp(540, text='paused'))
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'anon_key': jwt(REF_A, 'anon')})
        assert v['verdict'] == 'cannot_tell' and 'paused' in v['message']

    def test_no_url_is_cannot_tell(self, env):
        v = env.connectors.validate_supabase_fields({'anon_key': jwt(REF_A, 'anon')})
        assert v['verdict'] == 'cannot_tell' and 'Project URL' in v['message']

    @pytest.mark.parametrize('raw', [
        f'https://supabase.com/dashboard/project/{REF_A}/settings/api',
        f'https://{REF_A}.supabase.co/rest/v1/',
        f'{REF_A}.supabase.co',
    ])
    def test_url_normalization(self, env, raw):
        base, ref, _note = env.connectors.normalize_supabase_url(raw)
        assert base == f'https://{REF_A}.supabase.co' and ref == REF_A


# ---------------------------------------------------------------------------
# A2. The Project URL is typed by the user and the SERVER fetches it (SSRF).
#     Review of PR #515: a URL that redirected to http://127.0.0.1:<port>/
#     returned internal-only data in the save response; a malformed port or
#     IPv6 bracket turned the save into an HTTP 500.
# ---------------------------------------------------------------------------
SECRET_BODY = '{"admin_token": "INTERNAL-ONLY-7f3a9c"}'


class TestProjectUrlGuard:
    def test_probe_never_follows_redirects(self, env):
        env.http.on('GET', f'https://{REF_A}.supabase.co', FakeResp(
            302, text=SECRET_BODY))
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co',
            'anon_key': jwt(REF_A, 'anon'), 'service_role_key': jwt(REF_A, 'service_role')})
        assert env.http.calls, 'the public project itself is still probed'
        assert all(c.allow_redirects is False for c in env.http.calls), \
            [c.allow_redirects for c in env.http.calls]
        assert v['verdict'] == 'cannot_tell', 'a redirect is not an answer about the key'
        assert 'INTERNAL-ONLY' not in json.dumps(v)

    @pytest.mark.parametrize('status', [302, 404, 418])
    def test_remote_body_is_never_echoed(self, env, status):
        env.http.on('GET', f'https://{REF_A}.supabase.co', FakeResp(status, text=SECRET_BODY))
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co', 'anon_key': jwt(REF_A, 'anon')})
        assert v['verdict'] == 'cannot_tell'
        assert str(status) in v['message'], 'the status code is the whole report'
        assert 'INTERNAL-ONLY' not in json.dumps(v) and 'admin_token' not in json.dumps(v)

    @pytest.mark.parametrize('host,addrs', [
        ('loopback.example.com', ['127.0.0.1']),
        ('rfc1918.example.com', ['10.0.0.5']),
        ('home.example.com', ['192.168.1.20']),
        ('metadata.example.com', ['169.254.169.254']),
        ('tailnet.example.com', ['100.117.10.28']),       # CGNAT / Tailscale: is_private is False here
        ('v6loop.example.com', ['::1']),
        ('mapped.example.com', ['::ffff:127.0.0.1']),
        ('reserved.example.com', ['240.0.0.1']),
        ('multicast.example.com', ['224.0.0.1']),
        ('split.example.com', [PUBLIC_IP, '127.0.0.1']),  # ONE bad address refuses the host
    ])
    def test_host_resolving_to_a_non_public_address_is_refused_without_fetching(self, env, host, addrs):
        env.dns[host] = addrs
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{host}', 'anon_key': 'sb_publishable_x', 'service_role_key': 'sb_secret_x'})
        assert env.http.calls == [], 'refused means never fetched'
        assert v['verdict'] == 'fail'
        assert 'not a valid Project URL' in v['message']

    @pytest.mark.parametrize('url', [
        'https://127.0.0.1', 'https://10.0.0.1:8443', 'https://[::1]', 'https://0.0.0.0',
        f'http://{REF_A}.supabase.co',                    # https only
        'file:///etc/passwd',
    ])
    def test_literal_and_non_https_urls_are_refused_without_fetching(self, env, url):
        v = env.connectors.validate_supabase_fields({'url': url, 'anon_key': 'sb_publishable_x'})
        assert env.http.calls == []
        assert v['verdict'] == 'fail' and 'not a valid Project URL' in v['message']

    def test_real_resolver_refuses_loopback(self, env):
        """The same guard with the production resolver (a literal needs no DNS)."""
        env.monkeypatch.setattr(env.connectors, '_resolve_addresses', env.real_resolve, raising=False)
        v = env.connectors.validate_supabase_fields({'url': 'https://127.0.0.1:1', 'anon_key': 'sb_publishable_x'})
        assert env.http.calls == [] and v['verdict'] == 'fail'

    def test_unresolvable_host_is_cannot_tell_without_fetching(self, env):
        import socket
        env.dns['gone.example.com'] = socket.gaierror(-2, 'Name or service not known')
        v = env.connectors.validate_supabase_fields({'url': 'https://gone.example.com', 'anon_key': 'sb_publishable_x'})
        assert env.http.calls == []
        assert v['verdict'] == 'cannot_tell', 'no answer is not a verdict about the key'
        assert 'nothing answered' in v['message']

    def test_unresolvable_host_still_runs_the_offline_checks(self, env):
        import socket
        env.dns[f'{REF_A}.supabase.co'] = socket.gaierror(-2, 'Name or service not known')
        v = env.connectors.validate_supabase_fields({
            'url': f'https://{REF_A}.supabase.co',
            'anon_key': jwt(REF_B, 'anon'), 'service_role_key': jwt(REF_B, 'service_role')})
        assert env.http.calls == []
        assert v['verdict'] == 'fail' and REF_B in v['message'], 'a key/URL mismatch needs no network'

    @pytest.mark.parametrize('url', ['https://abc.supabase.co:99999', 'https://[::1', 'https://abc.supabase.co:port'])
    def test_malformed_url_is_an_invalid_verdict_not_an_exception(self, env, url):
        base, ref, _ = env.connectors.normalize_supabase_url(url)
        assert base == ''
        v = env.connectors.validate_supabase_fields({'url': url, 'anon_key': jwt(REF_A, 'anon')})
        assert env.http.calls == []
        assert v['verdict'] == 'fail' and 'not a valid Project URL' in v['message']

    @pytest.mark.parametrize('url', ['https://abc.supabase.co:99999', 'https://[::1'])
    def test_malformed_url_save_is_422_with_save_anyway_not_500(self, env, client, url):
        r = client.put('/api/vault/credentials/supabase', json={'fields': {
            'url': url, 'anon_key': jwt(REF_A, 'anon')}})
        assert r.status_code == 422
        d = r.get_json()
        assert d['verdict'] == 'fail' and d['can_force'] is True and d['saved'] is False
        assert 'not a valid Project URL' in d['message']

    def test_ssrf_save_is_refused_and_leaks_nothing(self, env, client):
        env.dns['internal.example.com'] = ['127.0.0.1']
        r = client.put('/api/vault/credentials/supabase', json={'fields': {
            'url': 'https://internal.example.com', 'anon_key': 'sb_publishable_x'}})
        assert r.status_code == 422 and r.get_json()['verdict'] == 'fail'
        assert env.http.calls == []
        assert 'supabase' not in env.vault.read_vault(env.user).get('credentials', {})

    def test_real_requests_does_not_follow_a_redirect_to_an_internal_server(self, env):
        """Second layer, real sockets: even if a host passed the address check
        (DNS rebinding between check and fetch), the probe does not follow a
        redirect, so an internal service is never reached through it."""
        hits = []

        class Internal(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                body = SECRET_BODY.encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        internal = HTTPServer(('127.0.0.1', 0), Internal)
        target = f'http://127.0.0.1:{internal.server_address[1]}/internal'

        class Redirector(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', target)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *a):
                pass

        redirector = HTTPServer(('127.0.0.1', 0), Redirector)
        for srv in (internal, redirector):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            env.monkeypatch.setattr(env.connectors, 'requests', real_requests)
            env.monkeypatch.setattr(env.connectors, '_fetch_refusal', lambda _url: None, raising=False)
            v = env.connectors.validate_supabase_fields({
                'url': f'http://127.0.0.1:{redirector.server_address[1]}', 'anon_key': 'sb_publishable_x'})
        finally:
            for srv in (internal, redirector):
                srv.shutdown()
                srv.server_close()
        assert hits == [], 'the redirect was followed to the internal server'
        assert v['verdict'] == 'cannot_tell' and 'INTERNAL-ONLY' not in json.dumps(v)


# ---------------------------------------------------------------------------
# B. The paste path: validate before save, and the card tells the truth
# ---------------------------------------------------------------------------
class TestPastePath:
    def test_rejected_key_is_not_saved(self, env, client):
        _probe_invalid(env)
        r = client.put('/api/vault/credentials/supabase', json={'fields': {
            'url': f'https://{REF_A}.supabase.co', 'service_role_key': 'sb_secret_wrong_project'}})
        assert r.status_code == 422
        d = r.get_json()
        assert d['ok'] is False and d['saved'] is False and d['verdict'] == 'fail'
        assert 'rejected' in d['message'] and d['can_force'] is True
        assert 'supabase' not in env.vault.read_vault(env.user).get('credentials', {})
        assert env.restarts == []

    def test_mismatched_project_message_reaches_the_user(self, env, client):
        r = client.put('/api/vault/credentials/supabase', json={'fields': {
            'url': f'https://{REF_A}.supabase.co',
            'anon_key': jwt(REF_B, 'anon'), 'service_role_key': jwt(REF_B, 'service_role')}})
        assert r.status_code == 422
        assert REF_B in r.get_json()['message']

    def test_forced_save_reads_as_rejected_never_working(self, env, client):
        _probe_invalid(env)
        r = client.put('/api/vault/credentials/supabase', json={'force': True, 'fields': {
            'url': f'https://{REF_A}.supabase.co', 'anon_key': 'sb_publishable_x',
            'service_role_key': 'sb_secret_wrong_project'}})
        assert r.status_code == 200 and r.get_json()['verdict'] == 'fail'
        card = _creds(env)['supabase']
        assert card['has_value'] is True
        assert card['validation']['verdict'] == 'fail', 'configured must not read as working'

    def test_good_keys_save_as_pass_and_sync(self, env, client):
        _probe_ok(env)
        r = client.put('/api/vault/credentials/supabase', json={'fields': {
            'url': f'https://{REF_A}.supabase.co/rest/v1/',
            'anon_key': jwt(REF_A, 'anon'), 'service_role_key': jwt(REF_A, 'service_role')}})
        d = r.get_json()
        assert r.status_code == 200 and d['ok'] and d['verdict'] == 'pass'
        saved = env.vault.get_credential_fields(env.user, 'supabase')
        assert saved['url'] == f'https://{REF_A}.supabase.co', 'trailing /rest/v1/ removed before saving'
        assert _creds(env)['supabase']['validation']['verdict'] == 'pass'
        envfile = (env.clients / 'alice' / 'compose' / '.openclaw.env').read_text()
        assert f'SUPABASE_URL=https://{REF_A}.supabase.co' in envfile
        assert env.restarts == ['openclaw-alice']
        stored = json.dumps(env.vault.read_vault(env.user)['credentials']['supabase']['validation'])
        assert 'eyJ' not in stored and 'sb_secret' not in stored, 'no key material in the verdict record'

    def test_partial_save_is_checked_against_the_saved_url(self, env, client):
        _probe_ok(env)
        env.vault.set_credential(env.user, 'supabase', fields={'url': f'https://{REF_A}.supabase.co'})
        r = client.put('/api/vault/credentials/supabase', json={'fields': {
            'service_role_key': jwt(REF_A, 'service_role')}})
        assert r.get_json()['verdict'] == 'pass'
        assert env.http.made('GET', f'https://{REF_A}.supabase.co/rest/v1/')

    def test_verdict_goes_stale_when_values_change(self, env, client):
        _probe_ok(env)
        client.put('/api/vault/credentials/supabase', json={'fields': {
            'url': f'https://{REF_A}.supabase.co', 'anon_key': jwt(REF_A, 'anon'),
            'service_role_key': jwt(REF_A, 'service_role')}})
        env.vault.set_credential(env.user, 'supabase', fields={'anon_key': jwt(REF_A, 'anon', exp=2 ** 40)})
        assert _creds(env)['supabase']['validation']['verdict'] == 'unchecked'

    def test_test_button_runs_validator_and_stores_verdict(self, env, client):
        env.vault.set_credential(env.user, 'supabase', fields={
            'url': f'https://{REF_A}.supabase.co', 'service_role_key': 'sb_secret_gone'})
        assert _creds(env)['supabase']['has_test'] is True
        _probe_invalid(env)
        d = client.post('/api/vault/credentials/supabase/test').get_json()
        assert d['ok'] is False and d['verdict'] == 'fail'
        assert _creds(env)['supabase']['validation']['verdict'] == 'fail'


# ---------------------------------------------------------------------------
# C. OAuth plumbing: authorize URL, PKCE, callback, refresh
# ---------------------------------------------------------------------------
def _authorize(env, cred_id):
    url = env.vault.build_oauth_url(cred_id, env.user, 'alice.jam-bot.com')
    assert url, f'{cred_id} authorize URL not built'
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    return url, q, json.loads(q['state'])


class TestOAuth:
    def test_connectors_disabled_until_platform_app_registered(self, env):
        cards = _creds(env)
        for cid in ('supabase_connect', 'github', 'netlify_connect'):
            assert cards[cid]['platform_configured'] is False
            assert env.vault.build_oauth_url(cid, env.user, 'alice.jam-bot.com') is None
        _platform_on(env, 'github')
        assert _creds(env)['github']['platform_configured'] is True

    def test_platform_catalog_entry_wins_over_builtin(self, env, tmp_path):
        custom = dict(GOOGLE_ENTRY, id='github', name='GitHub (operator)')
        env.vault._PLATFORM_CATALOG_PATH.write_text(json.dumps({'credentials': [SUPABASE_ENTRY, custom]}))
        env.monkeypatch.setattr(env.vault, '_catalog_cache', None)
        names = [c['name'] for c in env.vault.get_platform_catalog() if c['id'] == 'github']
        assert names == ['GitHub (operator)']

    def test_supabase_authorize_url_relay_pkce_no_scope(self, env):
        _platform_on(env, 'supabase')
        _relay_on(env)
        url, q, state = _authorize(env, 'supabase_connect')
        assert url.startswith('https://api.supabase.com/v1/oauth/authorize?')
        assert q['redirect_uri'] == 'https://oauth.jam-bot.com/api/vault/oauth/relay/supabase'
        assert q['response_type'] == 'code' and 'scope' not in q
        assert q['code_challenge_method'] == 'S256' and len(q['code_challenge']) == 43
        assert state['d'] == 'alice.jam-bot.com' and state['nonce']
        store = json.loads((env.vault._oauth_nonce_path(env.user)).read_text())
        cv = store[state['nonce']]['cv']
        expect = base64.urlsafe_b64encode(hashlib.sha256(cv.encode()).digest()).decode().rstrip('=')
        assert expect == q['code_challenge']

    def test_without_relay_falls_back_to_tenant_callback(self, env):
        _platform_on(env, 'supabase')
        _url, q, _ = _authorize(env, 'supabase_connect')
        assert q['redirect_uri'] == 'https://alice.jam-bot.com/api/vault/oauth/callback/supabase'

    def test_existing_google_flow_unchanged(self, env):
        _platform_on(env, 'google')
        _relay_on(env)
        _url, q, _ = _authorize(env, 'google_analytics')
        assert q['redirect_uri'] == 'https://alice.jam-bot.com/api/vault/oauth/callback/google'
        assert q['scope'] == 'https://www.googleapis.com/auth/analytics.readonly'
        assert q['access_type'] == 'offline' and 'code_challenge' not in q

    def _supabase_callback(self, env, client, token_body=None):
        _platform_on(env, 'supabase')
        _relay_on(env)
        _url, q, state = _authorize(env, 'supabase_connect')
        env.http.on('POST', 'https://api.supabase.com/v1/oauth/token', FakeResp(200, token_body or {
            'access_token': 'sbp_oauth_access', 'refresh_token': 'sbp_oauth_refresh',
            'expires_in': 86400, 'token_type': 'Bearer'}))
        env.http.on('GET', 'https://api.supabase.com/v1/profile', FakeResp(200, {
            'gotrue_id': 'u1', 'primary_email': 'owner@example.com', 'username': 'owner'}))
        env.http.on('GET', 'https://api.supabase.com/v1/organizations', FakeResp(200, [
            {'id': 'org1', 'slug': 'org1', 'name': 'Acme Co'}]))
        r = client.get('/api/vault/oauth/callback/supabase', query_string={'code': 'the-code', 'state': q['state']})
        return r, q, state

    def test_supabase_callback_basic_auth_pkce_and_storage(self, env, client):
        r, q, _state = self._supabase_callback(env, client)
        assert r.status_code == 200 and b'Connected' in r.data
        tok = env.http.made('POST', 'https://api.supabase.com/v1/oauth/token')[0]
        assert tok.auth == ('supabase-client-id', 'supabase-client-secret'), 'Basic auth per Supabase docs'
        assert 'client_secret' not in tok.data
        assert tok.data['grant_type'] == 'authorization_code' and tok.data['code'] == 'the-code'
        assert tok.data['redirect_uri'] == q['redirect_uri'], 'must equal the authorize redirect_uri'
        assert tok.data['code_verifier'], 'PKCE verifier sent'
        st = env.vault.get_oauth_status(env.user, 'supabase_connect')
        assert st['connected'] and st['account'] == 'owner@example.com'
        assert st['account_detail']['orgs'] == [{'slug': 'org1', 'name': 'Acme Co'}]
        tf = env.vault._vault_oauth_path(env.user, 'supabase_connect')
        assert oct(tf.stat().st_mode & 0o777) == '0o600'

    def test_callback_nonce_is_single_use(self, env, client):
        _r, q, _ = self._supabase_callback(env, client)
        r2 = client.get('/api/vault/oauth/callback/supabase', query_string={'code': 'again', 'state': q['state']})
        assert b'CSRF' in r2.data
        assert len(env.http.made('POST', 'https://api.supabase.com/v1/oauth/token')) == 1

    def test_forged_state_never_exchanges(self, env, client):
        _platform_on(env, 'supabase')
        forged = json.dumps({'cred_id': 'supabase_connect', 'username': 'alice', 'nonce': 'guess', 'd': 'alice.jam-bot.com'})
        r = client.get('/api/vault/oauth/callback/supabase', query_string={'code': 'x', 'state': forged})
        assert b'CSRF' in r.data and not env.http.made('POST', 'https://api.supabase.com')

    def test_github_token_error_in_200_is_a_failure(self, env, client):
        _platform_on(env, 'github')
        _relay_on(env)
        _url, q, _ = _authorize(env, 'github')
        assert q['scope'] == 'repo read:user' and q['code_challenge_method'] == 'S256'
        env.http.on('POST', 'https://github.com/login/oauth/access_token', FakeResp(200, {
            'error': 'bad_verification_code', 'error_description': 'The code passed is incorrect or expired.'}))
        r = client.get('/api/vault/oauth/callback/github', query_string={'code': 'bad', 'state': q['state']})
        assert b'Connection Failed' in r.data and b'incorrect or expired' in r.data
        tok = env.http.made('POST', 'https://github.com/login/oauth/access_token')[0]
        assert tok.headers['Accept'] == 'application/json'
        assert tok.data['client_id'] == 'github-client-id' and tok.auth is None
        assert env.vault.get_oauth_status(env.user, 'github') == {'connected': False}

    def test_netlify_token_without_refresh_or_expiry_stays_connected(self, env, client):
        _platform_on(env, 'netlify')
        _relay_on(env)
        _url, q, _ = _authorize(env, 'netlify_connect')
        assert 'scope' not in q and 'code_challenge' not in q
        env.http.on('POST', 'https://api.netlify.com/oauth/token', FakeResp(200, {
            'access_token': 'nf_access', 'token_type': 'Bearer'}))
        env.http.on('GET', 'https://api.netlify.com/api/v1/user', FakeResp(200, {'email': 'j@example.com'}))
        client.get('/api/vault/oauth/callback/netlify', query_string={'code': 'c', 'state': q['state']})
        assert env.vault.get_oauth_status(env.user, 'netlify_connect')['connected'] is True
        assert _creds(env)['netlify_connect']['has_value'] is True
        assert env.vault.get_fresh_oauth_token(env.user, 'netlify_connect') == 'nf_access'

    def test_expired_token_refreshes_with_env_app_creds(self, env, client):
        self._supabase_callback(env, client)
        tf = env.vault._vault_oauth_path(env.user, 'supabase_connect')
        data = json.loads(tf.read_text())
        data['expires_at'] = '2000-01-01T00:00:00+00:00'
        tf.write_text(json.dumps(data))
        env.http.on('POST', 'https://api.supabase.com/v1/oauth/token', FakeResp(200, {
            'access_token': 'sbp_new_access', 'refresh_token': 'sbp_rotated', 'expires_in': 3600}))
        assert env.vault.get_fresh_oauth_token(env.user, 'supabase_connect') == 'sbp_new_access'
        ref_call = env.http.made('POST', 'https://api.supabase.com/v1/oauth/token')[-1]
        assert ref_call.data['grant_type'] == 'refresh_token' and ref_call.data['refresh_token'] == 'sbp_oauth_refresh'
        assert ref_call.auth == ('supabase-client-id', 'supabase-client-secret')
        assert json.loads(tf.read_text())['refresh_token'] == 'sbp_rotated'

    def test_disconnect_revokes_and_keeps_token_file_aside(self, env, client):
        self._supabase_callback(env, client)
        env.http.on('POST', 'https://api.supabase.com/v1/oauth/revoke', FakeResp(204, text=''))
        env.vault.set_credential(env.user, 'supabase', fields={'url': f'https://{REF_A}.supabase.co'})
        client.post('/api/vault/oauth/supabase_connect/disconnect')
        rv = env.http.made('POST', 'https://api.supabase.com/v1/oauth/revoke')[0]
        assert rv.json['refresh_token'] == 'sbp_oauth_refresh'
        tf = env.vault._vault_oauth_path(env.user, 'supabase_connect')
        assert not tf.exists() and tf.with_suffix('.revoked').exists()
        assert env.vault.get_credential_fields(env.user, 'supabase')['url'], 'filled keys stay'


# ---------------------------------------------------------------------------
# D. The single-callback relay
# ---------------------------------------------------------------------------
class TestRelay:
    def _state(self, d):
        return json.dumps({'cred_id': 'supabase_connect', 'username': 'alice', 'nonce': 'n', 'd': d})

    def test_forwards_code_and_state_to_allowed_tenant(self, env, client):
        _relay_on(env)
        st = self._state('alice.jam-bot.com')
        r = client.get('/api/vault/oauth/relay/supabase', query_string={'code': 'c0de', 'state': st})
        assert r.status_code == 302
        loc = urlparse(r.headers['Location'])
        assert (loc.scheme, loc.netloc, loc.path) == ('https', 'alice.jam-bot.com', '/api/vault/oauth/callback/supabase')
        q = {k: v[0] for k, v in parse_qs(loc.query).items()}
        assert q == {'code': 'c0de', 'state': st}
        assert r.headers['Cache-Control'] == 'no-store'

    def test_forwards_provider_errors_too(self, env, client):
        _relay_on(env)
        r = client.get('/api/vault/oauth/relay/github', query_string={
            'error': 'access_denied', 'state': self._state('alice.jam-bot.com')})
        assert r.status_code == 302 and 'error=access_denied' in r.headers['Location']

    @pytest.mark.parametrize('d', ['evil.com', 'jam-bot.com.evil.com', 'eviljam-bot.com', '', 'a b.jam-bot.com',
                                   'alice.jam-bot.com/../x', 'alice.jam-bot.com@evil.com'])
    def test_refuses_domains_off_the_allow_list(self, env, client, d):
        _relay_on(env)
        r = client.get('/api/vault/oauth/relay/supabase', query_string={'code': 'c', 'state': self._state(d)})
        assert r.status_code == 400 and 'Location' not in r.headers

    def test_empty_allow_list_fails_closed(self, env, client):
        env.monkeypatch.setenv('OAUTH_RELAY_BASE_URL', 'https://oauth.jam-bot.com')
        r = client.get('/api/vault/oauth/relay/supabase', query_string={
            'code': 'c', 'state': self._state('alice.jam-bot.com')})
        assert r.status_code == 400

    def test_relay_is_public_but_connectors_api_is_not(self, env, monkeypatch):
        """With Clerk on, the provider's redirect (no session) must reach the
        relay, while the connector APIs stay behind admin auth."""
        monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_placeholder')
        _relay_on(env)
        import services.auth as auth
        monkeypatch.setattr(auth, 'get_token_from_request', lambda: None)
        monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: None)
        from app import create_app
        from routes.vault import vault_bp
        app, _ = create_app(config_override={'TESTING': True})
        if 'vault' not in app.blueprints:  # server.py registers it in production
            app.register_blueprint(vault_bp)
        c = app.test_client()
        r = c.get('/api/vault/oauth/relay/supabase', query_string={
            'code': 'c', 'state': self._state('alice.jam-bot.com')})
        assert r.status_code == 302
        assert c.get('/api/vault/connectors').status_code == 401
        assert c.get('/api/vault/connectors/supabase/projects').status_code == 401


# ---------------------------------------------------------------------------
# E. Project picker + key fill
# ---------------------------------------------------------------------------
def _connect_supabase(env, access='sbp_live_access'):
    tf = env.vault._vault_oauth_path(env.user, 'supabase_connect')
    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(json.dumps({'access_token': access, 'refresh_token': 'r', 'expires_at': '2999-01-01T00:00:00+00:00',
                              'connected_account': 'owner@example.com'}))


def _mgmt(env, projects=None, keys=None):
    env.http.on('GET', 'https://api.supabase.com/v1/profile', FakeResp(200, {'primary_email': 'owner@example.com'}))
    env.http.on('GET', 'https://api.supabase.com/v1/organizations', FakeResp(200, [{'slug': 'org1', 'name': 'Acme Co'}]))
    env.http.on('GET', 'https://api.supabase.com/v1/projects', FakeResp(200, projects if projects is not None else [
        {'ref': REF_A, 'name': 'Main App', 'organization_slug': 'org1', 'region': 'us-east-1', 'status': 'ACTIVE_HEALTHY'},
    ]))
    env.http.on('GET', f'https://api.supabase.com/v1/projects/{REF_A}/api-keys', FakeResp(200, keys if keys is not None else [
        {'name': 'anon', 'type': 'legacy', 'api_key': jwt(REF_A, 'anon')},
        {'name': 'service_role', 'type': 'legacy', 'api_key': jwt(REF_A, 'service_role')},
        {'name': 'default', 'type': 'publishable', 'api_key': 'sb_publishable_new'},
        {'name': 'default', 'type': 'secret', 'api_key': 'sb_secret_new'},
    ]))


class TestProjectPicker:
    def test_not_connected(self, env, client):
        r = client.get('/api/vault/connectors/supabase/projects')
        assert r.status_code == 409 and r.get_json()['code'] == 'not_connected'

    def test_lists_projects_and_flags_a_saved_url_outside_the_org(self, env, client):
        _connect_supabase(env)
        _mgmt(env)
        env.vault.set_credential(env.user, 'supabase', fields={'url': f'https://{REF_B}.supabase.co'})
        d = client.get('/api/vault/connectors/supabase/projects').get_json()
        assert d['account']['email'] == 'owner@example.com'
        assert [p['ref'] for p in d['projects']] == [REF_A]
        assert d['projects'][0]['organization_name'] == 'Acme Co'
        assert d['current'] == {'url': f'https://{REF_B}.supabase.co', 'ref': REF_B, 'in_list': False}
        call = env.http.made('GET', 'https://api.supabase.com/v1/projects')[0]
        assert call.headers['Authorization'] == 'Bearer sbp_live_access'

    def test_revoked_connection_says_reconnect(self, env, client):
        _connect_supabase(env)
        env.http.on('GET', 'https://api.supabase.com/v1/', FakeResp(401, {'message': 'Unauthorized'}))
        r = client.get('/api/vault/connectors/supabase/projects')
        assert r.status_code == 401 and 'connect Supabase again' in r.get_json()['message']

    def test_select_fills_the_existing_credential(self, env, client):
        _connect_supabase(env)
        _mgmt(env)
        _probe_ok(env)
        r = client.post('/api/vault/connectors/supabase/select', json={'ref': REF_A})
        d = r.get_json()
        assert r.status_code == 200 and d['ok'] and d['verdict'] == 'pass' and d['key_type'] == 'legacy'
        keys_call = env.http.made('GET', f'https://api.supabase.com/v1/projects/{REF_A}/api-keys')[0]
        assert keys_call.params == {'reveal': 'true'}
        f = env.vault.get_credential_fields(env.user, 'supabase')
        assert f == {'url': f'https://{REF_A}.supabase.co', 'anon_key': jwt(REF_A, 'anon'),
                     'service_role_key': jwt(REF_A, 'service_role')}
        card = _creds(env)['supabase']
        assert card['validation']['verdict'] == 'pass'
        assert card['oauth_project']['name'] == 'Main App'
        assert env.vault.read_vault(env.user)['credentials']['supabase']['source'] == 'oauth'
        envfile = (env.clients / 'alice' / 'compose' / '.openclaw.env').read_text()
        assert 'SUPABASE_SERVICE_ROLE_KEY=' in envfile and env.restarts == ['openclaw-alice']

    def test_select_falls_back_to_new_keys_when_legacy_rejected(self, env, client):
        _connect_supabase(env)
        _mgmt(env)

        def probe(call):
            return FakeResp(200, {}) if call.headers['apikey'].startswith('sb_') else \
                FakeResp(401, {'message': 'Invalid API key'})
        env.http.on('GET', f'https://{REF_A}.supabase.co', probe)
        d = client.post('/api/vault/connectors/supabase/select', json={'ref': REF_A}).get_json()
        assert d['ok'] and d['key_type'] == 'new'
        assert env.vault.get_credential_fields(env.user, 'supabase')['service_role_key'] == 'sb_secret_new'

    def test_select_refuses_a_project_outside_the_connection(self, env, client):
        _connect_supabase(env)
        _mgmt(env)
        r = client.post('/api/vault/connectors/supabase/select', json={'ref': REF_B})
        assert r.status_code == 404
        assert 'supabase' not in env.vault.read_vault(env.user).get('credentials', {})
        assert not env.http.made('GET', f'https://api.supabase.com/v1/projects/{REF_B}/api-keys')

    def test_select_rejects_garbage_ref(self, env, client):
        _connect_supabase(env)
        r = client.post('/api/vault/connectors/supabase/select', json={'ref': '../../etc'})
        assert r.status_code == 400

    def test_missing_secrets_scope(self, env, client):
        _connect_supabase(env)
        _mgmt(env)
        env.http.on('GET', f'https://api.supabase.com/v1/projects/{REF_A}/api-keys', FakeResp(403, {}))
        r = client.post('/api/vault/connectors/supabase/select', json={'ref': REF_A})
        assert r.status_code == 403 and 'permission' in r.get_json()['message']


# ---------------------------------------------------------------------------
# F. Connectors view data
# ---------------------------------------------------------------------------
def test_connectors_endpoint(env, client):
    _platform_on(env, 'supabase')
    _connect_supabase(env)
    d = client.get('/api/vault/connectors').get_json()
    by = {c['id']: c for c in d['connectors']}
    assert {'supabase_connect', 'github', 'netlify_connect', 'google_analytics'} <= set(by)
    assert d['connectors'][0]['id'] == 'supabase_connect', 'connected first'
    assert by['supabase_connect']['connected'] and by['supabase_connect']['account'] == 'owner@example.com'
    assert by['supabase_connect']['target']['id'] == 'supabase'
    assert by['github']['platform_configured'] is False and by['github']['icon'] == 'github'
    assert d['relay_origin'] == ''


def test_platform_setup_lists_single_relay_uri(env, client):
    _relay_on(env)
    d = client.get('/api/vault/platform-setup').get_json()
    by = {p['id']: p for p in d['providers']}
    for pid in ('supabase', 'github', 'netlify'):
        assert by[pid]['redirect_uris'] == [f'https://oauth.jam-bot.com/api/vault/oauth/relay/{pid}']
        assert by[pid]['redirect_note']
    assert by['supabase']['env_vars']['client_id'] == 'SUPABASE_OAUTH_CLIENT_ID'


# ---------------------------------------------------------------------------
# G. The admin page still parses
# ---------------------------------------------------------------------------
def test_admin_inline_js_parses(tmp_path):
    import re
    node = shutil.which('node')
    if not node:
        pytest.skip('node not on PATH')
    html = (Path(__file__).resolve().parent.parent / 'src' / 'admin.html').read_text()
    js = '\n;\n'.join(re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S | re.I))
    assert 'const Connectors=' in js
    f = tmp_path / 'admin.js'
    f.write_text(js)
    r = subprocess.run([node, '--check', str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[:1200]
