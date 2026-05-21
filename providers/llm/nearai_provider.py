"""
NEAR AI Cloud LLM provider.

OpenAI-compatible chat completions over NEAR AI Cloud TEE inference.
"""

import os
import time
from typing import Any, Dict, Iterator, List, Optional

from providers.llm.base import LLMError, LLMProvider, LLMResponse
from providers.registry import ProviderType, registry


class NearAIProvider(LLMProvider):
    """NEAR AI Cloud provider via OpenAI-compatible REST API."""

    DEFAULT_BASE_URL = "https://cloud-api.near.ai/v1"
    DEFAULT_MODEL = "zai-org/GLM-5.1-FP8"

    def __init__(self, config: Dict[str, Any] = None) -> None:
        super().__init__(config)
        self.api_key = self._resolve_api_key()
        self.base_url = self._resolve_base_url()
        self.default_model = self._config.get("default_model", self.DEFAULT_MODEL)

    def _resolve_api_key(self) -> str:
        key = self._config.get("api_key", "")
        if key and not key.startswith("${"):
            return key
        return os.getenv("NEARAI_API_KEY", "")

    def _resolve_base_url(self) -> str:
        base_url = (
            self._config.get("base_url")
            or self._config.get("baseUrl")
            or os.getenv("NEARAI_BASE_URL", "")
            or self.DEFAULT_BASE_URL
        )
        return base_url.rstrip("/")

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _build_messages(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str],
    ) -> List[Dict[str, str]]:
        full_messages: List[Dict[str, str]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})

        for message in messages:
            role = message.get("role", "user")
            if role == "developer":
                role = "system"
            full_messages.append({
                "role": role,
                "content": message.get("content", ""),
            })
        return full_messages

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
            raise LLMError("nearai", "requests library not installed")

        model = model or self.default_model
        max_tokens = kwargs.get("max_tokens", kwargs.get("max_completion_tokens", 512))
        timeout = kwargs.get("timeout", 30)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": self._build_messages(messages, system_prompt),
            "max_tokens": max_tokens,
        }

        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]
        if "tool_choice" in kwargs:
            payload["tool_choice"] = kwargs["tool_choice"]

        start = time.time()
        try:
            resp = requests.post(
                self._chat_url(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise LLMError("nearai", f"API request failed: {exc}") from exc

        data = resp.json()
        latency_ms = (time.time() - start) * 1000
        choice = data["choices"][0]

        return LLMResponse(
            content=choice["message"]["content"],
            model=model,
            provider="nearai",
            usage=data.get("usage", {}),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason", "stop"),
            raw_response=data,
        )

    def generate_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Iterator[str]:
        response = self.generate(messages, system_prompt, model, **kwargs)
        yield response.content

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["name"] = self._config.get("name", "NEAR AI Cloud")
        info["base_url"] = self.base_url
        return info


registry.register(ProviderType.LLM, "nearai", NearAIProvider)
