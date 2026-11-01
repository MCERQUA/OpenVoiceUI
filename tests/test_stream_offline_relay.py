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


PROMISE = "Got it. I'll rebuild the three service pages now."
TOOL_START = {'type': 'tool', 'phase': 'start', 'name': 'write', 'toolCallId': 't1'}


class _MultiLegGateway(_ScriptedGateway):
    """Like _ScriptedGateway, but each stream_to_queue call (first leg,
    auto-continue leg, ...) takes its own (release, final) pair, so a test can
    drive the promise leg and the continue leg separately."""

    def __init__(self, openclaw, before, legs):
        super().__init__(openclaw, before, None, None)
        self.legs = list(legs)

    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs):
        self.calls.append(message)
        release, final = self.legs[min(len(self.calls), len(self.legs)) - 1]
        for evt in self.before:
            event_queue.put(evt)
        release.wait(10)
        event_queue.put(final)


def _open_multi(client, monkeypatch, oc, legs):
    stub = _MultiLegGateway(oc, BEFORE, legs)
    monkeypatch.setattr(conv, 'gateway_manager', stub)
    resp = client.post('/api/conversation?stream=1', buffered=False,
                       json={'message': 'rebuild the three service pages',
                             'tts_provider': 'groq'})
    assert resp.status_code == 200
    return stub, resp


class TestStreamOfflineRelayUncommittedPromise:
    """Review of #563: a promise reply ("I'll ...", 0 tools, >1s) is the turn
    the in-stream auto-continue would have steered into doing the work. When
    the stream is dead that steer never runs, so the promise must NOT be
    relayed to the owner as if it were the finished answer."""

    def test_disconnect_before_promise_on_original_queue_no_relay(
            self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        stub, resp = _open_stream(app_client, monkeypatch, oc, _final(PROMISE), release)
        it = iter(resp.response)
        _chunks_until(it, 'heartbeat')
        resp.close()          # client drops while the first leg is still running
        time.sleep(1.1)       # past is_uncommitted_promise's 1s floor
        release.set()         # ...and the first leg ends on a bare promise
        time.sleep(0.6)
        assert calls == [], 'an uncommitted promise is never relayed as the answer'
        assert _relays(conn) == []
        assert len(stub.calls) == 1, 'no continue steer runs from a dead stream'

    def test_generator_exit_at_retrying_yield_no_relay(
            self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        threading.Timer(1.1, release.set).start()
        stub, resp = _open_stream(app_client, monkeypatch, oc, _final(PROMISE), release)
        it = iter(resp.response)
        # The stream sees the promise text_done, starts auto-continue and
        # yields {"type": "retrying"} BEFORE it swaps event_queue. The write of
        # that chunk is the one that fails: GeneratorExit lands at that yield.
        assert 'retrying' in _chunks_until(it, 'retrying')
        resp.close()
        time.sleep(0.6)
        assert calls == [], 'the promise text_done taken by the dead stream is not relayed'
        assert _relays(conn) == []
        assert len(stub.calls) == 1, 'the continue thread was never started'

    def test_promise_wording_with_a_tool_call_still_relays(
            self, app_client, monkeypatch, quiet, openclaw):
        """Control: the guard keys on the tool count from evt['actions'], not
        on the wording. Same 'I'll ...' text, >1s, but a tool ran -> relayed."""
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        _, resp = _open_stream(app_client, monkeypatch, oc,
                               dict(_final(PROMISE), actions=[TOOL_START]), release)
        it = iter(resp.response)
        _chunks_until(it, 'heartbeat')
        resp.close()
        time.sleep(1.1)
        release.set()
        assert _wait(lambda: len(calls) == 1 and len(_relays(conn)) == 1)
        assert calls[0][0] == PROMISE

    def test_disconnect_during_continue_leg_relays_finished_reply(
            self, app_client, monkeypatch, quiet, openclaw):
        """The swap happened while the client was connected; the client drops
        during the continue leg; the continue leg's finished reply relays."""
        oc, conn, calls, _ = openclaw
        rel1, rel2 = threading.Event(), threading.Event()
        threading.Timer(1.1, rel1.set).start()
        stub, resp = _open_multi(app_client, monkeypatch, oc, [
            (rel1, _final(PROMISE)),
            (rel2, dict(_final(FINAL), actions=[TOOL_START])),
        ])
        it = iter(resp.response)
        assert 'retrying' in _chunks_until(it, 'retrying')
        # The next chunk (the continue leg's heartbeat) is pulled, so the
        # 'retrying' write succeeded and event_queue is already the continue queue.
        assert 'heartbeat' in _chunks_until(it, 'heartbeat')
        resp.close()
        rel2.set()
        assert _wait(lambda: len(calls) == 1 and len(_relays(conn)) == 1)
        assert calls[0][0] == FINAL
        assert len(stub.calls) == 2 and stub.calls[1].startswith('[SYSTEM: You said you would')


class TestStreamOfflineRelayDegenerate:
    """#565: a bare NO/YES final is broken-LLM output. The in-stream retry
    would have re-asked; with the stream dead it must not reach the owner as
    the answer, and a drop at the 'retrying' yield must still run the retry."""

    @pytest.mark.parametrize('bare', ['NO', 'yes.', ' No! '])
    def test_disconnect_before_bare_final_no_relay(
            self, app_client, monkeypatch, quiet, openclaw, bare):
        oc, conn, calls, _ = openclaw
        release = threading.Event()
        stub, resp = _open_stream(app_client, monkeypatch, oc, _final(bare), release)
        it = iter(resp.response)
        _chunks_until(it, 'heartbeat')
        resp.close()
        release.set()
        time.sleep(0.6)
        assert calls == [], 'a bare NO/YES is never relayed as the answer'
        assert _relays(conn) == []

    def test_drop_at_retrying_yield_retry_still_runs_and_real_answer_relays(
            self, app_client, monkeypatch, quiet, openclaw):
        oc, conn, calls, _ = openclaw
        rel1, rel2 = threading.Event(), threading.Event()
        rel1.set()
        stub, resp = _open_multi(app_client, monkeypatch, oc, [
            (rel1, _final('NO')),
            (rel2, _final(FINAL)),
        ])
        it = iter(resp.response)
        assert 'retrying' in _chunks_until(it, 'retrying')
        resp.close()          # GeneratorExit lands at the 'retrying' yield
        rel2.set()
        assert _wait(lambda: len(calls) == 1 and len(_relays(conn)) == 1)
        assert calls[0][0] == FINAL, 'the retry leg ran and its real answer is relayed'
        assert len(stub.calls) == 2

    def test_offline_relay_text_uses_the_shared_predicate(self):
        assert conv._offline_relay_text(_final('NO')) is None
        assert conv._offline_relay_text(_final('Yes!')) is None
        assert conv._offline_relay_text(_final('No problem, done.')) == 'No problem, done.'
        assert conv.is_degenerate_reply('YES.') and not conv.is_degenerate_reply('Yes, done')


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
