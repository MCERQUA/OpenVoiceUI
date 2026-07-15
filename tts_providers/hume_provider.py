#!/usr/bin/env python3
"""
Hume Octave TTS Provider — acting-direction TTS with custom voices.

Hume's Octave model is a "voice actor" TTS: every utterance can carry a free-text
`description` (acting direction — "dazed stoner, giggles at his own jokes") and the
model PERFORMS the line rather than reading it. Voices are created by design
(describe a voice → generate → save), not by audio cloning.

Two ways to use it in the JamBot stack:
  1. Direct provider — synthesize with a saved Hume custom voice (or a stock
     Hume Voice Library voice) + optional per-utterance acting direction.
  2. Performance donor — render an acted take here, then speech-to-speech convert
     it into a Resemble custom clone (<resemble:convert src=...>). The donor's
     comedic timing survives conversion beat-for-beat. (Kyle/BHB pipeline,
     proven 2026-07-14.)

API (verified live 2026-07-15):
  POST https://api.hume.ai/v0/tts                  — synthesize (JSON, base64 audio)
  GET  https://api.hume.ai/v0/tts/voices           — list voices (CUSTOM_VOICE | HUME_AI)
  POST https://api.hume.ai/v0/tts/voices           — save a generation as a custom voice
Auth: X-Hume-Api-Key header. Env: HUME_API_KEY.

GOTCHA: api.hume.ai sits behind Cloudflare bot protection — requests with a bare
python UA get 403 (error code 1010). Always send a browser-like User-Agent.
"""

import os
import time
import base64
import logging
import threading
from typing import List, Dict, Any, Optional

import httpx

from .base_provider import TTSProvider

logger = logging.getLogger(__name__)

API_BASE = "https://api.hume.ai/v0"

# Cloudflare in front of api.hume.ai rejects default python UAs (403 / code 1010).
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SYNTH_TIMEOUT = 60.0    # Octave renders a full acted take (no streaming here) —
                        # slower than plain TTS; 60s covers long utterances.
CONNECT_TIMEOUT = 10.0
API_TIMEOUT = 15.0

# Hume caps utterance text; stay under it.
MAX_TEXT_CHARS = 5000

# Module-level shared client + voice cache (same pattern as resemble_provider:
# provider singletons live for the process, pool TCP connections).
_client = None
_client_lock = threading.Lock()

_voices_cache: Optional[List[Dict[str, Any]]] = None
_voices_cache_time: float = 0
_voices_loading = False


def _get_client() -> httpx.Client:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(SYNTH_TIMEOUT, connect=CONNECT_TIMEOUT),
                limits=httpx.Limits(max_keepalive_connections=10,
                                    max_connections=20,
                                    keepalive_expiry=60.0),
            )
    return _client


class HumeProvider(TTSProvider):
    """
    TTS provider using Hume's Octave acting-direction model.

    generate_speech kwargs:
        description : str  — acting direction for THIS utterance ("deep stoner,
                             dazed, laughs a lot"). Works alone (voice designed
                             on the fly) or with a saved voice (directs the take).
        speed       : float — 0.65-1.5 relative speaking rate (Hume-side).
        trailing_silence : float — seconds of silence appended (0-5).

    voice: name or id of a saved Hume voice. Custom voices are checked first,
    then the stock Hume Voice Library. Empty voice + description = one-off
    designed voice.

    Output: WAV bytes (mono, 48kHz from Hume).
    """

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv('HUME_API_KEY', '')
        self._status = 'active' if self.api_key else 'error'
        self._init_error = None if self.api_key else 'HUME_API_KEY not set'

    def _headers(self) -> Dict[str, str]:
        return {
            'X-Hume-Api-Key': self.api_key,
            'Content-Type': 'application/json',
            'User-Agent': _BROWSER_UA,
            'Accept': 'application/json',
        }

    # ------------------------------------------------------------------
    # Voice listing (custom + stock library)
    # ------------------------------------------------------------------

    def _fetch_voices(self) -> List[Dict[str, Any]]:
        """Fetch saved custom voices + stock library voices. Cached 5 minutes."""
        global _voices_cache, _voices_cache_time, _voices_loading
        now = time.time()
        if _voices_cache is not None and (now - _voices_cache_time) < 300:
            return _voices_cache

        voices: List[Dict[str, Any]] = []
        try:
            client = _get_client()
            for provider in ('CUSTOM_VOICE', 'HUME_AI'):
                page = 0
                while True:
                    resp = client.get(
                        f"{API_BASE}/tts/voices",
                        params={'provider': provider, 'page_number': page,
                                'page_size': 100},
                        headers=self._headers(),
                        timeout=API_TIMEOUT,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    items = data.get('voices_page', data.get('voices', [])) or []
                    for v in items:
                        voices.append({
                            'id': v.get('id', ''),
                            'name': v.get('name', ''),
                            'provider': provider,
                        })
                    total_pages = data.get('total_pages', 1)
                    page += 1
                    if page >= total_pages or not items:
                        break
            _voices_cache = voices
            _voices_cache_time = now
            logger.info(f"[Hume] Fetched {len(voices)} voices "
                        f"({sum(1 for v in voices if v['provider'] == 'CUSTOM_VOICE')} custom)")
        except Exception as e:
            logger.warning(f"[Hume] Voice fetch failed: {e}")
            return _voices_cache or []
        finally:
            _voices_loading = False
        return voices

    def _resolve_voice(self, voice: str) -> Optional[Dict[str, str]]:
        """Map a voice name-or-id to a Hume voice spec dict, or None."""
        if not voice:
            return None
        cache = self._fetch_voices()
        # custom voices win over stock on name collision
        for want_provider in ('CUSTOM_VOICE', 'HUME_AI'):
            for v in cache:
                if v['provider'] != want_provider:
                    continue
                if voice in (v['id'], v['name']):
                    return {'id': v['id'], 'provider': v['provider']}
        # Unknown to the cache — pass through as a name and let Hume resolve;
        # covers voices created after the cache warmed.
        return {'name': voice, 'provider': 'CUSTOM_VOICE'}

    # ------------------------------------------------------------------
    # Custom voice creation (design-based, not audio cloning)
    # ------------------------------------------------------------------

    def save_voice(self, generation_id: str, name: str) -> Dict[str, Any]:
        """Save a previous generation as a named custom voice.

        Octave 'cloning' is by design: synthesize with a `description` until a
        take sounds right, then save that generation's voice under a name.
        """
        if not self.api_key:
            raise RuntimeError("HUME_API_KEY not set")
        client = _get_client()
        resp = client.post(
            f"{API_BASE}/tts/voices",
            json={'generation_id': generation_id, 'name': name},
            headers=self._headers(),
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        global _voices_cache, _voices_cache_time
        _voices_cache = None
        _voices_cache_time = 0
        return resp.json()

    # ------------------------------------------------------------------
    # Speech generation
    # ------------------------------------------------------------------

    def generate_speech(self, text: str, voice: str = '', **kwargs) -> bytes:
        if not self.api_key:
            raise RuntimeError("HUME_API_KEY not set")
        self.validate_text(text)

        description = (kwargs.get('description') or '').strip()
        speed = kwargs.get('speed')
        trailing_silence = kwargs.get('trailing_silence')

        utterance: Dict[str, Any] = {'text': text[:MAX_TEXT_CHARS]}
        if description:
            utterance['description'] = description[:1000]
        voice_spec = self._resolve_voice(voice or os.getenv('HUME_VOICE', ''))
        if voice_spec:
            utterance['voice'] = voice_spec
        if speed is not None:
            utterance['speed'] = max(0.65, min(1.5, float(speed)))
        if trailing_silence is not None:
            utterance['trailing_silence'] = max(0.0, min(5.0, float(trailing_silence)))

        payload = {'utterances': [utterance], 'format': {'type': 'wav'}}

        t = time.time()
        logger.info(f"[Hume] TTS: '{text[:60]}...' voice={voice or '(designed)'} "
                    f"desc={'yes' if description else 'no'}")
        try:
            client = _get_client()
            resp = client.post(f"{API_BASE}/tts", json=payload,
                               headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            elapsed_ms = int((time.time() - t) * 1000)
            logger.error(f"[Hume] API error {e.response.status_code} "
                         f"after {elapsed_ms}ms: {body}")
            raise RuntimeError(f"Hume API error {e.response.status_code}: {body}")
        except httpx.TimeoutException:
            elapsed_ms = int((time.time() - t) * 1000)
            raise RuntimeError(f"Hume API timeout after {elapsed_ms}ms")

        gens = data.get('generations') or []
        if not gens or not gens[0].get('audio'):
            raise RuntimeError(f"Hume returned no audio: {str(data)[:200]}")
        audio = base64.b64decode(gens[0]['audio'])
        # Surface the generation_id — needed by save_voice() to keep a take.
        self.last_generation_id = gens[0].get('generation_id', '')

        elapsed = int((time.time() - t) * 1000)
        logger.info(f"[Hume] Generated {len(audio)} bytes in {elapsed}ms "
                    f"(gen={self.last_generation_id[:12]})")

        try:
            from services.jambot_books_hook import record_provider_call
            record_provider_call('hume', endpoint='/v0/tts', op='tts',
                                 units=str(len(text)), status=200, model='octave')
        except Exception:
            pass  # never break a voice turn

        if len(audio) < 100:
            raise RuntimeError(f"Hume returned suspiciously small audio ({len(audio)} bytes)")
        return audio

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        if not self.api_key:
            return {"ok": False, "latency_ms": 0, "detail": "HUME_API_KEY not set"}
        t = time.time()
        try:
            client = _get_client()
            resp = client.get(f"{API_BASE}/tts/voices",
                              params={'provider': 'CUSTOM_VOICE', 'page_number': 0,
                                      'page_size': 1},
                              headers=self._headers(), timeout=API_TIMEOUT)
            resp.raise_for_status()
            return {"ok": True, "latency_ms": int((time.time() - t) * 1000),
                    "detail": "Hume Octave reachable"}
        except Exception as e:
            return {"ok": False, "latency_ms": int((time.time() - t) * 1000),
                    "detail": str(e)}

    def list_voices(self) -> List[str]:
        return [v['name'] or v['id'] for v in self._fetch_voices()]

    def get_default_voice(self) -> Optional[str]:
        return os.getenv('HUME_VOICE', '') or None

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> Dict[str, Any]:
        custom = []
        if _voices_cache:
            custom = [v['name'] for v in _voices_cache
                      if v['provider'] == 'CUSTOM_VOICE' and v['name']]
        return {
            'name': 'Hume Octave',
            'provider_id': 'hume',
            'status': self._status,
            'description': ('Hume Octave — acting-direction TTS. Per-utterance '
                            '"description" directs the performance; custom voices '
                            'are designed (describe → generate → save), not cloned. '
                            'Also the performance-donor stage for Resemble STS '
                            'character pipelines.'),
            'quality': 'very-high',
            'latency': 'medium',
            'cost_per_minute': 0.20,
            'voices': custom,
            'features': ['acting-direction', 'voice-design', 'emotion-control',
                         'cloud', 'wav-output'],
            'requires_api_key': True,
            'languages': ['en', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh'],
            'max_characters': MAX_TEXT_CHARS,
            'notes': ('POST /v0/tts with utterances[{text, description, voice}]. '
                      'Needs browser User-Agent (Cloudflare blocks python UA — 403/1010). '
                      'save_voice(generation_id, name) keeps a take as a custom voice. '
                      'HUME_API_KEY + optional HUME_VOICE env.'),
            'default_voice': os.getenv('HUME_VOICE', ''),
            'audio_format': 'wav',
            'sample_rate': 48000,
            'error': self._init_error,
        }
