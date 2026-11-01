"""
Deferred text_done — a gateway ends a turn ON PURPOSE with nothing to say.

A gateway plugin (e.g. a "brain" that answers later through the proactive
push channel) signals an intentional empty turn with
    {'type': 'text_done', 'response': '', 'deferred': True, 'actions': [...]}

Rule (Mike 2026-10-20, NO FAKE REPLIES): the app must never speak canned
filler as if it were the agent. So a deferred turn must end silently:
no empty-retry, no Z.AI-direct fallback LLM call, no substituted line, no
TTS, no saved assistant turn — just a `no_audio` event so the browser
re-enables the mic.

Controls: an empty text_done WITHOUT `deferred` must still take the existing
empty-response retry path, and `deferred` with a non-empty response is
ignored (the reply is spoken as normal).
"""

import json
import sys
import types

import pytest

import routes.conversation as conv


class _StubGateway:
    """Stand-in for services.gateway_manager: each call to stream_to_queue
    pops the next scripted turn (a list of events) and puts it on the queue."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def is_configured(self):
        return True

    def get(self, gateway_id=None):
        return None

    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs):
        self.calls.append(message)
        events = self.turns.pop(0) if self.turns else [
            {'type': 'text_done', 'response': '', 'actions': []}]
        for evt in events:
            event_queue.put(evt)


@pytest.fixture(scope='module')
def app_client():
    # Local/open mode: these tests exercise the conversation route, not the
    # Clerk gate, so build the app with no Clerk / require-auth env set.
    with pytest.MonkeyPatch.context() as mp:
        for var in ('CLERK_PUBLISHABLE_KEY', 'VITE_CLERK_PUBLISHABLE_KEY',
                    'CLERK_SECRET_KEY', 'CANVAS_REQUIRE_AUTH'):
            mp.delenv(var, raising=False)
        from app import create_app
        app, _ = create_app(config_override={'TESTING': True})
        from routes.canvas import canvas_bp
        from routes.conversation import conversation_bp
        if 'canvas' not in app.blueprints:
            app.register_blueprint(canvas_bp)
        if 'conversation' not in app.blueprints:
            app.register_blueprint(conversation_bp)
        yield app.test_client()


@pytest.fixture
def spies(monkeypatch):
    """Record every side effect the deferred path must NOT produce."""
    rec = {'tts': [], 'saved': [], 'logged': [], 'metrics': [],
           'zai_direct': [], 'requests_post': [], 'flash': []}

    def _tts(text, **kw):
        rec['tts'].append(text)
        return 'QUJD'  # base64 "ABC"

    monkeypatch.setattr(conv, '_tts_generate_b64', _tts)
    monkeypatch.setattr(conv, 'save_conversation_turn',
                        lambda **kw: rec['saved'].append(kw))
    monkeypatch.setattr(conv, 'log_conversation',
                        lambda role, msg, **kw: rec['logged'].append((role, msg)))
    monkeypatch.setattr(conv, 'log_metrics',
                        lambda m: rec['metrics'].append(dict(m)))

    import services.zai_direct as zd

    def _zai(*a, **kw):
        rec['zai_direct'].append(a)
        raise RuntimeError('fallback LLM must not be called in this test')

    monkeypatch.setattr(zd, 'zai_messages_post', _zai)

    import requests

    def _post(*a, **kw):
        rec['requests_post'].append(a)
        raise RuntimeError('fallback LLM must not be called in this test')

    monkeypatch.setattr(requests, 'post', _post)

    # Non-streaming last resort imports `server` lazily — stub it.
    fake_server = types.ModuleType('server')

    def _flash(*a, **kw):
        rec['flash'].append(a)
        return 'FLASH FALLBACK TEXT'

    fake_server.get_zai_direct_response = _flash
    monkeypatch.setitem(sys.modules, 'server', fake_server)
    return rec


def _stream(client, monkeypatch, turns, message='what is the weather like'):
    gw = _StubGateway(turns)
    monkeypatch.setattr(conv, 'gateway_manager', gw)
    resp = client.post('/api/conversation?stream=1',
                       json={'message': message, 'tts_provider': 'groq'})
    assert resp.status_code == 200
    events = [json.loads(l) for l in resp.get_data(as_text=True).splitlines() if l.strip()]
    return gw, events


def _types(events):
    return [e.get('type') for e in events]


DEFERRED_TURN = [[
    {'type': 'handshake', 'ms': 3},
    {'type': 'text_done', 'response': '', 'deferred': True, 'actions': []},
]]


class TestDeferredStreaming:

    def test_deferred_turn_ends_silently(self, app_client, monkeypatch, spies):
        gw, events = _stream(app_client, monkeypatch, DEFERRED_TURN)
        types_ = _types(events)

        # Exactly one gateway call — no empty-retry, no steer re-fire.
        assert len(gw.calls) == 1
        assert 'retrying' not in types_
        # No fallback LLM of any kind.
        assert spies['zai_direct'] == []
        assert spies['requests_post'] == []
        # No TTS, no audio.
        assert spies['tts'] == []
        assert 'audio' not in types_
        # Frontend told: deferred text_done, then no_audio.
        done = [e for e in events if e['type'] == 'text_done']
        assert len(done) == 1
        assert done[0]['response'] == ''
        assert done[0]['deferred'] is True
        no_audio = [e for e in events if e['type'] == 'no_audio']
        assert len(no_audio) == 1 and no_audio[0].get('deferred') is True
        assert types_.index('text_done') < types_.index('no_audio')
        # No substituted line anywhere on the wire.
        body = json.dumps(events)
        for filler in ('brain glitched', 'One moment', 'Here you go',
                       'took a bit longer', 'still working on that'):
            assert filler not in body
        # Nothing saved as an assistant turn; metrics still logged.
        assert spies['saved'] == []
        assert [r for r in spies['logged'] if r[0] == 'assistant'] == []
        assert len(spies['metrics']) == 1
        assert spies['metrics'][0].get('deferred') == 1
        assert spies['metrics'][0].get('response_len') == 0

    def test_control_plain_empty_still_retries(self, app_client, monkeypatch, spies):
        """No `deferred` → today's behaviour: fast empty triggers the retry."""
        turns = [
            [{'type': 'text_done', 'response': '', 'actions': []}],
            [{'type': 'text_done', 'response': 'It is sunny today.', 'actions': []}],
        ]
        gw, events = _stream(app_client, monkeypatch, turns)
        types_ = _types(events)
        assert len(gw.calls) == 2
        assert 'retrying' in types_
        done = [e for e in events if e['type'] == 'text_done']
        assert done[-1]['response'] == 'It is sunny today.'
        assert 'deferred' not in done[-1]
        assert spies['tts']  # spoken
        assert spies['saved']  # saved as a real assistant turn

    def test_deferred_with_text_is_ignored(self, app_client, monkeypatch, spies):
        """`deferred` only applies to an EMPTY response."""
        turns = [[{'type': 'text_done', 'response': 'Here is the answer.',
                   'deferred': True, 'actions': []}]]
        gw, events = _stream(app_client, monkeypatch, turns)
        done = [e for e in events if e['type'] == 'text_done']
        assert done[-1]['response'] == 'Here is the answer.'
        assert 'audio' in _types(events)
        assert spies['saved']


class TestDeferredNonStreaming:

    def test_deferred_non_streaming_no_flash_fallback(self, app_client, monkeypatch, spies):
        gw = _StubGateway(DEFERRED_TURN)
        monkeypatch.setattr(conv, 'gateway_manager', gw)
        resp = app_client.post('/api/conversation',
                               json={'message': 'what is the weather like'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['response'] == ''
        assert data['deferred'] is True
        assert 'audio' not in data
        assert spies['flash'] == []
        assert spies['tts'] == []
        assert spies['saved'] == []

    def test_control_non_streaming_empty_uses_flash(self, app_client, monkeypatch, spies):
        gw = _StubGateway([[{'type': 'text_done', 'response': '', 'actions': []}]])
        monkeypatch.setattr(conv, 'gateway_manager', gw)
        resp = app_client.post('/api/conversation',
                               json={'message': 'what is the weather like'})
        data = resp.get_json()
        assert spies['flash'], 'non-deferred empty must still reach the flash fallback'
        assert data['response'] == 'FLASH FALLBACK TEXT'
        assert 'deferred' not in data
