"""
Groq Orpheus TTS provider.

Ref: future-dev-plans/02-PROVIDER-SYSTEMS.md (GroqProvider section)
Fallback TTS when Supertonic is unavailable.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from providers.tts.base import TTSError, TTSProvider
from providers.registry import ProviderType, registry

logger = logging.getLogger(__name__)


class GroqTTSProvider(TTSProvider):
    """Groq Orpheus cloud TTS provider."""

    def __init__(self, config: Dict[str, Any] = None) -> None:
        super().__init__(config)
        self.api_key = self._resolve_api_key()
        self.model = self._config.get("model", "canopylabs/orpheus-v1-english")
        self.default_voice = self._config.get("voice", "autumn")

    def _resolve_api_key(self) -> str:
        key = self._config.get("api_key", "")
        if key and not key.startswith("${"):
            return key
        return os.getenv("GROQ_API_KEY", "")

    def generate_speech(self, text: str, **kwargs) -> bytes:
        self.validate_text(text)
        if not self.api_key:
            raise TTSError("groq", "GROQ_API_KEY not set")

        try:
            import groq as groq_lib  # type: ignore
        except ImportError:
            raise TTSError("groq", "groq library not installed: pip install groq")

        voice = kwargs.get("voice", self.default_voice)
        try:
            client = groq_lib.Groq(api_key=self.api_key)
            response = client.audio.speech.create(
                model=self.model,
                voice=voice,
                input=text,
                response_format="wav",  # Groq Orpheus now ONLY accepts wav; mp3 → 400 (2026-06-15)
            )
            return response.read()
        except Exception as exc:
            raise TTSError("groq", f"Generation failed: {exc}") from exc

    def list_voices(self) -> List[str]:
        return ["autumn", "diana", "hannah", "austin", "daniel", "troy"]

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self._config.get("name", "Groq Orpheus"),
            "status": "active" if self.is_available() else "inactive",
            "model": self.model,
            "available": self.is_available(),
        }


# Auto-register when this module is imported
registry.register(ProviderType.TTS, "groq", GroqTTSProvider)
