"""
Tests for services.warmup_retry.retry_until_success — the gateway boot
warm-up retry loop (server.py's _warm_gateway_connection). Docker restarts
containers without compose `depends_on` ordering, so openvoiceui can come
up before its openclaw gateway is listening; this loop is what keeps the
boot warm-up from giving up after the underlying connect's fixed 5
attempts.
"""

from services.warmup_retry import retry_until_success


def test_retries_until_success_then_stops():
    calls = {"connect": 0, "sleep": 0}
    logs = []

    def connect_fn():
        calls["connect"] += 1
        if calls["connect"] < 3:
            raise RuntimeError("Failed to connect to Gateway after 5 attempts")
        # third call succeeds

    def sleep_fn(seconds):
        calls["sleep"] += 1

    attempt = retry_until_success(connect_fn, sleep_fn, logs.append)

    assert attempt == 3
    assert calls["connect"] == 3
    assert calls["sleep"] == 2
    assert len(logs) == 2
    assert "attempt 1 failed" in logs[0]
    assert "attempt 2 failed" in logs[1]


def test_succeeds_immediately_without_sleeping():
    calls = {"connect": 0, "sleep": 0}

    def connect_fn():
        calls["connect"] += 1

    def sleep_fn(seconds):
        calls["sleep"] += 1

    attempt = retry_until_success(connect_fn, sleep_fn, lambda msg: None)

    assert attempt == 1
    assert calls["connect"] == 1
    assert calls["sleep"] == 0


def test_uses_given_delay_seconds():
    delays = []

    def connect_fn():
        if len(delays) < 1:
            raise RuntimeError("boom")

    retry_until_success(connect_fn, delays.append, lambda msg: None, delay_seconds=5)

    assert delays == [5]
