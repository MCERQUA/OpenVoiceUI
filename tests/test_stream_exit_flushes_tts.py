"""
Tests for routes.conversation.drain_pending_tts_on_exit (issue #487).

Measured live 2026-09-10 (tenant danielle): TEXT_DONE received with
_tts_pending=4, four Groq TTS completions logged, tts_ok=1 metrics, then
"### STREAM EXIT with 4 unprocessed events: ['tts_ready']*4" — the generator
returned while TTS audio was still sitting unconsumed.

Root cause (routes/conversation.py stream_response()): the main event loop
has two `break` paths — STREAM HARD TIMEOUT (queue.Empty branch, elapsed >
_STREAM_HARD_TIMEOUT) and the gateway 'error' branch — that exit WITHOUT ever
running the text_done flush loop that waits on `_tts_pending`. Each TTS
chunk's background thread (_fire_tts()._run()) always pings event_queue with
a payload-free {'type': 'tts_ready'} wake event when it finishes, regardless
of whether anything consumes it — that ping is what the old warning was
counting, but it carries no audio, so re-yielding it to the client would do
nothing. The actual payload lives in `_tts_pending`'s (done_event, result)
tuples, which is what drain_pending_tts_on_exit() now drains, bounded by a
wall-clock cap, before the generator returns.

These tests drive drain_pending_tts_on_exit() directly with a fake
_tts_pending list of (threading.Event, dict) tuples, mirroring exactly what
stream_response() builds via _fire_tts().
"""

import threading
import time

from routes.conversation import drain_pending_tts_on_exit


def _make_ready_chunk(audio='YXVkaW8=', error=None, delay=0.0):
    """A (done_event, result) pair that becomes ready after `delay` seconds,
    mirroring _fire_tts()'s background thread completing late (arriving
    AFTER TEXT_DONE was logged, as in the live incident)."""
    done = threading.Event()
    result = {'audio': None, 'error': None}

    def _finish():
        if delay:
            time.sleep(delay)
        result['audio'] = audio
        result['error'] = error
        done.set()

    if delay:
        threading.Thread(target=_finish, daemon=True).start()
    else:
        _finish()
    return done, result


def _audio_event_fn(audio_b64, chunk_idx):
    return f'audio:{chunk_idx}:{audio_b64}'


def _error_event_fn(err_str):
    return f'error:{err_str}'


class TestQueuedChunksArrivingAfterTextDone:
    """The exact shape of the live incident: 4 tts_ready-bound chunks finish
    shortly after TEXT_DONE, on a path that skipped the normal flush."""

    def test_four_late_chunks_all_flushed_before_return(self):
        pending = [
            _make_ready_chunk(audio=f'chunk{i}'.encode().hex(), delay=0.05)
            for i in range(4)
        ]
        events, dropped = drain_pending_tts_on_exit(
            pending, chunks_sent=0,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=5,
        )
        assert dropped == 0
        assert len(events) == 4
        # Order preserved, chunk indices offset by chunks_sent.
        for i in range(4):
            assert events[i] == f'audio:{i}:{f"chunk{i}".encode().hex()}'

    def test_error_chunk_yields_error_event_not_dropped(self):
        pending = [_make_ready_chunk(error='provider_500')]
        events, dropped = drain_pending_tts_on_exit(
            pending, chunks_sent=0,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=5,
        )
        assert dropped == 0
        assert events == ['error:provider_500']

    def test_chunks_sent_offsets_indices(self):
        pending = [_make_ready_chunk(audio='a'), _make_ready_chunk(audio='b')]
        events, dropped = drain_pending_tts_on_exit(
            pending, chunks_sent=3,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=5,
        )
        assert dropped == 0
        assert events == ['audio:3:a', 'audio:4:b']


class TestNeverCompletingProviderHitsTheCap:
    """A provider that hangs past the cap must be counted DROPPED — never
    hang the generator forever, and never claim a success that didn't
    happen."""

    def test_cap_fires_and_reports_dropped_count(self):
        never_done = threading.Event()  # never .set()
        pending = [(never_done, {'audio': None, 'error': None})]
        start = time.time()
        events, dropped = drain_pending_tts_on_exit(
            pending, chunks_sent=0,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=0.2,
        )
        elapsed = time.time() - start
        assert dropped == 1
        assert events == []
        # Bounded by the cap, not left to hang indefinitely.
        assert elapsed < 1.0

    def test_cap_is_a_shared_budget_not_per_chunk(self):
        # First chunk never completes and eats the whole 0.1s cap. The
        # second chunk finishes at 0.5s — well within its OWN fresh 0.1s cap
        # would have been impossible either way, but the point is it must
        # not get a fresh 0.1s of its own: the cap is one wall-clock
        # deadline shared across all remaining chunks, so by the time we
        # reach chunk 2 there is ~0s left and it is dropped too, and the
        # whole call returns in ~0.1s rather than waiting out chunk 2's 0.5s.
        never_done = threading.Event()
        late_done, late_res = _make_ready_chunk(audio='late-but-ready', delay=0.5)
        pending = [
            (never_done, {'audio': None, 'error': None}),
            (late_done, late_res),
        ]
        start = time.time()
        events, dropped = drain_pending_tts_on_exit(
            pending, chunks_sent=0,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=0.1,
        )
        elapsed = time.time() - start
        assert dropped == 2  # first hangs the whole budget; second gets ~0s left
        assert events == []
        assert elapsed < 0.3  # proves the budget is shared, not 0.1 + 0.5

    def test_mixed_one_ready_one_hung_partial_flush_partial_drop(self):
        ready_done, ready_res = _make_ready_chunk(audio='ok')
        never_done = threading.Event()
        pending = [
            (ready_done, ready_res),
            (never_done, {'audio': None, 'error': None}),
        ]
        events, dropped = drain_pending_tts_on_exit(
            pending, chunks_sent=0,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=0.3,
        )
        assert dropped == 1
        assert events == ['audio:0:ok']


class TestNormalPathUnchanged:
    """No pending TTS at exit (the common case: text_done already flushed
    and reset _tts_pending to []) must be a true no-op — no delay, no
    events, no dropped count, no log noise."""

    def test_empty_pending_is_a_noop(self):
        events, dropped = drain_pending_tts_on_exit(
            [], chunks_sent=0,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=15,
        )
        assert events == []
        assert dropped == 0

    def test_empty_pending_returns_immediately(self):
        start = time.time()
        drain_pending_tts_on_exit(
            [], chunks_sent=0,
            audio_event_fn=_audio_event_fn, error_event_fn=_error_event_fn,
            cap_seconds=15,
        )
        assert time.time() - start < 0.05
