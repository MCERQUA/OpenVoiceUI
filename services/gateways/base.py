"""
GatewayBase — abstract interface all gateway implementations must satisfy.

A gateway is the backend LLM connection: it receives a user message and
streams response events into a queue that conversation.py consumes.

Built-in implementation: services/gateways/openclaw.py
Plugin implementations:  plugins/<id>/gateway.py  (see plugins/README.md)

Implementing a gateway
----------------------
1. Subclass GatewayBase
2. Set gateway_id (unique string slug, e.g. "claude-api")
3. Set persistent = True if you maintain a live connection (WS, gRPC, etc.)
   Set persistent = False if you connect on each request (REST APIs)
4. Implement is_configured(), stream_to_queue()
5. Optionally override is_healthy() for a richer health check

Event protocol
--------------
stream_to_queue() must put dicts onto event_queue in this order:

  {'type': 'handshake', 'ms': int}           optional — connection latency
  {'type': 'delta', 'text': str}             one or more streaming tokens
  {'type': 'action', 'action': dict}         tool calls / lifecycle events
  {'type': 'text_done',                      final — MUST always be sent
   'response': str | None,
   'actions': list}
  {'type': 'error', 'error': str}            on failure instead of text_done
"""

import queue
from typing import Optional


class GatewayBase:
    """Abstract base class for all OpenVoiceUI gateway implementations."""

    # Unique slug used as the routing key in gateway_manager and profiles.
    # Set this on every subclass. Example: "openclaw", "claude-api", "langchain"
    gateway_id: str = "unnamed"

    # True  → maintain a persistent connection (WS, long-lived thread, etc.)
    #         gateway_manager will call is_healthy() on startup to warm it up.
    # False → connect per-request (REST APIs, stateless clients)
    #         zero idle cost; no background thread required.
    persistent: bool = False

    # Capability descriptor (WO-2.1). Machine-readable set of features this
    # framework supports, so the admin panel can render capability chips and
    # decide which controls to show. Known tokens (extend as needed):
    #   'streaming'     — token-by-token streaming responses
    #   'steer'         — inject a message into an in-flight run
    #   'sessions'      — per-session conversational memory / history
    #   'tool-events'   — emits structured tool/action lifecycle events
    #   'config-rpc'    — supports live config read/patch (get_config_schema+configure applied hot)
    #   'reset'         — supports reset_session()
    #   'delegation'    — used for inter-gateway delegation (not a primary brain)
    # Set on each subclass. Default: streaming only (the one required behaviour).
    capabilities: set = frozenset({"streaming"})

    # ------------------------------------------------------------------ #
    # Required — subclasses must implement these                          #
    # ------------------------------------------------------------------ #

    def is_configured(self) -> bool:
        """
        Return True if all required env vars / config are present.
        Called on startup. If False, gateway is registered but marked inactive
        and a warning is logged. Requests routed to it will return an error.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement is_configured()"
        )

    def stream_to_queue(
        self,
        event_queue: queue.Queue,
        message: str,
        session_key: str,
        captured_actions: Optional[list] = None,
        **kwargs,
    ) -> None:
        """
        Send message to the LLM backend and stream response events into
        event_queue. This method is blocking — it returns when the full
        response is done (or on error).

        Called from a background thread by conversation.py.

        Args:
            event_queue:      thread-safe queue.Queue for yielded events
            message:          user message string (already context-enriched)
            session_key:      session identifier for conversational memory
            captured_actions: list to append tool/lifecycle events to
            **kwargs:         gateway-specific extras (e.g. agent_id for openclaw)
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement stream_to_queue()"
        )

    # ------------------------------------------------------------------ #
    # Optional — override for richer behaviour                            #
    # ------------------------------------------------------------------ #

    def is_healthy(self) -> bool:
        """
        Quick synchronous health check. No I/O — just inspect local state.
        Default: same as is_configured().
        Override to check live connection state, last-error timestamp, etc.

        NOTE: this returns a plain bool and is kept that way for backwards
        compatibility (gateway_manager.list_gateways() and the readiness probe
        consume it). For a health check that ALSO reports latency, use
        check_health() below (WO-2.1).
        """
        return self.is_configured()

    # ------------------------------------------------------------------ #
    # Capability / config / richer-health contracts (WO-2.1)             #
    # ------------------------------------------------------------------ #

    def check_health(self) -> tuple:
        """Richer health probe: return (healthy: bool, latency_ms: float|None).

        Kept separate from is_healthy() (which must stay a bare bool for its
        existing callers). The Service Catalog / Agent Framework tab call this
        for the health ring + latency. Default: no I/O — mirror is_healthy()
        with no latency. Override to measure real reachability + round-trip.
        Implementations MUST be cheap (socket/local ping), never a vendor API
        call on the catalog path.
        """
        return (self.is_healthy(), None)

    def get_config_schema(self) -> dict:
        """Return a machine-readable config-field schema for this framework.

        Shape (Hermes install_config style — the panel renders it generically):
            {"fields": [
                {"id": "HERMES_HOST", "type": "text", "label": "...",
                 "required": True},
                {"id": "HERMES_API_KEY", "type": "password", "label": "...",
                 "credential": "hermes_api_key"},
            ]}

        Field types: text | password | select | toggle | number.
        A field may carry "credential": <vault-cred-id> to route its write
        through the vault instead of a plain config value.

        Default: an empty, display-only schema (no editable fields). Override
        to expose configurable settings.
        """
        return {"fields": [], "editable": False}

    def configure(self, partial: dict) -> dict:
        """Apply a partial configuration change to this framework.

        Args:
            partial: subset of fields from get_config_schema() to change.

        Returns a status dict:
            {"status": "applied",       "detail": "..."}   — live, no restart
            {"status": "needs_restart", "detail": "..."}   — written, restart X
            {"status": "error",         "detail": "..."}   — rejected/failed

        Default: not supported (display-only frameworks).
        """
        return {"status": "error", "detail": "configure() not supported by this gateway"}

    def restart_scope(self) -> str:
        """What a configure() change restarts: 'none' | 'ovui' | 'container:<name>'.

        Default 'none' (hot-applied or display-only). Override when a change
        requires bouncing a sibling container.
        """
        return "none"

    def shutdown(self) -> None:
        """
        Called when the server shuts down. Override to close connections,
        cancel background threads, etc. Default: no-op.
        """

    def __repr__(self) -> str:
        status = "configured" if self.is_configured() else "not configured"
        kind = "persistent" if self.persistent else "on-demand"
        return f"<{self.__class__.__name__} id={self.gateway_id!r} {kind} {status}>"
