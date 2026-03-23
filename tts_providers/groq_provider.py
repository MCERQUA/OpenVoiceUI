"""
Groq Orpheus TTS Provider — canopylabs/orpheus-v1-english via Groq LPU.

~130-200ms TTFB, natural human-like prosody, MP3 output.
API key: GROQ_API_KEY env var
"""

import os
import time
import logging

from .base_provider import TTSProvider

logger = logging.getLogger(__name__)

MODEL = "canopylabs/orpheus-v1-english"
# If a single TTS call exceeds this, abort and let fallback handle it.
# Normal Groq latency is 130-700ms. Rate-limited calls take 12-27s.
GROQ_TTS_TIMEOUT_SECONDS = 5.0

AVAILABLE_VOICES = [
    "autumn",  # Female (default)
    "diana",   # Female
    "hannah",  # Female
    "austin",  # Male
    "daniel",  # Male
    "troy",    # Male
]


class GroqProvider(TTSProvider):
    """
    TTS Provider using Groq Orpheus (canopylabs/orpheus-v1-english).

    Voices: autumn, diana, hannah, austin, daniel, troy
    Output: WAV audio bytes
    Latency: ~130-200ms (Groq LPU)
    Cost: ~$0.05/1K chars
    """

    def __init__(self):
        super().__init__()
        self.api_key = os.getenv('GROQ_API_KEY', '')
        self._status = 'active' if self.api_key else 'error'
        self._init_error = None if self.api_key else 'GROQ_API_KEY not set'
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from groq import Groq
                self._client = Groq(
                    api_key=self.api_key,
                    timeout=GROQ_TTS_TIMEOUT_SECONDS,
                )
            except ImportError:
                raise RuntimeError("groq package not installed — run: pip install groq")
        return self._client

    def generate_speech(self, text: str, voice: str = 'autumn', **kwargs) -> bytes:
        """
        Generate speech via Groq Orpheus.

        Args:
            text: Text to synthesize.
            voice: One of AVAILABLE_VOICES. Default: 'autumn'.

        Returns:
            MP3 audio bytes.
        """
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")

        self.validate_text(text)

        if voice not in AVAILABLE_VOICES:
            logger.warning(f"[Groq] Unknown voice '{voice}', using autumn")
            voice = 'autumn'

        t = time.time()
        logger.info(f"[Groq] Requesting TTS: '{text[:60]}' voice={voice}")

        try:
            client = self._get_client()
            resp = client.audio.speech.create(
                model=MODEL,
                voice=voice,
                input=text,
                response_format="wav",
            )
            audio_bytes = resp.content if hasattr(resp, 'content') else resp.read()
        except Exception as e:
            # Parse structured Groq API errors to extract error code
            import re
            err_str = str(e)
            err_code = 'unknown'
            err_msg = err_str
            try:
                code_match = re.search(r"'code':\s*'([^']+)'", err_str)
                msg_match = re.search(r"'message':\s*'([^']+)'", err_str)
                if code_match:
                    err_code = code_match.group(1)
                if msg_match:
                    err_msg = msg_match.group(1)
            except Exception:
                pass
            raise RuntimeError(f"[groq:{err_code}] {err_msg}")

        elapsed = int((time.time() - t) * 1000)
        logger.info(f"[Groq] Generated {len(audio_bytes)} bytes in {elapsed}ms")
        return audio_bytes

    def health_check(self) -> dict:
        if not self.api_key:
            return {"ok": False, "latency_ms": 0, "detail": "GROQ_API_KEY not set"}
        t = time.time()
        try:
            from groq import Groq
            client = Groq(api_key=self.api_key)
            client.models.list()
            latency_ms = int((time.time() - t) * 1000)
            return {"ok": True, "latency_ms": latency_ms, "detail": "Groq reachable — Orpheus ready"}
        except Exception as e:
            latency_ms = int((time.time() - t) * 1000)
            return {"ok": False, "latency_ms": latency_ms, "detail": str(e)}

    def list_voices(self) -> list:
        return AVAILABLE_VOICES.copy()

    def get_default_voice(self) -> str:
        return 'autumn'

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_info(self) -> dict:
        return {
            'name': 'Groq Orpheus',
            'provider_id': 'groq',
            'status': self._status,
            'description': 'Orpheus TTS via Groq LPU — fast, natural, human-like prosody',
            'quality': 'high',
            'latency': 'very-fast',
            'cost_per_minute': 0.05,
            'voices': AVAILABLE_VOICES.copy(),
            'features': ['fast', 'natural', 'empathetic', 'mp3-output', 'cloud'],
            'requires_api_key': True,
            'languages': ['en'],
            'max_characters': 5000,
            'notes': 'Orpheus v1 English on Groq LPU. ~130-200ms latency. GROQ_API_KEY required.',
            'default_voice': 'autumn',
            'audio_format': 'wav',
            'sample_rate': 24000,
            'error': self._init_error,
        }
