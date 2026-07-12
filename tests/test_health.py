"""
Tests for health probe logic (ADR-006, P1-T6).

We test the HealthChecker class directly (unit tests) rather than spinning
up the full server.py monolith.  The /health/live and /health/ready endpoints
delegate entirely to HealthChecker, so these tests cover their behaviour.
"""

import os
import pytest


class TestLiveness:
    """Liveness probe — should always return healthy=True."""

    def test_liveness_is_healthy(self, health_checker):
        result = health_checker.liveness()
        assert result.healthy is True

    def test_liveness_has_message(self, health_checker):
        result = health_checker.liveness()
        assert isinstance(result.message, str)
        assert len(result.message) > 0

    def test_liveness_has_uptime(self, health_checker):
        result = health_checker.liveness()
        assert result.details is not None
        assert "uptime_seconds" in result.details
        assert result.details["uptime_seconds"] >= 0


class TestReadiness:
    """Readiness probe — healthy only when Gateway env vars + TTS are set."""

    def test_readiness_returns_check_result(self, health_checker):
        """readiness() must return a CheckResult regardless of env state."""
        result = health_checker.readiness()
        assert hasattr(result, "healthy")
        assert hasattr(result, "message")
        assert hasattr(result, "details")

    def test_readiness_unhealthy_without_env(self, health_checker, monkeypatch):
        """Without Gateway env vars the probe must report unhealthy."""
        monkeypatch.delenv("CLAWDBOT_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAWDBOT_GATEWAY_URL", raising=False)
        result = health_checker.readiness()
        assert result.healthy is False

    def test_readiness_details_contains_gateway_key(self, health_checker):
        """details dict must include a 'gateway' sub-check."""
        result = health_checker.readiness()
        assert result.details is not None
        assert "gateway" in result.details

    def test_readiness_details_contains_tts_key(self, health_checker):
        """details dict must include a 'tts' sub-check."""
        result = health_checker.readiness()
        assert result.details is not None
        assert "tts" in result.details

    def test_readiness_healthy_when_gateway_reachable(self, health_checker, monkeypatch):
        """WO-0.3: readiness now probes REACHABILITY (a live socket), not just
        env-var presence. With a real listener bound at the gateway URL the
        gateway sub-check is healthy."""
        import socket as _socket
        listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        monkeypatch.setenv("CLAWDBOT_AUTH_TOKEN", "test-token")
        monkeypatch.setenv("CLAWDBOT_GATEWAY_URL", f"ws://127.0.0.1:{port}")
        try:
            result = health_checker.readiness()
        finally:
            listener.close()
        # Gateway sub-check should pass because the socket is reachable; TTS may
        # or may not load in CI — only assert the gateway sub-check.
        assert result.details["gateway"]["healthy"] is True

    def test_readiness_unhealthy_when_gateway_unreachable(self, health_checker, monkeypatch):
        """WO-0.3: env vars set but nothing listening → gateway is NOT ready
        (previously this returned healthy on env-var presence alone)."""
        import socket as _socket
        # Bind then immediately close to get a definitely-closed port.
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()
        monkeypatch.setenv("CLAWDBOT_AUTH_TOKEN", "test-token")
        monkeypatch.setenv("CLAWDBOT_GATEWAY_URL", f"ws://127.0.0.1:{dead_port}")
        result = health_checker.readiness()
        assert result.details["gateway"]["healthy"] is False
