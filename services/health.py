"""
Health probe endpoints for liveness and readiness checks.
ADR-006: Separate liveness + readiness probes (Kubernetes-compatible).

Liveness  (/health/live)  — is the process running?
Readiness (/health/ready) — can it serve requests? (Gateway + TTS loaded)
"""

import os
import socket
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse


@dataclass
class CheckResult:
    healthy: bool
    message: str
    details: Optional[Dict] = field(default=None)


class HealthChecker:
    """Liveness and readiness health checks."""

    def __init__(self):
        self.start_time = time.time()

    def liveness(self) -> CheckResult:
        """Liveness probe — always 200 if the process is alive."""
        return CheckResult(
            healthy=True,
            message="Process is running",
            details={"uptime_seconds": round(time.time() - self.start_time, 1)},
        )

    def readiness(self) -> CheckResult:
        """Readiness probe — 200 only when Gateway configured + TTS loaded."""
        checks: Dict[str, Dict] = {}
        all_ok = True

        # --- Gateway check ---
        try:
            gateway_ok = _check_gateway()
            checks["gateway"] = gateway_ok.__dict__
            if not gateway_ok.healthy:
                all_ok = False
        except Exception as exc:
            checks["gateway"] = {"healthy": False, "message": str(exc)}
            all_ok = False

        # --- TTS providers check ---
        try:
            tts_ok = _check_tts()
            checks["tts"] = tts_ok.__dict__
            if not tts_ok.healthy:
                all_ok = False
        except Exception as exc:
            checks["tts"] = {"healthy": False, "message": str(exc)}
            all_ok = False

        return CheckResult(
            healthy=all_ok,
            message="All checks passed" if all_ok else "One or more checks failed",
            details=checks,
        )


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

def _check_gateway() -> CheckResult:
    """Check gateway REACHABILITY without making a third-party HTTP call.

    Readiness must be cheap: no WebSocket handshake, no vendor API. We (1)
    confirm the gateway is configured, then (2) prefer the persistent
    GatewayConnection's live `_connected` flag if one exists, else fall back to
    a cheap local TCP connect to the gateway host:port. This makes readiness go
    503 when the socket is actually down, not merely when env vars are missing.
    """
    token = os.getenv("CLAWDBOT_AUTH_TOKEN")
    if not token:
        return CheckResult(healthy=False, message="CLAWDBOT_AUTH_TOKEN not set")
    gateway_url = os.getenv("CLAWDBOT_GATEWAY_URL", "")
    if not gateway_url:
        return CheckResult(healthy=False, message="CLAWDBOT_GATEWAY_URL not set")

    # (1) Prefer the persistent connection's live `_connected` flag if a
    # GatewayRouter/connection is already established (no handshake, no I/O).
    try:
        from services.gateways import openclaw as _oc
        router = getattr(_oc, "_router", None) or getattr(_oc, "gateway_router", None)
        conns = getattr(router, "_connections", None) if router is not None else None
        if isinstance(conns, dict) and conns:
            if any(getattr(c, "_connected", False) for c in conns.values()):
                return CheckResult(healthy=True, message="Gateway connected (live)")
        # No live connection object → fall through to a cheap socket probe rather
        # than trusting env-var presence alone.
    except Exception:
        pass

    # (2) Cheap local TCP reachability probe (no handshake, no vendor call).
    try:
        parsed = urlparse(gateway_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme in ("wss", "https") else 18789)
        with socket.create_connection((host, port), timeout=1.5):
            return CheckResult(healthy=True, message=f"Gateway reachable ({host}:{port})")
    except Exception as exc:
        return CheckResult(healthy=False, message=f"Gateway unreachable: {exc}")


# Cached TTS provider-id list (module-level, TTL). Readiness must not
# instantiate providers (which triggers Supertonic health GET / ElevenLabs
# voices fetch — third-party calls per probe). We only need the registered
# ids, which are static config, so cache them.
_TTS_LIST_CACHE: Dict[str, object] = {"ids": None, "fetched_at": 0.0}
_TTS_LIST_TTL = 300  # 5 minutes


def _get_tts_provider_ids() -> List[str]:
    """Return registered TTS provider ids from the registry WITHOUT instantiating.

    Cached for _TTS_LIST_TTL. Prefers a non-instantiating registry accessor
    (`available_providers()` / `_PROVIDERS` keys) so no provider __init__ runs.
    """
    now = time.time()
    cached = _TTS_LIST_CACHE.get("ids")
    if cached is not None and (now - float(_TTS_LIST_CACHE.get("fetched_at", 0))) < _TTS_LIST_TTL:
        return cached  # type: ignore[return-value]

    ids: List[str] = []
    try:
        import tts_providers as _tp
        # Prefer the raw registry keys — no instantiation, no network.
        registry = getattr(_tp, "_PROVIDERS", None)
        if isinstance(registry, dict) and registry:
            ids = list(registry.keys())
        else:
            fn = getattr(_tp, "available_providers", None)
            if callable(fn):
                ids = list(fn())
    except Exception:
        ids = []

    _TTS_LIST_CACHE["ids"] = ids
    _TTS_LIST_CACHE["fetched_at"] = now
    return ids


def _check_tts() -> CheckResult:
    """Check that at least one TTS provider is REGISTERED (no instantiation)."""
    try:
        ids = _get_tts_provider_ids()
        if not ids:
            return CheckResult(healthy=False, message="No TTS providers available")
        return CheckResult(
            healthy=True,
            message=f"{len(ids)} TTS provider(s) registered",
            details={"providers": ids},
        )
    except ImportError:
        return CheckResult(healthy=False, message="tts_providers module not importable")


# Module-level singleton — imported by server.py
health_checker = HealthChecker()
