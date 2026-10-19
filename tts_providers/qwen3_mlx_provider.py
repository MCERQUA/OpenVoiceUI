"""
Qwen3-TTS over HTTP on an Apple-silicon (MLX) node — provider id ``qwen3-mlx``.

Talks to a small self-hosted server that runs Qwen3-TTS under mlx-audio and
speaks reference-cloned voices:

    GET  <url>/health      -> {"ok": true, "model": "...", "voices": [...]}
    POST <url>/tts/stream  {"text": "<one sentence>", "voice": "<id>", "chunk": 6}
                           -> 200, Content-Type audio/pcm, raw s16le mono PCM,
                              X-Sample-Rate header (e.g. 24000)

What this provider guarantees:
  * ONE request per SENTENCE. A long generation drifts off the reference
    voice, so text is split on sentence boundaries; fragments shorter than
    MIN_SENTENCE_CHARS merge with a neighbour. The PCM of every sentence is
    stitched into ONE WAV, which is what the rest of the pipeline expects.
  * ONE request in flight per process (module-level lock). The server's
    concurrency is not something this side can assume.
  * VOICE ALLOW-LIST, fail closed. Only ids named in QWEN_MLX_TTS_VOICES are
    offered or spoken, minus any id matching QWEN_MLX_TTS_DENY_PREFIXES.
    Unset allow-list = no voice is allowed. A cloned voice of a real person
    must never be reachable just because the server happens to carry it.
  * CLEAR FAILURES. Unconfigured, unreachable, non-200, non-PCM, empty or
    refused-voice all raise, so services/tts.py's retry + fallback chain
    (config/tts-fallback.json: qwen3-mlx -> groq -> supertonic) takes over.

Configuration (environment):
  QWEN_MLX_TTS_URL            base URL of the server (required; unset = inactive)
  QWEN_MLX_TTS_VOICES         comma-separated allowed voice ids (required)
  QWEN_MLX_TTS_DENY_PREFIXES  comma-separated id prefixes that are never allowed
  QWEN_MLX_TTS_DEFAULT_VOICE  default voice (must be allowed; else first allowed)
  QWEN_MLX_TTS_CHUNK          server "chunk" steps (default 6)
  QWEN_MLX_TTS_CALLER         X-Caller header (default "openvoiceui")
  QWEN_MLX_TTS_PURPOSE        X-Purpose header (default "voice")
  QWEN_MLX_TTS_READ_TIMEOUT   per-sentence read timeout seconds (default 20)
  QWEN_MLX_TTS_LOCK_TIMEOUT   max seconds to wait for the in-flight slot (default 30)
"""

from __future__ import annotations

import logging
import os
import re
import struct
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from .base_provider import (
    TTSProvider,
    TTSConfigurationError,
    TTSGenerationError,
    TTSVoiceNotFoundError,
)

logger = logging.getLogger(__name__)

PROVIDER_ID = "qwen3-mlx"
MIN_SENTENCE_CHARS = 40

_HEALTH_TTL_OK = 30.0     # seconds a good health reading is trusted
_HEALTH_TTL_BAD = 10.0    # seconds a bad reading short-circuits (fast fallback)
_HEALTH: Dict[str, Any] = {"ok": None, "checked_at": 0.0, "voices": [], "model": None,
                           "error": None, "url": None}
_HEALTH_LOCK = threading.Lock()

# One request in flight per process: parallel sentence threads queue here.
_INFLIGHT = threading.Lock()


# ── configuration helpers (read live so an env change in tests is honoured) ──

def _csv_env(name: str) -> List[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


def _base_url() -> str:
    return os.getenv("QWEN_MLX_TTS_URL", "").strip().rstrip("/")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def voice_allowed(voice: Optional[str]) -> bool:
    """True only if `voice` is on the allow-list and matches no deny prefix."""
    if not voice:
        return False
    if any(voice.startswith(p) for p in _csv_env("QWEN_MLX_TTS_DENY_PREFIXES")):
        return False
    return voice in _csv_env("QWEN_MLX_TTS_VOICES")


def split_sentences(text: str, min_chars: int = MIN_SENTENCE_CHARS) -> List[str]:
    """Split on sentence boundaries; merge fragments shorter than min_chars.

    A short fragment joins the FOLLOWING sentence; a short tail joins the
    previous one. Text with no boundary comes back as a single item.
    """
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text.strip()) if p.strip()]
    out: List[str] = []
    carry = ""
    for p in parts:
        cur = (carry + " " + p).strip() if carry else p
        if len(cur) < min_chars:
            carry = cur
            continue
        out.append(cur)
        carry = ""
    if carry:
        if out:
            out[-1] = out[-1] + " " + carry
        else:
            out.append(carry)
    return out


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw little-endian PCM in a canonical 44-byte RIFF/WAVE header."""
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    return (struct.pack('<4sI4s', b'RIFF', 36 + len(pcm), b'WAVE')
            + struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, channels, sample_rate,
                          byte_rate, block_align, bits)
            + struct.pack('<4sI', b'data', len(pcm))
            + pcm)


def _check_health(force: bool = False) -> bool:
    """GET /health with a short cache. Records the server's voice list."""
    url = _base_url()
    now = time.time()
    with _HEALTH_LOCK:
        ttl = _HEALTH_TTL_OK if _HEALTH["ok"] else _HEALTH_TTL_BAD
        if (not force and _HEALTH["ok"] is not None and _HEALTH["url"] == url
                and now - _HEALTH["checked_at"] < ttl):
            return bool(_HEALTH["ok"])
    ok, voices, model, err = False, [], None, None
    if not url:
        err = "QWEN_MLX_TTS_URL not set"
    else:
        try:
            with httpx.Client(timeout=httpx.Timeout(3.0)) as c:
                r = c.get(f"{url}/health")
            if r.status_code != 200:
                err = f"health HTTP {r.status_code}"
            else:
                body = r.json()
                ok = bool(body.get("ok"))
                voices = [v for v in (body.get("voices") or []) if isinstance(v, str)]
                model = body.get("model")
                if not ok:
                    err = "server reports ok=false"
        except Exception as e:  # unreachable, timeout, bad JSON
            err = f"unreachable: {type(e).__name__}"
    with _HEALTH_LOCK:
        _HEALTH.update(ok=ok, checked_at=time.time(), voices=voices, model=model,
                       error=err, url=url)
    return ok


def _mark_unhealthy(reason: str) -> None:
    with _HEALTH_LOCK:
        _HEALTH.update(ok=False, checked_at=time.time(), error=reason)


class Qwen3MlxProvider(TTSProvider):
    """Qwen3-TTS on an MLX node over HTTP — one sentence per request, WAV out."""

    def __init__(self):
        super().__init__()
        self._status = "active" if _base_url() else "inactive"

    # ── synthesis ────────────────────────────────────────────────────────────

    def _synth_sentence(self, url: str, sentence: str, voice: str) -> tuple:
        """POST one sentence. Returns (pcm_bytes, sample_rate, first_byte_ms)."""
        headers = {
            "Content-Type": "application/json",
            "X-Caller": os.getenv("QWEN_MLX_TTS_CALLER", "openvoiceui"),
            "X-Purpose": os.getenv("QWEN_MLX_TTS_PURPOSE", "voice"),
        }
        try:
            chunk = int(os.getenv("QWEN_MLX_TTS_CHUNK", "6"))
        except ValueError:
            chunk = 6
        payload = {"text": sentence, "voice": voice, "chunk": chunk}
        timeout = httpx.Timeout(connect=3.0, read=_float_env("QWEN_MLX_TTS_READ_TIMEOUT", 20.0),
                                write=5.0, pool=3.0)
        t0 = time.time()
        first_ms = None
        buf = bytearray()
        try:
            with httpx.Client(timeout=timeout) as c:
                with c.stream("POST", f"{url}/tts/stream", json=payload, headers=headers) as r:
                    ctype = r.headers.get("content-type", "")
                    if r.status_code != 200:
                        r.read()
                        raise TTSGenerationError(
                            PROVIDER_ID, f"TTS error {r.status_code}: {r.text[:200]}")
                    if not ctype.startswith("audio/pcm"):
                        raise TTSGenerationError(
                            PROVIDER_ID, f"unexpected content-type {ctype!r} (want audio/pcm)")
                    try:
                        rate = int(r.headers.get("x-sample-rate", "24000"))
                    except ValueError:
                        raise TTSGenerationError(PROVIDER_ID, "bad X-Sample-Rate header")
                    for part in r.iter_bytes():
                        if part and first_ms is None:
                            first_ms = int((time.time() - t0) * 1000)
                        buf.extend(part)
        except TTSGenerationError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            _mark_unhealthy(f"connect failed: {type(e).__name__}")
            raise TTSGenerationError(PROVIDER_ID, f"server unreachable: {type(e).__name__}") from e
        except httpx.HTTPError as e:
            raise TTSGenerationError(PROVIDER_ID, f"request failed: {type(e).__name__}: {e}") from e
        if not buf:
            raise TTSGenerationError(PROVIDER_ID, "empty audio response")
        if len(buf) % 2:
            buf = buf[:-1]  # s16le: drop a dangling half-sample
        return bytes(buf), rate, first_ms

    def generate_speech(self, text: str, voice: Optional[str] = None, **kwargs) -> bytes:
        self.validate_text(text)
        url = _base_url()
        if not url:
            raise TTSConfigurationError(PROVIDER_ID, "QWEN_MLX_TTS_URL not set")
        voice = voice or self.get_default_voice()
        if not voice_allowed(voice):
            raise TTSVoiceNotFoundError(
                PROVIDER_ID, f"voice {voice!r} is not on the allow-list (QWEN_MLX_TTS_VOICES)")
        if not _check_health():
            raise TTSGenerationError(PROVIDER_ID, f"server unavailable ({_HEALTH.get('error')})")

        sentences = split_sentences(text)
        lock_timeout = _float_env("QWEN_MLX_TTS_LOCK_TIMEOUT", 30.0)
        if not _INFLIGHT.acquire(timeout=lock_timeout):
            raise TTSGenerationError(PROVIDER_ID, f"busy: no slot within {lock_timeout:.0f}s")
        try:
            t0 = time.time()
            pcm = bytearray()
            rate = None
            first_audio_ms = None
            for s in sentences:
                part, r, first_ms = self._synth_sentence(url, s, voice)
                if rate is None:
                    rate = r
                    first_audio_ms = first_ms  # time to the first PCM byte of the reply
                elif r != rate:
                    raise TTSGenerationError(
                        PROVIDER_ID, f"sample rate changed mid-reply ({rate} -> {r})")
                pcm.extend(part)
        finally:
            _INFLIGHT.release()
        total_ms = int((time.time() - t0) * 1000)
        logger.info(f"[Qwen3Mlx] TTS voice={voice} sentences={len(sentences)} "
                    f"chars={len(text)} pcm={len(pcm)}B rate={rate} "
                    f"first_byte={first_audio_ms}ms total={total_ms}ms")
        return pcm_to_wav(bytes(pcm), rate or 24000)

    # ── provider interface ───────────────────────────────────────────────────

    def list_voices(self) -> List[str]:
        """Allowed voices; intersected with the server's list when it is known."""
        allowed = [v for v in _csv_env("QWEN_MLX_TTS_VOICES") if voice_allowed(v)]
        server = _HEALTH.get("voices") or []
        if _HEALTH.get("ok") and server:
            allowed = [v for v in allowed if v in server]
        return allowed

    def get_default_voice(self) -> Optional[str]:
        d = os.getenv("QWEN_MLX_TTS_DEFAULT_VOICE", "").strip()
        if d and voice_allowed(d):
            return d
        voices = self.list_voices()
        return voices[0] if voices else None

    def is_available(self) -> bool:
        return bool(_base_url()) and bool(self.list_voices()) and _check_health()

    def health_check(self) -> dict:
        t = time.time()
        ok = _check_health(force=True)
        return {"ok": ok, "latency_ms": int((time.time() - t) * 1000),
                "detail": _HEALTH.get("model") if ok else _HEALTH.get("error")}

    def get_info(self) -> Dict[str, Any]:
        configured = bool(_base_url())
        ok = _check_health() if configured else False
        if not configured:
            status = "inactive"
        else:
            status = "active" if ok else "offline"
        self._status = status
        return {
            "name": "Qwen3-TTS (MLX node)",
            "provider_id": PROVIDER_ID,
            "status": status,
            "description": ("Qwen3-TTS on an Apple-silicon node over HTTP. One request per "
                            "sentence, allow-listed reference voices, WAV output."),
            "quality": "very-high",
            "latency": "fast",
            "cost_per_minute": 0.0,
            "voices": self.list_voices(),
            "features": ["voice-cloning", "self-hosted", "free", "wav-output"],
            "requires_api_key": False,
            "audio_format": "wav",
            "sample_rate": 24000,
            "model": _HEALTH.get("model"),
            "available": ok,
            "error": None if ok else (_HEALTH.get("error") or
                                      ("QWEN_MLX_TTS_URL not set" if not configured else None)),
        }
