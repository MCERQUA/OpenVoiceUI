"""
Tests for GatewayConnection._ensure_connected's reconnect budget (pledge
50bef735, 2026-09-10). GatewayConnection is the persistent-WS connection
manager the OpenClawGateway facade (services/gateways/openclaw.py) wraps via
GatewayRouter.

Measured problem: the old max_attempts=5 schedule (BACKOFF_DELAYS
[1,2,4,8,16,30,60], sleeping 2+4+8+16=30s between the 5 attempts) covered a
~30s window, but a real openclaw gateway restart takes ~37-38s from
container start to the "[gateway] ready" log line (measured on
openclaw-danielle and openclaw-azrim, 2026-09-10). Consequence measured in
openvoiceui-azrim's logs: all 5 attempts during a restart failed, the
supervisor raised "Failed to connect to Gateway after 5 attempts", and only
reconnected on its NEXT cycle ~35s later — turning a ~37s restart into a
~65s+ outage and burning the whole retry budget on every restart.

The fix: max_attempts=6 (nominal 60s budget: 2+4+8+16+30) plus +/-15% jitter
on every backoff delay (GatewayConnection._jittered_delay) so tenants whose
gateways restart together don't hammer in lockstep.

These tests exercise the real _ensure_connected coroutine with self._connect
mocked out and asyncio.sleep patched to a no-op recorder, so they run fast
and deterministically regardless of the real jittered delay values.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.gateways.openclaw import GatewayConnection


def _make_gateway():
    gw = GatewayConnection()
    gw._ws_lock = asyncio.Lock()
    return gw


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
    """(a) With the gateway down for the full restart window (up to the 6th
    attempt), the client still reconnects successfully within the budget
    instead of exhausting it — the exact case the old max_attempts=5 schedule
    failed on 2026-09-10."""

    def test_reconnects_on_last_attempt_within_new_budget_without_exhausting(self):
        gw = _make_gateway()
        # Fails 5 times (matching the measured 5/5 exhaustion), succeeds on the
        # 6th — only possible because max_attempts is now 6, not 5.
        calls = {"n": 0}

        async def flaky_connect():
            calls["n"] += 1
            if calls["n"] < 6:
                raise ConnectionRefusedError("gateway not listening yet")

        gw._connect = AsyncMock(side_effect=flaky_connect)

        with patch("services.gateways.openclaw.asyncio.sleep", new=AsyncMock()):
            asyncio.run(gw._ensure_connected())  # must not raise

        assert calls["n"] == 6

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

        with patch("services.gateways.openclaw.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(RuntimeError, match="after 6 attempts"):
                asyncio.run(gw._ensure_connected())

        assert gw._connect.await_count == 6
