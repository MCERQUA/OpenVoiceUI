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
import threading
from pathlib import Path
from typing import Optional

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

# Module-level voice cache — shared across all ResembleProvider instances.
# list_providers() creates new instances each call, so instance-level cache
# is lost. This persists across the process lifetime.
_voices_cache_global = None
_voices_cache_time_global = 0
_voices_loading_global = False

# Per-voice style configuration file path (JSON, optional).
# Maps voice UUID → {exaggeration, model, prompt} overrides.
# Falls back to env var RESEMBLE_VOICE_STYLES_PATH.
_VOICE_STYLES_PATH = os.getenv(
    'RESEMBLE_VOICE_STYLES_PATH',
    os.path.join(os.path.dirname(__file__), 'resemble_voice_styles.json')
)
_voice_styles_cache = None
_voice_styles_mtime = 0


def _get_voice_styles() -> dict:
    """Load per-voice style overrides from JSON file. Hot-reloads on change."""
    global _voice_styles_cache, _voice_styles_mtime
    try:
        mt = os.path.getmtime(_VOICE_STYLES_PATH)
        if _voice_styles_cache is not None and mt == _voice_styles_mtime:
            return _voice_styles_cache
        import json
        with open(_VOICE_STYLES_PATH) as f:
            _voice_styles_cache = json.load(f)
        _voice_styles_mtime = mt
        logger.info(f"[Resemble] Loaded voice styles: {list(_voice_styles_cache.keys())}")
        return _voice_styles_cache
    except (FileNotFoundError, ValueError):
        return {}


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

        # Warm the global voice cache in background on first instantiation
        global _voices_loading_global
        if self.api_key and not _voices_cache_global and not _voices_loading_global:
            _voices_loading_global = True
            t = threading.Thread(target=self._fetch_voices_from_api, daemon=True)
            t.start()

    def _auth_headers(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, audio_path: str, name: str, **kwargs) -> dict:
        """
        Clone a voice using Resemble AI's voice creation API.

        Steps: Create voice -> Upload recording -> Build -> Poll until ready.
        Build time varies: typically 1-5 minutes for a single recording.

        Args:
            audio_path: Local path to audio file.
            name: Human-readable name for the voice.
            **kwargs:
                reference_text: Optional transcript of the recording.
                max_wait: Max seconds to wait for build (default 300).

        Returns:
            dict with: voice_id, name, provider, created_at, clone_time_ms
        """
        if not self.api_key:
            raise RuntimeError("RESEMBLE_API_KEY not set")

        reference_text = kwargs.get('reference_text', '')
        max_wait = kwargs.get('max_wait', 300)

        t = time.time()
        logger.info(f"[Resemble] Cloning voice '{name}' from {audio_path}")

        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise RuntimeError(f"Audio file not found: {audio_path}")

        # Step 1: Create voice entry
        try:
            with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT)) as client:
                resp = client.post(
                    f"{API_BASE_URL}/voices",
                    json={"name": name, "language": "en"},
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                voice_data = resp.json().get('item', {})
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Resemble voice creation error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )

        voice_uuid = voice_data.get('uuid', '')
        if not voice_uuid:
            raise RuntimeError(
                f"No UUID in Resemble voice creation response"
            )
        logger.info(f"[Resemble] Voice created: {voice_uuid}")

        # Step 2: Upload recording
        with open(audio_file, 'rb') as f:
            audio_bytes = f.read()

        ct_map = {
            '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
            '.m4a': 'audio/mp4', '.ogg': 'audio/ogg',
            '.webm': 'audio/webm', '.flac': 'audio/flac',
        }
        ct = ct_map.get(audio_file.suffix.lower(), 'audio/wav')

        try:
            with httpx.Client(
                timeout=httpx.Timeout(30.0, connect=10.0)
            ) as client:
                upload_data = {'name': f'{name}_sample', 'is_active': 'true'}
                if reference_text:
                    upload_data['emotion'] = 'neutral'

                resp = client.post(
                    f"{API_BASE_URL}/voices/{voice_uuid}/recordings",
                    headers={'Authorization': f'Bearer {self.api_key}'},
                    files={'file': (audio_file.name, audio_bytes, ct)},
                    data=upload_data,
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Resemble recording upload error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )
        logger.info(f"[Resemble] Recording uploaded for {voice_uuid}")

        # Step 3: Start voice build
        try:
            with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT)) as client:
                resp = client.post(
                    f"{API_BASE_URL}/voices/{voice_uuid}/build",
                    headers=self._auth_headers(),
                    json={},
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Resemble build error {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )
        logger.info(f"[Resemble] Build started for {voice_uuid}")

        # Step 4: Poll for build completion
        poll_interval = 5
        polls = int(max_wait / poll_interval)
        build_ready = False

        for i in range(polls):
            time.sleep(poll_interval)
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(API_TIMEOUT)
                ) as client:
                    resp = client.get(
                        f"{API_BASE_URL}/voices/{voice_uuid}",
                        headers=self._auth_headers(),
                    )
                    resp.raise_for_status()
                    status = resp.json().get('item', {}).get(
                        'voice_status', ''
                    )
            except Exception as e:
                logger.warning(f"[Resemble] Poll error: {e}")
                continue

            if status == 'Ready':
                build_ready = True
                break
            elif status in ('Failed', 'Disabled'):
                raise RuntimeError(
                    f"Resemble voice build failed (status: {status})"
                )
            # Still building — continue polling

        if not build_ready:
            raise RuntimeError(
                f"Resemble voice build timed out after {max_wait}s. "
                f"Voice UUID: {voice_uuid} — it may still finish building."
            )

        elapsed_ms = int((time.time() - t) * 1000)
        logger.info(
            f"[Resemble] Voice cloned: {voice_uuid} in {elapsed_ms}ms"
        )

        # Invalidate voice cache
        global _voices_cache_global, _voices_cache_time_global
        _voices_cache_global = None
        _voices_cache_time_global = 0

        return {
            'voice_id': voice_uuid,
            'name': name,
            'provider': 'resemble',
            'created_at': time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            'clone_time_ms': elapsed_ms,
        }

    def get_voice_status(self, voice_uuid: str) -> str:
        """Check the build status of a Resemble voice."""
        if not self.api_key:
            return 'error'
        try:
            with httpx.Client(timeout=httpx.Timeout(API_TIMEOUT)) as client:
                resp = client.get(
                    f"{API_BASE_URL}/voices/{voice_uuid}",
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                return resp.json().get('item', {}).get(
                    'voice_status', 'unknown'
                )
        except Exception as e:
            logger.warning(f"[Resemble] Status check failed: {e}")
            return 'error'

    # ------------------------------------------------------------------
    # Voice listing (cached from Resemble API)
    # ------------------------------------------------------------------

    def _fetch_voices_from_api(self) -> list:
        """Fetch available voices from Resemble API. Cached globally for 5 minutes."""
        global _voices_cache_global, _voices_cache_time_global, _voices_loading_global
        now = time.time()
        if _voices_cache_global and (now - _voices_cache_time_global) < 300:
            return _voices_cache_global

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

            _voices_cache_global = voices
            _voices_cache_time_global = now
            _voices_loading_global = False
            logger.info(f"[Resemble] Fetched {len(voices)} voices from API")
            return voices

        except Exception as e:
            _voices_loading_global = False
            logger.warning(f"[Resemble] Failed to fetch voices: {e}")
            return _voices_cache_global or []

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

        # Resolve voice — accept UUID or display name
        voice_uuid = voice or os.getenv('RESEMBLE_VOICE_UUID', '')
        if not voice_uuid:
            raise RuntimeError(
                "No voice_uuid provided and RESEMBLE_VOICE_UUID not set. "
                "Create a voice at app.resemble.ai and set the UUID."
            )

        # If the voice looks like a name (not a short hex UUID), resolve it
        if not all(c in '0123456789abcdef' for c in voice_uuid):
            cache = _voices_cache_global or self._fetch_voices_from_api()
            for v in cache:
                if v['name'] == voice_uuid:
                    logger.info(f"[Resemble] Resolved voice name '{voice_uuid}' → {v['id']}")
                    voice_uuid = v['id']
                    break
            else:
                logger.warning(f"[Resemble] Voice name '{voice_uuid}' not found in {len(cache)} voices")

        model = kwargs.get('model', '')
        sample_rate = kwargs.get('sample_rate', 24000)
        precision = kwargs.get('precision', 'PCM_16')
        exaggeration = kwargs.get('exaggeration')

        # Per-voice style presets — apply character-specific direction
        # when no explicit exaggeration/model is provided via kwargs.
        voice_styles = _get_voice_styles()
        style = voice_styles.get(voice_uuid, {})
        if exaggeration is None and 'exaggeration' in style:
            exaggeration = style['exaggeration']
        if not model and 'model' in style:
            model = style['model']
        prompt = style.get('prompt', '')

        # Wrap in SSML with prompt/exaggeration if applicable
        if not text.strip().startswith('<speak'):
            attrs = []
            if exaggeration is not None:
                attrs.append(f'exaggeration="{exaggeration}"')
            if prompt:
                attrs.append(f'prompt="{prompt}"')
            if attrs:
                text = f'<speak {" ".join(attrs)}>{text}</speak>'

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
        voices = _voices_cache_global or self._fetch_voices_from_api()
        return [v['id'] for v in voices] if voices else []

    def get_default_voice(self) -> str:
        return os.getenv('RESEMBLE_VOICE_UUID', '')

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> dict:
        # Use global cache — populated by background thread on first init.
        # Never fetch synchronously here; that blocks the settings panel.
        cached_names = [v['name'] for v in _voices_cache_global] if _voices_cache_global else []
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
