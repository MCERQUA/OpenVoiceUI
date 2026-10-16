"""Paid Suno work must sit behind the login gate (2026-10-16).

`/api/suno` is on the app's public-READ prefix list so the UI can poll `status` and load
song lists before Clerk initialises. But the endpoint selects its operation with
`?action=` on GET as well as POST, and the gate only looked at the method, so with login
REQUIRED an anonymous `GET /api/suno?action=generate&prompt=...` ran a paid generation.

These tests run the real gate (Clerk key set, so `require_auth` is registered) with the
Suno provider replaced by a recorder: no test reaches sunoapi.org or spends a credit.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import routes.suno as suno

AGENT_KEY = 'agent-key-suno-auth'

# Every action that submits paid work to the provider (or is otherwise not a plain read).
PAID_GET_QUERIES = [
    'action=generate&prompt=a+song',
    'action=jingle&brand=Acme',
    'action=sfx&prompt=coin+pickup',
    'action=extend&task_id=t1&audio_id=a1',
    'action=cover&task_id=t1',
    'action=add_vocals&upload_url=https://example.com/a.mp3&prompt=p',
    'action=add_instrumental&upload_url=https://example.com/a.mp3',
    'action=replace_section&task_id=t1&audio_id=a1',
    'action=generate_lyrics&prompt=a+song+about+rain',
    'action=timestamped_lyrics&task_id=t1&audio_id=a1',
    'action=wav_convert&task_id=t1&audio_id=a1',
    'action=stem_separate&task_id=t1&audio_id=a1',
    'action=style_boost&content=lofi',
    'action=music_video&task_id=t1&audio_id=a1',
    'action=generate_persona&task_id=t1&audio_id=a1',
    'action=voice_validate&voice_url=https://example.com/v.mp3',
    'action=voice_generate&task_id=t1&verify_url=https://example.com/v.mp3',
    'action=file_upload&url=https://example.com/v.mp3',
]


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    r.headers = {}
    return r


class Provider:
    """Stands in for sunoapi.org and records every outbound call."""

    def __init__(self):
        self.calls = []

    def post(self, url, *a, **kw):
        self.calls.append(('POST', url))
        return _resp({'code': 200, 'msg': 'success', 'data': {'taskId': 'task-1'}})

    def get(self, url, *a, **kw):
        self.calls.append(('GET', url))
        return _resp({'code': 200, 'msg': 'success', 'data': {'status': 'PENDING'}})


@pytest.fixture(scope='module')
def app_client():
    """App with the login gate ENFORCED (Clerk key set) and a known agent key."""
    saved = {k: os.environ.get(k) for k in ('CLERK_PUBLISHABLE_KEY', 'AGENT_API_KEY')}
    os.environ['CLERK_PUBLISHABLE_KEY'] = 'pk_test_suno_auth'
    os.environ['AGENT_API_KEY'] = AGENT_KEY
    try:
        from app import create_app
        app, _ = create_app(config_override={'TESTING': True})
        if 'suno' not in app.blueprints:    # server.py registers it, create_app() does not
            app.register_blueprint(suno.suno_bp)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return app.test_client()


@pytest.fixture
def provider(tmp_path, monkeypatch):
    p = Provider()
    monkeypatch.setattr(suno, 'SUNO_API_KEY', 'test-key')
    monkeypatch.setattr(suno, 'SUNO_CALLBACK_URL', '')
    monkeypatch.setattr(suno, 'GENERATED_MUSIC_DIR', tmp_path)
    monkeypatch.setattr(suno, 'GENERATED_METADATA_FILE', tmp_path / 'generated_metadata.json')
    monkeypatch.setattr(suno, 'suno_jobs', {})
    monkeypatch.setattr(suno, '_provider_receipt', lambda *a, **k: None)
    monkeypatch.setattr(suno.http_requests, 'post', p.post)
    monkeypatch.setattr(suno.http_requests, 'get', p.get)
    return p


@pytest.fixture
def anonymous():
    with patch('services.auth.get_token_from_request', return_value=None), \
         patch('services.auth.verify_clerk_token', return_value=None):
        yield


@pytest.mark.parametrize('query', PAID_GET_QUERIES)
def test_anonymous_get_cannot_start_paid_work(app_client, provider, anonymous, query):
    r = app_client.get(f'/api/suno?{query}')
    assert r.status_code == 401, (query, r.status_code, r.get_data(as_text=True)[:200])
    assert r.get_json()['code'] == 'auth_required'
    assert provider.calls == [], f'{query} reached the provider without a login: {provider.calls}'


def test_anonymous_head_cannot_start_a_generation(app_client, provider, anonymous):
    # Flask runs the GET view for HEAD, so HEAD must be gated the same way.
    r = app_client.head('/api/suno?action=generate&prompt=a+song')
    assert r.status_code == 401
    assert provider.calls == []


def test_anonymous_post_generation_is_still_refused(app_client, provider, anonymous):
    r = app_client.post('/api/suno', json={'action': 'jingle', 'brand': 'Acme'})
    assert r.status_code == 401
    assert provider.calls == []


@pytest.mark.parametrize('query', ['', 'action=list', 'action=list_jingles',
                                   'action=jingle_styles', 'action=status&job_id=nope'])
def test_public_reads_still_work_without_login(app_client, provider, anonymous, query):
    r = app_client.get(f'/api/suno?{query}')
    assert r.status_code == 200, (query, r.status_code)
    assert provider.calls == []


def test_agent_key_can_generate(app_client, provider, anonymous):
    r = app_client.get('/api/suno?action=generate&prompt=a+song&title=T',
                       headers={'X-Agent-Key': AGENT_KEY})
    assert r.status_code == 200
    assert r.get_json()['action'] == 'generating'
    assert [c for c in provider.calls if c[0] == 'POST'] == [('POST', f'{suno.SUNO_API_BASE}/api/v1/generate')]


def test_wrong_agent_key_is_refused(app_client, provider, anonymous):
    r = app_client.get('/api/suno?action=generate&prompt=a+song',
                       headers={'X-Agent-Key': 'not-the-key'})
    assert r.status_code == 401
    assert provider.calls == []


def test_signed_in_user_can_generate(app_client, provider):
    with patch('services.auth.get_token_from_request', return_value='tok'), \
         patch('services.auth.verify_clerk_token', return_value='user_123'):
        r = app_client.get('/api/suno?action=generate&prompt=a+song&title=T')
    assert r.status_code == 200
    assert r.get_json()['action'] == 'generating'
    assert ('POST', f'{suno.SUNO_API_BASE}/api/v1/generate') in provider.calls
