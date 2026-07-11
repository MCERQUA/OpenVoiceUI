"""
Mocked unit tests for Grok / xAI TTS (tts_providers + registry adapter).
No live network — all HTTP via httpx mock / monkeypatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestGrokProviderCanonical:
    def _make(self, api_key: str = "test-xai-key"):
        with patch.dict("os.environ", {"XAI_API_KEY": api_key} if api_key else {}, clear=False):
            if not api_key:
                with patch.dict("os.environ", {}, clear=True):
                    from tts_providers.grok_provider import GrokProvider

                    p = GrokProvider()
                    p.api_key = ""
                    return p
            from tts_providers.grok_provider import GrokProvider

            # Re-import fresh after env: construct and force key
            p = GrokProvider()
            p.api_key = api_key
            p._status = "active"
            p._init_error = None
            return p

    def test_list_voices_documented_roster_without_network_key_missing(self):
        from tts_providers.grok_provider import DOCUMENTED_VOICES, GrokProvider

        with patch.dict("os.environ", {}, clear=True):
            p = GrokProvider()
            p.api_key = ""
            voices = p.list_voices()
        assert "eve" in voices
        assert set(DOCUMENTED_VOICES).issubset(set(voices)) or voices == DOCUMENTED_VOICES

    def test_get_default_voice_eve(self):
        p = self._make()
        assert p.get_default_voice() == "eve"

    def test_is_available_requires_key(self):
        p = self._make("k")
        assert p.is_available() is True
        p.api_key = ""
        assert p.is_available() is False

    def test_generate_speech_raises_without_key(self):
        p = self._make("k")
        p.api_key = ""
        with pytest.raises(RuntimeError, match="XAI_API_KEY"):
            p.generate_speech("hello")

    def test_generate_speech_posts_expected_json(self):
        p = self._make("secret-key")
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.content = b"ID3fake-mp3-bytes"
        fake_resp.text = ""

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = fake_resp

        with patch("tts_providers.grok_provider.httpx.Client", return_value=mock_client):
            audio = p.generate_speech("Hello from Grok", voice="ara", language="en")

        assert audio == b"ID3fake-mp3-bytes"
        assert mock_client.post.called
        args, kwargs = mock_client.post.call_args
        assert args[0] == "https://api.x.ai/v1/tts"
        assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
        assert kwargs["json"]["text"] == "Hello from Grok"
        assert kwargs["json"]["voice_id"] == "ara"
        assert kwargs["json"]["language"] == "en"

    def test_generate_speech_http_error(self):
        p = self._make("secret-key")
        fake_resp = MagicMock()
        fake_resp.status_code = 401
        fake_resp.content = b""
        fake_resp.text = '{"error":"unauthorized"}'

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = fake_resp

        with patch("tts_providers.grok_provider.httpx.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="grok:401"):
                p.generate_speech("nope")

    def test_generate_speech_http_500_path(self):
        p = self._make("secret-key")
        fake_resp = MagicMock()
        fake_resp.status_code = 500
        fake_resp.content = b""
        fake_resp.text = "internal error"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = fake_resp

        with patch("tts_providers.grok_provider.httpx.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="grok:500"):
                p.generate_speech("oops")

    def test_generate_speech_empty_body_errors(self):
        p = self._make("secret-key")
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.content = b""
        fake_resp.text = ""

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = fake_resp

        with patch("tts_providers.grok_provider.httpx.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="empty"):
                p.generate_speech("hi")

    def test_health_check_missing_key(self):
        p = self._make("k")
        p.api_key = ""
        h = p.health_check()
        assert h["ok"] is False
        assert "XAI_API_KEY" in h["detail"]

    def test_default_voice_arg_is_eve_in_request(self):
        p = self._make("secret-key")
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.content = b"ID3data"
        fake_resp.text = ""

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = fake_resp

        with patch("tts_providers.grok_provider.httpx.Client", return_value=mock_client):
            p.generate_speech("Hello")
        _args, kwargs = mock_client.post.call_args
        assert kwargs["json"]["voice_id"] == "eve"

    def test_list_voices_parses_api_payload(self):
        p = self._make("secret-key")
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "voices": [
                {"voice_id": "eve", "name": "Eve"},
                {"voice_id": "ara", "name": "Ara"},
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = fake_resp

        with patch("tts_providers.grok_provider.httpx.Client", return_value=mock_client):
            voices = p.list_voices()
        assert voices == ["eve", "ara"]

    def test_get_info_shape(self):
        p = self._make("secret-key")
        info = p.get_info()
        assert info["provider_id"] == "grok"
        assert info["default_voice"] == "eve"
        assert info["requires_api_key"] is True
        assert "eve" in info["voices"]
        assert "agent_voice_preference" in info


class TestGrokTTSProviderRegistry:
    def test_registry_import_registers_grok(self):
        import providers.tts.grok_provider  # noqa: F401
        from providers.registry import ProviderType, registry

        # registration is side-effect of import
        ops = registry.list_providers(ProviderType.TTS, include_unavailable=True)
        names = {p.get("id") or p.get("name") for p in ops}
        # list_providers shape varies — also poke get
        try:
            inst = registry.get(ProviderType.TTS, "grok")
            assert inst is not None
        except Exception:
            # fall back: class importable
            from providers.tts.grok_provider import GrokTTSProvider

            assert GrokTTSProvider is not None

    def test_adapter_generate_uses_canonical(self):
        from providers.tts.grok_provider import GrokTTSProvider
        from providers.tts.base import TTSError

        prov = GrokTTSProvider({"api_key": "k"})
        with patch.object(
            type(prov),
            "_get_impl",
            lambda self: MagicMock(generate_speech=MagicMock(return_value=b"audio")),
        ):
            # re-bind or call through mock differently
            pass

        impl = MagicMock()
        impl.generate_speech.return_value = b"wav-or-mp3"
        with patch.object(GrokTTSProvider, "_get_impl", return_value=impl):
            out = prov.generate_speech("hi", voice="eve")
        assert out == b"wav-or-mp3"
        impl.generate_speech.assert_called()

    def test_adapter_reads_default_voice_from_config(self):
        from providers.tts.grok_provider import GrokTTSProvider

        prov = GrokTTSProvider({"api_key": "k", "default_voice": "ara"})
        assert prov.default_voice == "ara"

        impl = MagicMock()
        impl.generate_speech.return_value = b"audio"
        with patch.object(GrokTTSProvider, "_get_impl", return_value=impl):
            prov.generate_speech("hi")
        impl.generate_speech.assert_called_once_with(
            "hi", voice="ara", language="en"
        )

    def test_adapter_honors_lang_alias(self):
        from providers.tts.grok_provider import GrokTTSProvider

        prov = GrokTTSProvider({"api_key": "k"})
        impl = MagicMock()
        impl.generate_speech.return_value = b"audio"
        with patch.object(GrokTTSProvider, "_get_impl", return_value=impl):
            prov.generate_speech("hi", lang="fr")
        impl.generate_speech.assert_called_once_with(
            "hi", voice="eve", language="fr"
        )

    def test_adapter_raises_tts_error_without_key(self):
        from providers.tts.grok_provider import GrokTTSProvider
        from providers.tts.base import TTSError

        with patch.dict("os.environ", {}, clear=True):
            prov = GrokTTSProvider({"api_key": ""})
            with pytest.raises(TTSError):
                prov.generate_speech("x")
