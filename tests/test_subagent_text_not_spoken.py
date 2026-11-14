"""
A sub-agent's own text is never spoken on the parent's voice turn.

Measured 2026-11-05 on a delegated voice turn ("research this in the
background with a sub-agent"): the dispatcher hands every sub-agent event to
the parent's live subscription, and the `assistant` branch of
OpenClawGateway._stream_events streamed ALL of them as `delta`s. The user
heard the sub-agent narrate to its parent ("Let me grab details and a third
source"), its raw markdown report, and its protocol line
"OUTPUT_SAVED: .agents/<file>.md".

What identifies the user's turn is the event's sessionKey, not its runId: on
that turn the parent spoke from THREE runIds (the ACKed run, the announce-back
run "announce:v1:<child session key>:<child runId>", and the continuation run)
and only the first one was ever ACKed. A runId allow-list would mute the
announce-back summary, which is the answer the user is waiting for. Every
assistant event captured on that turn (363 of 363) carried a sessionKey, and
every sub-agent one carried spawnedBy as well.

The event shapes below are copied from that capture (ids shortened).
"""

import asyncio
import json
import queue

import pytest

import services.gateways.openclaw as gw
from services.gateways.openclaw import EventDispatcher, GatewayConnection


def is_subagent_agent_event(*a, **k):
    return gw.is_subagent_agent_event(*a, **k)

ALIAS = 'main'
MAIN_SK = 'agent:openvoiceui:main'
SUB_SK = 'agent:openvoiceui:subagent:935c6045'
MAIN_RUN = 'b551ac90'
SUB_RUN = 'a3879c1e'
ANNOUNCE_RUN = f'announce:v1:{SUB_SK}:{SUB_RUN}'

ACK_TEXT = "On it, I'll dig into rental pricing with a background agent."
SUB_NARRATION = 'Two providers found. Let me grab details and a third source.'
SUB_REPORT = ('Research complete. Rentals cluster at **$0.50-$0.85/hr**.\n\n'
              'OUTPUT_SAVED: .agents/rental-prices.md')
ANNOUNCE_TEXT = 'Rentals run roughly fifty to eighty-five cents an hour.'
CONT_TEXT = 'That research task is wrapped up.'


def _agent(run_id, sk, stream, data, spawned_by=None):
    payload = {'runId': run_id, 'stream': stream, 'data': data}
    if sk is not None:
        payload['sessionKey'] = sk
    if spawned_by:
        payload['spawnedBy'] = spawned_by
    return {'type': 'event', 'event': 'agent', 'payload': payload}


def _deltas(run_id, sk, text, spawned_by=None, step=12):
    """Cumulative text + delta, the way the gateway streams assistant output."""
    out = []
    for i in range(0, len(text), step):
        out.append(_agent(run_id, sk, 'assistant',
                          {'text': text[:i + step], 'delta': text[i:i + step]},
                          spawned_by))
    return out


def _lifecycle(run_id, sk, phase, spawned_by=None):
    return _agent(run_id, sk, 'lifecycle', {'phase': phase}, spawned_by)


def _chat_final(run_id, sk, text=None):
    payload = {'runId': run_id, 'sessionKey': sk, 'state': 'final'}
    if text:
        payload['message'] = {'role': 'assistant', 'content': text}
    return {'type': 'event', 'event': 'chat', 'payload': payload}


class FakeWS:
    def __init__(self, on_send=None):
        self.sent = []
        self.on_send = on_send
        self.close_code = None

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if self.on_send is not None:
            asyncio.ensure_future(self.on_send(frame))


_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def fast_sleep(monkeypatch):
    async def _instant(_s, *a, **k):
        await _REAL_SLEEP(0)
    monkeypatch.setattr(gw.asyncio, 'sleep', _instant)


def _run_turn(script, sub_sk_without_key=False):
    """Drive the REAL dispatcher + _stream_events through a delegated turn.

    `script` is the list of gateway frames after the chat.send ACK. The
    continuation nudge (if the gateway code sends one) is answered with
    CONT_TEXT on a fresh runId, as the gateway does. Returns the list of
    events _stream_events put on the HTTP event_queue."""
    conn = GatewayConnection()
    d = conn._dispatcher = EventDispatcher()

    async def reply(frame):
        if frame.get('method') != 'chat.send' or not frame['id'].startswith('chat-'):
            return
        cid = frame['id'][len('chat-'):]
        await d._route({'type': 'res', 'id': frame['id'], 'ok': True,
                        'payload': {'runId': cid, 'status': 'started'}})
        await d._route(_lifecycle(cid, MAIN_SK, 'start'))
        for evt in _deltas(cid, MAIN_SK, CONT_TEXT):
            await d._route(evt)
        await d._route(_lifecycle(cid, MAIN_SK, 'end'))

    conn._ws = FakeWS(reply)
    conn._connected = True
    conn._default_agent_id = 'openvoiceui'
    out = queue.Queue()

    async def go():
        sub = d.subscribe('chat-user', ALIAS)
        await d._route({'type': 'res', 'id': 'chat-chat-user', 'ok': True,
                        'payload': {'runId': MAIN_RUN, 'status': 'started'}})

        async def feed():
            for evt in script:
                await d._route(evt)
                await _REAL_SLEEP(0)

        feeder = asyncio.ensure_future(feed())
        await conn._stream_events(sub, out, ALIAS, [], _cleanup_ids=[])
        await feeder

    asyncio.run(asyncio.wait_for(go(), timeout=20))
    events = []
    while not out.empty():
        events.append(out.get())
    return events


def _delegated_script(drop_sub_session_key=False):
    """The measured order: ack -> spawn -> main end (sub active) -> interim
    final -> sub-agent works and reports -> sub end -> announce-back run
    speaks the summary -> main end -> continuation."""
    sub_sk = None if drop_sub_session_key else SUB_SK
    spawned = None if drop_sub_session_key else MAIN_SK
    s = [_lifecycle(MAIN_RUN, MAIN_SK, 'start')]
    s += _deltas(MAIN_RUN, MAIN_SK, ACK_TEXT)
    s += [_agent(MAIN_RUN, MAIN_SK, 'tool',
                 {'phase': 'start', 'name': 'sessions_spawn', 'toolCallId': 'c1'}),
          _agent(MAIN_RUN, MAIN_SK, 'tool',
                 {'phase': 'result', 'name': 'sessions_spawn', 'toolCallId': 'c1'}),
          # The child's lifecycle.start always carries its session key —
          # that is how the gateway code learns the child's runId.
          _lifecycle(SUB_RUN, SUB_SK, 'start', MAIN_SK),
          _lifecycle(MAIN_RUN, MAIN_SK, 'end'),
          _chat_final(MAIN_RUN, MAIN_SK, ACK_TEXT)]
    s += _deltas(SUB_RUN, sub_sk, SUB_NARRATION + SUB_REPORT, spawned)
    s += [_lifecycle(SUB_RUN, SUB_SK, 'end', MAIN_SK),
          _chat_final(SUB_RUN, SUB_SK, SUB_NARRATION + SUB_REPORT),
          _lifecycle(ANNOUNCE_RUN, MAIN_SK, 'start')]
    s += _deltas(ANNOUNCE_RUN, MAIN_SK, ANNOUNCE_TEXT)
    s += [_lifecycle(ANNOUNCE_RUN, MAIN_SK, 'end')]
    return s


def _spoken(events):
    return ''.join(e['text'] for e in events if e.get('type') == 'delta')


class TestDelegatedTurnSpeech:

    def test_subagent_text_is_not_streamed(self, fast_sleep):
        spoken = _spoken(_run_turn(_delegated_script()))
        assert 'Let me grab details' not in spoken
        assert 'OUTPUT_SAVED' not in spoken
        assert 'Research complete' not in spoken

    def test_parent_text_from_every_parent_run_is_still_streamed(self, fast_sleep):
        # ACKed run, announce-back run (unACKed runId that itself contains
        # "subagent:") and continuation run must all still reach TTS.
        events = _run_turn(_delegated_script())
        spoken = _spoken(events)
        assert ACK_TEXT in spoken
        assert ANNOUNCE_TEXT in spoken
        assert CONT_TEXT in spoken
        done = [e for e in events if e.get('type') == 'text_done']
        assert done and done[-1]['response'] == CONT_TEXT

    def test_subagent_text_without_session_key_is_muted_by_learned_run_id(self, fast_sleep):
        spoken = _spoken(_run_turn(_delegated_script(drop_sub_session_key=True)))
        assert 'Let me grab details' not in spoken
        assert 'OUTPUT_SAVED' not in spoken
        assert ANNOUNCE_TEXT in spoken

    def test_plain_turn_unchanged(self, fast_sleep):
        text = 'The weather today is sunny with a light breeze.'
        script = [_lifecycle(MAIN_RUN, MAIN_SK, 'start')]
        script += _deltas(MAIN_RUN, MAIN_SK, text)
        script += [_lifecycle(MAIN_RUN, MAIN_SK, 'end')]
        events = _run_turn(script)
        assert _spoken(events) == text
        assert events[-1]['type'] == 'text_done' and events[-1]['response'] == text

    def test_legacy_event_with_no_session_key_and_no_run_id_is_spoken(self, fast_sleep):
        # Neither field: cannot be attributed to a sub-agent, so it is kept.
        # Muting the user's answer would be worse than a stray line.
        text = 'Legacy gateways sent bare assistant events like this one.'
        script = [{'type': 'event', 'event': 'agent', 'payload': {
            'stream': 'assistant', 'data': {'text': text, 'delta': text}}},
            _lifecycle(MAIN_RUN, MAIN_SK, 'end')]
        assert _spoken(_run_turn(script)) == text


class TestIsSubagentAgentEvent:

    def test_subagent_session_key(self):
        assert is_subagent_agent_event({'runId': SUB_RUN, 'sessionKey': SUB_SK})

    def test_main_session_key(self):
        assert not is_subagent_agent_event({'runId': MAIN_RUN, 'sessionKey': MAIN_SK})

    def test_announce_run_id_is_not_mistaken_for_a_subagent(self):
        # The announce-back runId embeds the child's session key.
        assert 'subagent:' in ANNOUNCE_RUN
        assert not is_subagent_agent_event(
            {'runId': ANNOUNCE_RUN, 'sessionKey': MAIN_SK}, {SUB_RUN})

    def test_spawned_by_without_session_key(self):
        assert is_subagent_agent_event({'runId': 'x', 'spawnedBy': MAIN_SK})

    def test_known_subagent_run_id_without_session_key(self):
        assert is_subagent_agent_event({'runId': SUB_RUN}, {SUB_RUN})
        assert not is_subagent_agent_event({'runId': MAIN_RUN}, {SUB_RUN})

    def test_no_fields_is_kept(self):
        assert not is_subagent_agent_event({})


class TestOrphanContinuation:
    """The orphan continuation (originating stream gone) collects the parent's
    reply for the proactive push; a sibling sub-agent's text arriving on the
    same subscription must not become that reply."""

    def test_sibling_subagent_text_is_not_the_pushed_reply(self, fast_sleep, monkeypatch):
        calls = []
        monkeypatch.setattr(gw, '_PROACTIVE_HANDLER',
                            lambda text, sk, run_id=None: calls.append(text) or 1)
        conn = GatewayConnection()
        d = conn._dispatcher = EventDispatcher()

        async def reply(frame):
            if frame.get('method') != 'chat.send' or not frame['id'].startswith('chat-'):
                return
            await d._route({'type': 'res', 'id': frame['id'], 'ok': True,
                            'payload': {'runId': 'cont-run', 'status': 'started'}})
            await d._route(_lifecycle(SUB_RUN, SUB_SK, 'start', MAIN_SK))
            for evt in _deltas(SUB_RUN, SUB_SK, SUB_NARRATION + SUB_REPORT, MAIN_SK):
                await d._route(evt)
            for evt in _deltas('cont-run', MAIN_SK, ANNOUNCE_TEXT):
                await d._route(evt)
            await d._route(_chat_final('cont-run', MAIN_SK))

        conn._ws = FakeWS(reply)
        conn._connected = True
        conn._default_agent_id = 'openvoiceui'

        async def go():
            await conn._run_orphan_continuation(ALIAS, (SUB_RUN,))
            for _ in range(50):
                if calls:
                    break
                await _REAL_SLEEP(0.01)

        asyncio.run(asyncio.wait_for(go(), timeout=15))
        assert calls == [ANNOUNCE_TEXT]
