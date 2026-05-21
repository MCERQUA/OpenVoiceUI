"""
Tests for provider registry + auto-discovery (P5-T2).
ADR-003: Abstract base class + registry pattern.
"""

import pytest
from providers.registry import ProviderRegistry, ProviderType, registry


# ---------------------------------------------------------------------------
# Minimal concrete providers for testing
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self, config=None):
        self._config = config or {}
    def is_available(self):
        return self._config.get("available", True)
    def get_info(self):
        return {"name": self._config.get("name", "fake-llm"), "status": "active", "available": True}


class _FakeTTS:
    def __init__(self, config=None):
        self._config = config or {}
    def is_available(self):
        return self._config.get("available", True)
    def get_info(self):
        return {"name": self._config.get("name", "fake-tts"), "status": "active", "available": True}


class _FakeSTT:
    def __init__(self, config=None):
        self._config = config or {}
    def is_available(self):
        return self._config.get("available", True)
    def get_info(self):
        return {"name": self._config.get("name", "fake-stt"), "status": "active", "available": True}


class _UnavailableTTS:
    def __init__(self, config=None):
        self._config = config or {}
    def is_available(self):
        return False
    def get_info(self):
        return {"name": "unavailable", "status": "inactive", "available": False}


# ---------------------------------------------------------------------------
# Fixture: isolated registry (don't pollute the global singleton for tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def reg():
    """A fresh ProviderRegistry isolated from the global singleton."""
    return ProviderRegistry()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

def test_register_and_get_provider(reg):
    reg.register(ProviderType.LLM, "fake", _FakeLLM)
    instance = reg.get_provider(ProviderType.LLM, "fake")
    assert isinstance(instance, _FakeLLM)


def test_register_with_config(reg):
    reg.register(ProviderType.TTS, "fake-tts", _FakeTTS, config={"name": "My TTS", "priority": 5})
    instance = reg.get_provider(ProviderType.TTS, "fake-tts")
    assert instance._config["name"] == "My TTS"


def test_get_unknown_provider_raises(reg):
    with pytest.raises(ValueError, match="Unknown"):
        reg.get_provider(ProviderType.LLM, "does_not_exist")


def test_registered_ids(reg):
    reg.register(ProviderType.STT, "ws", _FakeSTT)
    reg.register(ProviderType.STT, "whisper", _FakeSTT)
    ids = reg.registered_ids(ProviderType.STT)
    assert "ws" in ids
    assert "whisper" in ids


def test_is_registered(reg):
    reg.register(ProviderType.LLM, "test-llm", _FakeLLM)
    assert reg.is_registered(ProviderType.LLM, "test-llm") is True
    assert reg.is_registered(ProviderType.LLM, "nope") is False


# ---------------------------------------------------------------------------
# Default provider tests
# ---------------------------------------------------------------------------

def test_get_default_falls_back_to_first_registered(reg):
    reg.register(ProviderType.TTS, "first", _FakeTTS)
    reg.register(ProviderType.TTS, "second", _FakeTTS)
    instance = reg.get_provider(ProviderType.TTS)  # no ID → default
    assert isinstance(instance, _FakeTTS)


def test_get_default_no_providers_raises(reg):
    with pytest.raises(ValueError, match="No tts providers registered"):
        reg.get_provider(ProviderType.TTS)


# ---------------------------------------------------------------------------
# list_providers tests
# ---------------------------------------------------------------------------

def test_list_providers_available_only(reg):
    reg.register(ProviderType.TTS, "good", _FakeTTS)
    reg.register(ProviderType.TTS, "bad", _UnavailableTTS)
    result = reg.list_providers(ProviderType.TTS, include_unavailable=False)
    ids = [p["id"] for p in result]
    assert "good" in ids
    assert "bad" not in ids


def test_list_providers_include_unavailable(reg):
    reg.register(ProviderType.TTS, "good", _FakeTTS)
    reg.register(ProviderType.TTS, "bad", _UnavailableTTS)
    result = reg.list_providers(ProviderType.TTS, include_unavailable=True)
    ids = [p["id"] for p in result]
    assert "good" in ids
    assert "bad" in ids


def test_list_providers_sorted_by_priority(reg):
    reg.register(ProviderType.LLM, "low-pri", _FakeLLM, config={"priority": 50})
    reg.register(ProviderType.LLM, "high-pri", _FakeLLM, config={"priority": 5})
    result = reg.list_providers(ProviderType.LLM)
    assert result[0]["id"] == "high-pri"
    assert result[1]["id"] == "low-pri"


def test_list_providers_returns_expected_keys(reg):
    reg.register(ProviderType.STT, "ws", _FakeSTT)
    result = reg.list_providers(ProviderType.STT)
    assert len(result) == 1
    entry = result[0]
    assert "id" in entry
    assert "name" in entry
    assert "available" in entry
    assert "priority" in entry
    assert "info" in entry


# ---------------------------------------------------------------------------
# Env-var resolution tests
# ---------------------------------------------------------------------------

def test_env_var_resolution(monkeypatch, reg):
    monkeypatch.setenv("MY_TEST_KEY", "secret123")
    reg.register(ProviderType.LLM, "env-test", _FakeLLM, config={"api_key": "${MY_TEST_KEY}"})
    instance = reg.get_provider(ProviderType.LLM, "env-test")
    assert instance._config["api_key"] == "secret123"


def test_unresolved_env_var_keeps_placeholder(monkeypatch, reg):
    monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
    reg.register(ProviderType.LLM, "noenv", _FakeLLM, config={"api_key": "${NONEXISTENT_VAR}"})
    instance = reg.get_provider(ProviderType.LLM, "noenv")
    # Placeholder stays intact when env var is missing
    assert instance._config["api_key"] == "${NONEXISTENT_VAR}"


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------

def test_global_registry_is_singleton():
    from providers.registry import registry as r1
    from providers.registry import ProviderRegistry
    r2 = ProviderRegistry.get_instance()
    assert r1 is r2


# ---------------------------------------------------------------------------
# Concrete provider registration smoke tests
# (These verify the global registry has providers from the package imports)
# ---------------------------------------------------------------------------

def test_concrete_providers_registered():
    """Importing providers.llm/tts/stt should auto-register concrete providers."""
    import providers.llm  # noqa: F401
    import providers.tts  # noqa: F401
    import providers.stt  # noqa: F401

    from providers.registry import registry as r, ProviderType as PT

    assert r.is_registered(PT.LLM, "zai")
    assert r.is_registered(PT.LLM, "nearai")
    assert r.is_registered(PT.LLM, "clawdbot")
    assert r.is_registered(PT.TTS, "supertonic")
    assert r.is_registered(PT.TTS, "groq")
    assert r.is_registered(PT.STT, "webspeech")
    assert r.is_registered(PT.STT, "whisper")


def test_convenience_functions_importable():
    from providers.registry import get_llm_provider, get_tts_provider, get_stt_provider
    assert callable(get_llm_provider)
    assert callable(get_tts_provider)
    assert callable(get_stt_provider)


def test_webspeech_always_available():
    from providers.stt.webspeech_provider import WebSpeechProvider
    ws = WebSpeechProvider()
    assert ws.is_available() is True


def test_whisper_availability_depends_on_lib():
    from providers.stt.whisper_provider import WhisperProvider
    ws = WhisperProvider()
    # is_available returns bool — just check it doesn't crash
    result = ws.is_available()
    assert isinstance(result, bool)
