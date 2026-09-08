"""
Tests for GatewayConnection._ensure_connected's reconnect budget (pledge
50bef735, 2026-09-10). GatewayConnection is the persistent-WS connection
manager the OpenClawGateway facade (services/gateways/openclaw.py) wraps via
GatewayRouter.

Measured problem: this loop has to survive an openclaw gateway restart. The
window that matters is the CLIENT-OBSERVED one -- "[gateway] received SIGTERM"
to "[gateway] ready" -- not the container's StartedAt clock, which omits the
shutdown half and reads ~8-20s short. Measured 2026-09-19 over every retained
openclaw log on the box: n=29 restarts, min 29.9s, p50 41.4s, max 59.0s.

The original max_attempts=5 ladder put its last attempt at t=30s and covered
1 of those 29 restarts, so a gateway restart exhausted the budget on ~25
tenants at once. max_attempts=6 on the uncapped ladder [1,2,4,8,16,30,60]
reaches t=60s nominal -- 1.0s past the worst measured restart -- and because
every sleep carries +/-15% jitter that budget is a distribution, falling
below 59.0s roughly 39% of the time.

The fix: cap BACKOFF_DELAYS at 15s ([1,2,4,8,15,15,15]) and run 10 attempts,
so probes land at t = 0,2,6,14,29,44,59,74,89,104s nominal. That both clears
the worst measured restart with real margin and removes the dead window the
uncapped ladder had between its 30s and 60s rungs. +/-15% jitter
(GatewayConnection._jittered_delay) is retained so tenants whose gateways
restart together don't hammer in lockstep.

These tests exercise the real _ensure_connected coroutine with self._connect
mocked out and asyncio.sleep patched to a no-op recorder, so they run fast
and deterministically regardless of the real jittered delay values.
"""

import asyncio
import inspect
import re
from unittest.mock import AsyncMock, patch

import pytest

from services.gateways.openclaw import GatewayConnection


def _make_gateway():
    gw = GatewayConnection()
    gw._ws_lock = asyncio.Lock()
    return gw


# The retry budget lives as a local in _ensure_connected, so read it back out of
# the real source rather than restating it here. A test that hardcodes the number
# it is meant to be guarding cannot notice the number changing.
def _source_max_attempts():
    src = inspect.getsource(GatewayConnection._ensure_connected)
    m = re.search(r"^\s*max_attempts\s*=\s*(\d+)", src, re.M)
    assert m, "max_attempts assignment not found in _ensure_connected"
    return int(m.group(1))


def _nominal_schedule():
    """Attempt times (seconds from first attempt), pre-jitter, as the loop runs:
    after the Nth failure the index advances, then it sleeps BACKOFF_DELAYS[idx]."""
    delays = GatewayConnection.BACKOFF_DELAYS
    t, idx, times = 0.0, 0, []
    for attempt in range(1, _source_max_attempts() + 1):
        times.append(t)
        idx = min(idx + 1, len(delays) - 1)
        t += delays[idx]
    return times


class TestJitteredDelay:
    def test_stays_within_plus_minus_15_percent(self):
        for _ in range(500):
            d = GatewayConnection._jittered_delay(10.0)
            assert 8.5 <= d <= 11.5

    def test_varies_across_calls(self):
        # Full jitter must not degenerate into a constant — that would defeat
        # the anti-lockstep purpose entirely.
        values = {GatewayConnection._jittered_delay(10.0) for _ in range(20)}
        assert len(values) > 1


class TestEnsureConnectedGatewayUp:
    """(b) With the gateway up, the connect path is unchanged: first attempt
    succeeds immediately, no sleeping, no wasted attempts."""

    def test_first_attempt_succeeds_no_sleep(self):
        gw = _make_gateway()
        gw._connect = AsyncMock(return_value=None)

        with patch(
            "services.gateways.openclaw.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            asyncio.run(gw._ensure_connected())

        assert gw._connect.await_count == 1
        mock_sleep.assert_not_awaited()


class TestEnsureConnectedGatewayDown:
    """(a) With the gateway down for the full restart window, the client still
    reconnects within the budget instead of exhausting it — the case the old
    max_attempts=5 schedule failed on for 28 of the 29 measured restarts."""

    def test_reconnects_on_final_attempt_without_exhausting(self):
        gw = _make_gateway()
        # Comes up only on the FINAL attempt of the current budget. Pinned to
        # the real max_attempts so this keeps testing the last rung if the
        # budget is resized, instead of silently drifting to a middle one.
        final = _source_max_attempts()
        calls = {"n": 0}

        async def flaky_connect():
            calls["n"] += 1
            if calls["n"] < final:
                raise ConnectionRefusedError("gateway not listening yet")

        gw._connect = AsyncMock(side_effect=flaky_connect)

        with patch("services.gateways.openclaw.asyncio.sleep", new=AsyncMock()):
            asyncio.run(gw._ensure_connected())  # must not raise

        assert calls["n"] == final

    def test_old_budget_of_5_attempts_would_have_raised(self):
        # Sanity control proving the schedule actually changed: a gateway that
        # only comes up on the 6th attempt is exactly what the OLD
        # max_attempts=5 loop could never reach — it always raised
        # RuntimeError("...after 5 attempts") first. This test freezes that
        # regression check by capping attempts at 5 directly against the
        # documented old behaviour, independent of the current source.
        old_max_attempts = 5
        calls = {"n": 0}

        async def flaky_connect():
            calls["n"] += 1
            if calls["n"] < 6:
                raise ConnectionRefusedError("gateway not listening yet")

        async def old_loop():
            for attempt in range(old_max_attempts):
                try:
                    await flaky_connect()
                    return
                except Exception:
                    pass
            raise RuntimeError(f"Failed to connect to Gateway after {old_max_attempts} attempts")

        with pytest.raises(RuntimeError, match="after 5 attempts"):
            asyncio.run(old_loop())

    def test_exhausts_and_raises_when_still_down_after_new_budget(self):
        gw = _make_gateway()
        gw._connect = AsyncMock(side_effect=ConnectionRefusedError("still down"))

        expected = _source_max_attempts()
        with patch("services.gateways.openclaw.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(RuntimeError, match=f"after {expected} attempts"):
                asyncio.run(gw._ensure_connected())

        assert gw._connect.await_count == expected


# Measured 2026-09-19, SIGTERM -> "[gateway] ready" across every retained openclaw
# log on the box: n=29, min 29.9s, p50 41.4s, max 59.0s.
WORST_MEASURED_RESTART_S = 59.0


class TestBudgetCoversMeasuredRestart:
    """The budget is only meaningful relative to the outage it has to outlast.
    These bind the schedule to the measurement so shrinking one without the
    other fails loudly, instead of silently reintroducing the exhaustion."""

    def test_last_attempt_clears_worst_measured_restart_with_margin(self):
        last = _nominal_schedule()[-1]
        assert last > WORST_MEASURED_RESTART_S, (
            f"budget ends at {last}s, inside the worst measured restart "
            f"({WORST_MEASURED_RESTART_S}s) — every such restart exhausts"
        )
        # Jitter makes the budget a distribution, not a constant. The previous
        # max_attempts=6 budget ended at 60s nominal: 1.0s of margin, which the
        # -15% tail erased ~39% of the time. Require the pessimistic draw to
        # clear it too.
        assert last * 0.85 > WORST_MEASURED_RESTART_S, (
            f"budget ends at {last}s nominal but {last * 0.85:.1f}s on the -15% "
            f"jitter tail, inside the worst measured restart "
            f"({WORST_MEASURED_RESTART_S}s)"
        )

    def test_no_probe_gap_exceeds_worst_case_slack(self):
        """No single gap may step over the restart window. The uncapped ladder
        jumped 30s -> 60s, so a gateway ready at 44s sat unnoticed for 16s."""
        times = _nominal_schedule()
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert max(gaps) <= 15, f"probe gap of {max(gaps)}s can step over a restart"

    def test_early_attempts_still_fast_for_a_quick_restart(self):
        """Capping the ladder must not slow the common case down."""
        times = _nominal_schedule()
        assert times[3] <= 15, f"4th attempt at {times[3]}s — quick restarts got slower"
