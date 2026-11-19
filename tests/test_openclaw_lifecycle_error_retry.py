"""
A transient lifecycle error that openclaw RETRIES must not end the voice turn.

Measured 2026-11-19 05:29Z on tenant mike (local Qwen provider behind a
single-card listener). The provider answered 503 "card busy"; openclaw
emitted, for the user's run 10e3c880:

    lifecycle start                                             seq 1
    lifecycle error  error="503 Quinn or another caller is using the card
                     (voice first)"  livenessState="blocked"    seq 2

and then retried the SAME runId: 5.4 s later the session row 91f0db5b
(parent = the error row a799912b) held the right answer
"Opening the desktop page for you now. [CANVAS:desktop]". OVU had already
returned text_done(error) on the lifecycle error, so the route spoke the
filler "One moment, still working on that." and the answer was lost.

openclaw does not mark the error event as retryable (`livenessState` is
"blocked" on every errored attempt). Its own gateway holds a lifecycle error
for AGENT_LIFECYCLE_ERROR_RETRY_GRACE_MS (15 s) because a retry or fallback
reuses the runId; the retry re-emits `lifecycle start`.

The frames below copy the measured shapes (ids shortened).
"""

import asyncio
import json
import queue

import pytest

import services.gateways.openclaw as gw
from services.gateways.openclaw import EventDispatcher, GatewayConnection

ALIAS = 'main'
MAIN_SK = 'agent:mike-qwen-trial:main'
RUN = '10e3c880-146f-4bc9-b6b5-110e2db01286'
BUSY_503 = '503 Quinn or another caller is using the card (voice first)'
ANSWER = 'Opening the desktop page for you now. [CANVAS:desktop]'


def _agent(stream, data, run_id=RUN, sk=MAIN_SK, seq=None):
    payload = {'runId': run_id, 'stream': stream, 'data': data, 'sessionKey': sk}
    if seq is not None:
        payload['seq'] = seq
    return {'type': 'event', 'event': 'agent', 'payload': payload}


def _start(seq=None):
    return _agent('lifecycle', {'phase': 'start', 'startedAt': 1795066146927}, seq=seq)


def _error(msg, seq=None):
    return _agent('lifecycle', {'phase': 'error', 'error': msg,
                                'livenessState': 'blocked',
                                'endedAt': 1795066149917}, seq=seq)


def _end():
    return _agent('lifecycle', {'phase': 'end', 'endedAt': 1795066156485})


def _deltas(text, step=12):
    return [_agent('assistant', {'text': text[:i + step], 'delta': text[i:i + step]})
            for i in range(0, len(text), step)]


def _chat_final(text=None):
    payload = {'runId': RUN, 'sessionKey': MAIN_SK, 'state': 'final'}
    if text:
        payload['message'] = {'role': 'assistant', 'content': text}
    return {'type': 'event', 'event': 'chat', 'payload': payload}


def _chat_error(msg):
    return {'type': 'event', 'event': 'chat',
            'payload': {'runId': RUN, 'sessionKey': MAIN_SK,
                        'state': 'error', 'errorMessage': msg}}


class FakeWS:
    def __init__(self):
        self.sent = []
        self.close_code = None

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def _run_turn(script, timeout=20):
    """Drive the REAL dispatcher + _stream_events. `script` items are gateway
    frames, or a float = pause (seconds) before the next frame. Returns
    (events put on the HTTP event_queue, frames OVU sent to the gateway)."""
    conn = GatewayConnection()
    d = conn._dispatcher = EventDispatcher()
    conn._ws = FakeWS()
    conn._connected = True
    conn._default_agent_id = 'mike-qwen-trial'
    out = queue.Queue()

    async def go():
        sub = d.subscribe('chat-user', ALIAS)
        await d._route({'type': 'res', 'id': 'chat-chat-user', 'ok': True,
                        'payload': {'runId': RUN, 'status': 'started'}})

        async def feed():
            for item in script:
                if isinstance(item, (int, float)):
                    await asyncio.sleep(item)
                    continue
                await d._route(item)
                await asyncio.sleep(0)

        feeder = asyncio.ensure_future(feed())
        await conn._stream_events(sub, out, ALIAS, [], _cleanup_ids=[])
        feeder.cancel()

    asyncio.run(asyncio.wait_for(go(), timeout=timeout))
    events = []
    while not out.empty():
        events.append(out.get())
    return events, conn._ws.sent


def _done(events):
    done = [e for e in events if e.get('type') == 'text_done']
    assert len(done) == 1, f'expected exactly one text_done, got {done}'
    return done[0]


def _spoken(events):
    return ''.join(e['text'] for e in events if e.get('type') == 'delta')


class TestRetriedErrorDeliversAnswer:

    def test_503_busy_then_retry_delivers_the_answer(self):
        # The measured 05:29Z sequence: error -> openclaw retry -> answer.
        script = [_start(seq=1), _error(BUSY_503, seq=2), 0.3, _start()]
        script += _deltas(ANSWER)
        script += [_end(), _chat_final(ANSWER)]
        events, _ = _run_turn(script)
        done = _done(events)
        assert done.get('error') is None
        assert done['response'] == ANSWER
        assert _spoken(events) == ANSWER

    def test_retry_that_streams_without_a_new_start_still_delivers(self):
        script = [_start(), _error('overloaded_error: 529 Overloaded'), 0.1]
        script += _deltas(ANSWER)
        script += [_end()]
        events, _ = _run_turn(script)
        assert _done(events)['response'] == ANSWER


class TestTerminalErrorStillEndsTheTurn:

    def test_non_transient_error_ends_the_turn_at_once(self):
        # e.g. a bad key: no hold, same text_done(error) as before the fix,
        # even if something arrives afterwards.
        script = [_start(), _error('401 Incorrect API key provided'), 0.5,
                  _start()] + _deltas(ANSWER) + [_end()]
        events, _ = _run_turn(script)
        done = _done(events)
        assert done['response'] is None
        assert done['error'] == '401 Incorrect API key provided'
        assert _spoken(events) == ''

    def test_transient_error_with_no_retry_ends_with_the_error(self):
        # openclaw gave up (run final, no retry start): the turn ends with
        # the original error, the final's error-payload text is not spoken,
        # and OVU never aborts or re-sends.
        script = [_start(), _error(BUSY_503), 0.1,
                  _chat_final('Request failed after repeated internal retries.')]
        events, sent = _run_turn(script)
        done = _done(events)
        assert done['response'] is None
        assert done['error'] == BUSY_503
        assert not [f for f in sent if f.get('method') in ('chat.abort', 'chat.send')]

    def test_transient_error_with_chat_error_ends_with_an_error(self):
        script = [_start(), _error(BUSY_503), 0.1, _chat_error(BUSY_503)]
        events, _ = _run_turn(script)
        done = _done(events)
        assert done['response'] is None
        assert done['error'] == BUSY_503

    def test_transient_error_then_silence_ends_after_the_grace(self, monkeypatch):
        monkeypatch.setattr(gw, 'LIFECYCLE_ERROR_RETRY_GRACE_S', 0.4, raising=False)
        events, _ = _run_turn([_start(), _error(BUSY_503)], timeout=10)
        done = _done(events)
        assert done['response'] is None
        assert done['error'] == BUSY_503

    def test_auth_error_with_a_transient_word_ends_the_turn_at_once(self):
        # A terminal marker (403) outranks "temporarily unavailable": no hold,
        # the original error ends the turn even if a start follows.
        msg = '403 Forbidden: model temporarily unavailable in your region'
        script = [_start(), _error(msg), 0.3, _start()] + _deltas(ANSWER) + [_end()]
        events, _ = _run_turn(script)
        done = _done(events)
        assert done['response'] is None
        assert done['error'] == msg
        assert _spoken(events) == ''

    def test_error_after_partial_speech_ends_the_turn_at_once(self):
        # A retry restarts the text; holding after speech would repeat it.
        script = [_start()] + _deltas('Opening the') + [_error(BUSY_503), 0.3,
                  _start()] + _deltas(ANSWER) + [_end()]
        events, _ = _run_turn(script)
        done = _done(events)
        assert done['response'] is None
        assert done['error'] == BUSY_503


class TestRetryableClassifier:

    @pytest.mark.parametrize('msg', [
        BUSY_503, '429 Too Many Requests', 'API rate limit reached',
        'The AI service is temporarily overloaded. Please try again in a moment.',
        'LLM request timed out.', '502 Bad Gateway', 'ECONNRESET',
        'overloaded_error: 529 Overloaded', 'Gateway Timeout (504)',
        'upstream load balancer returned 503',
    ])
    def test_transient(self, msg):
        assert gw.is_retryable_lifecycle_error({'error': msg}) is True

    @pytest.mark.parametrize('msg', [
        '401 Incorrect API key provided', 'Context overflow: prompt too large',
        'billing: insufficient credits', '', None,
        # terminal marker + transient word: the terminal marker wins
        '403 Forbidden: model temporarily unavailable in your region',
        '401 Unauthorized (upstream returned 503)',
        '429 You exceeded your current quota, please check your plan and billing details',
        'Request blocked by content policy (code 500)',
        'Your account is temporarily suspended for policy violation',
        'Authentication unavailable. The request to the managed identity endpoint timed out.',
        'permission denied: service unavailable for this key',
        # a 5xx-looking number that is not a status
        'invalid_request_error: max_tokens must be <= 512',
    ])
    def test_terminal(self, msg):
        assert gw.is_retryable_lifecycle_error({'error': msg}) is False

    def test_never_holds_after_speech(self):
        assert gw.is_retryable_lifecycle_error({'error': BUSY_503}, 'Opening') is False
