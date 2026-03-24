"""
ElevenLabs TTS Provider — Instant voice cloning and speech generation.

Supports:
  - Instant voice cloning from audio samples (IVC)
  - High-quality multilingual text-to-speech
  - Multiple models: eleven_multilingual_v2, eleven_turbo_v2_5
  - Voice stability and similarity controls

API key: ELEVENLABS_API_KEY env var
API base: https://api.elevenlabs.io/v1
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

from .base_provider import TTSProvider

logger = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io/v1"

CLONE_TIMEOUT = 60.0
GENERATE_TIMEOUT = 30.0
API_TIMEOUT = 15.0

# Content type map for audio files
_AUDIO_CONTENT_TYPES = {
    '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4', '.ogg': 'audio/ogg',
    '.webm': 'audio/webm', '.flac': 'audio/flac',
}


class ElevenLabsProvider(TTSProvider):
    """
    TTS Provider using ElevenLabs API.

    Supports instant voice cloning and high-quality TTS.
    Output: MP3 audio bytes (default) or other formats via output_format param.
    """

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv('ELEVENLABS_API_KEY', '')
        self._status = 'active' if self.api_key else 'error'
        self._init_error = None if self.api_key else 'ELEVENLABS_API_KEY not set'
        self._voices_cache = None
        self._voices_cache_time = 0

    def _headers(self):
        return {'xi-api-key': self.api_key}

    def _json_headers(self):
        return {'xi-api-key': self.api_key, 'Content-Type': 'application/json'}

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, audio_path: str, name: str,
                    description: str = '', **kwargs) -> dict:
        """
        Clone a voice using ElevenLabs instant voice cloning.

        Args:
            audio_path: Local path to audio file (WAV, MP3, M4A, etc.).
            name: Human-readable name for the cloned voice.
            description: Optional description of the voice.

        Returns:
            dict with: voice_id, name, provider, created_at, clone_time_ms
        """
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")

        t = time.time()
        logger.info(f"[ElevenLabs] Cloning voice '{name}' from {audio_path}")

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise RuntimeError(f"Audio file not found: {audio_path}")

        with open(audio_file, 'rb') as f:
            audio_bytes = f.read()

        ct = _AUDIO_CONTENT_TYPES.get(audio_file.suffix.lower(), 'audio/mpeg')

        try:
            with httpx.Client(
                timeout=httpx.Timeout(CLONE_TIMEOUT, connect=10.0)
            ) as client:
                resp = client.post(
                    f"{API_BASE}/voices/add",
                    headers=self._headers(),
                    data={
                        'name': name,
                        'description': description or f'Cloned voice: {name}',
                    },
                    files={
                        'files': (audio_file.name, audio_bytes, ct),
                    },
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            raise RuntimeError(
                f"ElevenLabs clone error {e.response.status_code}: {body}"
            )
        except httpx.TimeoutException:
            raise RuntimeError(f"ElevenLabs clone timeout after {CLONE_TIMEOUT}s")

        voice_id = result.get('voice_id', '')
        if not voice_id:
            raise RuntimeError(f"No voice_id in ElevenLabs response: {result}")

        elapsed_ms = int((time.time() - t) * 1000)
        logger.info(f"[ElevenLabs] Voice cloned: {voice_id} in {elapsed_ms}ms")

        # Invalidate cache so new voice appears in lists
        self._voices_cache = None
        self._voices_cache_time = 0

        return {
            'voice_id': voice_id,
            'name': name,
            'provider': 'elevenlabs',
            'created_at': time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            'clone_time_ms': elapsed_ms,
        }

    # ------------------------------------------------------------------
    # Speech generation
    # ------------------------------------------------------------------

    def generate_speech(self, text: str, voice: str = '', **kwargs) -> bytes:
        """
        Generate speech via ElevenLabs TTS.

        Args:
            text: Text to synthesize (max 5000 chars).
            voice: ElevenLabs voice ID. Falls back to ELEVENLABS_VOICE_ID env.
            **kwargs:
                model_id: 'eleven_multilingual_v2' (default) or 'eleven_turbo_v2_5'
                stability: 0.0-1.0 (default 0.5)
                similarity_boost: 0.0-1.0 (default 0.75)
                style: 0.0-1.0 style exaggeration (default 0.0)
                output_format: 'mp3_44100_128' (default)

        Returns:
            MP3 audio bytes.
        """
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")

        self.validate_text(text)

        if not voice:
            voice = self.get_default_voice()
        if not voice:
            raise RuntimeError(
                "No voice specified and ELEVENLABS_VOICE_ID not set"
            )

        model_id = kwargs.get('model_id', 'eleven_multilingual_v2')
        stability = kwargs.get('stability', 0.5)
        similarity_boost = kwargs.get('similarity_boost', 0.75)
        style = kwargs.get('style', 0.0)
        output_format = kwargs.get('output_format', 'mp3_44100_128')

        payload = {
            'text': text[:5000],
            'model_id': model_id,
            'voice_settings': {
                'stability': stability,
                'similarity_boost': similarity_boost,
                'style': style,
            },
        }

        t = time.time()
        logger.info(f"[ElevenLabs] TTS: '{text[:60]}...' voice={voice[:20]}")

        try:
            with httpx.Client(
                timeout=httpx.Timeout(GENERATE_TIMEOUT, connect=10.0)
            ) as client:
                resp = client.post(
                    f"{API_BASE}/text-to-speech/{voice}",
                    json=payload,
                    headers=self._json_headers(),
                    params={'output_format': output_format},
                )
                resp.raise_for_status()
                audio_bytes = resp.content
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"ElevenLabs TTS error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )
        except httpx.TimeoutException:
            raise RuntimeError(f"ElevenLabs TTS timeout after {GENERATE_TIMEOUT}s")

        elapsed = int((time.time() - t) * 1000)
        logger.info(f"[ElevenLabs] Generated {len(audio_bytes)} bytes in {elapsed}ms")

        if len(audio_bytes) < 100:
            raise RuntimeError(
                f"ElevenLabs returned suspiciously small response "
                f"({len(audio_bytes)} bytes)"
            )

        return audio_bytes

    # ------------------------------------------------------------------
    # Voice listing (cached)
    # ------------------------------------------------------------------

    def _fetch_voices(self) -> list:
        """Fetch voices from ElevenLabs API. Cached for 5 minutes."""
        now = time.time()
        if self._voices_cache and (now - self._voices_cache_time) < 300:
            return self._voices_cache

        try:
            with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT)) as client:
                resp = client.get(
                    f"{API_BASE}/voices",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            voices = []
            for v in data.get('voices', []):
                voices.append({
                    'id': v.get('voice_id', ''),
                    'name': v.get('name', ''),
                    'category': v.get('category', ''),
                    'labels': v.get('labels', {}),
                })
            self._voices_cache = voices
            self._voices_cache_time = now
            logger.info(f"[ElevenLabs] Fetched {len(voices)} voices from API")
            return voices
        except Exception as e:
            logger.warning(f"[ElevenLabs] Failed to fetch voices: {e}")
            return self._voices_cache or []

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        if not self.api_key:
            return {
                "ok": False, "latency_ms": 0,
                "detail": "ELEVENLABS_API_KEY not set",
            }
        t = time.time()
        try:
            with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT)) as client:
                resp = client.get(
                    f"{API_BASE}/user",
                    headers=self._headers(),
                )
                resp.raise_for_status()
            latency_ms = int((time.time() - t) * 1000)
            return {
                "ok": True, "latency_ms": latency_ms,
                "detail": "ElevenLabs API reachable",
            }
        except Exception as e:
            latency_ms = int((time.time() - t) * 1000)
            return {"ok": False, "latency_ms": latency_ms, "detail": str(e)}

    def list_voices(self) -> list:
        voices = self._fetch_voices()
        return [v['id'] for v in voices]

    def get_default_voice(self) -> str:
        return os.getenv('ELEVENLABS_VOICE_ID', '')

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> dict:
        voices = self._fetch_voices() if self.api_key else []
        cloned = [v for v in voices if v.get('category') == 'cloned']
        builtin = [v for v in voices if v.get('category') != 'cloned']
        return {
            'name': 'ElevenLabs',
            'provider_id': 'elevenlabs',
            'status': self._status,
            'description': (
                'ElevenLabs — premium TTS, instant voice cloning, '
                'multilingual, emotion control'
            ),
            'quality': 'premium',
            'latency': 'fast',
            'cost_per_minute': 0.30,
            'voices': [v['name'] for v in builtin],
            'cloned_voices': [
                {"voice_id": v['id'], "name": v['name']}
                for v in cloned
            ],
            'features': [
                'voice-cloning', 'multilingual', 'streaming',
                'emotion-control', 'cloud', 'mp3-output',
            ],
            'requires_api_key': True,
            'languages': [
                'en', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko',
                'zh', 'ar', 'ru', 'hi', 'pl', 'tr', 'nl', 'sv',
            ],
            'max_characters': 5000,
            'notes': (
                'Premium TTS with instant voice cloning. '
                'Models: eleven_multilingual_v2, eleven_turbo_v2_5. '
                'ELEVENLABS_API_KEY required.'
            ),
            'default_voice': self.get_default_voice(),
            'audio_format': 'mp3',
            'sample_rate': 44100,
            'error': self._init_error,
        }
