"""
Grok / xAI TTS Provider — api.x.ai text-to-speech.

POST https://api.x.ai/v1/tts with Bearer XAI_API_KEY.
JSON body: text, voice_id, language (and optional output_format, speed).

API key: XAI_API_KEY env var
Docs: https://docs.x.ai/developers/model-capabilities/audio/text-to-speech

Provenance: originally contributed in the Bartok9/OpenVoiceUI fork (MIT, same lineage
as this repo) and adopted upstream here. Thanks to @Bartok9.

Availability: registers unconditionally but reports is_available() == False when
XAI_API_KEY is unset, so it is inert on installs that do not use xAI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from .base_provider import TTSProvider

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.ai/v1"
TTS_URL = f"{API_BASE}/tts"
TTS_WS_URL = "wss://api.x.ai/v1/tts"
VOICES_URL = f"{API_BASE}/tts/voices"

GENERATE_TIMEOUT = 30.0
STREAM_TIMEOUT = 60.0
LIST_TIMEOUT = 15.0
HEALTH_TIMEOUT = 10.0

# Documented public roster (case-insensitive IDs). Prefer these for agents;
# do not embed private Team-of-Light / tenant custom voice secrets here.
# Default product preference for agents: eve (xAI default) then ara / rex.
DOCUMENTED_VOICES: List[str] = [
    "eve",  # default — general agents & UI confirmations
    "ara",  # warm / support-style
    "rex",  # clearer / media narration
    "sal",
    "leo",
]

AGENT_VOICE_PREFERENCE_NOTES = (
    "Agents: default voice_id=eve (xAI default). Prefer documented IDs only; "
    "pass custom voice_ids via config/env if operators clone voices registry-side. "
    "Do not commit private custom voice secrets into this package."
)

MAX_CHARACTERS = 15000


class GrokProvider(TTSProvider):
    """
    TTS Provider using xAI Grok Text-to-Speech API.

    Output: MP3 audio bytes by default (24 kHz / 128 kbps).
    """

    def __init__(self) -> None:
        super().__init__()
        self.api_key = os.getenv("XAI_API_KEY", "")
        self._status = "active" if self.api_key else "error"
        self._init_error: Optional[str] = None if self.api_key else "XAI_API_KEY not set"
        self._voices_cache: Optional[List[str]] = None
        self._voices_cache_time = 0.0

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def generate_speech(self, text: str, voice: str = "eve", **kwargs) -> bytes:
        """
        Generate speech via xAI TTS.

        Args:
            text: Text to synthesize (max 15_000 chars). Supports speech tags.
            voice: voice_id (default ``eve``). Also accepts kwargs ``voice_id``.
            **kwargs:
                language: BCP-47 code or ``auto`` (default ``en``).
                speed: 0.7–1.5 (optional).
                output_format: optional dict ``{codec, sample_rate, bit_rate}``.

        Returns:
            Raw audio bytes (default MP3).
        """
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY not set")

        self.validate_text(text)
        if len(text) > MAX_CHARACTERS:
            raise ValueError(f"Text exceeds max {MAX_CHARACTERS} characters")

        voice_id = kwargs.get("voice_id") or voice or "eve"
        language = kwargs.get("language") or kwargs.get("lang") or "en"

        body: Dict[str, Any] = {
            "text": text,
            "voice_id": voice_id,
            "language": language,
        }
        if "speed" in kwargs and kwargs["speed"] is not None:
            body["speed"] = float(kwargs["speed"])
        if kwargs.get("output_format"):
            body["output_format"] = kwargs["output_format"]
        if kwargs.get("text_normalization") is not None:
            body["text_normalization"] = bool(kwargs["text_normalization"])
        if kwargs.get("optimize_streaming_latency") is not None:
            body["optimize_streaming_latency"] = int(kwargs["optimize_streaming_latency"])

        t = time.time()
        logger.info("[Grok/xAI] TTS request: '%s' voice=%s lang=%s", text[:60], voice_id, language)

        try:
            with httpx.Client(timeout=httpx.Timeout(GENERATE_TIMEOUT, connect=10.0)) as client:
                resp = client.post(TTS_URL, headers=self._auth_headers(), json=body)
                if resp.status_code >= 400:
                    detail = (resp.text or "")[:400]
                    raise RuntimeError(f"[grok:{resp.status_code}] {detail}")
                audio_bytes = resp.content
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"[grok:timeout] TTS after {GENERATE_TIMEOUT}s") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"[grok:network] {exc}") from exc

        if not audio_bytes:
            raise RuntimeError("[grok:empty] Empty audio body from xAI TTS")

        elapsed = int((time.time() - t) * 1000)
        logger.info("[Grok/xAI] Generated %s bytes in %sms", len(audio_bytes), elapsed)
        return audio_bytes

    def _stream_ws_query(
        self,
        *,
        voice: str = "eve",
        language: str = "en",
        codec: str = "mp3",
        sample_rate: int = 24000,
        bit_rate: int = 128000,
        speed: Optional[float] = None,
        optimize_streaming_latency: int = 1,
        text_normalization: bool = False,
    ) -> str:
        params: Dict[str, Any] = {
            "language": language,
            "voice": voice or "eve",
            "codec": codec,
            "sample_rate": int(sample_rate),
            "bit_rate": int(bit_rate),
            "optimize_streaming_latency": int(optimize_streaming_latency),
            "text_normalization": "true" if text_normalization else "false",
        }
        if speed is not None:
            params["speed"] = float(speed)
        return f"{TTS_WS_URL}?{urlencode(params)}"

    def stream_speech_sync(
        self,
        text: str,
        voice: str = "eve",
        *,
        voice_id: Optional[str] = None,
        language: str = "en",
        codec: str = "mp3",
        sample_rate: int = 24000,
        bit_rate: int = 128000,
        optimize_streaming_latency: int = 1,
        speed: Optional[float] = None,
        text_normalization: bool = False,
        connect_fn=None,
    ) -> Iterator[bytes]:
        """
        Stream TTS via bidirectional WebSocket ``wss://api.x.ai/v1/tts`` (TTFA path).

        Yields raw audio chunks decoded from ``audio.delta`` events.
        ``connect_fn`` is injectable for unit tests (mock sockets).

        Official docs: Streaming TTS (WebSocket) on xAI TTS docs.
        """
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY not set")

        # Honor voice_id alias for parity with unary generate_speech.
        voice = voice_id or voice
        self.validate_text(text)
        # WS path has no hard total length on the session; still guard unary-like
        # single-delta size
        if len(text) > MAX_CHARACTERS:
            # split into MAX chunks at sentence-ish boundaries would be nicer;
            # for now, one delta max per official client message cap.
            raise ValueError(
                f"stream_speech_sync text delta exceeds max {MAX_CHARACTERS} characters; "
                "split caller-side or use multi-delta helpers"
            )

        uri = self._stream_ws_query(
            voice=voice or "eve",
            language=language or "en",
            codec=codec,
            sample_rate=sample_rate,
            bit_rate=bit_rate,
            speed=speed,
            optimize_streaming_latency=optimize_streaming_latency,
            text_normalization=text_normalization,
        )
        headers = {"Authorization": f"Bearer {self.api_key}"}

        def _default_connect(url: str, hdrs: Dict[str, str]):
            try:
                import websockets
                from websockets.sync.client import connect as ws_connect
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "websockets package required for Grok streaming TTS"
                ) from exc
            return ws_connect(url, additional_headers=hdrs, open_timeout=15, close_timeout=5)

        connector = connect_fn or _default_connect

        t0 = time.time()
        first = True
        logger.info(
            "[Grok/xAI] TTS WS stream: '%s' voice=%s lang=%s osl=%s",
            text[:60],
            voice,
            language,
            optimize_streaming_latency,
        )

        deadline = t0 + STREAM_TIMEOUT
        with connector(uri, headers) as ws:
            ws.send(json.dumps({"type": "text.delta", "delta": text}))
            ws.send(json.dumps({"type": "text.done"}))
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"[grok:ws] no audio.done within {STREAM_TIMEOUT}s stream timeout"
                    )
                try:
                    raw = ws.recv(timeout=remaining)
                except TypeError:
                    # Older websockets.sync without timeout kwarg support.
                    raw = ws.recv()
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"[grok:ws] recv timed out after {STREAM_TIMEOUT}s"
                    ) from exc
                if isinstance(raw, bytes):
                    # Some stacks may deliver binary audio frames — yield as-is
                    if first:
                        logger.info(
                            "[Grok/xAI] TTFA (binary) %sms",
                            int((time.time() - t0) * 1000),
                        )
                        first = False
                    yield raw
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("[Grok/xAI] non-JSON WS frame skipped")
                    continue
                et = event.get("type") or ""
                if et in ("audio.delta", "response.output_audio.delta"):
                    b64 = event.get("delta") or event.get("audio") or ""
                    if not b64:
                        continue
                    chunk = base64.b64decode(b64)
                    if first:
                        logger.info(
                            "[Grok/xAI] TTFA %sms (%s bytes first chunk)",
                            int((time.time() - t0) * 1000),
                            len(chunk),
                        )
                        first = False
                    yield chunk
                elif et in ("audio.done", "response.output_audio.done"):
                    break
                elif et == "error":
                    raise RuntimeError(
                        f"[grok:ws] {event.get('message') or event.get('error') or event}"
                    )
                # ignore other session metadata

    def stream_speech(
        self,
        text: str,
        voice: str = "eve",
        **kwargs: Any,
    ) -> Iterator[bytes]:
        """Alias for :meth:`stream_speech_sync` (iterator of audio bytes)."""
        # Accept unary-style aliases without TypeError; strip keys WS signature rejects.
        voice = kwargs.get("voice_id") or kwargs.get("voice") or voice
        language = kwargs.get("language") or kwargs.get("lang") or "en"
        allowed = {
            "codec",
            "sample_rate",
            "bit_rate",
            "optimize_streaming_latency",
            "speed",
            "text_normalization",
            "connect_fn",
        }
        clean = {k: v for k, v in kwargs.items() if k in allowed}
        return self.stream_speech_sync(text, voice=voice, language=language, **clean)

    async def stream_speech_async(
        self,
        text: str,
        voice: str = "eve",
        *,
        voice_id: Optional[str] = None,
        language: str = "en",
        codec: str = "mp3",
        sample_rate: int = 24000,
        bit_rate: int = 128000,
        optimize_streaming_latency: int = 1,
        speed: Optional[float] = None,
        text_normalization: bool = False,
        connect_fn=None,
    ) -> AsyncIterator[bytes]:
        """Async variant of WebSocket streaming TTS (prefer TTFA path)."""
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY not set")
        voice = voice_id or voice
        self.validate_text(text)
        if len(text) > MAX_CHARACTERS:
            raise ValueError(f"Text exceeds max {MAX_CHARACTERS} characters per delta")

        uri = self._stream_ws_query(
            voice=voice or "eve",
            language=language or "en",
            codec=codec,
            sample_rate=sample_rate,
            bit_rate=bit_rate,
            speed=speed,
            optimize_streaming_latency=optimize_streaming_latency,
            text_normalization=text_normalization,
        )
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async def _default_connect(url: str, hdrs: Dict[str, str]):
            import websockets

            return websockets.connect(
                url, additional_headers=hdrs, open_timeout=15, close_timeout=5
            )

        connector = connect_fn or _default_connect
        t0 = time.time()
        first = True

        async with connector(uri, headers) as ws:
            await ws.send(json.dumps({"type": "text.delta", "delta": text}))
            await ws.send(json.dumps({"type": "text.done"}))
            deadline = t0 + STREAM_TIMEOUT
            aiter = ws.__aiter__()
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"[grok:ws] no audio.done within {STREAM_TIMEOUT}s stream timeout"
                    )
                try:
                    raw = await asyncio.wait_for(aiter.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    raise RuntimeError(
                        "[grok:ws] WebSocket closed before audio.done"
                    )
                except asyncio.TimeoutError as exc:
                    raise TimeoutError(
                        f"[grok:ws] recv timed out after {STREAM_TIMEOUT}s"
                    ) from exc
                if isinstance(raw, bytes):
                    if first:
                        first = False
                    yield raw
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                et = event.get("type") or ""
                if et in ("audio.delta", "response.output_audio.delta"):
                    b64 = event.get("delta") or event.get("audio") or ""
                    if not b64:
                        continue
                    chunk = base64.b64decode(b64)
                    if first:
                        logger.info(
                            "[Grok/xAI] async TTFA %sms",
                            int((time.time() - t0) * 1000),
                        )
                        first = False
                    yield chunk
                elif et in ("audio.done", "response.output_audio.done"):
                    break
                elif et == "error":
                    raise RuntimeError(
                        f"[grok:ws] {event.get('message') or event.get('error') or event}"
                    )

    def list_voices(self) -> List[str]:
        """
        Return voice IDs: live GET /v1/tts/voices when keyed, else documented roster.
        """
        if not self.api_key:
            return DOCUMENTED_VOICES.copy()

        # Short TTL cache
        now = time.time()
        if self._voices_cache is not None and (now - self._voices_cache_time) < 300:
            return self._voices_cache.copy()

        try:
            with httpx.Client(timeout=httpx.Timeout(LIST_TIMEOUT, connect=5.0)) as client:
                resp = client.get(
                    VOICES_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code >= 400:
                    logger.warning(
                        "[Grok/xAI] list voices HTTP %s — using documented roster",
                        resp.status_code,
                    )
                    return DOCUMENTED_VOICES.copy()
                payload = resp.json()
                voices = payload.get("voices") or []
                ids = [
                    str(v.get("voice_id") or v.get("id") or "").strip()
                    for v in voices
                    if isinstance(v, dict)
                ]
                ids = [i for i in ids if i]
                if not ids:
                    return DOCUMENTED_VOICES.copy()
                self._voices_cache = ids
                self._voices_cache_time = now
                return ids.copy()
        except Exception as exc:
            logger.warning("[Grok/xAI] list_voices failed: %s — documented roster", exc)
            return DOCUMENTED_VOICES.copy()

    def get_default_voice(self) -> str:
        return "eve"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def health_check(self) -> dict:
        if not self.api_key:
            return {"ok": False, "latency_ms": 0, "detail": "XAI_API_KEY not set"}
        t = time.time()
        try:
            with httpx.Client(timeout=httpx.Timeout(HEALTH_TIMEOUT, connect=5.0)) as client:
                resp = client.get(
                    VOICES_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                latency_ms = int((time.time() - t) * 1000)
                if resp.status_code >= 400:
                    return {
                        "ok": False,
                        "latency_ms": latency_ms,
                        "detail": f"HTTP {resp.status_code}: {(resp.text or '')[:200]}",
                    }
                return {
                    "ok": True,
                    "latency_ms": latency_ms,
                    "detail": "xAI TTS reachable — /v1/tts/voices OK",
                }
        except Exception as exc:
            latency_ms = int((time.time() - t) * 1000)
            return {"ok": False, "latency_ms": latency_ms, "detail": str(exc)}

    def get_info(self) -> dict:
        return {
            "name": "Grok / xAI TTS",
            "provider_id": "grok",
            "status": self._status if self.api_key else "error",
            "description": "xAI Text-to-Speech (Grok voices) — cloud MP3 TTS with speech tags",
            "quality": "high",
            "latency": "fast",
            "cost_per_minute": 0.0,  # operators: check console.x.ai for current pricing
            "voices": DOCUMENTED_VOICES.copy(),
            "features": [
                "cloud",
                "multilingual",
                "speech-tags",
                "mp3-output",
                "custom-voices-api",
                "agent-friendly-defaults",
                "streaming-websocket",
                "ttfa-optimize-streaming-latency",
            ],
            "requires_api_key": True,
            "languages": [
                "en",
                "zh",
                "fr",
                "de",
                "hi",
                "id",
                "it",
                "ja",
                "ko",
                "pt-BR",
                "pt-PT",
                "ru",
                "es-MX",
                "es-ES",
                "tr",
                "vi",
                "bn",
                "ar-EG",
                "ar-SA",
                "ar-AE",
                "auto",
            ],
            "max_characters": MAX_CHARACTERS,
            "notes": (
                "XAI_API_KEY required. Default voice_id=eve. "
                + AGENT_VOICE_PREFERENCE_NOTES
            ),
            "default_voice": "eve",
            "audio_format": "mp3",
            "sample_rate": 24000,
            "documentation_url": (
                "https://docs.x.ai/developers/model-capabilities/audio/text-to-speech"
            ),
            "error": self._init_error,
            "agent_voice_preference": AGENT_VOICE_PREFERENCE_NOTES,
        }


__all__ = [
    "GrokProvider",
    "DOCUMENTED_VOICES",
    "AGENT_VOICE_PREFERENCE_NOTES",
    "TTS_URL",
    "TTS_WS_URL",
]
