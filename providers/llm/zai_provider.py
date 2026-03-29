"""
Z.AI GLM-4 LLM provider.

Ref: future-dev-plans/02-PROVIDER-SYSTEMS.md (ZAIProvider section)
IMPORTANT: This is the primary LLM backend for clawdbot (ADR — NEVER switch clawdbot to Anthropic).
"""

import os
import time
from typing import Any, Dict, Iterator, List, Optional

from providers.llm.base import LLMError, LLMProvider, LLMResponse
from providers.registry import ProviderType, registry


class ZAIProvider(LLMProvider):
    """Z.AI GLM-4 provider via REST API."""

    API_URL = "https://api.zukijourney.com/v1/chat/completions"

    def __init__(self, config: Dict[str, Any] = None) -> None:
        super().__init__(config)
        self.api_key = self._resolve_api_key()
        self.default_model = self._config.get("default_model", "glm-5-turbo")

    def _resolve_api_key(self) -> str:
        key = self._config.get("api_key", "")
        # Skip unresolved placeholder
        if key and not key.startswith("${"):
            return key
        return os.getenv("ZAI_API_KEY", "")

    def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> LLMResponse:
        try:
            import requests  # type: ignore
        except ImportError:
            raise LLMError("zai", "requests library not installed")

        model = model or self.default_model
        max_tokens = kwargs.get("max_tokens", 512)
        timeout = kwargs.get("timeout", 30)

        # Temperature and penalty settings for natural conversation
        temperature = kwargs.get("temperature", 0.7)
        frequency_penalty = kwargs.get("frequency_penalty", 0.5)
        presence_penalty = kwargs.get("presence_penalty", 0.3)

        full_messages: List[Dict[str, str]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        start = time.time()
        try:
            resp = requests.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": full_messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "frequency_penalty": frequency_penalty,
                    "presence_penalty": presence_penalty,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise LLMError("zai", f"API request failed: {exc}") from exc

        data = resp.json()
        latency_ms = (time.time() - start) * 1000

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=model,
            provider="zai",
            usage=data.get("usage", {}),
            latency_ms=latency_ms,
            finish_reason=data["choices"][0].get("finish_reason", "stop"),
            raw_response=data,
        )

    def generate_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Iterator[str]:
        # Z.AI REST API does not support streaming — fall back to non-streaming
        response = self.generate(messages, system_prompt, model, **kwargs)
        yield response.content

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["name"] = self._config.get("name", "Z.AI GLM")
        return info


# Auto-register when this module is imported
registry.register(ProviderType.LLM, "zai", ZAIProvider)
