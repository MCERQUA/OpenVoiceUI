"""
External STT provider — bring your own Whisper / transcription API.

Sends audio to any user-provided URL via HTTP POST. Supports two API formats:
  1. OpenAI-compatible: POST /v1/audio/transcriptions (multipart form)
  2. Generic Whisper ASR: POST /asr (multipart form)

Auto-detects the format based on the response shape, or set STT_API_FORMAT
to force one: "openai" or "whisper_asr".

Env vars:
  STT_API_URL     — base URL of the external STT service (required)
  STT_API_KEY     — optional Bearer token for authenticated endpoints
  STT_API_FORMAT  — "openai" | "whisper_asr" | "auto" (default: auto)
  STT_MODEL       — model name to pass to the API (default: whisper-1)
  STT_LANGUAGE    — language code (default: en)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

from providers.stt.base import STTError, STTProvider, TranscriptionResult
from providers.registry import ProviderType, registry

logger = logging.getLogger(__name__)


class ExternalSTTProvider(STTProvider):
    """External STT via any HTTP transcription endpoint."""

    def __init__(self, config: Dict[str, Any] = None) -> None:
        super().__init__(config)
        self.api_url = (
            self._config.get("api_url")
            or os.environ.get("STT_API_URL", "")
        ).rstrip("/")
        self.api_key = (
            self._config.get("api_key")
            or os.environ.get("STT_API_KEY", "")
        )
        self.api_format = (
            self._config.get("api_format")
            or os.environ.get("STT_API_FORMAT", "auto")
        ).lower()
        self.model = (
            self._config.get("model")
            or os.environ.get("STT_MODEL", "whisper-1")
        )
        self.language = (
            self._config.get("language")
            or os.environ.get("STT_LANGUAGE", "en")
        )

    def transcribe(
        self,
        audio_data: bytes,
        language: Optional[str] = None,
        **kwargs,
    ) -> TranscriptionResult:
        if not self.api_url:
            raise STTError(
                "external",
                "STT_API_URL not set. Configure it in .env or admin panel.",
            )

        lang = language or self.language
        start = time.time()

        fmt = self.api_format
        if fmt == "auto":
            fmt = self._detect_format()

        if fmt == "openai":
            result = self._transcribe_openai(audio_data, lang)
        else:
            result = self._transcribe_whisper_asr(audio_data, lang)

        result.duration_ms = (time.time() - start) * 1000
        result.provider = "external"
        logger.info(
            "External STT (%s): %r (%.0fms)",
            fmt, result.text, result.duration_ms,
        )
        return result

    def _transcribe_openai(self, audio_data: bytes, language: str) -> TranscriptionResult:
        """OpenAI-compatible: POST /v1/audio/transcriptions"""
        url = self.api_url
        if not url.endswith("/v1/audio/transcriptions"):
            url = url.rstrip("/") + "/v1/audio/transcriptions"

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        files = {"file": ("audio.webm", audio_data, "audio/webm")}
        data = {
            "model": self.model,
            "language": language,
            "response_format": "verbose_json",
        }

        try:
            resp = requests.post(url, files=files, data=data, headers=headers, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise STTError("external", f"OpenAI-format STT request failed: {exc}") from exc

        body = resp.json()
        return TranscriptionResult(
            text=(body.get("text") or "").strip(),
            confidence=0.9,
            language=body.get("language", language),
        )

    def _transcribe_whisper_asr(self, audio_data: bytes, language: str) -> TranscriptionResult:
        """Generic Whisper ASR: POST /asr"""
        url = self.api_url
        if not url.endswith("/asr"):
            url = url.rstrip("/") + "/asr"

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        files = {"audio_file": ("audio.webm", audio_data, "audio/webm")}
        params = {
            "task": "transcribe",
            "language": language,
            "output": "json",
            "encode": "true",
        }

        try:
            resp = requests.post(url, files=files, params=params, headers=headers, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise STTError("external", f"Whisper ASR request failed: {exc}") from exc

        body = resp.json()
        return TranscriptionResult(
            text=(body.get("text") or "").strip(),
            confidence=0.9,
            language=body.get("language", language),
        )

    def _detect_format(self) -> str:
        """Guess API format from URL path."""
        if "/v1/" in self.api_url:
            return "openai"
        if "/asr" in self.api_url:
            return "whisper_asr"
        # Default to openai format — most common standard
        return "openai"

    def is_available(self) -> bool:
        if not self.api_url:
            return False
        try:
            # Try common health endpoints
            for path in ["/health", "/docs", "/"]:
                try:
                    resp = requests.get(
                        self.api_url.rstrip("/") + path,
                        timeout=5,
                    )
                    if resp.status_code < 500:
                        return True
                except requests.RequestException:
                    continue
            return False
        except Exception:
            return False

    def get_info(self) -> Dict[str, Any]:
        available = self.is_available()
        return {
            "name": self._config.get("name", "External STT"),
            "status": "active" if available else "inactive",
            "api_url": self.api_url or "(not configured)",
            "api_format": self.api_format,
            "model": self.model,
            "available": available,
        }


# Auto-register when this module is imported
registry.register(ProviderType.STT, "external", ExternalSTTProvider)
