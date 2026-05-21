"""
Tests for providers/llm/ — ClawdbotProvider, ZAIProvider (P7-T1, ADR-010, ADR-003)
Tests focus on class instantiation, metadata, and availability checks.
Live API calls are NOT made — no real gateway/API connections.
"""

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# ClawdbotProvider
# ---------------------------------------------------------------------------

class TestClawdbotProvider:
    def test_instantiate_default(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider()
        assert p is not None

    def test_gateway_url_default(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider()
        assert "18791" in p.gateway_url or "localhost" in p.gateway_url or "127.0.0.1" in p.gateway_url

    def test_gateway_url_from_config(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider({"gateway_url": "ws://custom:9999"})
        assert p.gateway_url == "ws://custom:9999"

    def test_is_available_without_token(self, monkeypatch):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        monkeypatch.delenv("CLAWDBOT_AUTH_TOKEN", raising=False)
        p = ClawdbotProvider({"auth_token": ""})
        assert p.is_available() is False

    def test_is_available_with_token(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider({"auth_token": "test-token"})
        assert p.is_available() is True

    def test_get_info_returns_dict(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider()
        info = p.get_info()
        assert isinstance(info, dict)
        assert "gateway_url" in info

    def test_get_info_has_name(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider({"name": "Test Gateway"})
        info = p.get_info()
        assert info["name"] == "Test Gateway"

    def test_default_agent_is_main(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider()
        assert p.default_agent == "main"

    def test_custom_agent(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        p = ClawdbotProvider({"default_agent": "assistant"})
        assert p.default_agent == "assistant"

    def test_generate_raises_when_no_websocket_lib(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        from providers.llm.base import LLMError
        p = ClawdbotProvider({"auth_token": "test"})
        with patch.dict("sys.modules", {"websocket": None}):
            with pytest.raises((LLMError, Exception)):
                p.generate([{"role": "user", "content": "hi"}])

    def test_generate_stream_yields_string(self):
        from providers.llm.clawdbot_provider import ClawdbotProvider
        from providers.llm.base import LLMResponse
        p = ClawdbotProvider()
        mock_response = LLMResponse(
            content="hello", model="glm-4", provider="clawdbot",
            usage={}, latency_ms=0
        )
        with patch.object(p, "generate", return_value=mock_response):
            results = list(p.generate_stream([{"role": "user", "content": "hi"}]))
        assert results == ["hello"]


# ---------------------------------------------------------------------------
# ZAIProvider
# ---------------------------------------------------------------------------

class TestZAIProvider:
    def test_instantiate_default(self):
        from providers.llm.zai_provider import ZAIProvider
        p = ZAIProvider()
        assert p is not None

    def test_default_model(self):
        from providers.llm.zai_provider import ZAIProvider
        p = ZAIProvider()
        assert "glm" in p.default_model.lower()

    def test_custom_model(self):
        from providers.llm.zai_provider import ZAIProvider
        p = ZAIProvider({"default_model": "glm-4-plus"})
        assert p.default_model == "glm-4-plus"

    def test_is_available_without_key(self, monkeypatch):
        from providers.llm.zai_provider import ZAIProvider
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        p = ZAIProvider({"api_key": ""})
        assert p.is_available() is False

    def test_is_available_with_key(self):
        from providers.llm.zai_provider import ZAIProvider
        p = ZAIProvider({"api_key": "test-key"})
        assert p.is_available() is True

    def test_resolve_api_key_ignores_placeholder(self, monkeypatch):
        from providers.llm.zai_provider import ZAIProvider
        monkeypatch.setenv("ZAI_API_KEY", "env-key")
        p = ZAIProvider({"api_key": "${ZAI_API_KEY}"})
        assert p.api_key == "env-key"

    def test_get_info_returns_dict(self):
        from providers.llm.zai_provider import ZAIProvider
        p = ZAIProvider()
        info = p.get_info()
        assert isinstance(info, dict)

    def test_get_info_has_name(self):
        from providers.llm.zai_provider import ZAIProvider
        p = ZAIProvider({"name": "My ZAI"})
        info = p.get_info()
        assert info["name"] == "My ZAI"

    def test_generate_raises_on_api_failure(self):
        from providers.llm.zai_provider import ZAIProvider
        from providers.llm.base import LLMError
        p = ZAIProvider({"api_key": "bad-key"})
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("401 Unauthorized")
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(LLMError):
                p.generate([{"role": "user", "content": "hello"}])

    def test_generate_stream_yields_string(self):
        from providers.llm.zai_provider import ZAIProvider
        from providers.llm.base import LLMResponse
        p = ZAIProvider({"api_key": "key"})
        mock_response = LLMResponse(
            content="test response", model="glm-4", provider="zai",
            usage={}, latency_ms=10
        )
        with patch.object(p, "generate", return_value=mock_response):
            chunks = list(p.generate_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["test response"]


# ---------------------------------------------------------------------------
# NearAIProvider
# ---------------------------------------------------------------------------

class TestNearAIProvider:
    def test_instantiate_default(self):
        from providers.llm.nearai_provider import NearAIProvider
        p = NearAIProvider()
        assert p is not None

    def test_default_model(self):
        from providers.llm.nearai_provider import NearAIProvider
        p = NearAIProvider()
        assert p.default_model == "zai-org/GLM-5.1-FP8"

    def test_base_url_default(self, monkeypatch):
        from providers.llm.nearai_provider import NearAIProvider
        monkeypatch.delenv("NEARAI_BASE_URL", raising=False)
        p = NearAIProvider()
        assert p.base_url == "https://cloud-api.near.ai/v1"

    def test_is_available_without_key(self, monkeypatch):
        from providers.llm.nearai_provider import NearAIProvider
        monkeypatch.delenv("NEARAI_API_KEY", raising=False)
        p = NearAIProvider({"api_key": ""})
        assert p.is_available() is False

    def test_is_available_with_key(self):
        from providers.llm.nearai_provider import NearAIProvider
        p = NearAIProvider({"api_key": "test-key"})
        assert p.is_available() is True

    def test_resolve_api_key_ignores_placeholder(self, monkeypatch):
        from providers.llm.nearai_provider import NearAIProvider
        monkeypatch.setenv("NEARAI_API_KEY", "env-key")
        p = NearAIProvider({"api_key": "${NEARAI_API_KEY}"})
        assert p.api_key == "env-key"

    def test_generate_maps_max_completion_tokens_to_max_tokens(self):
        from providers.llm.nearai_provider import NearAIProvider

        p = NearAIProvider({"api_key": "key"})
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [
                {"message": {"content": "hello"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        mock_resp.raise_for_status.return_value = None

        with patch("requests.post", return_value=mock_resp) as post:
            response = p.generate(
                [{"role": "developer", "content": "be concise"}, {"role": "user", "content": "hi"}],
                max_completion_tokens=123,
                store=True,
                reasoning_effort="high",
            )

        payload = post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 123
        assert "max_completion_tokens" not in payload
        assert "store" not in payload
        assert "reasoning_effort" not in payload
        assert payload["messages"][0]["role"] == "system"
        assert response.content == "hello"

    def test_generate_raises_on_api_failure(self):
        from providers.llm.nearai_provider import NearAIProvider
        from providers.llm.base import LLMError
        p = NearAIProvider({"api_key": "bad-key"})
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("401 Unauthorized")
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(LLMError):
                p.generate([{"role": "user", "content": "hello"}])

    def test_generate_stream_yields_string(self):
        from providers.llm.nearai_provider import NearAIProvider
        from providers.llm.base import LLMResponse
        p = NearAIProvider({"api_key": "key"})
        mock_response = LLMResponse(
            content="test response", model="zai-org/GLM-5.1-FP8", provider="nearai",
            usage={}, latency_ms=10
        )
        with patch.object(p, "generate", return_value=mock_response):
            chunks = list(p.generate_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["test response"]
