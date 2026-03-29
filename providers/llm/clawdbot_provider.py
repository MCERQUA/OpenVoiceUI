"""
Clawdbot Gateway WebSocket LLM provider.

Ref: future-dev-plans/02-PROVIDER-SYSTEMS.md (ClawdbotProvider section)
Routes messages through the Clawdbot Gateway WebSocket for full agent context.
"""

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional

from providers.llm.base import LLMError, LLMProvider, LLMResponse
from providers.registry import ProviderType, registry


class ClawdbotProvider(LLMProvider):
    """Clawdbot Gateway WebSocket provider."""

    def __init__(self, config: Dict[str, Any] = None) -> None:
        super().__init__(config)
        self.gateway_url = (
            self._config.get("gateway_url")
            or os.getenv("CLAWDBOT_GATEWAY_URL", "ws://127.0.0.1:18791")
        )
        self.auth_token = (
            self._config.get("auth_token")
            or os.getenv("CLAWDBOT_AUTH_TOKEN", "")
        )
        self.default_agent = self._config.get("default_agent", "main")
        self.default_model = "glm-5-turbo"  # Gateway uses Z.AI/GLM

    def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        try:
            import websocket  # type: ignore
        except ImportError:
            raise LLMError("clawdbot", "websocket-client library not installed")

        # Extract last user message
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        start = time.time()
        try:
            ws = websocket.create_connection(self.gateway_url, timeout=10)
            try:
                # Handshake
                ws.send(json.dumps({"type": "connect.challenge", "token": self.auth_token}))
                challenge = json.loads(ws.recv())
                ws.send(json.dumps({"type": "connect", "response": challenge.get("challenge", "")}))
                ws.recv()  # hello frame

                # Send message
                agent = kwargs.get("agent", self.default_agent)
                ws.send(json.dumps({"type": "chat.send", "content": user_message, "agent": agent}))

                # Collect response
                content = ""
                while True:
                    raw = json.loads(ws.recv())
                    if raw.get("type") == "chat.response":
                        content += raw.get("content", "")
                    elif raw.get("type") in ("chat.done", "chat.final"):
                        if not content:
                            content = raw.get("content", "")
                        break
            finally:
                ws.close()
        except Exception as exc:
            raise LLMError("clawdbot", f"Gateway error: {exc}") from exc

        latency_ms = (time.time() - start) * 1000
        return LLMResponse(
            content=content,
            model=self.default_model,
            provider="clawdbot",
            usage={},
            latency_ms=latency_ms,
        )

    def generate_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Iterator[str]:
        # Full streaming would require a persistent connection (PG-T2)
        response = self.generate(messages, system_prompt, model, **kwargs)
        yield response.content

    def is_available(self) -> bool:
        return bool(self.auth_token)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["name"] = self._config.get("name", "Clawdbot Gateway")
        info["gateway_url"] = self.gateway_url
        return info


# Auto-register when this module is imported
registry.register(ProviderType.LLM, "clawdbot", ClawdbotProvider)
