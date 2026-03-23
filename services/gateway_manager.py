"""
GatewayManager — registry and router for all gateway implementations.

Responsibilities:
  - Registers built-in gateways (OpenClaw) on startup
  - Discovers and loads plugin gateways from plugins/*/plugin.json
  - Routes stream_to_queue() calls to the correct gateway by ID
  - Provides ask() for inter-gateway delegation (one agent calling another)
  - Exposes list_gateways() for health endpoint and admin UI

Usage:
    from services.gateway_manager import gateway_manager

    # Standard voice request (routes to gateway from active profile):
    gateway_manager.stream_to_queue(
        event_queue, message, session_key, captured_actions,
        gateway_id='openclaw'   # or whatever the active profile specifies
    )

    # Inter-gateway delegation (one agent calling another):
    response = gateway_manager.ask('claude-api', 'Summarise this text: ...', session_key)

Profile integration:
    Profiles select a gateway via adapter_config.gateway_id.
    If not set, defaults to 'openclaw'. Example profile snippet:

        "adapter_config": {
            "gateway_id": "claude-api",
            "sessionKey": "claude-1"
        }

Plugin discovery:
    On startup, the manager scans plugins/*/plugin.json for entries where
    "provides": "gateway". See plugins/README.md for the contributor guide.
"""

import importlib.util
import json
import logging
import os
import queue
import sys
from pathlib import Path
from typing import Optional

from services.gateways.base import GatewayBase

logger = logging.getLogger(__name__)

# Root of the project (two levels up from this file)
_PROJECT_ROOT = Path(__file__).parent.parent
_PLUGINS_DIR = _PROJECT_ROOT / 'plugins'


class GatewayManager:
    """Registry and router for all gateway implementations."""

    def __init__(self):
        self._gateways: dict[str, GatewayBase] = {}

    # ------------------------------------------------------------------ #
    # Registration                                                         #
    # ------------------------------------------------------------------ #

    def register(self, gateway: GatewayBase) -> None:
        """Register a gateway instance. Overwrites any existing entry with the same ID."""
        if not isinstance(gateway, GatewayBase):
            raise TypeError(f"Expected GatewayBase subclass, got {type(gateway)}")
        gid = gateway.gateway_id
        if not gateway.is_configured():
            logger.warning(
                f"Gateway '{gid}' registered but not configured "
                f"(missing env vars). Requests to it will fail."
            )
        self._gateways[gid] = gateway
        status = "configured" if gateway.is_configured() else "NOT configured"
        logger.info(f"GatewayManager: registered '{gid}' ({status})")

    # ------------------------------------------------------------------ #
    # Routing                                                              #
    # ------------------------------------------------------------------ #

    def get(self, gateway_id: Optional[str]) -> Optional[GatewayBase]:
        """Return the gateway for the given ID, or None if not found."""
        gid = gateway_id or 'openclaw'
        return self._gateways.get(gid)

    def stream_to_queue(
        self,
        event_queue: queue.Queue,
        message: str,
        session_key: str,
        captured_actions: Optional[list] = None,
        gateway_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Route a voice request to the named gateway and stream events into
        event_queue. Blocking — returns when the response is complete.

        Falls back to 'openclaw' if gateway_id is None or not registered.
        """
        gid = gateway_id or 'openclaw'
        gw = self._gateways.get(gid)

        if gw is None:
            # Fallback to openclaw if the requested gateway isn't loaded
            logger.warning(
                f"Gateway '{gid}' not registered — falling back to 'openclaw'"
            )
            gw = self._gateways.get('openclaw')

        if gw is None:
            err = f"No gateway available (requested: '{gid}', openclaw fallback also missing)"
            logger.error(err)
            if captured_actions is None:
                captured_actions = []
            event_queue.put({'type': 'error', 'error': err})
            return

        if not gw.is_configured():
            err = f"Gateway '{gw.gateway_id}' is not configured (check env vars)"
            logger.error(err)
            if captured_actions is None:
                captured_actions = []
            event_queue.put({'type': 'error', 'error': err})
            return

        gw.stream_to_queue(event_queue, message, session_key, captured_actions, **kwargs)

    def ask(self, gateway_id: str, message: str, session_key: str) -> str:
        """
        Inter-gateway delegation: ask a gateway a question and return the
        full response as a string. Synchronous.

        Used when one agent wants to delegate work to another. Example:
            response = gateway_manager.ask('claude-api', 'Summarise: ...', 'sub-1')

        Returns empty string on error (errors are logged).
        """
        gw = self._gateways.get(gateway_id)
        if gw is None:
            logger.error(f"ask(): gateway '{gateway_id}' not registered")
            return ''
        if not gw.is_configured():
            logger.error(f"ask(): gateway '{gateway_id}' not configured")
            return ''

        q: queue.Queue = queue.Queue()
        captured: list = []
        gw.stream_to_queue(q, message, session_key, captured)

        # Drain queue for text_done or error
        while True:
            try:
                event = q.get(timeout=330)
            except queue.Empty:
                logger.warning(f"ask('{gateway_id}'): timeout waiting for response")
                return ''
            if event.get('type') == 'text_done':
                return event.get('response') or ''
            if event.get('type') == 'error':
                logger.error(f"ask('{gateway_id}'): gateway error: {event.get('error')}")
                return ''

    # ------------------------------------------------------------------ #
    # Steer (inject message into active run)                               #
    # ------------------------------------------------------------------ #

    def send_steer(
        self,
        message: str,
        session_key: str,
        gateway_id: Optional[str] = None,
    ) -> bool:
        """
        Send a steer message to the named gateway (fire-and-forget).

        Used when the user speaks while the agent is silently working
        (tools / sub-agents).  Instead of aborting the active run,
        this injects the user's message at the next tool boundary via
        OpenClaw's messages.queue.mode=steer.

        The active /api/conversation streaming response continues
        receiving the steered output — no new streaming connection
        is created.

        Returns True if the message was delivered, False otherwise.
        """
        gid = gateway_id or 'openclaw'
        gw = self._gateways.get(gid)
        if gw is None:
            gw = self._gateways.get('openclaw')
        if gw is None or not gw.is_configured():
            return False
        if not hasattr(gw, 'send_steer'):
            logger.warning(f"Gateway '{gw.gateway_id}' does not support steer")
            return False
        return gw.send_steer(message, session_key)

    # ------------------------------------------------------------------ #
    # Health / status                                                      #
    # ------------------------------------------------------------------ #

    def list_gateways(self) -> list[dict]:
        """Return status of all registered gateways (for health endpoint / admin UI)."""
        return [
            {
                'id': gw.gateway_id,
                'configured': gw.is_configured(),
                'healthy': gw.is_healthy(),
                'persistent': gw.persistent,
            }
            for gw in self._gateways.values()
        ]

    def is_configured(self) -> bool:
        """True if at least one gateway is configured (backwards compat for health check)."""
        return any(gw.is_configured() for gw in self._gateways.values())

    # ------------------------------------------------------------------ #
    # Startup loading                                                      #
    # ------------------------------------------------------------------ #

    def _load_builtin_gateways(self) -> None:
        """Register the built-in OpenClaw gateway if configured."""
        from services.gateways.openclaw import OpenClawGateway
        self.register(OpenClawGateway())

    def _load_plugins(self) -> None:
        """
        Scan plugins/*/plugin.json for gateway plugins and register them.

        Plugin structure:
            plugins/
              my-gateway/
                plugin.json    {"id": "...", "provides": "gateway", "gateway_class": "Gateway"}
                gateway.py     class Gateway(GatewayBase): ...

        Plugins are skipped (with a warning) if:
          - plugin.json is missing or malformed
          - provides != "gateway"
          - gateway.py is missing
          - required env vars (requires_env) are not set
          - the gateway class fails to instantiate
        """
        if not _PLUGINS_DIR.exists():
            return

        loaded = []
        for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / 'plugin.json'
            if not manifest_path.exists():
                continue

            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception as e:
                logger.warning(f"plugins/{plugin_dir.name}: invalid plugin.json — {e}")
                continue

            if manifest.get('provides') != 'gateway':
                continue  # not a gateway plugin, skip silently

            plugin_id = manifest.get('id', plugin_dir.name)

            # Check required env vars
            required_env = manifest.get('requires_env', [])
            missing = [v for v in required_env if not os.getenv(v)]
            if missing:
                logger.warning(
                    f"plugins/{plugin_dir.name}: skipping '{plugin_id}' — "
                    f"missing env vars: {', '.join(missing)}"
                )
                continue

            # Load gateway.py
            gateway_file = plugin_dir / 'gateway.py'
            if not gateway_file.exists():
                logger.warning(f"plugins/{plugin_dir.name}: no gateway.py found, skipping")
                continue

            try:
                spec = importlib.util.spec_from_file_location(
                    f"plugins.{plugin_dir.name}.gateway",
                    gateway_file
                )
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
            except Exception as e:
                logger.warning(f"plugins/{plugin_dir.name}: failed to import gateway.py — {e}")
                continue

            # Get the gateway class
            class_name = manifest.get('gateway_class', 'Gateway')
            cls = getattr(module, class_name, None)
            if cls is None:
                logger.warning(
                    f"plugins/{plugin_dir.name}: class '{class_name}' not found in gateway.py"
                )
                continue
            if not (isinstance(cls, type) and issubclass(cls, GatewayBase)):
                logger.warning(
                    f"plugins/{plugin_dir.name}: '{class_name}' must subclass GatewayBase"
                )
                continue

            # Instantiate and register
            try:
                instance = cls()
                self.register(instance)
                loaded.append(plugin_id)
            except Exception as e:
                logger.warning(f"plugins/{plugin_dir.name}: failed to instantiate {class_name} — {e}")
                continue

        if loaded:
            logger.info(f"GatewayManager: loaded plugin gateways: {', '.join(loaded)}")


# ---------------------------------------------------------------------------
# Singleton — used everywhere via: from services.gateway_manager import gateway_manager
# ---------------------------------------------------------------------------

gateway_manager = GatewayManager()
gateway_manager._load_builtin_gateways()
gateway_manager._load_plugins()

ids = list(gateway_manager._gateways.keys())
logger.info(f"GatewayManager ready — {len(ids)} gateway(s): {', '.join(ids)}")
