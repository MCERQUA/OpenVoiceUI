"""
stream-offline-relay: a streamed reply that finishes after the browser
disconnected reaches the owner through the tenant's own agent.

#549 (test_offline_completion_relay.py) covers orphaned SUBAGENT completions.
The in-stream path was not covered: when the client of /api/conversation?stream=1
went away mid-turn (tab closed, network drop) the generator died at its next
write, nobody read the rest of event_queue, and the final reply (often a long
auto-continue turn) landed in the gateway history only.

Now, when the stream's client is gone before a text_done was written, the
turn's final reply is handed to the SAME delivery path as #549:
OpenClawGateway.deliver_offline_completion -> proactive handler
(server.push_proactive_message) -> delivered=0 -> _relay_offline_completion.

These tests drive the real route + the real OpenClawGateway/GatewayConnection
delivery code; only the gateway's event source, the proactive handler and the
gateway WebSocket are stubbed.
"""

import asyncio
import json
import threading
import time

import pytest

import routes.conversation as conv
import services.gateways.openclaw as gw
from services.gateways.openclaw import EventDispatcher, GatewayConnection, OpenClawGateway

FINAL = 'All three pages are rebuilt and live. [CANVAS:site-report]'
RELAY_WIRE_KEY = 'agent:openvoiceui:offline-relay'


class FakeWS:
    def __init__(self):
        self.sent = []
        self.close_code = None

    async def send(self, raw):
        self.sent.append(json.loads(raw))


class _ScriptedGateway:
    """gateway_manager stand-in. stream_to_queue puts the `before` events,
    then blocks until `release` is set, then puts the terminal `final` event —
    so the test can drop the client while the turn is still running.
    get() returns a real OpenClawGateway whose connection talks to a FakeWS."""

    def __init__(self, openclaw, before, final, release):
        self.openclaw = openclaw
        self.before = before
        self.final = final
        self.release = release
        self.calls = []

    def is_configured(self):
        return True

    def get(self, gateway_id=None):
        return self.openclaw

    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs):
        self.calls.append(message)
        for evt in self.before:
            event_queue.put(evt)
        self.release.wait(10)
        event_queue.put(self.final)


@pytest.fixture(scope='module')
def app_client():
    with pytest.MonkeyPatch.context() as mp:
        for var in ('CLERK_PUBLISHABLE_KEY', 'VITE_CLERK_PUBLISHABLE_KEY',
                    'CLERK_SECRET_KEY', 'CANVAS_REQUIRE_AUTH'):
            mp.delenv(var, raising=False)
        from app import create_app
        app, _ = create_app(config_override={'TESTING': True})
        from routes.conversation import conversation_bp
        if 'conversation' not in app.blueprints:
            app.register_blueprint(conversation_bp)
        yield app.test_client()


@pytest.fixture
def quiet(monkeypatch):
    """No real TTS, DB writes or metrics; no relay grace sleep."""
    monkeypatch.setattr(conv, '_tts_generate_b64', lambda text, **kw: 'QUJD')
    monkeypatch.setattr(conv, 'save_conversation_turn', lambda **kw: None)
    monkeypatch.setattr(conv, 'log_conversation', lambda *a, **kw: None)
    monkeypatch.setattr(conv, 'log_metrics', lambda m: None)
    monkeypatch.setattr(conv, '_STREAM_RELAY_ABORT_GRACE_S', 0, raising=False)
    monkeypatch.setattr(conv, '_last_user_abort_ts', 0.0, raising=False)


@pytest.fixture
def openclaw(monkeypatch):
    """A real OpenClawGateway whose default connection is 'connected' to a
    FakeWS, with its asyncio loop running on a thread (as in production)."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    conn = GatewayConnection()
    conn._dispatcher = EventDispatcher()
    conn._ws = FakeWS()
    conn._connected = True
    conn._default_agent_id = 'openvoiceui'
    conn._loop = loop
    oc = OpenClawGateway()
    oc._router._connections[gw._GATEWAY_URLS['default']] = conn

    calls = []
    state = {'delivered': 0}

    def handler(text, session_key, run_id=None):
        calls.append((text, session_key, run_id))
        return state['delivered']

    monkeypatch.setattr(gw, '_PROACTIVE_HANDLER', handler)
    yield oc, conn, calls, state
    loop.call_soon_threadsafe(loop.stop)
    t.join(2)


def _relays(conn):
    return [f for f in conn._ws.sent
            if f.get('method') == 'chat.send' and f['id'].startswith('relay-')]


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


BEFORE = [{'type': 'handshake', 'ms': 3}, {'type': 'heartbeat', 'elapsed': 1}]


def _open_stream(client, monkeypatch, oc, final, release, before=BEFORE,
                 message='rebuild the three service pages'):
    stub = _ScriptedGateway(oc, before, final, release)
    monkeypatch.setattr(conv, 'gateway_manager', stub)
    resp = client.post('/api/conversation?stream=1', buffered=False,
                       json={'message': message, 'tts_provider': 'groq'})
    assert resp.status_code == 200
    return stub, resp


def _chunks_until(it, kind):
    """Read the stream until a chunk of the given event type has been read."""
    seen = []
    for raw in it:
        for line in (raw.decode() if isinstance(raw, bytes) else raw).splitlines():
            if line.strip():
                seen.append(json.loads(line)['type'])
        if kind in seen:
            return seen
    return seen


def _final(text=FINAL, **extra):
    return dict({'type': 'text_done', 'response': text, 'actions': []}, **extra)


class TestStreamOfflineRelay:

    def test_disconnect_before_final_relays_once(self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        _, resp = _open_stream(app_client, monkeypatch, oc, _final(), release)
        it = iter(resp.response)
        assert 'heartbeat' in _chunks_until(it, 'heartbeat')
        resp.close()          # the browser goes away mid-turn
        release.set()         # ...and the agent finishes afterwards

        assert _wait(lambda: len(calls) == 1 and len(_relays(conn)) == 1)
        time.sleep(0.2)
        assert len(calls) == 1, 'the proactive handler is called exactly once'
        text, session_key, run_id = calls[0]
        assert text == FINAL
        assert run_id.startswith('stream:')
        relays = _relays(conn)
        assert len(relays) == 1
        assert relays[0]['params']['sessionKey'] == RELAY_WIRE_KEY
        assert gw._OFFLINE_RELAY_MARKER in relays[0]['params']['message']
        assert FINAL in relays[0]['params']['message']
        assert run_id in conn._relayed_runs, 'deduped per stream run id'

    def test_client_connected_whole_time_no_relay(self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        release.set()
        _, resp = _open_stream(app_client, monkeypatch, oc, _final(), release)
        events = [json.loads(l) for l in resp.get_data(as_text=True).splitlines() if l.strip()]
        resp.close()
        done = [e for e in events if e['type'] == 'text_done']
        assert done and done[-1]['response'] == FINAL
        time.sleep(0.5)
        assert calls == []
        assert _relays(conn) == []

    def test_disconnect_after_text_done_written_no_relay(self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        release.set()
        _, resp = _open_stream(app_client, monkeypatch, oc, _final(), release)
        it = iter(resp.response)
        assert 'text_done' in _chunks_until(it, 'text_done')
        # The WSGI server asks for the next chunk only after the previous write
        # succeeded; the client then drops during the audio tail.
        next(it, None)
        resp.close()
        time.sleep(0.5)
        assert calls == [], 'a reply the client received is never re-notified'

    @pytest.mark.parametrize('flag', ['aborted', 'timed_out', 'error', 'deferred'])
    def test_partial_or_failed_final_never_relayed(self, app_client, monkeypatch, quiet, openclaw, flag):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        final = _final('I was halfway through the', **{flag: True})
        _, resp = _open_stream(app_client, monkeypatch, oc, final, release)
        it = iter(resp.response)
        _chunks_until(it, 'heartbeat')
        resp.close()
        release.set()
        time.sleep(0.5)
        assert calls == []

    def test_user_abort_is_never_relayed(self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        _, resp = _open_stream(app_client, monkeypatch, oc, _final(), release)
        it = iter(resp.response)
        _chunks_until(it, 'heartbeat')
        resp.close()
        app_client.post('/api/conversation/abort', json={'source': 'stopVoiceInput'})
        release.set()
        time.sleep(0.5)
        assert calls == []

    def test_system_trigger_never_relayed(self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        _, resp = _open_stream(app_client, monkeypatch, oc, _final('Hey, what are we doing?'),
                               release, message='__session_start__')
        it = iter(resp.response)
        _chunks_until(it, 'heartbeat')
        resp.close()
        release.set()
        time.sleep(0.5)
        assert calls == []

    def test_negative_control_without_the_hook_nothing_is_delivered(
            self, app_client, monkeypatch, quiet, openclaw):
        """Same scenario as the first test with the stream-offline-relay
        starter disabled (= origin/main behaviour): the harness itself never
        produces a handler call, so the first test's call comes from the hook."""
        oc, conn, calls, _ = openclaw
        monkeypatch.setattr(conv, '_start_stream_offline_relay', lambda **kw: None, raising=False)
        release = threading.Event()
        _, resp = _open_stream(app_client, monkeypatch, oc, _final(), release)
        it = iter(resp.response)
        _chunks_until(it, 'heartbeat')
        resp.close()
        release.set()
        time.sleep(0.5)
        assert calls == []
        assert _relays(conn) == []


class TestDeliverOfflineCompletion:
    """The shared gateway entry point both offline paths now use."""

    def test_browser_received_it_no_relay(self, openclaw):
        oc, conn, calls, state = openclaw
        state['delivered'] = 1
        assert oc.deliver_offline_completion('Done.', 'main', ('stream:x',)) == 1
        time.sleep(0.2)
        assert calls == [('Done.', 'main', 'stream:x')]
        assert _relays(conn) == []

    def test_same_stream_run_id_relayed_once(self, openclaw):
        oc, conn, calls, _ = openclaw
        oc.deliver_offline_completion('Done.', 'main', ('stream:y',))
        oc.deliver_offline_completion('Done.', 'main', ('stream:y',))
        assert _wait(lambda: len(_relays(conn)) >= 1)
        time.sleep(0.2)
        assert len(calls) == 2 and len(_relays(conn)) == 1

    def test_relay_session_never_delivered(self, openclaw):
        oc, conn, calls, _ = openclaw
        assert oc.deliver_offline_completion('x', gw._OFFLINE_RELAY_SESSION, ('stream:z',)) is None
        assert calls == []

    def test_gateway_disconnected_no_relay(self, openclaw):
        oc, conn, calls, _ = openclaw
        conn._connected = False
        oc.deliver_offline_completion('Done.', 'main', ('stream:w',))
        time.sleep(0.2)
        assert len(calls) == 1 and _relays(conn) == []
