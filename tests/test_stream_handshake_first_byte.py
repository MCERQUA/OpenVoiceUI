"""
The browser learns the gateway is connected the moment it IS connected.

Measured 2026-11-14 (a comms-brain tenant): the gateway handshake landed
~0.2 s after the request, but the stream loop consumed the `handshake` event
without writing anything. The first byte the browser got was the 10 s idle
heartbeat, so `fetch()` stayed pending and the UI sat on "connecting" for
~10 s on EVERY turn (werkzeug logged each 200 exactly 10 s after the turn
began). A slow gateway (one that only speaks after 15-30 s of thinking) shows
the whole wait as "connecting".

Rule: a `handshake` from the gateway is forwarded to the browser at once.
The browser has no handler for it (unknown event types are ignored), so the
only effect is that response headers + the first line go out immediately.

Control: a turn with no handshake still works and still ends in text_done.
"""

import json
import sys
import threading
import time
import types

import pytest

import routes.conversation as conv


class _SlowGateway:
    """Connects at once (handshake), then 'thinks' for THINK_S before replying."""

    THINK_S = 2.5

    def __init__(self, send_handshake=True):
        self.send_handshake = send_handshake
        self.calls = []

    def is_configured(self):
        return True

    def get(self, gateway_id=None):
        return None

    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs):
        self.calls.append(message)
        if self.send_handshake:
            event_queue.put({'type': 'handshake', 'ms': 5})
        time.sleep(self.THINK_S)
        event_queue.put({'type': 'delta', 'text': 'It is sunny today.'})
        event_queue.put({'type': 'text_done', 'response': 'It is sunny today.',
                         'actions': []})


@pytest.fixture(scope='module')
def app_client():
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
def quiet(monkeypatch):
    monkeypatch.setattr(conv, '_tts_generate_b64', lambda text, **kw: 'QUJD')
    monkeypatch.setattr(conv, 'save_conversation_turn', lambda **kw: None)
    monkeypatch.setattr(conv, 'log_conversation', lambda role, msg, **kw: None)
    monkeypatch.setattr(conv, 'log_metrics', lambda m: None)
    fake_server = types.ModuleType('server')
    fake_server.get_zai_direct_response = lambda *a, **kw: 'FLASH'
    monkeypatch.setitem(sys.modules, 'server', fake_server)


def _first_line_and_rest(client, monkeypatch, gw):
    monkeypatch.setattr(conv, 'gateway_manager', gw)
    t0 = time.time()
    resp = client.post('/api/conversation?stream=1', buffered=False,
                       json={'message': 'what is the weather like',
                             'tts_provider': 'groq'})
    assert resp.status_code == 200
    it = iter(resp.response)
    buf = b''
    first_at = None
    lines = []
    for chunk in it:
        if not chunk:
            continue
        if first_at is None:
            first_at = time.time() - t0
        buf += chunk if isinstance(chunk, bytes) else chunk.encode()
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            if line.strip():
                lines.append(json.loads(line))
    resp.close()
    return first_at, lines


class TestHandshakeReachesBrowser:

    def test_handshake_is_the_first_byte(self, app_client, monkeypatch, quiet):
        first_at, events = _first_line_and_rest(app_client, monkeypatch, _SlowGateway())
        types_ = [e.get('type') for e in events]
        assert types_[0] == 'handshake', types_
        # The browser hears "connected" while the gateway is still thinking,
        # not when the reply (THINK_S later) or a 10 s heartbeat arrives.
        assert first_at < 1.0, 'first byte took %.2fs' % first_at
        assert 'text_done' in types_
        assert types_.index('handshake') < types_.index('text_done')

    def test_control_no_handshake_still_completes(self, app_client, monkeypatch, quiet):
        first_at, events = _first_line_and_rest(
            app_client, monkeypatch, _SlowGateway(send_handshake=False))
        types_ = [e.get('type') for e in events]
        assert 'handshake' not in types_
        done = [e for e in events if e['type'] == 'text_done']
        assert done and done[-1]['response'] == 'It is sunny today.'
        # Without a handshake nothing can be sent early: the first byte waits
        # for the reply (proves the timing assertion above can fail).
        assert first_at >= _SlowGateway.THINK_S - 0.3
