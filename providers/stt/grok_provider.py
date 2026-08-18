"""
Grok / xAI STT provider — POST https://api.x.ai/v1/stt

Unary file/URL transcription with multipart form.
API key: XAI_API_KEY env only (never ship browser-side).

Docs: https://docs.x.ai/developers/model-capabilities/audio/speech-to-text

Provenance: originally contributed in the Bartok9/OpenVoiceUI fork (MIT, same lineage
as this repo) and adopted upstream here. Thanks to @Bartok9.

Availability: this provider registers unconditionally but reports is_available() == False
when XAI_API_KEY is unset, so it is inert on installs that do not use xAI. Nothing else
in the app changes behaviour when the key is absent.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from providers.stt.base import STTError, STTProvider, TranscriptionResult
from providers.registry import ProviderType, registry

logger = logging.getLogger(__name__)

STT_URL = "https://api.x.ai/v1/stt"
STT_TIMEOUT = 120.0


class GrokSTTProvider(STTProvider):
    """xAI Grok Speech-to-Text (batch/file unary)."""

    def __init__(self, config: Dict[str, Any] = None) -> None:
        super().__init__(config)
        self.api_key = self._resolve_api_key()
        self.default_language = (
            self._config.get("language") or os.getenv("XAI_STT_LANGUAGE") or "en"
        )

    def _resolve_api_key(self) -> str:
        key = self._config.get("api_key", "")
        if key and not str(key).startswith("${"):
            return str(key)
        return os.getenv("XAI_API_KEY", "")

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def transcribe(
        self,
        audio_data: bytes,
        language: Optional[str] = None,
        **kwargs: Any,
    ) -> TranscriptionResult:
        if not self.api_key:
            raise STTError("grok", "XAI_API_KEY not set")
        if not audio_data:
            raise STTError("grok", "empty audio_data")

        lang = language or self.default_language or "en"
        filename = kwargs.get("filename") or "audio.webm"
        content_type = (
            kwargs.get("content_type")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )

        # multipart: other fields first, file last (xAI requirement)
        data_fields: List[tuple] = []
        if kwargs.get("format") is not None:
            data_fields.append(("format", "true" if kwargs["format"] else "false"))
        elif kwargs.get("text_format") is not None:
            data_fields.append(
                ("format", "true" if kwargs["text_format"] else "false")
            )
        else:
            data_fields.append(("format", "true"))
        data_fields.append(("language", lang))
        if kwargs.get("diarize"):
            data_fields.append(("diarize", "true"))
        if kwargs.get("filler_words"):
            data_fields.append(("filler_words", "true"))
        keyterms = kwargs.get("keyterms") or kwargs.get("keyterm") or []
        if isinstance(keyterms, str):
            keyterms = [keyterms]
        for term in keyterms:
            data_fields.append(("keyterm", str(term)))
        if kwargs.get("audio_format"):
            data_fields.append(("audio_format", str(kwargs["audio_format"])))
        if kwargs.get("sample_rate"):
            data_fields.append(("sample_rate", str(int(kwargs["sample_rate"]))))

        files = {"file": (filename, audio_data, content_type)}

        start = time.time()
        try:
            with httpx.Client(timeout=httpx.Timeout(STT_TIMEOUT, connect=10.0)) as client:
                resp = client.post(
                    STT_URL,
                    headers=self._auth_headers(),
                    data=data_fields,
                    files=files,
                )
                if resp.status_code >= 400:
                    detail = (resp.text or "")[:400]
                    raise STTError("grok", f"HTTP {resp.status_code}: {detail}")
                body = resp.json()
        except STTError:
            raise
        except httpx.TimeoutException as exc:
            raise STTError("grok", f"timeout after {STT_TIMEOUT}s") from exc
        except httpx.RequestError as exc:
            raise STTError("grok", f"network: {exc}") from exc
        except Exception as exc:
            raise STTError("grok", f"transcription failed: {exc}") from exc

        text = (body.get("text") or "").strip()
        words = body.get("words") or []
        duration_s = float(body.get("duration") or 0.0)
        detected = body.get("language") or lang

        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            "[Grok/xAI STT] %r (%.0fms, audio=%.2fs words=%s)",
            text[:80],
            elapsed_ms,
            duration_s,
            len(words) if isinstance(words, list) else 0,
        )

        segments: Optional[List[Dict]] = None
        if isinstance(words, list) and words:
            segments = [
                {
                    "text": w.get("text"),
                    "start": w.get("start"),
                    "end": w.get("end"),
                    "speaker": w.get("speaker"),
                }
                for w in words
                if isinstance(w, dict)
            ]

        return TranscriptionResult(
            text=text,
            confidence=0.9 if text else 0.0,
            language=str(detected),
            duration_ms=duration_s * 1000.0 if duration_s else elapsed_ms,
            provider="grok",
            segments=segments,
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def list_languages(self) -> List[str]:
        return self._config.get(
            "languages",
            [
                "en",
                "es",
                "fr",
                "de",
                "it",
                "pt",
                "ja",
                "ko",
                "zh",
                "hi",
                "ar",
                "ru",
            ],
        )

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self._config.get("name", "Grok / xAI STT"),
            "provider_id": "grok",
            "status": "active" if self.is_available() else "inactive",
            "available": self.is_available(),
            "languages": self.list_languages(),
            "endpoint": STT_URL,
            "requires_api_key": True,
            "notes": "XAI_API_KEY required. POST multipart /v1/stt (unary). WS stream is separate.",
            "documentation_url": (
                "https://docs.x.ai/developers/model-capabilities/audio/speech-to-text"
            ),
        }


registry.register(ProviderType.STT, "grok", GrokSTTProvider)

__all__ = ["GrokSTTProvider", "STT_URL"]
