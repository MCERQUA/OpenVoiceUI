"""
Qwen3-TTS Local Provider — GPU inference on residential-laptop via reverse SSH tunnel.

Hits 127.0.0.1:8096 (raw Qwen3-TTS Mesh API) for synthesis and voice creation.
Checks 127.0.0.1:18791/health for availability — auto-disabled when laptop is off.

Falls back silently to qwen3 (fal.ai) provider when unavailable.

Voice clones stored as .pt files under VOICE_CLONES_DIR/qwen3-local/.
"""

import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from .base_provider import TTSProvider

logger = logging.getLogger(__name__)

RUNNER_URL = "http://127.0.0.1:18791"
API_URL    = os.getenv("QWEN_TTS_LOCAL_URL", "http://127.0.0.1:8096")

BUILTIN_VOICES = [
    "Vivian", "Serena", "Dylan", "Eric",
    "Ryan", "Aiden", "Uncle_Fu", "Ono_Anna", "Sohee",
]

# Cache availability so we don't health-check on every request
_AVAILABLE_CACHE: dict = {"ok": None, "checked_at": 0.0}
_CACHE_TTL = 30.0  # seconds


def _get_auth_token() -> str:
    return os.getenv("OVUI_BRIDGE_AUTH_TOKEN", "")


def _get_clones_dir() -> Path:
    try:
        from services.paths import VOICE_CLONES_DIR
        base = VOICE_CLONES_DIR
    except ImportError:
        base = Path(os.getenv("VOICE_CLONES_DIR", "./runtime/voice-clones"))
    d = base / "qwen3-local"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_available() -> bool:
    now = time.time()
    if now - _AVAILABLE_CACHE["checked_at"] < _CACHE_TTL and _AVAILABLE_CACHE["ok"] is not None:
        return _AVAILABLE_CACHE["ok"]
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.get(f"{API_URL}/health")
            ok = r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        ok = False
    _AVAILABLE_CACHE["ok"] = ok
    _AVAILABLE_CACHE["checked_at"] = now
    return ok


class Qwen3LocalProvider(TTSProvider):
    """
    Qwen3-TTS running on the residential-laptop RTX 3070 GPU.
    Free, zero API cost. Only available when laptop is on and tunnel is up.
    """

    def __init__(self):
        super().__init__()
        self._token = _get_auth_token()
        self._status = "active" if self._token else "error"
        self._init_error = None if self._token else "OVUI_BRIDGE_AUTH_TOKEN not set"

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, audio_path: str = None, audio_bytes: bytes = None,
                    audio_url: str = None, name: str = "",
                    reference_text: str = "") -> dict:
        """
        Clone a voice. Accepts audio_path (local file), audio_bytes, or audio_url.
        Returns dict with voice_id, name, pt_path, created_at, clone_time_ms.
        """
        if not _is_available():
            raise RuntimeError("qwen3-local unavailable — laptop offline or tunnel down")
        if not self._token:
            raise RuntimeError("OVUI_BRIDGE_AUTH_TOKEN not set")

        # Resolve audio bytes
        if audio_bytes is None:
            if audio_path:
                audio_bytes = Path(audio_path).read_bytes()
            elif audio_url:
                with httpx.Client(timeout=30.0) as c:
                    audio_bytes = c.get(audio_url).content
            else:
                raise ValueError("One of audio_path, audio_bytes, or audio_url required")

        filename = Path(audio_path).name if audio_path else "reference.wav"

        t = time.time()
        with httpx.Client(timeout=120.0) as c:
            resp = c.post(
                f"{API_URL}/create-voice",
                headers=self._auth_headers(),
                files={"audio": (filename, io.BytesIO(audio_bytes))},
                data={"transcript": reference_text or ""},
            )
            resp.raise_for_status()

        pt_bytes = resp.content
        elapsed_ms = int((time.time() - t) * 1000)

        voice_id = "clone_" + "".join(
            ch for ch in name.lower().replace(" ", "_") if ch.isalnum() or ch == "_"
        )[:40]
        voice_dir = _get_clones_dir() / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)

        pt_path = voice_dir / "voice.pt"
        pt_path.write_bytes(pt_bytes)

        import json
        meta = {
            "voice_id": voice_id,
            "name": name,
            "reference_text": reference_text,
            "pt_path": str(pt_path),
            "pt_size": len(pt_bytes),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "clone_time_ms": elapsed_ms,
            "provider": "qwen3-local",
        }
        (voice_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

        logger.info(f"[Qwen3Local] Voice cloned: {voice_id} ({len(pt_bytes)}B) in {elapsed_ms}ms")
        return meta

    def list_cloned_voices(self) -> list:
        import json
        voices = []
        clones_dir = _get_clones_dir()
        for voice_dir in sorted(clones_dir.iterdir()):
            meta_path = voice_dir / "metadata.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    meta["has_pt"] = (voice_dir / "voice.pt").exists()
                    voices.append(meta)
                except Exception as e:
                    logger.warning(f"[Qwen3Local] Bad metadata in {voice_dir}: {e}")
        return voices

    def _get_voice_pt(self, voice_id: str) -> Optional[bytes]:
        pt_path = _get_clones_dir() / voice_id / "voice.pt"
        if pt_path.exists():
            return pt_path.read_bytes()
        return None

    # ------------------------------------------------------------------
    # Speech generation
    # ------------------------------------------------------------------

    def generate_speech(self, text: str, voice: str = "Vivian", **kwargs) -> bytes:
        if not _is_available():
            raise RuntimeError("qwen3-local unavailable — laptop offline or tunnel down")
        if not self._token:
            raise RuntimeError("OVUI_BRIDGE_AUTH_TOKEN not set")

        self.validate_text(text)
        language = kwargs.get("language", "auto")

        is_cloned = voice and voice.startswith("clone_")

        if is_cloned:
            pt_bytes = self._get_voice_pt(voice)
            if not pt_bytes:
                raise RuntimeError(f"Cloned voice '{voice}' not found — .pt file missing")

            t = time.time()
            with httpx.Client(timeout=120.0) as c:
                resp = c.post(
                    f"{API_URL}/tts",
                    headers=self._auth_headers(),
                    files={"voice_pt": ("voice.pt", io.BytesIO(pt_bytes))},
                    data={"text": text, "language": language},
                )
                resp.raise_for_status()
        else:
            # Built-in voice: use tts-raw with no reference audio — not supported
            # by this API. Fall back: use tts-raw with empty audio placeholder
            # or raise so caller falls back to fal.ai.
            raise RuntimeError(
                f"qwen3-local only supports cloned voices (.pt). "
                f"Built-in voice '{voice}' requires fal.ai — use qwen3 provider instead."
            )

        audio_bytes = resp.content
        elapsed_ms = int((time.time() - t) * 1000)
        logger.info(f"[Qwen3Local] TTS: {len(audio_bytes)}B in {elapsed_ms}ms voice={voice}")
        return audio_bytes

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        _AVAILABLE_CACHE["checked_at"] = 0.0  # force re-check
        t = time.time()
        ok = _is_available()
        latency_ms = int((time.time() - t) * 1000)
        if ok:
            return {"ok": True, "latency_ms": latency_ms,
                    "detail": "RTX 3070 GPU — Qwen3-TTS-1.7B ready"}
        return {"ok": False, "latency_ms": latency_ms,
                "detail": "Laptop offline or tunnel down"}

    def list_voices(self) -> list:
        cloned = [v["voice_id"] for v in self.list_cloned_voices()]
        return cloned

    def get_default_voice(self) -> str:
        cloned = self.list_cloned_voices()
        return cloned[0]["voice_id"] if cloned else "clone_default"

    def is_available(self) -> bool:
        return bool(self._token) and _is_available()

    def get_info(self) -> dict:
        available = _is_available()
        cloned = self.list_cloned_voices() if available else []
        return {
            "name": "Qwen3-TTS (local GPU)",
            "provider_id": "qwen3-local",
            "status": "active" if available else "offline",
            "description": (
                "Qwen3-TTS-1.7B on residential RTX 3070 GPU — free, no API cost. "
                "Cloned voices only. Requires laptop + tunnel to be up."
            ),
            "quality": "very-high",
            "latency": "fast",
            "cost_per_minute": 0.0,
            "voices": [v["voice_id"] for v in cloned],
            "cloned_voices": [
                {"voice_id": v["voice_id"], "name": v["name"]} for v in cloned
            ],
            "features": [
                "voice-cloning", "multilingual", "local-gpu",
                "free", "mp3-output", "no-api-key",
            ],
            "requires_api_key": False,
            "gpu": "RTX 3070 (8GB VRAM)",
            "model": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
            "tunnel_url": API_URL,
            "available": available,
            "error": None if available else "Laptop offline or SSH tunnel down",
        }
