"""qwen3-gpu: OVU client for the GPU lane's vcg endpoint (voice-clone seam PR 2).

Every network test runs against FakeVcg, a real HTTP server on 127.0.0.1 with
vcg's wire behaviour (bearer -> tenant binding, uniform 401, 403
tenant_mismatch, 202 + job poll, /v1/tts WAV). No GPU, no tailnet.

Negative controls first: missing / empty / unreadable / malformed token, no
consent, no transcript, bad duration. Each asserts ZERO requests reached the
fake, which is the property that matters (the guard ran before any spend).
"""

import base64
import io
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import wave
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from tts_providers.base_provider import CloneError, CloneRequest

TENANT = 'vcg-ctl-a'
OTHER = 'vcg-ctl-b'


def _mint():
    """Same shape as scripts/voice-clone/vcg-token-mint (synthetic, test only)."""
    secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode()
    return f'vcg1.{secrets.token_hex(4)}.{secret}'


def _wav(seconds, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b'\x00\x00' * int(seconds * rate))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fake vcg
# ---------------------------------------------------------------------------

class FakeVcg:
    def __init__(self, tokens):
        self.tokens = dict(tokens)          # token -> tenant
        self.requests = []
        self.override = {}                  # (METHOD, path-regex) -> fn(rec) -> response
        self.jobs = {}
        self.clones = {}
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):      # keep test output quiet
                pass

            def _go(self, method):
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n) if n else b''
                u = urlparse(self.path)
                rec = {'method': method, 'path': u.path, 'query': parse_qs(u.query),
                       'auth': self.headers.get('Authorization'), 'body': body,
                       'ctype': self.headers.get('Content-Type', '')}
                fake.requests.append(rec)
                out = fake.dispatch(rec)
                if out == 'close':
                    return              # no response; the server closes the socket
                if isinstance(out, tuple) and out[0] == 'sleep':
                    time.sleep(out[1])
                    out = out[2]
                status, headers, payload = out
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                    headers = {'Content-Type': 'application/json', **headers}
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._go('GET')

            def do_POST(self):
                self._go('POST')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    # -- wire helpers -------------------------------------------------------

    @staticmethod
    def err(status, code, message='', retryable=False, headers=None):
        return (status, headers or {},
                {'error': {'code': code, 'retryable': retryable, 'message': message or code}})

    @staticmethod
    def multipart(rec):
        msg = BytesParser(policy=email_default).parsebytes(
            b'Content-Type: ' + rec['ctype'].encode() + b'\r\n\r\n' + rec['body'])
        parts = {}
        for part in msg.iter_parts():
            name = part.get_param('name', header='content-disposition')
            parts[name] = (part.get_filename(), part.get_payload(decode=True))
        return parts

    def tenant_of(self, rec):
        if rec['path'] == '/v1/clones' and rec['method'] == 'POST':
            return json.loads(self.multipart(rec)['meta'][1]).get('tenant_id')
        if rec['method'] == 'POST':
            return json.loads(rec['body'] or b'{}').get('tenant_id')
        return (rec['query'].get('tenant_id') or [None])[0]

    def dispatch(self, rec):
        for (method, pattern), fn in self.override.items():
            if method == rec['method'] and re.fullmatch(pattern, rec['path']):
                return fn(rec)
        auth = rec['auth'] or ''
        token = auth[7:] if auth.startswith('Bearer ') else None
        if token not in self.tokens:
            return self.err(401, 'unauthorized', 'unauthorized')
        tenant = self.tenant_of(rec)
        if not tenant:
            return self.err(400, 'bad_request', 'tenant_id required')
        if tenant != self.tokens[token]:
            return self.err(403, 'tenant_mismatch', 'token is not for this tenant')
        p, m = rec['path'], rec['method']
        if p == '/v1/health':
            return (200, {}, {'state': 'ok', 'accepting': True, 'clone_accepting': True,
                              'load': 0, 'reason': 'idle', 'retry_after_s': None})
        if p == '/v1/clones' and m == 'POST':
            parts = self.multipart(rec)
            meta = json.loads(parts['meta'][1])
            import hashlib
            sha = hashlib.sha256(parts['audio'][1]).hexdigest()
            if sha != meta['audio_sha256']:
                return self.err(422, 'bad_audio', 'sha mismatch')
            if not meta.get('reference_text'):
                return self.err(400, 'bad_request', 'reference_text required')
            vid = (f"clone_{tenant}_{meta['name'].lower().replace(' ', '-')}_"
                   f"{hashlib.sha256(meta['request_id'].encode()).hexdigest()[:6]}")
            job_id = 'job_' + secrets.token_hex(8)
            self.jobs[job_id] = {'job_id': job_id, 'status': 'ready', 'voice_id': vid,
                                 'request_id': meta['request_id'],
                                 'voice_ref': f'qwen3-gpu:{vid}@abcdef123456',
                                 'clone': {'voice_id': vid, 'name': meta['name'],
                                           'consent_id': meta['consent_id'], 'audio_sha256': sha,
                                           'created_at': '2026-11-07T06:00:00Z', 'disabled': False},
                                 'error': None, 'created_at': '2026-11-07T06:00:00Z',
                                 '_tenant': tenant}
            self.clones[vid] = self.jobs[job_id]['clone']
            return (202, {}, {'job_id': job_id, 'status': 'pending', 'voice_id': vid,
                              'request_id': meta['request_id'], 'voice_ref': None, 'clone': None,
                              'error': None, 'created_at': '2026-11-07T06:00:00Z'})
        mj = re.fullmatch(r'/v1/jobs/([A-Za-z0-9_-]+)', p)
        if mj and m == 'GET':
            job = self.jobs.get(mj.group(1))
            if not job or job['_tenant'] != tenant:
                return self.err(404, 'not_found', 'not found')
            return (200, {}, {k: v for k, v in job.items() if not k.startswith('_')})
        if p == '/v1/clones' and m == 'GET':
            return (200, {}, {'tenant_id': tenant, 'clones': [
                c for c in self.clones.values() if c['voice_id'].startswith(f'clone_{tenant}_')
                and not c['disabled']]})
        md = re.fullmatch(r'/v1/clones/([^/]+)/disable', p)
        if md and m == 'POST':
            c = self.clones.get(md.group(1))
            if not c or not c['voice_id'].startswith(f'clone_{tenant}_'):
                return self.err(404, 'not_found', 'not found')
            c['disabled'] = True
            return (200, {}, c)
        if p == '/v1/tts' and m == 'POST':
            body = json.loads(rec['body'])
            if len(body.get('text', '')) > 600:
                return self.err(400, 'bad_request', 'text over 600')
            c = self.clones.get(body.get('voice_id'))
            if not c or not c['voice_id'].startswith(f'clone_{tenant}_'):
                return self.err(404, 'not_found', 'not found')
            if c['disabled']:
                return self.err(410, 'disabled', 'disabled')
            return (200, {'Content-Type': 'audio/wav'}, _wav(0.25, rate=24000))
        return self.err(404, 'not_found', 'no route')


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    import services.paths as paths
    clones = tmp_path / 'voice-clones'
    clones.mkdir()
    seam = clones / '_seam'
    (seam / 'consent').mkdir(parents=True)
    monkeypatch.setattr(paths, 'VOICE_CLONES_DIR', clones)
    monkeypatch.setattr(paths, 'VOICE_CLONE_SEAM_DIR', seam)
    monkeypatch.setattr(paths, 'UPLOADS_DIR', tmp_path / 'uploads')
    for k in ('HOST_TENANT', 'CLERK_PUBLISHABLE_KEY', 'VITE_CLERK_PUBLISHABLE_KEY',
              'CLERK_SECRET_KEY', 'FAL_KEY', 'ELEVENLABS_API_KEY', 'RESEMBLE_API_KEY',
              'OVUI_BRIDGE_AUTH_TOKEN'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('JAMBOT_TENANT', TENANT)
    token_file = tmp_path / 'secrets' / 'vcg.token'
    token_file.parent.mkdir()
    monkeypatch.setenv('VCG_TOKEN_FILE', str(token_file))
    token_a, token_b = _mint(), _mint()
    fake = FakeVcg({token_a: TENANT, token_b: OTHER})
    monkeypatch.setenv('VCG_URL', fake.url)
    from services.voice_clone import invalidate_policy_cache
    invalidate_policy_cache()
    import tts_providers
    tts_providers.invalidate()
    ref = tmp_path / 'uploads' / 'voice_clone_abc.wav'
    ref.parent.mkdir()
    ref.write_bytes(_wav(12))
    (seam / 'consent' / 'c-1.json').write_text(json.dumps(
        {'consent_id': 'c-1', 'statement_accepted': True, 'speaker_is_requester': True,
         'text_version': 'v1'}))
    ns = type('Env', (), {})()
    ns.tmp, ns.seam, ns.clones, ns.token_file, ns.fake = tmp_path, seam, clones, token_file, fake
    ns.token_a, ns.token_b, ns.ref = token_a, token_b, ref
    yield ns
    fake.stop()
    tts_providers.invalidate()
    invalidate_policy_cache()


def _provider(**attrs):
    from tts_providers.qwen3_gpu_provider import Qwen3GpuProvider
    p = Qwen3GpuProvider()
    p.poll_interval_s = 0.01
    p.submit_timeout_s = 3.0
    p.tts_timeout_s = 3.0
    p.clone_deadline_s = 5.0
    for k, v in attrs.items():
        setattr(p, k, v)
    return p


def _req(env, **kw):
    base = dict(tenant_id=TENANT, request_id='req-1', name='Test Voice',
                audio_path=str(env.ref), reference_text='hello there this is me',
                consent_id='c-1')
    base.update(kw)
    return CloneRequest(**base)


def _expect(code, fn, *a, **kw):
    with pytest.raises(CloneError) as ei:
        fn(*a, **kw)
    assert ei.value.code == code, f'{ei.value.code}: {ei.value.message}'
    return ei.value


# ===========================================================================
# Negative controls: fail closed, ZERO requests
# ===========================================================================

class TestTokenFailsClosed:
    def _assert_closed(self, env):
        p = _provider()
        assert p.is_available() is False
        assert p.get_info()['status'] == 'inactive'
        err = _expect('unavailable', p.create_clone, _req(env))
        assert err.retryable is True
        assert p.health()['state'] == 'down'
        with pytest.raises(RuntimeError, match='TTS error 503'):
            p.generate_speech('hello', voice=f'clone_{TENANT}_x_000000')
        _expect('unavailable', p.list_clones, TENANT)
        assert env.fake.requests == [], 'a request reached vcg without a usable token'

    def test_missing_token(self, env):
        assert not env.token_file.exists()
        self._assert_closed(env)

    def test_empty_token(self, env):
        env.token_file.write_text('\n')
        self._assert_closed(env)

    def test_unreadable_token_is_a_directory(self, env):
        env.token_file.mkdir()
        self._assert_closed(env)

    @pytest.mark.skipif(os.geteuid() == 0, reason='root reads mode-000 files')
    def test_unreadable_token_permission(self, env):
        env.token_file.write_text(env.token_a + '\n')
        env.token_file.chmod(0)
        try:
            self._assert_closed(env)
        finally:
            env.token_file.chmod(0o600)

    def test_malformed_token(self, env):
        env.token_file.write_text('not-a-vcg-token\n')
        self._assert_closed(env)

    def test_no_tenant_in_env(self, env, monkeypatch):
        env.token_file.write_text(env.token_a + '\n')
        monkeypatch.delenv('JAMBOT_TENANT')
        self._assert_closed(env)


class TestRefusedBeforeAnyRequest:
    @pytest.fixture(autouse=True)
    def _token(self, env):
        env.token_file.write_text(env.token_a + '\n')

    def test_missing_consent_id(self, env):
        _expect('bad_request', _provider().create_clone, _req(env, consent_id=None))
        assert env.fake.requests == []

    def test_consent_id_without_record(self, env):
        _expect('consent_mismatch', _provider().create_clone, _req(env, consent_id='c-404'))
        assert env.fake.requests == []

    def test_consent_record_not_accepted(self, env):
        (env.seam / 'consent' / 'c-2.json').write_text(json.dumps({'statement_accepted': False}))
        _expect('consent_mismatch', _provider().create_clone, _req(env, consent_id='c-2'))
        assert env.fake.requests == []

    def test_consent_id_path_traversal(self, env):
        (env.seam / 'x.json').write_text(json.dumps({'statement_accepted': True}))
        _expect('bad_request', _provider().create_clone, _req(env, consent_id='../x'))
        assert env.fake.requests == []

    def test_no_reference_text_from_any_source(self, env):
        _expect('bad_request', _provider().create_clone, _req(env, reference_text=None))
        assert env.fake.requests == []

    def test_reference_too_short(self, env):
        env.ref.write_bytes(_wav(5))
        _expect('bad_audio', _provider().create_clone, _req(env))
        assert env.fake.requests == []

    def test_sha_given_does_not_match_upload(self, env):
        _expect('bad_audio', _provider().create_clone, _req(env, audio_sha256='0' * 64))
        assert env.fake.requests == []

    def test_other_tenants_list_is_never_asked(self, env):
        _expect('tenant_mismatch', _provider().list_clones, OTHER)
        assert env.fake.requests == []


# ===========================================================================
# Error mapping against the fake
# ===========================================================================

class TestErrorMapping:
    @pytest.fixture(autouse=True)
    def _token(self, env):
        env.token_file.write_text(env.token_a + '\n')

    def test_503_retry_after_is_busy_and_keeps_the_value(self, env):
        env.fake.override[('POST', '/v1/clones')] = lambda rec: FakeVcg.err(
            503, 'busy', 'the job slot is in use', True, {'Retry-After': '42'})
        err = _expect('busy', _provider().create_clone, _req(env))
        assert err.retryable is True and err.retry_after == 42
        assert len(env.fake.requests) == 1

    def test_503_retry_after_without_envelope_is_busy(self, env):
        env.fake.override[('POST', '/v1/clones')] = lambda rec: (503, {'Retry-After': '7'}, b'')
        err = _expect('busy', _provider().create_clone, _req(env))
        assert err.retry_after == 7

    def test_503_unavailable_quiet_window_keeps_retry_after(self, env):
        env.fake.override[('POST', '/v1/clones')] = lambda rec: FakeVcg.err(
            503, 'unavailable', 'quiet window', True, {'Retry-After': '3600'})
        err = _expect('unavailable', _provider().create_clone, _req(env))
        assert err.retry_after == 3600

    def test_connection_closed_mid_request_is_unavailable(self, env):
        env.fake.override[('POST', '/v1/clones')] = lambda rec: 'close'
        err = _expect('unavailable', _provider().create_clone, _req(env))
        assert err.retryable is True
        assert len(env.fake.requests) == 1   # it got there, and the socket closed

    def test_connection_refused_is_unavailable(self, env, monkeypatch):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        monkeypatch.setenv('VCG_URL', f'http://127.0.0.1:{port}')
        err = _expect('unavailable', _provider().create_clone, _req(env))
        assert err.retryable is True

    def test_timeout_is_timeout(self, env):
        env.fake.override[('POST', '/v1/clones')] = lambda rec: (
            'sleep', 1.5, (202, {}, {'status': 'pending', 'job_id': 'job_x'}))
        err = _expect('timeout', _provider(submit_timeout_s=0.3).create_clone, _req(env))
        assert err.retryable is True

    def test_tenant_mismatch_from_the_server(self, env, monkeypatch):
        # Container env says tenant B, but the file holds tenant A's token:
        # the misplaced-token case the duplication on the wire exists for.
        monkeypatch.setenv('JAMBOT_TENANT', OTHER)
        err = _expect('tenant_mismatch', _provider().create_clone, _req(env, tenant_id=OTHER))
        assert err.retryable is False
        meta = json.loads(FakeVcg.multipart(env.fake.requests[0])['meta'][1])
        assert meta['tenant_id'] == OTHER    # we sent OUR tenant; the server refused

    def test_revoked_token_is_unauthorized(self, env):
        env.token_file.write_text(_mint() + '\n')    # well-formed, unknown to the box
        err = _expect('unauthorized', _provider().create_clone, _req(env))
        assert err.retryable is False

    def test_unknown_error_code_is_internal(self, env):
        env.fake.override[('POST', '/v1/clones')] = lambda rec: FakeVcg.err(
            418, 'teapot_overheated', 'brew failed')
        err = _expect('internal', _provider().create_clone, _req(env))
        assert 'teapot_overheated' in err.message and '418' in err.message

    def test_500_without_envelope_is_internal_with_a_message(self, env):
        env.fake.override[('POST', '/v1/clones')] = lambda rec: (500, {}, b'<html>oops</html>')
        err = _expect('internal', _provider().create_clone, _req(env))
        assert '500' in err.message

    def test_failed_job_maps_its_code(self, env):
        def submit(rec):
            env.fake.jobs['job_bad'] = {
                'job_id': 'job_bad', 'status': 'failed', '_tenant': TENANT,
                'error': {'code': 'bad_audio', 'retryable': False,
                          'message': 'duration check failed'}}
            return (202, {}, {'job_id': 'job_bad', 'status': 'pending'})
        env.fake.override[('POST', '/v1/clones')] = submit
        _expect('bad_audio', _provider().create_clone, _req(env))

    def test_all_six_wire_codes_are_accepted(self, env):
        for code, status in (('unauthorized', 401), ('tenant_mismatch', 403),
                             ('bad_request', 400), ('not_found', 404),
                             ('disabled', 410), ('request_conflict', 409)):
            env.fake.override[('POST', '/v1/clones')] = (
                lambda rec, c=code, s=status: FakeVcg.err(s, c))
            _expect(code, _provider().create_clone, _req(env))


# ===========================================================================
# The token never leaks
# ===========================================================================

def test_token_never_in_logs_or_exception_text(env, caplog):
    env.token_file.write_text(env.token_a + '\n')
    secret = env.token_a.rsplit('.', 1)[1]
    caplog.set_level(logging.DEBUG)          # root: includes httpx / httpcore
    texts = []
    p = _provider()
    p.create_clone(_req(env))
    vid = next(iter(env.fake.clones))
    p.generate_speech('hello there', voice=vid)
    p.health()
    p.list_clones(TENANT)
    # A hostile/buggy server echoing the bearer back in its message:
    env.fake.override[('POST', '/v1/clones')] = lambda rec: FakeVcg.err(
        400, 'bad_request', f"bad header {rec['auth']}")
    texts.append(str(_expect('bad_request', p.create_clone, _req(env, request_id='r2'))))
    for code in ('busy', 'tenant_mismatch', 'unauthorized'):
        env.fake.override[('POST', '/v1/clones')] = lambda rec, c=code: FakeVcg.err(
            503 if c == 'busy' else 403, c, 'x', headers={'Retry-After': '5'})
        texts.append(str(_expect(code, p.create_clone, _req(env, request_id='r3'))))
    with pytest.raises(RuntimeError) as ei:
        env.fake.override[('POST', '/v1/tts')] = lambda rec: FakeVcg.err(
            503, 'busy', rec['auth'], True, {'Retry-After': '5'})
        p.generate_speech('hello', voice=vid)
    texts.append(str(ei.value))
    blob = caplog.text + '\n'.join(texts)
    assert caplog.records, 'nothing was logged; the leak check would be vacuous'
    assert env.token_a not in blob
    assert secret not in blob
    assert f'kid={env.token_a.split(".")[1]}' in caplog.text   # kid (public) IS logged


# ===========================================================================
# Happy path
# ===========================================================================

def test_happy_path_create_then_synthesize(env):
    env.token_file.write_text(env.token_a + '\n')
    p = _provider()
    assert p.is_available() and p.get_info()['status'] == 'active'
    res = p.create_clone(_req(env))
    assert res.status == 'ready' and res.provider == 'qwen3-gpu'
    assert res.voice_id.startswith(f'clone_{TENANT}_test-voice_')
    assert res.voice_ref.startswith(f'qwen3-gpu:{res.voice_id}@')
    import hashlib
    sha = hashlib.sha256(env.ref.read_bytes()).hexdigest()
    assert res.metadata['consent_id'] == 'c-1'
    assert res.metadata['audio_sha256'] == sha
    assert res.metadata['tenant_id'] == TENANT
    assert res.metadata['reference_text_source'] == 'typed'
    # wire: Bearer token, multipart meta carries tenant + consent + transcript
    submit = env.fake.requests[0]
    assert submit['auth'] == f'Bearer {env.token_a}'
    parts = FakeVcg.multipart(submit)
    meta = json.loads(parts['meta'][1])
    assert meta['tenant_id'] == TENANT and meta['consent_id'] == 'c-1'
    assert meta['reference_text'] == 'hello there this is me'
    assert abs(meta['duration_s'] - 12.0) < 0.01
    assert parts['audio'][0] == 'reference.wav'
    assert env.fake.requests[1]['path'].startswith('/v1/jobs/')
    assert env.fake.requests[1]['query']['tenant_id'] == [TENANT]
    # spec §4 OVU-side record, and it lists without calling the GPU
    rec = json.loads((env.clones / 'qwen3-gpu' / res.voice_id / 'metadata.json').read_text())
    assert rec['consent_id'] == 'c-1' and rec['audio_sha256'] == sha
    n = len(env.fake.requests)
    assert p.list_voices() == [res.voice_id]
    assert p.get_info()['cloned_voices'] == [{'voice_id': res.voice_id, 'name': 'Test Voice'}]
    assert len(env.fake.requests) == n
    # synthesize
    audio = p.synthesize('Hello from the GPU lane.', res.voice_id)
    with wave.open(io.BytesIO(audio)) as w:
        assert w.getframerate() == 24000 and w.getnframes() > 0
    tts = json.loads(env.fake.requests[-1]['body'])
    assert tts == {'voice_id': res.voice_id, 'text': 'Hello from the GPU lane.',
                   'format': 'wav', 'tenant_id': TENANT}
    # list + disable (box takedown, local rename, never delete)
    assert [c.voice_id for c in p.list_clones(TENANT)] == [res.voice_id]
    assert p.list_clones(TENANT)[0].consent_id == 'c-1'
    p.disable_clone(TENANT, res.voice_id, 'test takedown')
    assert (env.clones / 'qwen3-gpu' / (res.voice_id + '.retired') / 'metadata.json').exists()
    assert p.list_voices() == []
    with pytest.raises(RuntimeError, match='TTS error 410: disabled'):
        p.synthesize('again', res.voice_id)


def test_token_is_read_per_request(env):
    """Rotate the file between submit and poll: the poll carries the new one."""
    env.token_file.write_text(env.token_a + '\n')
    token_c = _mint()
    env.fake.tokens[token_c] = TENANT
    real = env.fake.dispatch

    def rotating(rec):
        out = real(rec)
        if rec['path'] == '/v1/clones' and rec['method'] == 'POST':
            env.token_file.write_text(token_c + '\n')
        return out
    env.fake.dispatch = rotating
    assert _provider().create_clone(_req(env)).status == 'ready'
    assert env.fake.requests[0]['auth'] == f'Bearer {env.token_a}'
    assert env.fake.requests[1]['auth'] == f'Bearer {token_c}'


def test_still_pending_at_deadline_returns_the_job_id(env):
    env.token_file.write_text(env.token_a + '\n')
    env.fake.override[('GET', r'/v1/jobs/.*')] = lambda rec: (
        200, {}, {'job_id': 'job_slow', 'status': 'pending'})
    env.fake.override[('POST', '/v1/clones')] = lambda rec: (
        202, {}, {'job_id': 'job_slow', 'status': 'pending'})
    res = _provider(clone_deadline_s=0.2).create_clone(_req(env))
    assert res.status == 'pending' and res.job_id == 'job_slow'


def test_reference_text_order(env):
    env.token_file.write_text(env.token_a + '\n')
    p = _provider(stt_transcriber=lambda path, lang: 'from stt')
    p.create_clone(_req(env, recitation_phrase='the recited phrase', request_id='a'))
    p.create_clone(_req(env, request_id='b'))
    p.create_clone(_req(env, reference_text=None, request_id='c'))
    sent = [json.loads(FakeVcg.multipart(r)['meta'][1])['reference_text']
            for r in env.fake.requests if r['method'] == 'POST' and r['path'] == '/v1/clones']
    assert sent == ['the recited phrase', 'hello there this is me', 'from stt']


def test_long_text_is_split_under_the_600_char_cap(env):
    env.token_file.write_text(env.token_a + '\n')
    p = _provider()
    vid = p.create_clone(_req(env)).voice_id
    text = ' '.join(['This is sentence number %d of a long reply.' % i for i in range(40)])
    assert len(text) > 1200
    audio = p.generate_speech(text, voice=vid)
    calls = [json.loads(r['body']) for r in env.fake.requests if r['path'] == '/v1/tts']
    assert len(calls) >= 3 and all(len(c['text']) <= 600 for c in calls)
    assert ' '.join(c['text'] for c in calls) == text
    with wave.open(io.BytesIO(audio)) as w:
        assert w.getnframes() == len(calls) * int(0.25 * 24000)


def test_synthesis_error_is_classified_for_fast_fallback(env):
    env.token_file.write_text(env.token_a + '\n')
    p = _provider()
    vid = p.create_clone(_req(env)).voice_id
    env.fake.override[('POST', '/v1/tts')] = lambda rec: FakeVcg.err(
        503, 'busy', 'no eligible card', True, {'Retry-After': '300'})
    with pytest.raises(RuntimeError) as ei:
        p.generate_speech('hi', voice=vid)
    from services.tts import _classify_http_error
    assert _classify_http_error(str(ei.value)) == '5xx'
    assert 'retry-after=300s' in str(ei.value)
    # another tenant's voice id is refused locally
    n = len(env.fake.requests)
    with pytest.raises(RuntimeError, match='TTS error 400'):
        p.generate_speech('hi', voice=f'clone_{OTHER}_x_000000')
    assert len(env.fake.requests) == n


# ===========================================================================
# Registry, tenant policy, fallback config: no tenant changes unless opted in
# ===========================================================================

class TestRegistration:
    def test_registered_and_clone_eligible(self, env):
        from tts_providers import _PROVIDERS, get_clone_provider_ids
        from tts_providers.qwen3_gpu_provider import Qwen3GpuProvider
        assert _PROVIDERS['qwen3-gpu'] is Qwen3GpuProvider
        assert 'qwen3-gpu' in get_clone_provider_ids()

    def test_no_policy_means_todays_four_and_qwen3_default(self, env):
        env.token_file.write_text(env.token_a + '\n')   # even WITH a token
        from services.voice_clone import clone_provider_ids, default_clone_provider
        assert clone_provider_ids() == ['qwen3', 'qwen3-local', 'elevenlabs', 'resemble']
        assert default_clone_provider() == 'qwen3'

    def test_policy_naming_it_selects_it(self, env):
        (env.seam / 'policy.json').write_text(json.dumps(
            {'allowed_providers': ['qwen3-gpu', 'elevenlabs'], 'default_provider': 'qwen3-gpu'}))
        from services.voice_clone import (clone_provider_ids, default_clone_provider,
                                          invalidate_policy_cache)
        invalidate_policy_cache()
        assert clone_provider_ids() == ['qwen3-gpu', 'elevenlabs']
        assert default_clone_provider() == 'qwen3-gpu'

    def test_synthesis_fallback_row(self, env):
        from services.tts_fallback import _DEFAULT_FALLBACK, invalidate, load_tts_fallback
        invalidate()
        assert load_tts_fallback()['chain']['qwen3-gpu'] == 'groq'
        assert _DEFAULT_FALLBACK['chain']['qwen3-gpu'] == 'groq'

    def test_unconfigured_tenant_voice_list_is_unchanged(self, env):
        """No token: /api/tts/voices has no qwen3-gpu key, and no request is made."""
        from flask import Flask
        from routes.conversation import conversation_bp
        app = Flask(__name__)
        app.register_blueprint(conversation_bp)
        body = app.test_client().get('/api/tts/voices').get_json()
        assert 'qwen3-gpu' not in body['voices']
        assert env.fake.requests == []


class TestThroughTheRoute:
    def _app(self):
        from flask import Flask
        from routes.conversation import conversation_bp
        app = Flask(__name__)
        app.register_blueprint(conversation_bp)
        return app.test_client()

    def _post(self, client):
        return client.post('/api/tts/clone', content_type='multipart/form-data', data={
            'name': 'Test Voice', 'provider': 'qwen3-gpu', 'reference_text': 'hello there',
            'audio': (io.BytesIO(_wav(12)), 'sample.wav')})

    def test_without_policy_qwen3_gpu_is_not_offered(self, env):
        env.token_file.write_text(env.token_a + '\n')
        r = self._post(self._app())
        assert r.status_code == 400 and 'not supported' in r.get_json()['error']
        assert env.fake.requests == []

    def test_with_policy_the_consent_precheck_holds_at_the_route(self, env):
        # PR 1's route does not create consent records yet (spec §3, a later
        # PR), so a qwen3-gpu clone through it is refused before any request.
        env.token_file.write_text(env.token_a + '\n')
        (env.seam / 'policy.json').write_text(json.dumps({'allowed_providers': ['qwen3-gpu']}))
        from services.voice_clone import invalidate_policy_cache
        invalidate_policy_cache()
        r = self._post(self._app())
        assert r.status_code == 400
        assert r.get_json()['error_code'] == 'bad_request'
        assert 'consent_id' in r.get_json()['error']
        assert env.fake.requests == []


@pytest.mark.parametrize('code,status', [
    ('busy', 503), ('unavailable', 503), ('unauthorized', 503), ('tenant_mismatch', 503),
    ('bad_request', 400), ('not_found', 404), ('disabled', 410), ('request_conflict', 409),
    ('internal', 500),
])
def test_route_http_status_for_every_code(code, status):
    from flask import Flask
    from routes.conversation import _clone_error_response
    with Flask(__name__).app_context():
        resp = _clone_error_response(CloneError(code, 'm', retry_after=42 if code == 'busy' else None),
                                     'qwen3-gpu')
    assert resp.status_code == status
    assert resp.get_json()['error_code'] == code
    if code == 'busy':
        assert resp.headers['Retry-After'] == '42'
