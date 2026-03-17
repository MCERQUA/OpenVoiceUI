"""
Resemble AI TTS Provider — Chatterbox models via Resemble API.

Supports:
  - HTTP streaming TTS (chunked WAV, progressive playback)
  - Multiple models: chatterbox (original), chatterbox-turbo, chatterbox-multilingual
  - Voice cloning via Resemble dashboard (voice_uuid per clone)
  - SSML support (prosody, emphasis, breaks, prompts)
  - Emotion/exaggeration control
  - 90+ languages (multilingual model)
  - 8-48kHz sample rate, PCM_16/24/32/MULAW

API key: RESEMBLE_API_KEY env var
Synthesis server: https://f.cluster.resemble.ai
API server: https://app.resemble.ai/api/v2
"""

import os
import io
import time
import logging

import httpx

from .base_provider import TTSProvider

logger = logging.getLogger(__name__)

# Resemble API endpoints
SYNTHESIS_URL = "https://f.cluster.resemble.ai/stream"
API_BASE_URL = "https://app.resemble.ai/api/v2"

# Models available via Resemble API
MODELS = {
    "chatterbox": "Default Chatterbox — emotion exaggeration + CFG control",
    "chatterbox-turbo": "Chatterbox Turbo — lowest latency, paralinguistic tags",
    "chatterbox-multilingual": "Chatterbox Multilingual — 23+ languages",
}

DEFAULT_MODEL = "chatterbox-turbo"

# Timeouts
STREAM_TIMEOUT = 30.0    # Max wait for full streaming response
CONNECT_TIMEOUT = 10.0   # TCP connect timeout
API_TIMEOUT = 15.0       # For voice listing / non-synthesis calls


class ResembleProvider(TTSProvider):
    """
    TTS Provider using Resemble AI's Chatterbox API.

    Uses HTTP streaming endpoint for progressive audio delivery.
    Voices are managed via Resemble dashboard — each voice has a UUID.

    Output: WAV audio bytes (PCM_16, configurable sample rate)
    Latency: sub-200ms time-to-first-byte (streaming)
    Cost: pay-as-you-go, character-based
    """

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv('RESEMBLE_API_KEY', '')
        self._status = 'active' if self.api_key else 'error'
        self._init_error = None if self.api_key else 'RESEMBLE_API_KEY not set'
        self._voices_cache = None
        self._voices_cache_time = 0

    def _auth_headers(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    # ------------------------------------------------------------------
    # Voice listing (cached from Resemble API)
    # ------------------------------------------------------------------

    def _fetch_voices_from_api(self) -> list:
        """Fetch available voices from Resemble API. Cached for 5 minutes."""
        now = time.time()
        if self._voices_cache and (now - self._voices_cache_time) < 300:
            return self._voices_cache

        try:
            voices = []
            page = 1
            with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT)) as client:
                while True:
                    resp = client.get(
                        f"{API_BASE_URL}/voices",
                        params={"page": page, "page_size": 50},
                        headers=self._auth_headers(),
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    for v in data.get('items', []):
                        if v.get('voice_status') == 'Ready':
                            voices.append({
                                'id': v.get('uuid', ''),
                                'name': v.get('name', 'Unknown'),
                                'language': v.get('default_language', 'en'),
                                'streaming': v.get('api_support', {}).get('streaming', False),
                            })

                    if page >= data.get('num_pages', 1):
                        break
                    page += 1

            self._voices_cache = voices
            self._voices_cache_time = now
            logger.info(f"[Resemble] Fetched {len(voices)} voices from API")
            return voices

        except Exception as e:
            logger.warning(f"[Resemble] Failed to fetch voices: {e}")
            return self._voices_cache or []

    # ------------------------------------------------------------------
    # Speech generation (HTTP streaming)
    # ------------------------------------------------------------------

    def generate_speech(self, text: str, voice: str = '', **kwargs) -> bytes:
        """
        Generate speech via Resemble streaming API.

        Args:
            text: Text or SSML to synthesize (max 2000 chars).
            voice: Resemble voice UUID. If empty, uses RESEMBLE_VOICE_UUID env var.
            **kwargs:
                model: 'chatterbox', 'chatterbox-turbo', or 'chatterbox-multilingual'
                sample_rate: 8000-48000 (default 24000)
                precision: 'PCM_16', 'PCM_24', 'PCM_32', 'MULAW' (default PCM_16)
                exaggeration: 0.0-1.0 emotion intensity (via SSML prompt attr)

        Returns:
            WAV audio bytes.
        """
        if not self.api_key:
            raise RuntimeError("RESEMBLE_API_KEY not set")

        self.validate_text(text)

        # Resolve voice UUID
        voice_uuid = voice or os.getenv('RESEMBLE_VOICE_UUID', '')
        if not voice_uuid:
            raise RuntimeError(
                "No voice_uuid provided and RESEMBLE_VOICE_UUID not set. "
                "Create a voice at app.resemble.ai and set the UUID."
            )

        model = kwargs.get('model', '')
        sample_rate = kwargs.get('sample_rate', 24000)
        precision = kwargs.get('precision', 'PCM_16')
        exaggeration = kwargs.get('exaggeration')

        # Wrap in SSML if exaggeration is set
        if exaggeration is not None and not text.strip().startswith('<speak'):
            text = f'<speak exaggeration="{exaggeration}">{text}</speak>'

        payload = {
            'voice_uuid': voice_uuid,
            'data': text[:2000],  # API limit
            'precision': precision,
            'sample_rate': sample_rate,
        }

        # Only include model if explicitly requested — API defaults to
        # the correct model for each voice. Forcing chatterbox-turbo on
        # voices that don't support it returns 500.
        if model:
            payload['model'] = model

        t = time.time()
        logger.info(
            f"[Resemble] TTS: '{text[:60]}...' model={model} "
            f"voice={voice_uuid[:12]}..."
        )

        try:
            with httpx.Client(
                timeout=httpx.Timeout(STREAM_TIMEOUT, connect=CONNECT_TIMEOUT)
            ) as client:
                resp = client.post(
                    SYNTHESIS_URL,
                    json=payload,
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                audio_bytes = resp.content

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            body = e.response.text[:200]
            raise RuntimeError(f"Resemble API error {status}: {body}")
        except httpx.TimeoutException:
            raise RuntimeError(
                f"Resemble API timeout after {STREAM_TIMEOUT}s"
            )
        except Exception as e:
            raise RuntimeError(f"Resemble request failed: {e}")

        elapsed = int((time.time() - t) * 1000)
        logger.info(f"[Resemble] Generated {len(audio_bytes)} bytes in {elapsed}ms")

        if len(audio_bytes) < 100:
            raise RuntimeError(
                f"Resemble returned suspiciously small response ({len(audio_bytes)} bytes)"
            )

        return audio_bytes

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        if not self.api_key:
            return {"ok": False, "latency_ms": 0, "detail": "RESEMBLE_API_KEY not set"}
        t = time.time()
        try:
            with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT)) as client:
                resp = client.get(
                    f"{API_BASE_URL}/voices",
                    params={"page": 1, "page_size": 1},
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
            latency_ms = int((time.time() - t) * 1000)
            return {
                "ok": True, "latency_ms": latency_ms,
                "detail": "Resemble API reachable — Chatterbox ready",
            }
        except Exception as e:
            latency_ms = int((time.time() - t) * 1000)
            return {"ok": False, "latency_ms": latency_ms, "detail": str(e)}

    def list_voices(self) -> list:
        voices = self._fetch_voices_from_api()
        return [v['id'] for v in voices] if voices else []

    def get_default_voice(self) -> str:
        return os.getenv('RESEMBLE_VOICE_UUID', '')

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> dict:
        # Don't fetch voices here — it makes 4 API calls and blocks page load.
        # Voices are fetched lazily via list_voices() / /api/tts/voices endpoint.
        cached_names = [v['name'] for v in self._voices_cache] if self._voices_cache else []
        return {
            'name': 'Resemble AI (Chatterbox)',
            'provider_id': 'resemble',
            'status': self._status,
            'description': (
                'Resemble AI Chatterbox — streaming TTS, voice cloning, '
                'emotion control, SSML, 90+ languages'
            ),
            'quality': 'very-high',
            'latency': 'very-fast',
            'cost_per_minute': 0.10,
            'voices': cached_names,
            'features': [
                'streaming', 'voice-cloning', 'emotion-control',
                'ssml', 'multilingual', 'cloud', 'wav-output',
                'paralinguistic-tags',
            ],
            'requires_api_key': True,
            'languages': [
                'en', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh',
                'ar', 'ru', 'hi', 'nl', 'pl', 'sv', 'da', 'fi', 'el',
                'cs', 'hu', 'ro', 'tr', 'uk', 'vi', 'th', 'id',
            ],
            'max_characters': 2000,
            'notes': (
                'Streaming HTTP TTS via f.cluster.resemble.ai. '
                'Models: chatterbox-turbo (fastest), chatterbox (emotion), '
                'chatterbox-multilingual (23 langs). '
                'Voice cloning via Resemble dashboard. '
                'RESEMBLE_API_KEY + RESEMBLE_VOICE_UUID required.'
            ),
            'default_voice': os.getenv('RESEMBLE_VOICE_UUID', ''),
            'audio_format': 'wav',
            'sample_rate': 24000,
            'models': list(MODELS.keys()),
            'error': self._init_error,
        }
