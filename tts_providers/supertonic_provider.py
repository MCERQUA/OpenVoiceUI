#!/usr/bin/env python3
"""
Supertonic TTS Provider for OpenVoiceUI.

This provider wraps the existing supertonic_tts.py module, implementing
the TTSProvider interface for seamless integration with the TTS provider system.

Supertonic is a local ONNX-based Text-to-Speech engine that supports multiple
voice styles (M1-M5 for male, F1-F5 for female voices) and multiple languages.

Author: OpenVoiceUI
Date: 2026-02-11
"""

import logging
import os
from typing import Dict, List, Any, Optional

from .base_provider import TTSProvider

# Configure logging
logger = logging.getLogger(__name__)

# ── API mode (preferred) ───────────────────────────────────────────────────────
# When SUPERTONIC_API_URL is set, all synthesis calls go to the shared
# supertonic-tts microservice (loaded once, serves all users).
# Falls back to local ONNX loading if the env var is not set.
_API_URL = os.environ.get("SUPERTONIC_API_URL", "").rstrip("/")

# ── Local mode (fallback) ──────────────────────────────────────────────────────
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from supertonic_tts import SupertonicTTS
except ImportError as e:
    SupertonicTTS = None
    _import_error = str(e)


class SupertonicProvider(TTSProvider):
    """
    TTS Provider for Supertonic ONNX-based Text-to-Speech engine.

    This provider offers high-quality local TTS with multiple voice styles.
    It runs entirely offline after initial model loading, making it ideal
    for applications that need low latency and privacy.

    Key Behavior:
        This provider REINITIALIZES SupertonicTTS with the requested voice
        for each generate_speech() call, matching the behavior of server.py
        (lines ~3089-3094). This ensures proper voice switching without
        state management issues, at the cost of slightly higher latency
        on first use of each voice.

    Voice Styles:
        - M1-M5: Male voices with different characteristics
        - F1-F5: Female voices with different characteristics

    Languages:
        - en (English)
        - ko (Korean)
        - es (Spanish)
        - pt (Portuguese)
        - fr (French)

    Example:
        >>> from tts_providers import SupertonicProvider
        >>> provider = SupertonicProvider()
        >>> audio = provider.generate_speech("Hello world", voice='M1')
        >>> with open('output.wav', 'wb') as f:
        ...     f.write(audio)

    Configuration:
        >>> provider = SupertonicProvider(
        ...     onnx_dir="/path/to/onnx",
        ...     voice_styles_dir="/path/to/voice_styles",
        ...     default_voice="M1"
        ... )
    """

    # Default paths — override via SUPERTONIC_MODEL_PATH env var
    DEFAULT_ONNX_DIR = os.getenv('SUPERTONIC_MODEL_PATH', '/opt/supertonic/assets/onnx')
    DEFAULT_VOICE_STYLES_DIR = None  # derived from onnx_dir if not set

    # Available voice styles (expanded from base implementation)
    AVAILABLE_VOICES = [
        'M1', 'M2', 'M3', 'M4', 'M5',  # Male voices
        'F1', 'F2', 'F3', 'F4', 'F5'   # Female voices
    ]

    # Supported languages
    SUPPORTED_LANGUAGES = ['en', 'ko', 'es', 'pt', 'fr']

    # Provider metadata
    PROVIDER_NAME = "Supertonic"
    PROVIDER_VERSION = "1.0.0"
    PROVIDER_DESCRIPTION = "Local ONNX-based TTS with multiple voice styles"

    def __init__(
        self,
        onnx_dir: Optional[str] = None,
        voice_styles_dir: Optional[str] = None,
        default_voice: str = 'F3',
        use_gpu: bool = False
    ):
        """
        Initialize the Supertonic TTS Provider.

        Args:
            onnx_dir: Path to ONNX models directory. If None, uses DEFAULT_ONNX_DIR.
            voice_styles_dir: Path to voice styles JSON files directory.
                            If None, uses DEFAULT_VOICE_STYLES_DIR.
            default_voice: Default voice to use (M1-M5, F1-F5). Default is 'M1'.
            use_gpu: Whether to use GPU for inference. Default is False (CPU only).

        Raises:
            ValueError: If SupertonicTTS module is not available.
            FileNotFoundError: If required directories don't exist.
            RuntimeError: If initialization fails.

        Example:
            >>> provider = SupertonicProvider(
            ...     onnx_dir="/custom/path/to/onnx",
            ...     default_voice="F1"
            ... )
        """
        super().__init__()

        self._status = 'inactive'
        self._init_error = None
        self._tts_cache: Dict[str, SupertonicTTS] = {}
        self.default_voice = default_voice
        self.use_gpu = use_gpu

        # ── API mode ──────────────────────────────────────────────────────────
        # Preferred: call the shared supertonic-tts microservice.
        # Models are loaded once system-wide; no per-process ONNX loading.
        #
        # LAZY (TTS-3): __init__ must NEVER make a blocking network call — with the
        # provider singleton this runs once, but a readiness/health probe or a
        # cold first-sentence must not eat a 3s health GET (and then, on local
        # fallback, a multi-second in-process ONNX load). If SUPERTONIC_API_URL is
        # set we assume API mode and let the FIRST generate reveal an outage (the
        # tts.py fallback chain handles a down service in <1s). No health GET here.
        if _API_URL:
            self._use_api = True
            self._api_url = _API_URL
            self.onnx_dir = onnx_dir or self.DEFAULT_ONNX_DIR
            self.voice_styles_dir = ""
            self._status = 'active'
            logger.info(f"SupertonicProvider: API mode → {_API_URL} (lazy — no init health GET)")
            return

        self._use_api = False

        # ── Local mode (fallback) ─────────────────────────────────────────────
        if SupertonicTTS is None:
            self._status = 'error'
            self._init_error = "supertonic_tts module not found. Set SUPERTONIC_API_URL or SUPERTONIC_HELPER_PATH."
            self.onnx_dir = onnx_dir or self.DEFAULT_ONNX_DIR
            self.voice_styles_dir = voice_styles_dir or self.DEFAULT_ONNX_DIR.replace('/onnx', '/voice_styles')
            return

        self.onnx_dir = onnx_dir or self.DEFAULT_ONNX_DIR
        self.voice_styles_dir = (
            voice_styles_dir
            or (self.DEFAULT_VOICE_STYLES_DIR if self.DEFAULT_VOICE_STYLES_DIR
                else os.path.join(os.path.dirname(self.onnx_dir), 'voice_styles'))
        )

        if not os.path.exists(self.onnx_dir):
            self._status = 'error'
            self._init_error = f"ONNX directory not found: {self.onnx_dir}. Set SUPERTONIC_MODEL_PATH in .env."
            logger.warning(f"SupertonicProvider: {self._init_error}")
            return

        if not os.path.exists(self.voice_styles_dir):
            self._status = 'error'
            self._init_error = f"Voice styles directory not found: {self.voice_styles_dir}"
            logger.warning(f"SupertonicProvider: {self._init_error}")
            return

        # LAZY (TTS-3): dirs exist → mark active but DO NOT load ONNX models here.
        # The first generate_speech() lazily creates (and caches) the SupertonicTTS
        # instance via _create_tts_instance(), so __init__ stays fast/non-blocking.
        self._status = 'active'
        logger.info(f"SupertonicProvider: local mode ready (voice '{default_voice}', ONNX loads on first use)")

    def _get_voice_style_path(self, voice: str) -> str:
        """
        Get the full path to a voice style JSON file.

        Args:
            voice: Voice identifier (e.g., 'M1', 'F2').

        Returns:
            Full path to the voice style JSON file.

        Raises:
            ValueError: If voice is not available.
            FileNotFoundError: If voice style file doesn't exist.
        """
        if voice not in self.AVAILABLE_VOICES:
            raise ValueError(
                f"Invalid voice: {voice}. Available: {self.AVAILABLE_VOICES}"
            )

        voice_path = os.path.join(self.voice_styles_dir, f"{voice}.json")

        if not os.path.exists(voice_path):
            raise FileNotFoundError(
                f"Voice style file not found: {voice_path}"
            )

        return voice_path

    def _create_tts_instance(self, voice: str) -> SupertonicTTS:
        """
        Get or create a TTS instance for the specified voice.

        Uses caching to avoid reloading ONNX models for every call.
        Instances are cached by voice name and reused.

        Args:
            voice: Voice identifier.

        Returns:
            SupertonicTTS instance for the specified voice.

        Raises:
            RuntimeError: If TTS instance creation fails.
        """
        # Check cache first
        if voice in self._tts_cache:
            logger.debug(f"Reusing cached TTS instance for voice '{voice}'")
            return self._tts_cache[voice]

        voice_style_path = self._get_voice_style_path(voice)

        try:
            tts_instance = SupertonicTTS(
                onnx_dir=self.onnx_dir,
                voice_style_path=voice_style_path,
                voice_style_name=voice,
                use_gpu=self.use_gpu
            )
            # Cache the instance for reuse
            self._tts_cache[voice] = tts_instance
            logger.debug(f"Created and cached new TTS instance for voice '{voice}'")
            return tts_instance
        except Exception as e:
            logger.error(f"Failed to create TTS instance for voice '{voice}': {e}")
            raise RuntimeError(f"TTS instance creation failed: {e}")

    def generate_speech(
        self,
        text: str,
        voice: Optional[str] = None,
        lang: str = 'en',
        speed: float = 1.0,
        total_step: int = 15,
        **options
    ) -> bytes:
        """
        Generate speech from text using Supertonic TTS.

        Args:
            text: The text to synthesize. Must not be empty.
            voice: Voice identifier (M1-M5, F1-F5). If None, uses default_voice.
            lang: Language code ('en', 'ko', 'es', 'pt', 'fr'). Default is 'en'.
            speed: Speech speed multiplier. Higher = faster.
                   Recommended range: 0.8 to 1.3. Default is 1.05.
            total_step: Number of denoising steps. More = better quality but slower.
                       Recommended range: 3-10. Default is 5.
            **options: Additional options (currently not used, reserved for future).

        Returns:
            bytes: WAV audio data ready to write to file or send via HTTP.

        Raises:
            ValueError: If text is empty, or voice/lang/speed/total_step invalid.
            RuntimeError: If speech generation fails.

        Example:
            >>> audio = provider.generate_speech(
            ...     text="Hello world!",
            ...     voice="M1",
            ...     lang="en",
            ...     speed=1.1,
            ...     total_step=6
            ... )
        """
        # Use default voice if not specified
        if voice is None:
            voice = self.default_voice

        # Validate inputs
        self.validate_text(text)

        if voice not in self.AVAILABLE_VOICES:
            raise ValueError(
                f"Invalid voice: {voice}. Available: {self.AVAILABLE_VOICES}"
            )

        if lang not in self.SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported language: {lang}. Supported: {self.SUPPORTED_LANGUAGES}"
            )

        if speed <= 0 or speed > 3:
            raise ValueError(f"Invalid speed: {speed}. Must be between 0 and 3")

        if total_step < 1 or total_step > 50:
            raise ValueError(f"Invalid total_step: {total_step}. Must be between 1 and 50")

        logger.info(
            f"Generating speech: '{text[:50]}...' "
            f"(voice={voice}, lang={lang}, speed={speed}, steps={total_step})"
        )

        try:
            # ── API mode: call shared supertonic-tts service ──────────────────
            if getattr(self, '_use_api', False):
                import requests
                resp = requests.post(
                    f"{self._api_url}/tts",
                    json={"text": text, "voice": voice, "speed": speed,
                          "steps": total_step, "lang": lang},
                    timeout=60,
                )
                if not resp.ok:
                    raise RuntimeError(f"Supertonic API error {resp.status_code}: {resp.text[:200]}")
                audio_bytes = resp.content
                logger.info(f"API: {len(audio_bytes)} bytes for voice '{voice}'")
                # JamBot Books: shared supertonic-tts container call (cost 0, local).
                try:
                    from services.jambot_books_hook import record_provider_call
                    record_provider_call('supertonic', endpoint='/tts', op='tts-api',
                                         units=str(len(text)), status=resp.status_code,
                                         model='supertonic')
                except Exception:
                    pass
                return audio_bytes

            # ── Local mode: load ONNX in-process ─────────────────────────────
            tts = self._create_tts_instance(voice)
            audio_bytes = tts.generate_speech(
                text=text, lang=lang, speed=speed, total_step=total_step
            )
            logger.info(f"Local: {len(audio_bytes)} bytes for voice '{voice}'")
            # JamBot Books: in-process ONNX supertonic (cost 0, local).
            try:
                from services.jambot_books_hook import record_provider_call
                record_provider_call('supertonic', endpoint='/local', op='tts-local',
                                     units=str(len(text)), status=200, model='supertonic')
            except Exception:
                pass
            return audio_bytes

        except Exception as e:
            logger.error(f"Speech generation failed: {e}")
            raise RuntimeError(f"Failed to generate speech: {e}")

    def list_voices(self) -> List[str]:
        """
        List all available voice styles.

        Returns:
            List of voice identifiers that can be used with generate_speech().

        Example:
            >>> provider.list_voices()
            ['M1', 'M2', 'M3', 'M4', 'M5', 'F1', 'F2', 'F3', 'F4', 'F5']
        """
        return self.AVAILABLE_VOICES.copy()

    def get_info(self) -> Dict[str, Any]:
        """
        Get provider metadata and status matching providers_config.json format.

        Returns:
            Dict containing complete metadata including:
                - 'name': Provider name
                - 'provider_id': Unique provider identifier
                - 'status': 'active', 'inactive', or 'error'
                - 'description': Human-readable description
                - 'quality': Audio quality rating
                - 'latency': Expected latency category
                - 'cost_per_minute': Cost per minute of audio
                - 'voices': List of all available voice identifiers
                - 'features': List of provider features
                - 'requires_api_key': Whether API key is required
                - 'languages': List of supported language codes
                - 'max_characters': Max text length per request
                - 'notes': Additional notes about the provider
                - 'documentation_url': Link to documentation
                - 'default_voice': Default voice identifier
                - 'capabilities': Dict of feature flags
                - 'onnx_dir': ONNX models directory path
                - 'voice_styles_dir': Voice styles directory path
                - 'error': Error message if status is 'error'

        Example:
            >>> info = provider.get_info()
            >>> print(f"{info['name']}: {info['status']}")
            Supertonic TTS: active
            >>> info['languages']
            ['en', 'ko', 'es', 'pt', 'fr']
        """
        onnx_dir = getattr(self, 'onnx_dir', self.DEFAULT_ONNX_DIR)
        return {
            'name': 'Supertonic TTS',
            'provider_id': 'supertonic',
            'status': self._status,
            'description': 'Local ONNX-based TTS engine with multiple voice styles',
            'quality': 'high',
            'latency': 'very-fast',
            'cost_per_minute': 0.0,
            'voices': self.AVAILABLE_VOICES.copy(),
            'features': [
                'multi-language',
                'local-processing',
                'open-source',
                'no-api-key-required',
                'onnx-based',
                'voice-style-switching',
                'offline-capable',
            ],
            'requires_api_key': False,
            'languages': self.SUPPORTED_LANGUAGES.copy(),
            'max_characters': 10000,
            'notes': (
                'Free, fast, local inference. Requires local ONNX models. '
                f'Set SUPERTONIC_MODEL_PATH in .env. Current path: {onnx_dir}'
            ),
            'documentation_url': 'https://github.com/playht/supertonic',
            'default_voice': self.default_voice,
            'capabilities': {
                'streaming': False,
                'ssml': False,
                'custom_voices': True,
                'offline': True,
                'gpu_support': True,
            },
            'onnx_dir': self.onnx_dir,
            'voice_styles_dir': self.voice_styles_dir,
            'error': self._init_error if self._status == 'error' else None,
            'requires_microphone': False,
            'requires_websocket': False,
            'mode': 'tts-only',
        }

    def is_available(self) -> bool:
        """
        Check if the provider is ready to generate speech.

        Returns:
            True if provider is active and can generate speech, False otherwise.
        """
        return self._status == 'active'

    def get_default_voice(self) -> str:
        """
        Get the default voice identifier.

        Returns:
            The default voice identifier (e.g., 'M1').
        """
        return self.default_voice

    def set_default_voice(self, voice: str) -> None:
        """
        Change the default voice.

        Args:
            voice: New default voice identifier (must be in AVAILABLE_VOICES).

        Raises:
            ValueError: If voice is not available.
            RuntimeError: If voice initialization fails.
        """
        if voice not in self.AVAILABLE_VOICES:
            raise ValueError(
                f"Invalid voice: {voice}. Available: {self.AVAILABLE_VOICES}"
            )

        try:
            # Test initialization with the new default voice
            self._create_tts_instance(voice)
            self.default_voice = voice
            logger.info(f"Default voice changed to '{voice}'")
        except Exception as e:
            raise RuntimeError(f"Failed to set default voice: {e}")

    def get_supported_languages(self) -> List[str]:
        """
        Get list of supported language codes.

        Returns:
            List of supported language codes.
        """
        return self.SUPPORTED_LANGUAGES.copy()

    def clear_cache(self) -> None:
        """
        Clear the TTS instance cache.

        Removes all cached TTS instances, forcing new instances to be
        created on the next generate_speech() call.
        """
        self._tts_cache.clear()
        logger.debug("TTS instance cache cleared")

    def preload_voice(self, voice: str) -> None:
        """
        Preload a TTS instance for a specific voice.

        Creates and caches the TTS instance so it's ready for immediate use.

        Args:
            voice: Voice identifier to preload.

        Raises:
            ValueError: If voice is not available.
            RuntimeError: If preloading fails.
        """
        if voice not in self.AVAILABLE_VOICES:
            raise ValueError(
                f"Invalid voice: {voice}. Available: {self.AVAILABLE_VOICES}"
            )

        try:
            # Create and cache the TTS instance
            self._create_tts_instance(voice)
            logger.info(f"Voice '{voice}' preloaded and cached")
        except Exception as e:
            raise RuntimeError(f"Failed to preload voice '{voice}': {e}")

    def preload_all_voices(self) -> Dict[str, bool]:
        """
        Test initialization for all available voices.

        Note: This only tests that each voice can be initialized. Since the
        provider reinitializes for each call, this is a validation check.

        Returns:
            Dict mapping voice identifiers to success status.

        Example:
            >>> results = provider.preload_all_voices()
            >>> print(results)
            {'M1': True, 'M2': True, 'F1': True, 'F2': False}
        """
        results = {}
        for voice in self.AVAILABLE_VOICES:
            try:
                self.preload_voice(voice)
                results[voice] = True
            except Exception as e:
                logger.error(f"Failed to preload voice '{voice}': {e}")
                results[voice] = False
        return results


__all__ = ['SupertonicProvider']
