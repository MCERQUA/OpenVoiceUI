"""Voice-clone PROVIDER-SEAM (docs/jambot/ovu-voice-clone-seam-spec.md §2).

(a) the four existing clone providers, wrapped by their seam shims, give the
    route the SAME success envelope and the SAME clone_voice() call as the
    pre-seam if/elif did (TestEnvelopeUnchanged runs green on origin/main too,
    which is the point: it pins today's behaviour, not the new code);
(b) registry selection excludes inactive / classless / non-clone-capable /
    unavailable providers;
(c) CloneError codes map to the spec §2.4 HTTP statuses (busy -> Retry-After),
    and a failed clone is never retried on another provider;
(d) no policy.json = exactly today's four providers; a policy restricts;
(e) the clone endpoint's rate limit is registered against the REAL endpoint;
plus the VOICE_CLONE_SEAM_DIR ("_seam") isolation checks.
"""

import io
import json
import os
import time

import pytest
from flask import Flask


FOUR = ['qwen3', 'qwen3-local', 'elevenlabs', 'resemble']

_KEY_ENV = ('FAL_KEY', 'ELEVENLABS_API_KEY', 'RESEMBLE_API_KEY',
            'OVUI_BRIDGE_AUTH_TOKEN', 'CLERK_PUBLISHABLE_KEY',
            'VITE_CLERK_PUBLISHABLE_KEY', 'CLERK_SECRET_KEY')


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """No vendor keys (Resemble's __init__ starts a network thread when its
    key is set), no Clerk, and every runtime dir pointed at tmp_path."""
    for k in _KEY_ENV:
        monkeypatch.delenv(k, raising=False)
    import services.paths as paths
    clones = tmp_path / 'voice-clones'
    clones.mkdir()
    monkeypatch.setattr(paths, 'UPLOADS_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(paths, 'VOICE_CLONES_DIR', clones)
    # raising=False: this attribute does not exist on pre-seam code, and the
    # envelope tests are meant to run there unchanged.
    monkeypatch.setattr(paths, 'VOICE_CLONE_SEAM_DIR', clones / '_seam', raising=False)
    try:
        from services.voice_clone import invalidate_policy_cache
        invalidate_policy_cache()
    except ImportError:
        pass
    yield


def _app():
    from routes.conversation import conversation_bp
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(conversation_bp)
    return app


def _post_multipart(client, provider=None, name='Test Voice', reference_text='hello there',
                    filename='sample.wav'):
    data = {'name': name, 'reference_text': reference_text,
            'audio': (io.BytesIO(b'RIFF....WAVEfmt fake'), filename)}
    if provider is not None:
        data['provider'] = provider
    return client.post('/api/tts/clone', data=data, content_type='multipart/form-data')


def _real(pid):
    from tts_providers import get_provider  # noqa: F401  (ensure package import)
    from tts_providers import _PROVIDERS
    return _PROVIDERS[pid]()


class _Recorder:
    def __init__(self, record):
        self.record = record
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.record)


def _legacy_envelope(pid, record, requested_name):
    """The success JSON the pre-seam route built from clone_voice()'s dict."""
    vid = record.get('voice_id', '')
    return {
        'status': 'ok',
        'provider': pid,
        'voice_id': vid,
        'name': record.get('name', requested_name),
        'created_at': record.get('created_at', ''),
        'clone_time_ms': record.get('clone_time_ms', 0),
        'embedding_size': record.get('embedding_size', 0),
        'usage': f'Use voice_id "{vid}" in /api/tts/generate with provider={pid}',
    }


def _wire(monkeypatch, instances):
    import routes.conversation as rc
    monkeypatch.setattr(rc, 'get_provider', lambda pid=None: instances[pid])


# ===========================================================================
# (a) envelope + call shape unchanged for the four providers
# ===========================================================================

FULL_RECORD = {
    'voice_id': 'clone_test_voice', 'name': 'Test Voice (stored)',
    'created_at': '2026-11-07T04:00:00Z', 'clone_time_ms': 1234,
    'embedding_size': 4096, 'provider': 'x', 'extra': 'ignored',
}
MINIMAL_RECORD = {'voice_id': 'clone_min'}


def _expected_call(pid, upload_path, reference_text):
    if pid == 'qwen3':
        return None  # URL is random; checked separately
    if pid == 'elevenlabs':
        return {'audio_path': upload_path, 'name': 'Test Voice'}
    return {'audio_path': upload_path, 'name': 'Test Voice', 'reference_text': reference_text}


class TestEnvelopeUnchanged:
    @pytest.mark.parametrize('pid', FOUR)
    @pytest.mark.parametrize('record', [FULL_RECORD, MINIMAL_RECORD], ids=['full', 'minimal'])
    def test_multipart_envelope_and_call(self, pid, record, monkeypatch, tmp_path):
        prov = _real(pid)
        rec = _Recorder(record)
        prov.clone_voice = rec
        prov.is_available = lambda: True
        _wire(monkeypatch, {pid: prov})

        resp = _post_multipart(_app().test_client(), provider=pid)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json() == _legacy_envelope(pid, record, 'Test Voice')

        assert len(rec.calls) == 1
        call = rec.calls[0]
        saved = list((tmp_path / 'uploads').glob('voice_clone_*.wav'))
        assert len(saved) == 1, 'reference audio must be saved server-side first'
        if pid == 'qwen3':
            assert set(call) == {'audio_url', 'name', 'reference_text'}
            assert call['audio_url'] == f'http://localhost/uploads/{saved[0].name}'
            assert call['name'] == 'Test Voice'
            assert call['reference_text'] == 'hello there'
        else:
            assert call == _expected_call(pid, str(saved[0]), 'hello there')

    def test_default_provider_is_qwen3_when_omitted(self, monkeypatch):
        prov = _real('qwen3')
        rec = _Recorder(FULL_RECORD)
        prov.clone_voice = rec
        prov.is_available = lambda: True
        _wire(monkeypatch, {'qwen3': prov})
        resp = _post_multipart(_app().test_client(), provider=None)
        assert resp.status_code == 200
        assert resp.get_json()['provider'] == 'qwen3'
        assert len(rec.calls) == 1

    def test_json_path_qwen3_passes_url_verbatim(self, monkeypatch):
        prov = _real('qwen3')
        rec = _Recorder(FULL_RECORD)
        prov.clone_voice = rec
        prov.is_available = lambda: True
        _wire(monkeypatch, {'qwen3': prov})
        resp = _app().test_client().post('/api/tts/clone', json={
            'audio_url': 'https://example.com/a.wav', 'name': 'Test Voice',
            'provider': 'qwen3'})
        assert resp.status_code == 200
        assert resp.get_json() == _legacy_envelope('qwen3', FULL_RECORD, 'Test Voice')
        assert rec.calls == [{'audio_url': 'https://example.com/a.wav',
                              'name': 'Test Voice', 'reference_text': None}]

    @pytest.mark.parametrize('pid,msg', [
        ('qwen3-local', 'qwen3-local requires audio file upload (multipart form)'),
        ('elevenlabs', 'ElevenLabs requires audio file upload (multipart form)'),
        ('resemble', 'Resemble requires audio file upload (multipart form)'),
    ])
    def test_json_path_file_providers_400_same_message(self, pid, msg, monkeypatch):
        prov = _real(pid)
        rec = _Recorder(FULL_RECORD)
        prov.clone_voice = rec
        prov.is_available = lambda: True
        _wire(monkeypatch, {pid: prov})
        resp = _app().test_client().post('/api/tts/clone', json={
            'audio_url': 'https://example.com/a.wav', 'name': 'V', 'provider': pid})
        assert resp.status_code == 400
        assert resp.get_json() == {'error': msg}
        assert rec.calls == []

    @pytest.mark.parametrize('pid', FOUR)
    def test_unavailable_is_503_same_message(self, pid, monkeypatch):
        prov = _real(pid)
        prov.is_available = lambda: False
        _wire(monkeypatch, {pid: prov})
        resp = _post_multipart(_app().test_client(), provider=pid)
        assert resp.status_code == 503
        assert resp.get_json() == {'error': f'{pid} provider not available (API key not set)'}

    def test_unsupported_provider_400_same_message(self, monkeypatch):
        resp = _post_multipart(_app().test_client(), provider='hume')
        assert resp.status_code == 400
        assert resp.get_json() == {'error': 'Voice cloning not supported for provider "hume". '
                                            'Supported: qwen3, qwen3-local, elevenlabs, resemble'}

    @pytest.mark.parametrize('pid', FOUR)
    def test_runtime_error_still_500_with_message(self, pid, monkeypatch):
        prov = _real(pid)

        def boom(**kw):
            raise RuntimeError(f'{pid} vendor said no')
        prov.clone_voice = boom
        prov.is_available = lambda: True
        _wire(monkeypatch, {pid: prov})
        resp = _post_multipart(_app().test_client(), provider=pid)
        assert resp.status_code == 500
        assert resp.get_json() == {'error': f'{pid} vendor said no'}

    def test_qwen3_real_clone_voice_network_mocked(self, monkeypatch, tmp_path):
        """Real Qwen3Provider.clone_voice through the route, fal.ai mocked."""
        import tts_providers.qwen3_provider as q
        monkeypatch.setenv('FAL_KEY', 'test-key')
        prov = q.Qwen3Provider()
        calls = []

        def fake_req(api_key, endpoint, payload, timeout=90.0):
            calls.append((endpoint, payload, timeout))
            return {'speaker_embedding': {'url': 'https://fal.example/emb.safetensors'}}
        monkeypatch.setattr(q, '_fal_request', fake_req)
        monkeypatch.setattr(q, '_fal_download', lambda url, timeout=30.0: b'E' * 77)
        _wire(monkeypatch, {'qwen3': prov})

        resp = _post_multipart(_app().test_client(), provider='qwen3', name='My Voice')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body['voice_id'] == 'clone_my_voice'
        assert body['embedding_size'] == 77
        assert body['name'] == 'My Voice'
        assert calls[0][0] == q.FAL_CLONE_1_7B
        assert calls[0][1]['reference_text'] == 'hello there'
        assert (tmp_path / 'voice-clones' / 'clone_my_voice' / 'embedding.safetensors').read_bytes() == b'E' * 77


# ===========================================================================
# (b) registry selection
# ===========================================================================

def _fake_classes():
    from tts_providers.base_provider import (
        TTSProvider, CloneCapableTTSProvider, CloneResult,
    )

    class _Plain(TTSProvider):
        def generate_speech(self, text, **kw): return b''
        def list_voices(self): return []
        def get_info(self): return {'name': 'plain', 'status': 'active'}

    class _Clone(CloneCapableTTSProvider):
        clone_label = 'Fake'
        clone_input = ('path', 'url')
        available = True
        behaviour = None
        calls = None

        def __init__(self):
            self.calls = []

        def generate_speech(self, text, **kw): return b'AUDIO:' + kw.get('voice', '').encode()
        def list_voices(self): return []
        def get_info(self): return {'name': 'fake', 'status': 'active',
                                    'capabilities': self.clone_capabilities()}
        def is_available(self): return self.available

        def create_clone(self, req):
            self.calls.append(req)
            if self.behaviour is not None:
                return self.behaviour(req)
            return CloneResult(status='ready', provider='fake', name=req.name,
                               voice_id='clone_fake', created_at='T', clone_time_ms=5)

        def list_clones(self, tenant_id): return []
        def disable_clone(self, tenant_id, voice_id, reason=''): return None

    return _Plain, _Clone


@pytest.fixture
def fake_registry(monkeypatch):
    """Synthetic catalog + class registry covering every exclusion case."""
    import tts_providers
    Plain, Clone = _fake_classes()

    class Unavail(Clone):
        available = False

    catalog = {'providers': {
        'good':          {'status': 'active',   'features': ['voice-cloning']},
        'good2':         {'status': 'active',   'features': ['voice-cloning']},
        'pocketlike':    {'status': 'active',   'features': ['voice-cloning']},  # no class
        'inactiveclone': {'status': 'inactive', 'features': ['voice-cloning']},
        'notcapable':    {'status': 'active',   'features': ['voice-cloning']},  # plain class
        'nofeature':     {'status': 'active',   'features': ['streaming']},
        'unavail':       {'status': 'active',   'features': ['voice-cloning']},
    }}
    registry = {'good': Clone, 'good2': Clone, 'inactiveclone': Clone,
                'notcapable': Plain, 'nofeature': Clone, 'unavail': Unavail}
    monkeypatch.setattr(tts_providers, '_load_config', lambda: catalog)
    monkeypatch.setattr(tts_providers, '_PROVIDERS', registry)
    instances = {pid: cls() for pid, cls in registry.items()}
    monkeypatch.setattr(tts_providers, 'get_provider', lambda pid=None: instances[pid])
    _wire(monkeypatch, instances)
    return instances


def _write_policy(tmp_path, obj):
    d = tmp_path / 'voice-clones' / '_seam'
    d.mkdir(parents=True, exist_ok=True)
    p = d / 'policy.json'
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    from services.voice_clone import invalidate_policy_cache
    invalidate_policy_cache()
    return p


ALL_FAKE = ['good', 'good2', 'pocketlike', 'inactiveclone', 'notcapable', 'nofeature', 'unavail']


class TestRegistrySelection:
    def test_real_catalog_is_exactly_the_four(self):
        from tts_providers import get_clone_provider_ids
        ids = get_clone_provider_ids()
        assert sorted(ids) == sorted(FOUR)
        assert 'pocket' not in ids          # inactive + no class
        assert 'qwen3-mlx' not in ids       # voice-cloning feature, class cannot clone

    def test_eligibility_excludes_classless_inactive_noncapable_nofeature(self, fake_registry):
        from tts_providers import get_clone_provider_ids
        assert get_clone_provider_ids() == ['good', 'good2', 'unavail']

    def test_available_only_excludes_unavailable(self, fake_registry, tmp_path):
        _write_policy(tmp_path, {'allowed_providers': ALL_FAKE})
        from services.voice_clone import clone_provider_ids
        assert clone_provider_ids() == ['good', 'good2', 'unavail']
        assert clone_provider_ids(available_only=True) == ['good', 'good2']

    @pytest.mark.parametrize('pid', ['pocketlike', 'inactiveclone', 'notcapable', 'nofeature'])
    def test_route_rejects_ineligible_even_if_policy_allows(self, pid, fake_registry, tmp_path):
        _write_policy(tmp_path, {'allowed_providers': ALL_FAKE})
        resp = _post_multipart(_app().test_client(), provider=pid)
        assert resp.status_code == 400
        assert 'not supported' in resp.get_json()['error']

    def test_route_unavailable_is_503(self, fake_registry, tmp_path):
        _write_policy(tmp_path, {'allowed_providers': ALL_FAKE})
        resp = _post_multipart(_app().test_client(), provider='unavail')
        assert resp.status_code == 503
        assert fake_registry['unavail'].calls == []

    @pytest.mark.parametrize('pid', ['pocket', 'qwen3-mlx'])
    def test_route_real_catalog_hides_pocket_and_mlx(self, pid, tmp_path):
        # Even a policy that names them cannot surface them: pocket is an
        # inactive, classless catalog entry; qwen3-mlx has the voice-cloning
        # feature but its class cannot clone.
        _write_policy(tmp_path, {'allowed_providers': FOUR + ['pocket', 'qwen3-mlx']})
        resp = _post_multipart(_app().test_client(), provider=pid)
        assert resp.status_code == 400
        assert f'not supported for provider "{pid}"' in resp.get_json()['error']

    def test_new_capable_provider_needs_policy_opt_in(self, fake_registry):
        # No policy file: a brand-new clone-capable provider is NOT offered.
        resp = _post_multipart(_app().test_client(), provider='good')
        assert resp.status_code == 400
        assert fake_registry['good'].calls == []


# ===========================================================================
# (c) CloneError -> HTTP mapping, no cross-provider retry
# ===========================================================================

@pytest.fixture
def fake_allowed(fake_registry, tmp_path):
    _write_policy(tmp_path, {'allowed_providers': ['good', 'good2'], 'default_provider': 'good'})
    return fake_registry


class TestCloneErrorMapping:
    @pytest.mark.parametrize('code,status', [
        ('unavailable', 503), ('busy', 503), ('bad_audio', 422), ('quota', 429),
        ('consent_mismatch', 409), ('timeout', 504), ('internal', 500),
    ])
    def test_raised_code_maps_to_status(self, code, status, fake_allowed):
        from tts_providers.base_provider import CloneError

        def raise_it(req):
            raise CloneError(code, f'{code} happened')
        fake_allowed['good'].behaviour = raise_it
        resp = _post_multipart(_app().test_client(), provider='good')
        assert resp.status_code == status
        body = resp.get_json()
        assert body['error_code'] == code
        assert body['error'] == f'{code} happened'
        assert body['retryable'] is (code in ('unavailable', 'busy', 'timeout'))
        if code == 'busy':
            assert resp.headers.get('Retry-After') == '30'
        else:
            assert 'Retry-After' not in resp.headers

    def test_busy_honours_provider_retry_after(self, fake_allowed):
        from tts_providers.base_provider import CloneError

        def busy(req):
            raise CloneError('busy', 'GPU busy', retry_after=7)
        fake_allowed['good'].behaviour = busy
        resp = _post_multipart(_app().test_client(), provider='good')
        assert resp.status_code == 503
        assert resp.headers['Retry-After'] == '7'

    def test_failed_result_maps_like_raise(self, fake_allowed):
        from tts_providers.base_provider import CloneResult
        fake_allowed['good'].behaviour = lambda req: CloneResult(
            status='failed', provider='good', name=req.name,
            error={'code': 'bad_audio', 'retryable': False, 'message': 'too short'})
        resp = _post_multipart(_app().test_client(), provider='good')
        assert resp.status_code == 422
        assert resp.get_json()['error'] == 'too short'

    def test_unexpected_exception_is_opaque_500(self, fake_allowed):
        def bad(req):
            raise KeyError('secret detail')
        fake_allowed['good'].behaviour = bad
        resp = _post_multipart(_app().test_client(), provider='good')
        assert resp.status_code == 500
        assert resp.get_json() == {'error': 'Internal server error'}

    def test_no_silent_retry_on_another_provider(self, fake_allowed):
        from tts_providers.base_provider import CloneError

        def down(req):
            raise CloneError('unavailable', 'node gone')
        fake_allowed['good'].behaviour = down
        resp = _post_multipart(_app().test_client(), provider='good')
        assert resp.status_code == 503
        assert len(fake_allowed['good'].calls) == 1
        assert fake_allowed['good2'].calls == []

    def test_pending_is_not_reported_as_success(self, fake_allowed):
        from tts_providers.base_provider import CloneResult
        fake_allowed['good'].behaviour = lambda req: CloneResult(
            status='pending', provider='good', name=req.name, job_id='job123')
        resp = _post_multipart(_app().test_client(), provider='good')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['job_id'] == 'job123'
        assert body.get('status') != 'ok'

    def test_clone_request_carries_tenant_from_env_not_body(self, fake_allowed, monkeypatch):
        monkeypatch.setenv('JAMBOT_TENANT', 'tenant-a')
        client = _app().test_client()
        client.post('/api/tts/clone', data={
            'name': 'V', 'provider': 'good', 'tenant_id': 'tenant-b',
            'audio': (io.BytesIO(b'x'), 'a.wav')}, content_type='multipart/form-data')
        req = fake_allowed['good'].calls[0]
        assert req.tenant_id == 'tenant-a'
        assert req.request_id and len(req.request_id) == 32

    def test_unknown_code_rejected(self):
        from tts_providers.base_provider import CloneError
        with pytest.raises(ValueError):
            CloneError('nope')


# ===========================================================================
# (d) per-tenant policy
# ===========================================================================

class TestPolicy:
    def test_absent_policy_is_exactly_todays_four(self):
        from services.voice_clone import load_policy, clone_provider_ids, default_clone_provider
        pol = load_policy()
        assert pol['present'] is False
        assert pol['allowed_providers'] == FOUR
        assert clone_provider_ids() == FOUR
        assert default_clone_provider() == 'qwen3'

    def test_present_policy_restricts_route(self, tmp_path, monkeypatch):
        _write_policy(tmp_path, {'allowed_providers': ['elevenlabs'],
                                 'default_provider': 'elevenlabs'})
        from services.voice_clone import clone_provider_ids, default_clone_provider
        assert clone_provider_ids() == ['elevenlabs']
        assert default_clone_provider() == 'elevenlabs'

        q = _real('qwen3')
        q.clone_voice = _Recorder(FULL_RECORD)
        q.is_available = lambda: True
        e = _real('elevenlabs')
        e.clone_voice = _Recorder(FULL_RECORD)
        e.is_available = lambda: True
        _wire(monkeypatch, {'qwen3': q, 'elevenlabs': e})
        client = _app().test_client()

        resp = _post_multipart(client, provider='qwen3')
        assert resp.status_code == 400
        assert resp.get_json()['error'].endswith('Supported: elevenlabs')
        assert q.clone_voice.calls == []

        resp = _post_multipart(client, provider=None)   # default_provider applies
        assert resp.status_code == 200
        assert resp.get_json()['provider'] == 'elevenlabs'

    def test_default_provider_wins_over_list_order(self, tmp_path, monkeypatch):
        _write_policy(tmp_path, {'allowed_providers': ['qwen3', 'elevenlabs'],
                                 'default_provider': 'elevenlabs'})
        q = _real('qwen3')
        q.clone_voice = _Recorder(FULL_RECORD)
        q.is_available = lambda: True
        e = _real('elevenlabs')
        e.clone_voice = _Recorder(FULL_RECORD)
        e.is_available = lambda: True
        _wire(monkeypatch, {'qwen3': q, 'elevenlabs': e})
        resp = _post_multipart(_app().test_client(), provider=None)
        assert resp.status_code == 200
        assert resp.get_json()['provider'] == 'elevenlabs'
        assert q.clone_voice.calls == []

    def test_disallowed_default_is_an_explicit_400_not_a_switch(self, tmp_path):
        _write_policy(tmp_path, {'allowed_providers': ['qwen3'],
                                 'default_provider': 'resemble'})
        resp = _post_multipart(_app().test_client(), provider=None)
        assert resp.status_code == 400
        assert 'provider "resemble"' in resp.get_json()['error']

    def test_policy_cannot_enable_an_ineligible_provider(self, tmp_path):
        _write_policy(tmp_path, {'allowed_providers': ['pocket', 'qwen3-mlx', 'qwen3']})
        from services.voice_clone import clone_provider_ids
        assert clone_provider_ids() == ['qwen3']

    @pytest.mark.parametrize('content', ['{not json', '[]', '{"allowed_providers": "qwen3"}'])
    def test_broken_policy_fails_closed(self, tmp_path, content):
        _write_policy(tmp_path, content)
        from services.voice_clone import load_policy, clone_provider_ids
        assert load_policy()['error']
        assert clone_provider_ids() == []
        resp = _post_multipart(_app().test_client(), provider='qwen3')
        assert resp.status_code == 400

    def test_policy_without_allowed_key_keeps_defaults(self, tmp_path):
        _write_policy(tmp_path, {'clone_requires_code': True})
        from services.voice_clone import clone_provider_ids
        assert clone_provider_ids() == FOUR

    def test_mtime_cache_picks_up_edit(self, tmp_path):
        p = _write_policy(tmp_path, {'allowed_providers': ['qwen3']})
        from services.voice_clone import clone_provider_ids
        assert clone_provider_ids() == ['qwen3']
        p.write_text(json.dumps({'allowed_providers': ['qwen3', 'resemble']}))
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        assert clone_provider_ids() == ['qwen3', 'resemble']

    def test_policy_lives_in_the_mounted_clone_volume(self):
        # Fresh interpreter: the autouse fixture patches services.paths, so
        # the real (unpatched) definitions are read in a subprocess.
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            [sys.executable, '-c',
             'from services import paths; from services.voice_clone import policy_path; '
             'print(paths.VOICE_CLONE_SEAM_DIR == paths.VOICE_CLONES_DIR / "_seam", '
             'policy_path() == paths.VOICE_CLONE_SEAM_DIR / "policy.json", '
             'paths.VOICE_CLONES_DIR == paths.RUNTIME_DIR / "voice-clones")'],
            cwd=root, capture_output=True, text=True, timeout=60)
        assert out.stdout.strip() == 'True True True', out.stderr[-500:]


# ===========================================================================
# (e) rate limit on the real endpoint
# ===========================================================================

class TestCloneRateLimit:
    def test_table_entry(self):
        from services.rate_limits import ENDPOINT_RATE_LIMITS
        assert ENDPOINT_RATE_LIMITS.get('conversation.tts_clone_voice') == '10/minute;30/hour'

    def test_table_key_is_the_real_endpoint_of_the_route(self):
        from services.rate_limits import ENDPOINT_RATE_LIMITS
        app = _app()
        endpoints = [r.endpoint for r in app.url_map.iter_rules() if r.rule == '/api/tts/clone']
        assert endpoints == ['conversation.tts_clone_voice']
        assert endpoints[0] in ENDPOINT_RATE_LIMITS

    def test_page_fanout_passes_and_eleventh_in_a_minute_is_429(self, monkeypatch):
        from app import create_app
        from routes.conversation import conversation_bp
        from services.rate_limits import apply_endpoint_rate_limits
        app, _sock = create_app(config_override={'TESTING': True})
        app.register_blueprint(conversation_bp)
        applied = apply_endpoint_rate_limits(app)
        assert 'conversation.tts_clone_voice' in applied
        client = app.test_client()
        # voice-studio.html fires one POST per selected provider card, in
        # parallel; count its cards so the limit can never drop below them.
        page = open(os.path.join(os.path.dirname(__file__), '..', 'default-pages',
                                 'voice-studio.html')).read()
        cards = page.count('class="platform-card" data-provider=')
        assert cards >= 4
        codes = [client.post('/api/tts/clone', data={}).status_code for _ in range(11)]
        assert 429 not in codes[:cards], codes   # one full fan-out is never throttled
        assert codes == [400] * 10 + [429]

    def test_server_applies_the_table(self):
        src = open(os.path.join(os.path.dirname(__file__), '..', 'server.py')).read()
        assert 'apply_endpoint_rate_limits(app)' in src


# ===========================================================================
# Shim behaviour beyond create_clone
# ===========================================================================

class TestShims:
    @pytest.mark.parametrize('pid,inputs', [
        ('qwen3', ['url']), ('qwen3-local', ['path']),
        ('elevenlabs', ['path']), ('resemble', ['path']),
    ])
    def test_get_info_carries_clone_capabilities(self, pid, inputs, monkeypatch):
        import tts_providers.qwen3_local_provider as ql
        monkeypatch.setattr(ql, '_is_available', lambda: False)
        from tts_providers.base_provider import CloneCapableTTSProvider
        prov = _real(pid)
        assert isinstance(prov, CloneCapableTTSProvider)
        caps = prov.get_info()['capabilities']
        assert caps['clone'] is True
        assert caps['clone_input'] == inputs
        assert caps['async_clone'] is False

    def test_synthesize_is_generate_speech(self, fake_registry):
        assert fake_registry['good'].synthesize('hi', voice_id='clone_x') == b'AUDIO:clone_x'

    def test_qwen3_list_and_disable_clone_soft_retire(self, tmp_path):
        clones = tmp_path / 'voice-clones'
        (clones / 'clone_bob').mkdir()
        (clones / 'clone_bob' / 'metadata.json').write_text(json.dumps(
            {'voice_id': 'clone_bob', 'name': 'Bob', 'created_at': 'T'}))
        (clones / 'clone_bob' / 'embedding.safetensors').write_bytes(b'E')
        prov = _real('qwen3')
        assert [c.voice_id for c in prov.list_clones('t1')] == ['clone_bob']
        prov.disable_clone('t1', 'clone_bob', reason='owner asked')
        assert not (clones / 'clone_bob').exists()
        retired = clones / 'clone_bob.retired'
        assert (retired / 'embedding.safetensors').read_bytes() == b'E'
        assert json.loads((retired / 'retired.json').read_text())['reason'] == 'owner asked'
        assert prov.list_clones('t1') == []

    def test_disable_refuses_non_clone_ids(self, tmp_path):
        from tts_providers.base_provider import CloneError
        (tmp_path / 'voice-clones' / '_seam').mkdir()
        with pytest.raises(CloneError):
            _real('qwen3').disable_clone('t1', '_seam')
        assert (tmp_path / 'voice-clones' / '_seam').is_dir()

    @pytest.mark.parametrize('pid', ['elevenlabs', 'resemble'])
    def test_remote_disable_refuses_instead_of_deleting(self, pid):
        from tts_providers.base_provider import CloneError
        with pytest.raises(CloneError):
            _real(pid).disable_clone('t1', 'abc')


# ===========================================================================
# VOICE_CLONE_SEAM_DIR ("_seam") isolation
# ===========================================================================

class TestSeamDirIsolation:
    def test_delete_route_cannot_retire_seam_dir(self, tmp_path):
        seam = tmp_path / 'voice-clones' / '_seam'
        seam.mkdir()
        (seam / 'policy.json').write_text('{}')
        resp = _app().test_client().delete('/api/tts/voices/_seam')
        assert resp.status_code == 400
        assert seam.is_dir() and (seam / 'policy.json').exists()
        assert not (tmp_path / 'voice-clones' / '_seam.retired').exists()

    def test_qwen3_listing_never_surfaces_seam(self, tmp_path):
        seam = tmp_path / 'voice-clones' / '_seam'
        seam.mkdir()
        (seam / 'policy.json').write_text('{}')
        # even if a future seam file were named metadata.json
        (seam / 'metadata.json').write_text(json.dumps({'voice_id': '_seam', 'name': 'x'}))
        prov = _real('qwen3')
        assert prov.list_cloned_voices() == []
        assert '_seam' not in prov.list_voices()

    def test_workspace_file_api_cannot_write_seam(self, tmp_path, monkeypatch):
        import routes.workspace as ws
        clones = tmp_path / 'voice-clones'
        monkeypatch.setitem(ws._WRITABLE_MAP, 'Voice-Clones', clones)
        assert ws._writable_path('Voice-Clones/_seam/policy.json') is None
        assert ws._writable_path('Voice-Clones/_seam') is None
        assert ws._writable_path('Voice-Clones/clone_a/../_seam/policy.json') is None
        assert ws._writable_path('Voice-Clones/clone_a/metadata.json') is not None

    def _ws_app(self, tmp_path, monkeypatch):
        """Workspace browser with the REAL two-path layout: Voice-Clones inside
        WORKSPACE_DIR is a second view (a bind mount in production; a symlink
        here) of the same VOICE_CLONES_DIR that holds _seam."""
        import routes.workspace as ws
        clones = tmp_path / 'voice-clones'
        seam = clones / '_seam'
        seam.mkdir(exist_ok=True)
        (seam / 'policy.json').write_text('{"clone_requires_code": true}')
        (seam / 'gpu.token').write_text('vcg1.kid.SECRET')
        (clones / 'clone_a').mkdir(exist_ok=True)
        (seam / 'jobs').mkdir(exist_ok=True)
        (clones / 'clone_a').mkdir(exist_ok=True)
        (clones / 'clone_a' / 'metadata.json').write_text('{"voice_id": "clone_a"}')
        wsdir = tmp_path / 'workspace'
        wsdir.mkdir()
        # A bind mount resolves IN PLACE (a symlink would resolve out of
        # WORKSPACE_DIR and be refused for the wrong reason), so model the
        # second view as a real copy at the workspace path.
        import shutil
        shutil.copytree(clones, wsdir / 'Voice-Clones')
        monkeypatch.setattr(ws, 'WORKSPACE_DIR', wsdir)
        monkeypatch.setitem(ws._WRITABLE_MAP, 'Voice-Clones', clones)
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.register_blueprint(ws.workspace_bp)
        return app.test_client()

    @pytest.mark.parametrize('route,path', [
        ('/api/workspace/file', 'Voice-Clones/_seam/policy.json'),
        ('/api/workspace/raw', 'Voice-Clones/_seam/gpu.token'),
        ('/api/workspace/raw', 'Voice-Clones/clone_a/../_seam/gpu.token'),
        ('/api/workspace/browse', 'Voice-Clones/_seam'),
        ('/api/workspace/tree', 'Voice-Clones/_seam'),
    ])
    def test_workspace_file_api_cannot_read_seam(self, tmp_path, monkeypatch, route, path):
        c = self._ws_app(tmp_path, monkeypatch)
        r = c.get(route, query_string={'path': path})
        if route == '/api/workspace/tree':
            # tree answers 200 {'dirs': []} for any unresolvable path, by design
            assert r.status_code == 200 and r.get_json() == {'dirs': []}, r.data
        else:
            assert r.status_code in (400, 404), (route, path, r.status_code)
        assert b'SECRET' not in r.data and b'clone_requires_code' not in r.data
        assert b'jobs' not in r.data

    def test_workspace_listing_hides_seam_but_shows_clones(self, tmp_path, monkeypatch):
        c = self._ws_app(tmp_path, monkeypatch)
        names = [e['name'] for e in c.get('/api/workspace/browse',
                 query_string={'path': 'Voice-Clones'}).get_json()['entries']]
        assert 'clone_a' in names and '_seam' not in names
        r = c.get('/api/workspace/file', query_string={'path': 'Voice-Clones/clone_a/metadata.json'})
        assert r.status_code == 200  # positive control: ordinary clone files stay readable
        tree = c.get('/api/workspace/tree', query_string={'path': 'Voice-Clones'}).get_json()
        assert [d['name'] for d in tree['dirs']] == ['clone_a'], tree
        top = c.get('/api/workspace/tree', query_string={'path': ''}).get_json()
        assert '_seam' not in json.dumps(top)

    def test_workspace_put_cannot_write_seam(self, tmp_path, monkeypatch):
        c = self._ws_app(tmp_path, monkeypatch)
        r = c.put('/api/workspace/file', json={'path': 'Voice-Clones/_seam/policy.json',
                                               'content': '{"clone_requires_code": false}'})
        assert r.status_code >= 400
        assert 'true' in (tmp_path / 'voice-clones' / '_seam' / 'policy.json').read_text()


class TestRetiredClonesLeaveTheList:
    """Pre-existing defect found while building disable_clone: a retired
    clone kept its metadata.json, so qwen3 / qwen3-local still LISTED it
    after DELETE /api/tts/voices/<id> "retired" it."""

    def test_qwen3_delete_route_then_list(self, tmp_path):
        clones = tmp_path / 'voice-clones'
        (clones / 'clone_amy').mkdir()
        (clones / 'clone_amy' / 'metadata.json').write_text(json.dumps(
            {'voice_id': 'clone_amy', 'name': 'Amy'}))
        prov = _real('qwen3')
        assert [v['voice_id'] for v in prov.list_cloned_voices()] == ['clone_amy']
        resp = _app().test_client().delete('/api/tts/voices/clone_amy')
        assert resp.status_code == 200
        assert (clones / 'clone_amy.retired' / 'metadata.json').exists()
        assert prov.list_cloned_voices() == []

    def test_qwen3_local_disable_then_list(self, tmp_path):
        d = tmp_path / 'voice-clones' / 'qwen3-local' / 'clone_kim'
        d.mkdir(parents=True)
        (d / 'metadata.json').write_text(json.dumps({'voice_id': 'clone_kim', 'name': 'Kim'}))
        prov = _real('qwen3-local')
        assert [c.voice_id for c in prov.list_clones('t1')] == ['clone_kim']
        prov.disable_clone('t1', 'clone_kim', 'test')
        assert (d.parent / 'clone_kim.retired' / 'metadata.json').exists()
        assert prov.list_clones('t1') == []
