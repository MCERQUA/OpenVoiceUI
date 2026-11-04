"""
Interim spoken status on DELEGATED voice turns (pledge 25dfb03c).

Incident 2026-09-16 (test-dev): a research ask was handed to a sub-agent and
the user heard nothing for ~9 minutes. Two gaps in the route made that
possible, and the end-to-end tests below reproduce both:

  1. The silence-breaker was evaluated ONLY on gateway `heartbeat` events.
     The gateway emits a heartbeat only after 5 s with no other event, and a
     working sub-agent streams tool events onto the parent's queue
     continuously, so the check never ran.
  2. After the agent's own acknowledgement (`text_interim`) the first status
     was pushed out to the 90 s repeat interval, i.e. well past a minute.

The status may only say what the stream showed (NO FAKE REPLIES, Mike
2026-10-20): task counts, sub-agent step counts, the latest step. Nothing new
since the last status = say nothing; never talk over the final answer.
"""

import json
import threading
import time

import pytest

import routes.conversation as conv
import services.interim_status as ist
from services.interim_status import DelegatedStatus

SUB_SK = 'agent:main:subagent:abc123'
FILLER = ('still working', 'One moment', 'one moment', 'Still here',
          'Still on it', 'almost there', 'brain glitched', 'Here you go')


# ── unit: DelegatedStatus ────────────────────────────────────────────

def _spawn(s, n=1):
    for _ in range(n):
        s.note_action({'type': 'tool', 'phase': 'start', 'name': 'sessions_spawn',
                       'sessionKey': 'agent:main:main'})
        s.note_action({'type': 'subagent', 'phase': 'spawning', 'tool': 'sessions_spawn'})
        s.note_action({'type': 'subagent', 'phase': 'start', 'sessionKey': SUB_SK})


def _step(s, name):
    s.note_action({'type': 'tool', 'phase': 'start', 'name': name, 'sessionKey': SUB_SK})


class TestDelegatedStatusUnit:

    def test_not_delegated_never_speaks(self):
        s = DelegatedStatus(first_after_s=40, repeat_after_s=60)
        s.note_action({'type': 'tool', 'phase': 'start', 'name': 'exec',
                       'sessionKey': 'agent:main:main'})
        assert s.due(now=1000, last_audio_time=0) is None

    def test_first_status_before_threshold_is_silent_then_due(self):
        s = DelegatedStatus(first_after_s=40, repeat_after_s=60)
        _spawn(s)
        assert s.due(now=39.9, last_audio_time=0) is None
        text = s.due(now=40, last_audio_time=0)
        assert text == ('That is running in the background. '
                        'You will hear the result when it is done.')

    def test_default_first_threshold_is_under_a_minute(self):
        assert ist.FIRST_AFTER_S <= 45

    def test_spawn_counted_once_not_twice(self):
        s = DelegatedStatus()
        _spawn(s)
        assert s.tasks == 1

    def test_reports_real_steps_and_latest_step(self):
        s = DelegatedStatus(first_after_s=1, repeat_after_s=1)
        _spawn(s)
        _step(s, 'web_search')
        _step(s, 'web_fetch')
        _step(s, 'web_search')
        text = s.due(now=5, last_audio_time=0)
        assert 'Three steps so far, most recently searching the web.' in text

    def test_unknown_step_name_is_not_guessed(self):
        s = DelegatedStatus(first_after_s=1)
        _spawn(s)
        _step(s, 'frobnicate_widget')
        text = s.due(now=5, last_audio_time=0)
        assert 'One step so far.' in text
        assert 'frobnicate' not in text

    def test_nothing_new_means_no_repeat(self):
        s = DelegatedStatus(first_after_s=1, repeat_after_s=1)
        _spawn(s)
        assert s.due(now=5, last_audio_time=0)
        assert s.due(now=500, last_audio_time=5) is None   # no new fact
        _step(s, 'web_fetch')
        assert 'most recently reading a webpage' in s.due(now=600, last_audio_time=5)

    def test_repeat_waits_for_repeat_interval(self):
        s = DelegatedStatus(first_after_s=1, repeat_after_s=60)
        _spawn(s)
        assert s.due(now=5, last_audio_time=0)
        _step(s, 'exec')
        assert s.due(now=30, last_audio_time=5) is None
        assert s.due(now=65, last_audio_time=5)

    def test_cap_per_turn(self):
        s = DelegatedStatus(first_after_s=1, repeat_after_s=1, max_per_turn=2)
        _spawn(s)
        fired = 0
        for i in range(10):
            _step(s, 'exec')
            if s.due(now=10 * (i + 1), last_audio_time=0):
                fired += 1
        assert fired == 2

    def test_all_finished_says_composing(self):
        s = DelegatedStatus(first_after_s=1)
        _spawn(s)
        s.note_action({'type': 'subagent', 'phase': 'end', 'sessionKey': SUB_SK})
        assert s.due(now=5, last_audio_time=0) == (
            'The background work has finished. I am putting the answer together now.')

    def test_multiple_tasks_counted(self):
        s = DelegatedStatus(first_after_s=1)
        _spawn(s, n=3)
        s.note_action({'type': 'subagent', 'phase': 'end', 'sessionKey': SUB_SK})
        assert s.due(now=5, last_audio_time=0).startswith(
            'Two of three background tasks are still running.')

    def test_never_talks_over_the_answer(self):
        s = DelegatedStatus(first_after_s=1)
        _spawn(s)
        s.note_answer_text()
        assert s.due(now=50, last_audio_time=0) is None

    def test_interim_marks_delegated_and_reopens(self):
        s = DelegatedStatus(first_after_s=1)
        s.note_answer_text()          # the ack streaming
        s.note_interim()              # ack spoken, sub-agents pending
        assert s.delegated
        assert s.due(now=5, last_audio_time=0)

    def test_tts_disabled_never_speaks(self):
        s = DelegatedStatus(first_after_s=1)
        _spawn(s)
        assert s.due(now=50, last_audio_time=0, tts_enabled=False) is None

    def test_no_canned_filler_phrases(self):
        s = DelegatedStatus(first_after_s=1, repeat_after_s=1, max_per_turn=10)
        _spawn(s, n=2)
        texts = [s.due(now=5, last_audio_time=0)]
        for name in ('web_search', 'exec', 'mystery'):
            _step(s, name)
            texts.append(s.due(now=100, last_audio_time=0))
        s.note_action({'type': 'subagent', 'phase': 'end', 'sessionKey': SUB_SK})
        s.note_action({'type': 'subagent', 'phase': 'end', 'sessionKey': SUB_SK})
        texts.append(s.due(now=200, last_audio_time=0))
        assert all(texts)
        for t in texts:
            for f in FILLER:
                assert f not in t


# ── end-to-end: the real streaming route ────────────────────────────

class _PacedGateway:
    """gateway_manager stand-in. stream_to_queue runs on the route's gateway
    thread; it puts scripted events with real delays between them."""

    def __init__(self, script):
        self.script = script  # [(delay_before_s, event), ...]
        self.calls = []

    def is_configured(self):
        return True

    def get(self, gateway_id=None):
        return None

    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs):
        self.calls.append(message)
        for delay, evt in self.script:
            if delay:
                time.sleep(delay)
            event_queue.put(evt)


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
def spoken(monkeypatch):
    """Record every TTS call with its time; no DB writes, no metrics."""
    rec = []
    lock = threading.Lock()

    def _tts(text, **kw):
        with lock:
            rec.append((time.time(), text))
        return 'QUJD'

    monkeypatch.setattr(conv, '_tts_generate_b64', _tts)
    monkeypatch.setattr(conv, 'save_conversation_turn', lambda **kw: None)
    monkeypatch.setattr(conv, 'log_conversation', lambda *a, **kw: None)
    monkeypatch.setattr(conv, 'log_metrics', lambda m: None)
    # Scale the delegated thresholds to test time: 40 s -> 0.4 s.
    monkeypatch.setattr(ist, 'FIRST_AFTER_S', 0.4)
    monkeypatch.setattr(ist, 'REPEAT_AFTER_S', 0.6)
    return rec


def _run(client, monkeypatch, script):
    gw = _PacedGateway(script)
    monkeypatch.setattr(conv, 'gateway_manager', gw)
    t0 = time.time()
    resp = client.post('/api/conversation?stream=1',
                       json={'message': 'research GPU rental prices for me',
                             'tts_provider': 'groq'})
    assert resp.status_code == 200
    events = [json.loads(l) for l in resp.get_data(as_text=True).splitlines() if l.strip()]
    return t0, events


def _spawn_events():
    return [
        (0, {'type': 'handshake', 'ms': 3}),
        (0.02, {'type': 'action', 'action': {'type': 'tool', 'phase': 'start',
                                             'name': 'sessions_spawn',
                                             'sessionKey': 'agent:main:main'}}),
        (0, {'type': 'action', 'action': {'type': 'subagent', 'phase': 'spawning',
                                          'tool': 'sessions_spawn'}}),
        (0, {'type': 'action', 'action': {'type': 'subagent', 'phase': 'start',
                                          'sessionKey': SUB_SK}}),
    ]


def _sub_step(name):
    return {'type': 'action', 'action': {'type': 'tool', 'phase': 'start',
                                         'name': name, 'sessionKey': SUB_SK}}


ANSWER = 'GPU rentals run about two dollars an hour on the big clouds.'


def _status_calls(spoken_rec):
    return [(t, x) for t, x in spoken_rec if 'background' in x]


class TestDelegatedTurnEndToEnd:

    def test_silent_delegation_with_busy_subagent_speaks_status(
            self, app_client, monkeypatch, spoken):
        """The incident shape: no acknowledgement, the sub-agent streams tool
        events continuously (so no gateway heartbeat ever comes)."""
        script = _spawn_events()
        for i in range(12):  # ~1.2 s of continuous sub-agent activity
            script.append((0.1, _sub_step('web_search' if i % 2 else 'web_fetch')))
        script.append((0.05, {'type': 'text_done', 'response': ANSWER, 'actions': []}))
        t0, events = _run(app_client, monkeypatch, script)

        status = _status_calls(spoken)
        assert status, f'no interim status spoken; TTS calls were {spoken!r}'
        # Spoken before the final answer, and inside the (scaled) first window.
        answer_t = next(t for t, x in spoken if 'rentals run about' in x)
        assert status[0][0] < answer_t
        assert status[0][0] - t0 < 0.4 + 0.5
        # It reports what the stream actually showed.
        assert 'so far, most recently' in status[0][1]
        # The final answer is spoken exactly once, never replaced.
        assert sum(1 for _, x in spoken if 'rentals run about' in x) == 1
        done = [e for e in events if e['type'] == 'text_done']
        assert done and done[-1]['response'] == ANSWER

    def test_status_after_spoken_ack_comes_well_before_a_minute(
            self, app_client, monkeypatch, spoken):
        """Agent acknowledges (text_interim), then the gateway idles with
        heartbeats while the sub-agent works quietly. The old code waited the
        90 s repeat interval after the ack."""
        script = _spawn_events() + [
            (0.02, {'type': 'text_interim', 'response': 'On it, researching that now.',
                    'actions': []}),
        ]
        for i in range(8):
            script.append((0.1, {'type': 'heartbeat', 'elapsed': i}))
        script.append((0.05, {'type': 'text_done', 'response': ANSWER, 'actions': []}))
        _, events = _run(app_client, monkeypatch, script)

        texts = [x for _, x in spoken]
        assert texts[0].startswith('On it')
        status = _status_calls(spoken)
        assert len(status) == 1, texts
        assert status[0][1] == ('That is running in the background. '
                                'You will hear the result when it is done.')
        assert texts.index(status[0][1]) < next(
            i for i, x in enumerate(texts) if 'rentals run about' in x)

    def test_control_quick_delegation_says_nothing_extra(
            self, app_client, monkeypatch, spoken):
        """Negative control: the result arrives before the threshold."""
        script = _spawn_events() + [
            (0.05, _sub_step('web_search')),
            (0.05, {'type': 'text_done', 'response': ANSWER, 'actions': []}),
        ]
        _run(app_client, monkeypatch, script)
        assert _status_calls(spoken) == []
        assert [x.lower() for _, x in spoken] == [ANSWER.lower()]

    def test_control_solo_tool_turn_gets_no_delegated_status(
            self, app_client, monkeypatch, spoken):
        """Negative control: a long NON-delegated tool run is left to the
        legacy path (45 s), unchanged by this feature."""
        script = [(0, {'type': 'handshake', 'ms': 3}),
                  (0.02, {'type': 'action', 'action': {'type': 'tool', 'phase': 'start',
                                                       'name': 'exec',
                                                       'sessionKey': 'agent:main:main'}})]
        for i in range(10):
            script.append((0.1, {'type': 'heartbeat', 'elapsed': i}))
        script.append((0.05, {'type': 'text_done', 'response': ANSWER, 'actions': []}))
        _run(app_client, monkeypatch, script)
        assert _status_calls(spoken) == []
        assert [x.lower() for _, x in spoken] == [ANSWER.lower()]

    def test_no_status_once_the_answer_is_streaming(
            self, app_client, monkeypatch, spoken):
        """The final answer has begun streaming after the sub-agent finished;
        a slow tail must not get a status spoken over it."""
        script = _spawn_events() + [
            (0.05, {'type': 'action', 'action': {'type': 'subagent', 'phase': 'end',
                                                 'sessionKey': SUB_SK}}),
            (0.05, {'type': 'delta', 'text': 'GPU rentals run about '}),
        ]
        for i in range(8):
            script.append((0.1, {'type': 'heartbeat', 'elapsed': i}))
        script.append((0.05, {'type': 'text_done', 'response': ANSWER, 'actions': []}))
        _run(app_client, monkeypatch, script)
        assert _status_calls(spoken) == []

    def test_result_after_ack_is_spoken_even_without_deltas(
            self, app_client, monkeypatch, spoken):
        """The agent acknowledged, delegated, and the result came back as a
        whole reply (no deltas). The old whole-reply fallback keyed on
        `_chunks_sent == 0`, which the acknowledgement had already made
        false, so the RESULT was never spoken."""
        monkeypatch.setattr(ist, 'FIRST_AFTER_S', 60)  # no status in this test
        script = _spawn_events() + [
            (0.02, {'type': 'text_interim', 'response': 'On it, researching that now.',
                    'actions': []}),
            (0.1, {'type': 'heartbeat', 'elapsed': 1}),
            (0.05, {'type': 'text_done', 'response': ANSWER, 'actions': []}),
        ]
        _run(app_client, monkeypatch, script)
        texts = [x.lower() for _, x in spoken]
        assert texts == ['on it, researching that now.', ANSWER.lower()]

    def test_control_streamed_answer_not_spoken_twice(
            self, app_client, monkeypatch, spoken):
        """Negative control for the double-audio guard: an answer that already
        streamed as deltas is not re-spoken whole at text_done."""
        script = _spawn_events() + [
            (0.02, {'type': 'text_interim', 'response': 'On it, researching that now.',
                    'actions': []}),
            (0.05, {'type': 'delta', 'text': ANSWER}),
            (0.05, {'type': 'text_done', 'response': ANSWER, 'actions': []}),
        ]
        _run(app_client, monkeypatch, script)
        texts = [x.lower() for _, x in spoken]
        assert sum(1 for x in texts if 'rentals run about' in x) == 1
