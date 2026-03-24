"""
Qwen3-TTS Provider — fal.ai hosted Qwen3-TTS models.

Supports:
  - Named speaker TTS (0.6B and 1.7B)
  - Voice cloning from audio samples via clone-voice endpoint
  - Emotion/style control via prompt (1.7B)
  - Cloned voice embeddings stored locally for reuse

API key: FAL_KEY env var
"""

import json
import os
import re
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

from .base_provider import TTSProvider

logger = logging.getLogger(__name__)

# fal.ai endpoints
FAL_TTS_1_7B = "https://fal.run/fal-ai/qwen-3-tts/text-to-speech/1.7b"
FAL_TTS_0_6B = "https://fal.run/fal-ai/qwen-3-tts/text-to-speech/0.6b"
FAL_CLONE_1_7B = "https://fal.run/fal-ai/qwen-3-tts/clone-voice/1.7b"
FAL_CLONE_0_6B = "https://fal.run/fal-ai/qwen-3-tts/clone-voice/0.6b"

BUILTIN_VOICES = [
    "Vivian",    # Female, warm
    "Serena",    # Female, clear
    "Dylan",     # Male, casual
    "Eric",      # Male, professional
    "Ryan",      # Male, energetic
    "Aiden",     # Male, deep
    "Uncle_Fu",  # Male, character
    "Ono_Anna",  # Female, Japanese accent
    "Sohee",     # Female, Korean accent
]


def _get_clones_dir() -> Path:
    """Resolve voice clones directory from paths module or fallback."""
    try:
        from services.paths import VOICE_CLONES_DIR
        return VOICE_CLONES_DIR
    except ImportError:
        return Path(os.getenv("VOICE_CLONES_DIR", "./runtime/voice-clones"))


def _fal_request(api_key: str, endpoint: str, payload: dict,
                 timeout: float = 90.0) -> dict:
    """Make a JSON request to fal.ai and return the parsed response."""
    headers = {
        'Authorization': f'Key {api_key}',
        'Content-Type': 'application/json',
    }
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _fal_download(url: str, timeout: float = 30.0) -> bytes:
    """Download binary content from a fal.ai result URL."""
    with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


class Qwen3Provider(TTSProvider):
    """
    TTS Provider using Qwen3-TTS via fal.ai.

    Built-in voices: Vivian, Serena, Dylan, Eric, Ryan, Aiden, Uncle_Fu, Ono_Anna, Sohee
    Cloned voices: stored locally as .safetensors embeddings, referenced by voice_id
    Output: MP3 audio bytes
    """

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv('FAL_KEY', '')
        self._status = 'active' if self.api_key else 'error'
        self._init_error = None if self.api_key else 'FAL_KEY not set in environment'

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, audio_url: str, name: str,
                    reference_text: Optional[str] = None) -> dict:
        """
        Clone a voice from a reference audio sample.

        Args:
            audio_url: Public URL to reference audio (WAV/MP3, 3+ seconds).
            name: Human-readable name for this cloned voice.
            reference_text: Optional transcript of what's said in the audio
                            (improves quality).

        Returns:
            dict with: voice_id, name, embedding_url, created_at, metadata
        """
        if not self.api_key:
            raise RuntimeError("FAL_KEY not set — cannot clone voice")

        t = time.time()
        logger.info(f"[Qwen3] Cloning voice '{name}' from {audio_url[:80]}")

        payload = {"audio_url": audio_url}
        if reference_text:
            payload["reference_text"] = reference_text

        try:
            result = _fal_request(self.api_key, FAL_CLONE_1_7B, payload,
                                  timeout=120.0)
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"fal.ai clone error {e.response.status_code}: {e.response.text}"
            )

        # Extract embedding URL from response
        embedding_url = result.get('speaker_embedding', {}).get('url')
        if not embedding_url:
            # Try alternate response shapes
            embedding_url = result.get('audio', {}).get('url')
        if not embedding_url:
            raise RuntimeError(f"No embedding URL in fal.ai response: {result}")

        elapsed_ms = int((time.time() - t) * 1000)

        # Download and persist the embedding locally
        embedding_bytes = _fal_download(embedding_url)

        clones_dir = _get_clones_dir()
        # voice_id = sanitized name
        voice_id = "clone_" + "".join(
            c for c in name.lower().replace(" ", "_")
            if c.isalnum() or c == "_"
        )[:40]
        voice_dir = clones_dir / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)

        embedding_path = voice_dir / "embedding.safetensors"
        with open(embedding_path, 'wb') as f:
            f.write(embedding_bytes)

        metadata = {
            "voice_id": voice_id,
            "name": name,
            "embedding_url": embedding_url,
            "embedding_size": len(embedding_bytes),
            "reference_text": reference_text,
            "source_audio_url": audio_url,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "clone_time_ms": elapsed_ms,
            "provider": "qwen3",
            "fal_response": result,
        }
        with open(voice_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info(
            f"[Qwen3] Voice cloned: {voice_id} ({len(embedding_bytes)} bytes) "
            f"in {elapsed_ms}ms"
        )
        return metadata

    def list_cloned_voices(self) -> list:
        """List all locally stored cloned voice embeddings."""
        clones_dir = _get_clones_dir()
        voices = []
        if not clones_dir.exists():
            return voices
        for voice_dir in sorted(clones_dir.iterdir()):
            meta_path = voice_dir / "metadata.json"
            if meta_path.exists():
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    meta["has_embedding"] = (voice_dir / "embedding.safetensors").exists()
                    voices.append(meta)
                except Exception as e:
                    logger.warning(f"Bad voice metadata in {voice_dir}: {e}")
        return voices

    def get_clone_embedding_url(self, voice_id: str) -> Optional[str]:
        """Get the fal.ai embedding URL for a cloned voice.

        Returns the cached remote URL from metadata. The embedding is also
        stored locally as a fallback, but fal.ai needs the URL for generation.
        """
        clones_dir = _get_clones_dir()
        meta_path = clones_dir / voice_id / "metadata.json"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            return meta.get("embedding_url")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Speech generation
    # ------------------------------------------------------------------

    def generate_speech(self, text: str, voice: str = 'Vivian', **kwargs) -> bytes:
        """
        Generate speech via fal.ai Qwen3-TTS.

        Args:
            text: Text to synthesize.
            voice: Built-in voice name OR cloned voice_id (clone_xxx).
            **kwargs:
                language: Language name (default 'English').
                prompt: Style/emotion instruction for 1.7B model.
                speaker_embedding_url: Direct embedding URL override.
                reference_text: Reference text for cloned voice quality.
                model: '0.6b' or '1.7b' (default '1.7b').

        Returns:
            MP3 audio bytes.
        """
        if not self.api_key:
            raise RuntimeError("FAL_KEY not set — cannot call fal.ai API")

        self.validate_text(text)

        language = kwargs.get('language', 'English')
        prompt = kwargs.get('prompt', '')
        embedding_url = kwargs.get('speaker_embedding_url')
        reference_text = kwargs.get('reference_text', '')
        model = kwargs.get('model', '1.7b')

        endpoint = FAL_TTS_1_7B if model == '1.7b' else FAL_TTS_0_6B

        # Resolve cloned voice → embedding URL
        is_cloned = voice.startswith("clone_") if voice else False
        if is_cloned and not embedding_url:
            embedding_url = self.get_clone_embedding_url(voice)
            if not embedding_url:
                raise RuntimeError(
                    f"Cloned voice '{voice}' not found or missing embedding"
                )
            # Load reference_text from metadata if not provided
            if not reference_text:
                clones_dir = _get_clones_dir()
                meta_path = clones_dir / voice / "metadata.json"
                if meta_path.exists():
                    try:
                        with open(meta_path) as f:
                            meta = json.load(f)
                        reference_text = meta.get("reference_text", "")
                    except Exception:
                        pass

        # ── Comic timing: add pauses at sentence boundaries ──
        # Ellipsis after sentence-ending punctuation creates natural pauses
        # in qwen3 TTS output without needing SSML.
        comic_timing = kwargs.get('comic_timing', True) if is_cloned else False
        if comic_timing:
            text = re.sub(r'([.!?])\s+', r'\1 ... ', text)

        # Scale max_new_tokens based on text length — default 200 truncates long responses.
        # ~4 tokens per character is a safe ratio for speech codec tokens.
        estimated_tokens = max(2000, len(text) * 4)
        max_tokens = min(estimated_tokens, 8192)

        payload = {
            "text": text,
            "language": language,
            "max_new_tokens": max_tokens,
        }

        if embedding_url:
            # Cloned voice — use embedding, skip built-in voice
            payload["speaker_voice_embedding_file_url"] = embedding_url
            if reference_text:
                payload["reference_text"] = reference_text
            if prompt:
                payload["prompt"] = prompt
        else:
            # Built-in voice
            if voice not in BUILTIN_VOICES:
                logger.warning(f"Unknown voice '{voice}', falling back to Vivian")
                voice = 'Vivian'
            payload["voice"] = voice
            if prompt:
                payload["prompt"] = prompt

        t = time.time()
        voice_label = voice if not is_cloned else f"{voice} (cloned)"
        logger.info(f"[Qwen3] TTS: '{text[:60]}...' voice={voice_label}")

        try:
            result = _fal_request(self.api_key, endpoint, payload)
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"fal.ai API error {e.response.status_code}: {e.response.text}"
            )
        except Exception as e:
            raise RuntimeError(f"fal.ai request failed: {e}")

        audio_url = result.get('audio', {}).get('url')
        if not audio_url:
            raise RuntimeError(f"No audio URL in fal.ai response: {result}")

        audio_bytes = _fal_download(audio_url)

        # ── Post-processing: speed adjustment via ffmpeg ──
        # Read speed from active profile if not passed directly
        speed = kwargs.get('speed', None)
        if speed is None:
            speed = self._get_profile_speed()
        if speed and speed != 1.0 and 0.5 <= speed <= 2.0:
            audio_bytes = self._ffmpeg_speed(audio_bytes, speed)

        elapsed = int((time.time() - t) * 1000)
        logger.info(f"[Qwen3] Generated {len(audio_bytes)} bytes in {elapsed}ms speed={speed}")
        return audio_bytes

    @staticmethod
    def _get_profile_speed():
        """Read speed from the active profile's voice settings."""
        try:
            from profiles.manager import get_profile_manager
            from routes.profiles import _active_profile_id
            mgr = get_profile_manager()
            prof = mgr.get_profile(_active_profile_id)
            if prof and hasattr(prof, 'voice') and prof.voice:
                return prof.voice.speed
        except Exception:
            pass
        return 1.0

    @staticmethod
    def _ffmpeg_speed(audio_bytes: bytes, speed: float) -> bytes:
        """Adjust audio speed using ffmpeg atempo filter."""
        try:
            cmd = [
                'ffmpeg', '-y', '-i', 'pipe:0',
                '-filter:a', f'atempo={speed}',
                '-f', 'mp3', 'pipe:1'
            ]
            proc = subprocess.run(
                cmd, input=audio_bytes, capture_output=True, timeout=30
            )
            if proc.returncode == 0 and proc.stdout:
                logger.info(
                    f"[Qwen3] ffmpeg speed={speed}: "
                    f"{len(audio_bytes)}→{len(proc.stdout)} bytes"
                )
                return proc.stdout
            else:
                logger.warning(f"[Qwen3] ffmpeg failed: {proc.stderr[:200]}")
        except Exception as e:
            logger.warning(f"[Qwen3] ffmpeg speed adjust error: {e}")
        return audio_bytes

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        if not self.api_key:
            return {"ok": False, "latency_ms": 0, "detail": "FAL_KEY not set"}
        t = time.time()
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0)) as client:
                resp = client.get(
                    "https://fal.run/",
                    headers={"Authorization": f"Key {self.api_key}"},
                )
            latency_ms = int((time.time() - t) * 1000)
            return {
                "ok": True, "latency_ms": latency_ms,
                "detail": "fal.ai reachable — Qwen3-TTS ready",
            }
        except Exception as e:
            latency_ms = int((time.time() - t) * 1000)
            return {"ok": False, "latency_ms": latency_ms, "detail": str(e)}

    def list_voices(self) -> list:
        voices = BUILTIN_VOICES.copy()
        for clone in self.list_cloned_voices():
            voices.append(clone["voice_id"])
        return voices

    def get_default_voice(self) -> str:
        return 'Vivian'

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> dict:
        cloned = self.list_cloned_voices()
        return {
            'name': 'Qwen3-TTS (fal.ai)',
            'provider_id': 'qwen3',
            'status': self._status,
            'description': (
                'Qwen3-TTS via fal.ai — expressive, multilingual, '
                'voice cloning, emotion control'
            ),
            'quality': 'very-high',
            'latency': 'fast',
            'cost_per_minute': 0.003,
            'voices': BUILTIN_VOICES.copy(),
            'cloned_voices': [
                {"voice_id": c["voice_id"], "name": c["name"]}
                for c in cloned
            ],
            'features': [
                'multilingual', 'expressive', 'voice-cloning',
                'emotion-control', 'cloud', 'mp3-output',
            ],
            'requires_api_key': True,
            'languages': ['en', 'zh', 'es', 'fr', 'de', 'it', 'ja', 'ko', 'pt', 'ru'],
            'max_characters': 5000,
            'notes': 'Qwen3-TTS 1.7B + 0.6B. Voice cloning via clone-voice endpoint. FAL_KEY required.',
            'default_voice': 'Vivian',
            'audio_format': 'mp3',
            'sample_rate': 24000,
            'error': self._init_error,
        }
