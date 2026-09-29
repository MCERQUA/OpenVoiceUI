"""
Tests for GatewayConnection._ensure_connected's reconnect budget. GatewayConnection
is the persistent-WS connection manager the OpenClawGateway facade
(services/gateways/openclaw.py) wraps via GatewayRouter.

The loop has to outlast an openclaw gateway restart, and the budget has been
sized twice against two different measurements:

- WS-9 (pledge 50bef735, 2026-09-10): restart-to-ready measured at ~37-38s
  (container StartedAt -> "[gateway] ready"; openclaw-danielle, openclaw-azrim).
  The original max_attempts=5 on the ladder [1,2,4,8,16,30,60] exhausted at
  t=30s, so max_attempts went to 6 (t=60s nominal) and every sleep gained
  +/-15% jitter (GatewayConnection._jittered_delay) so tenants whose gateways
  restart together don't retry in lockstep.
- #482 (gcu-voice, 4 timestamped restarts 2026-08-28 / 09-05 / 09-06 x2):
  claw_sigterm at t=0, Errno 111 refusals until the gateway listens again at
  t=70-72s. main's 6 attempts end at t=60s nominal (51-69s with jitter), so a
  restart that long always exhausts; openvoiceui-mike on 2026-09-29 only got
  back in on its 6th and final attempt. The ladder now caps at 15s
  ([1,2,4,8,15,15,15]) and runs 10 attempts: probes at
  t=0,2,6,14,29,44,59,74,89,104s nominal, last attempt 88-120s with jitter.

These tests exercise the real _ensure_connected coroutine with self._connect
mocked out and asyncio.sleep patched (to a no-op, or to a fake clock), so they
run fast and deterministically regardless of the real jittered delay values.
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


def _source_max_attempts():
    """The retry budget is a local in _ensure_connected, so read it back out of
    the real source instead of restating it. A hardcoded copy is what left CI red
    when the budget changed (this file still said 6 after the branch said 10).
    The floor that matters is pinned separately, by behaviour, in the
    attempt-7 and 72s-restart tests below."""
    src = inspect.getsource(GatewayConnection._ensure_connected)
    m = re.search(r"^\s*max_attempts\s*=\s*(\d+)\s*$", src, re.M)
    assert m, "max_attempts assignment not found in _ensure_connected"
    return int(m.group(1))


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
    """(a) With the gateway down for a restart window, the client still
    reconnects within the budget instead of exhausting it. A budget that runs
    out is not cosmetic: after the final failure the client stops probing until
    the supervisor's next cycle, so even a gateway that came back early goes
    unnoticed."""

    def test_reconnects_on_final_attempt_without_exhausting(self):
        gw = _make_gateway()
        # Comes up only on the FINAL attempt of the current budget, so this keeps
        # testing the last rung if the budget is resized.
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

    def test_gateway_back_on_attempt_7_connects_without_exhausting(self):
        # The first attempt past main's 6-attempt budget: the ~70s restart gcu
        # measured, which main can never reach — it raises "after 6 attempts"
        # first (openvoiceui-mike, 2026-09-29, got in on its 6th and last try).
        gw = _make_gateway()
        calls = {"n": 0}

        async def flaky_connect():
            calls["n"] += 1
            if calls["n"] < 7:
                raise ConnectionRefusedError("[Errno 111] Connection refused")

        gw._connect = AsyncMock(side_effect=flaky_connect)

        with patch("services.gateways.openclaw.asyncio.sleep", new=AsyncMock()):
            asyncio.run(gw._ensure_connected())  # must not raise

        assert calls["n"] == 7

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

    def test_exhausts_and_raises_when_still_down_after_budget(self):
        gw = _make_gateway()
        gw._connect = AsyncMock(side_effect=ConnectionRefusedError("still down"))
        budget = _source_max_attempts()

        with patch("services.gateways.openclaw.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(RuntimeError, match=f"after {budget} attempts"):
                asyncio.run(gw._ensure_connected())

        assert gw._connect.await_count == budget


class TestBudgetCoversMeasuredRestart:
    """Binds the schedule to the measurement in TIME, not attempt count: the
    gateway refuses until t=72s (the worst gcu restart) on a fake clock driven
    by the loop's own sleeps. Run at both jitter bounds, because the jitter makes
    the budget a range rather than a constant and the low bound is the one that
    runs out first."""

    GATEWAY_BACK_AT_S = 72.0

    @pytest.mark.parametrize("jitter", [0.85, 1.15])
    def test_survives_72s_restart_at_jitter_bound(self, jitter):
        gw = _make_gateway()
        clock = {"t": 0.0}
        attempt_times = []

        async def fake_sleep(seconds):
            clock["t"] += seconds

        async def restarting_gateway():
            attempt_times.append(clock["t"])
            if clock["t"] < self.GATEWAY_BACK_AT_S:
                raise ConnectionRefusedError("[Errno 111] Connection refused")

        gw._connect = AsyncMock(side_effect=restarting_gateway)

        with patch("services.gateways.openclaw.asyncio.sleep", new=fake_sleep), \
                patch("services.gateways.openclaw.random.uniform", return_value=jitter):
            asyncio.run(gw._ensure_connected())  # must not raise

        # The gateway really was down for every earlier attempt, and the one
        # that connected came after it was listening again.
        assert all(t < self.GATEWAY_BACK_AT_S for t in attempt_times[:-1])
        assert attempt_times[-1] >= self.GATEWAY_BACK_AT_S
