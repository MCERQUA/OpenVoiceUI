#!/usr/bin/env python3
"""
Base TTS Provider Abstract Class for OpenVoiceUI.

This module defines the abstract interface that all TTS providers must implement.
It provides a consistent API for generating speech, listing available voices,
and retrieving provider information.

Author: OpenVoiceUI
Date: 2026-02-11
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class TTSVoice:
    """
    Represents a single voice available from a TTS provider.

    Attributes:
        id: Unique identifier for the voice (e.g., 'M1', 'your-hume-voice-id')
        name: Human-readable name (e.g., 'Male Voice 1', 'Custom Voice')
        language: Language code (e.g., 'en-US', 'en', 'es')
        gender: Gender of the voice ('male', 'female', 'neutral', or None)
        description: Optional description of the voice characteristics
    """
    id: str
    name: str
    language: str = 'en'
    gender: Optional[str] = None
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the voice to a dictionary representation."""
        return {
            'id': self.id,
            'name': self.name,
            'language': self.language,
            'gender': self.gender,
            'description': self.description
        }


@dataclass
class TTSProviderInfo:
    """
    Metadata about a TTS provider.

    Attributes:
        name: Provider name (e.g., 'supertonic', 'hume')
        display_name: Human-readable name (e.g., 'Supertonic TTS', 'Hume EVI')
        version: Provider version string
        cost_per_minute: Cost in USD per minute of generated audio
        quality: Quality rating ('low', 'medium', 'high', 'premium')
        latency: Expected latency ('instant', 'fast', 'medium', 'slow')
        features: List of feature strings (e.g., ['emotion-aware', 'multi-language'])
        requires_api_key: Whether the provider requires an API key
        is_online: Whether the provider requires internet connectivity
        status: Current status ('active', 'inactive', 'error')
    """
    name: str
    display_name: str
    version: str
    cost_per_minute: float
    quality: str
    latency: str
    features: List[str]
    requires_api_key: bool
    is_online: bool
    status: str = 'active'

    def to_dict(self) -> Dict[str, Any]:
        """Convert the provider info to a dictionary representation."""
        return {
            'name': self.name,
            'display_name': self.display_name,
            'version': self.version,
            'cost_per_minute': self.cost_per_minute,
            'quality': self.quality,
            'latency': self.latency,
            'features': self.features,
            'requires_api_key': self.requires_api_key,
            'is_online': self.is_online,
            'status': self.status
        }


class TTSProvider(ABC):
    """
    Abstract base class for Text-to-Speech providers.

    All TTS providers must inherit from this class and implement the required methods.
    This ensures a consistent interface across different TTS backends.

    Required Methods:
        - generate_speech(text, **kwargs): Convert text to audio bytes
        - list_voices(): Return list of available voice names
        - get_info(): Return provider metadata (name, status, capabilities)

    Example:
        >>> class MyTTS(TTSProvider):
        ...     def generate_speech(self, text, **kwargs):
        ...         # Implementation here
        ...         return audio_bytes
        ...     def list_voices(self):
        ...         return ['voice1', 'voice2']
        ...     def get_info(self):
        ...         return {'name': 'MyTTS', 'status': 'active'}
    """

    @abstractmethod
    def generate_speech(self, text: str, **kwargs) -> bytes:
        """
        Generate speech audio from the given text.

        Args:
            text: The text to synthesize into speech.
            **kwargs: Provider-specific parameters (voice, speed, lang, etc.)

        Returns:
            bytes: Raw audio data (usually WAV format) that can be written
                   to a file or sent via HTTP with Content-Type: audio/wav.

        Raises:
            ValueError: If text is empty or parameters are invalid.
            RuntimeError: If speech generation fails.

        Example:
            >>> audio = provider.generate_speech("Hello world", voice='M1')
            >>> with open('output.wav', 'wb') as f:
            ...     f.write(audio)
        """
        pass

    @abstractmethod
    def list_voices(self) -> List[str]:
        """
        Return a list of available voice names for this provider.

        Returns:
            List[str]: List of voice identifiers (e.g., ['M1', 'M2', 'F1']).
                       These IDs should be valid values for a 'voice' parameter
                       in generate_speech().

        Example:
            >>> provider.list_voices()
            ['M1', 'M2', 'F1', 'F2']
        """
        pass

    @abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """
        Return metadata about this TTS provider.

        Returns:
            Dict with at minimum:
                - 'name': str - Provider display name
                - 'status': str - 'active', 'inactive', or 'error'
                - 'description': str - Brief description of the provider
                - 'capabilities': dict - Optional feature flags
                    - 'streaming': bool - Supports streaming audio
                    - 'ssml': bool - Supports SSML markup
                    - 'custom_voices': bool - Supports custom voice cloning
                    - 'languages': List[str] - Supported language codes

        Example:
            >>> provider.get_info()
            {
                'name': 'Supertonic',
                'status': 'active',
                'description': 'Local ONNX-based TTS engine',
                'capabilities': {
                    'streaming': False,
                    'ssml': False,
                    'custom_voices': True,
                    'languages': ['en', 'ko', 'es', 'pt', 'fr']
                }
            }
        """
        pass

    def is_available(self) -> bool:
        """
        Check if this provider is currently available for use.

        Returns:
            bool: True if the provider is active and ready, False otherwise.

        Default implementation checks if get_info()['status'] == 'active'.
        Subclasses can override for more complex availability checks.

        Example:
            >>> if provider.is_available():
            ...     audio = provider.generate_speech("Hello")
        """
        return self.get_info().get('status', 'inactive') == 'active'

    def validate_text(self, text: str) -> None:
        """
        Validate that text is suitable for speech generation.

        Args:
            text: Text to validate.

        Raises:
            ValueError: If text is None, empty, or only whitespace.

        Example:
            >>> provider.validate_text("Hello world")  # OK
            >>> provider.validate_text("")  # Raises ValueError
        """
        if text is None:
            raise ValueError("Text cannot be None")
        if not isinstance(text, str):
            raise ValueError(f"Text must be a string, got {type(text).__name__}")
        if not text.strip():
            raise ValueError("Text cannot be empty or contain only whitespace")

    def validate_voice(self, voice: str) -> bool:
        """
        Check if a given voice name is valid for this provider.

        Args:
            voice: Voice identifier to validate.

        Returns:
            bool: True if the voice is available, False otherwise.

        Example:
            >>> provider.validate_voice('M1')
            True
            >>> provider.validate_voice('invalid')
            False
        """
        return voice in self.list_voices()

    def get_default_voice(self) -> Optional[str]:
        """
        Return the default voice for this provider.

        Returns:
            The first voice in list_voices(), or None if no voices available.

        Example:
            >>> provider.get_default_voice()
            'M1'
        """
        voices = self.list_voices()
        return voices[0] if voices else None

    def __repr__(self) -> str:
        """String representation of the provider."""
        info = self.get_info()
        return f"{self.__class__.__name__}(name='{info.get('name', 'Unknown')}', status='{info.get('status', 'unknown')}')"


class TTSProviderError(Exception):
    """
    Base exception class for TTS provider errors.

    This exception is raised when a TTS provider encounters an error
    that is specific to the provider implementation.
    """

    def __init__(self, provider_name: str, message: str):
        """
        Initialize the exception.

        Args:
            provider_name: Name of the provider that raised the error.
            message: Error message describing what went wrong.
        """
        self.provider_name = provider_name
        super().__init__(f"[{provider_name}] {message}")


class TTSGenerationError(TTSProviderError):
    """
    Raised when speech generation fails.

    This can occur due to invalid input, network issues, or
    problems with the TTS service.
    """
    pass


class TTSConfigurationError(TTSProviderError):
    """
    Raised when the provider is misconfigured.

    This can occur due to missing API keys, invalid paths,
    or other configuration issues.
    """
    pass


class TTSVoiceNotFoundError(TTSProviderError):
    """
    Raised when a requested voice is not available.

    This occurs when a voice ID is specified that doesn't
    exist in the provider's voice catalog.
    """
    pass



# ===========================================================================
# Voice-clone PROVIDER-SEAM (docs/jambot/ovu-voice-clone-seam-spec.md §2)
# ===========================================================================
# A provider that can create a voice clone implements CloneCapableTTSProvider.
# POST /api/tts/clone selects providers through the registry (catalog feature
# "voice-cloning" + a class implementing this ABC + availability) instead of a
# hard-coded tuple, so a new provider (e.g. the GPU lane's qwen3-gpu) plugs in
# without touching the route.

#: Error enum, spec §2.4. Each code maps to one HTTP status in the route.
CLONE_ERROR_CODES = (
    'unavailable', 'busy', 'bad_audio', 'quota',
    'consent_mismatch', 'timeout', 'internal',
)

_CLONE_ERROR_RETRYABLE = {
    'unavailable': True,
    'busy': True,
    'bad_audio': False,
    'quota': False,
    'consent_mismatch': False,
    'timeout': True,
    'internal': False,
}


class CloneError(Exception):
    """A typed clone failure (spec §2.4).

    Raised by ``CloneCapableTTSProvider.create_clone`` (or derived from a
    ``CloneResult`` with ``status == 'failed'``). The route maps ``code`` to an
    HTTP status; anything that is NOT a CloneError keeps today's behaviour
    (RuntimeError -> 500 with its message, anything else -> opaque 500).

    Args:
        code: one of CLONE_ERROR_CODES.
        message: human-readable detail (never audio bytes, never an access code).
        retryable: defaults per code (unavailable/busy/timeout retryable).
        retry_after: seconds, sent as the Retry-After header when set
            (always sent for ``busy``; the route defaults it).
    """

    def __init__(self, code: str, message: str = '',
                 retryable: Optional[bool] = None,
                 retry_after: Optional[int] = None):
        if code not in CLONE_ERROR_CODES:
            raise ValueError(
                f"Unknown CloneError code {code!r}; expected one of {CLONE_ERROR_CODES}"
            )
        self.code = code
        self.message = message or code
        self.retryable = (_CLONE_ERROR_RETRYABLE[code]
                          if retryable is None else bool(retryable))
        self.retry_after = retry_after
        super().__init__(f"[{code}] {self.message}")

    def to_dict(self) -> Dict[str, Any]:
        return {'code': self.code, 'retryable': self.retryable,
                'message': self.message}

    @classmethod
    def from_dict(cls, err: Optional[Dict[str, Any]]) -> 'CloneError':
        err = err or {}
        code = err.get('code', 'internal')
        if code not in CLONE_ERROR_CODES:
            code = 'internal'
        return cls(code, err.get('message', ''), err.get('retryable'),
                   err.get('retry_after'))


@dataclass
class CloneRequest:
    """One clone request (spec §2.2).

    ``tenant_id`` comes from the OVU container env, NEVER from the client body.
    ``audio_url`` is not in spec §2.2 but is required by the fal.ai qwen3
    provider, which takes a URL rather than a file; it is the public URL of
    the same saved upload (or the caller's URL on the JSON path).
    Fields marked TODO are carried so the shape is stable, but nothing in this
    release populates or enforces them (access codes: spec §1, consent: §3,
    duration caps: §3.4).
    """
    tenant_id: str
    request_id: str
    name: str
    audio_path: Optional[str] = None
    audio_url: Optional[str] = None
    audio_sha256: Optional[str] = None     # TODO spec §2.2/§4
    duration_s: Optional[float] = None     # TODO spec §3.4 (ffprobe)
    reference_text: Optional[str] = None
    language: Optional[str] = None
    consent_id: Optional[str] = None       # TODO spec §3
    code_id: Optional[str] = None          # TODO spec §1


@dataclass
class CloneResult:
    """Outcome of ``create_clone`` / ``get_clone_status`` (spec §2.2).

    ``metadata`` is the provider's own record, verbatim (e.g. the dict the
    legacy ``clone_voice`` returned); the route reads provider-specific extras
    such as ``embedding_size`` from it so its success envelope is unchanged.
    """
    status: str                       # 'ready' | 'pending' | 'failed'
    provider: str
    name: str
    voice_id: Optional[str] = None
    job_id: Optional[str] = None
    created_at: str = ''
    clone_time_ms: int = 0
    voice_ref: str = ''               # opaque provider handle, stored verbatim
    error: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(cls, provider: str, requested_name: str,
                    record: Dict[str, Any], voice_ref: str = '') -> 'CloneResult':
        """Wrap the dict a legacy ``clone_voice`` returned, field-for-field
        the way the pre-seam route read it (voice_id default '', name default
        = requested name, created_at default '', clone_time_ms default 0)."""
        record = record or {}
        return cls(
            status='ready',
            provider=provider,
            name=record.get('name', requested_name),
            voice_id=record.get('voice_id', ''),
            created_at=record.get('created_at', ''),
            clone_time_ms=record.get('clone_time_ms', 0),
            voice_ref=voice_ref,
            metadata=record,
        )


@dataclass
class CloneInfo:
    """One stored clone as returned by ``list_clones`` (spec §2.1)."""
    voice_id: str
    name: str
    provider: str
    created_at: str = ''
    tenant_id: Optional[str] = None
    consent_id: Optional[str] = None
    audio_sha256: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'voice_id': self.voice_id, 'name': self.name,
            'provider': self.provider, 'created_at': self.created_at,
            'tenant_id': self.tenant_id, 'consent_id': self.consent_id,
            'audio_sha256': self.audio_sha256,
        }


class CloneCapableTTSProvider(TTSProvider):
    """A TTSProvider that can create voice clones (spec §2.1).

    Subclasses set the class attributes below and implement ``create_clone``,
    ``list_clones`` and ``disable_clone``. ``get_clone_status`` is only needed
    when ``async_clone`` is True. ``health`` defaults to wrapping
    ``health_check()``/``is_available()``.

    Class attributes:
        clone_label: human name used in input-validation errors.
        clone_input: accepted reference inputs, subset of ('bytes','path','url').
            The route rejects a request that carries none of them (400).
        async_clone: True if create_clone may return status 'pending'.
        min_ref_seconds / max_ref_seconds: reference length bounds or None.
        clone_languages: languages a clone can speak (empty = unknown).
    """

    clone_label: str = ''
    clone_input: tuple = ('path',)
    async_clone: bool = False
    min_ref_seconds: Optional[int] = None
    max_ref_seconds: Optional[int] = None
    clone_languages: tuple = ()

    def clone_capabilities(self) -> Dict[str, Any]:
        """The ``get_info()['capabilities']`` clone block (spec §2.1)."""
        return {
            'clone': True,
            'clone_input': list(self.clone_input),
            'async_clone': bool(self.async_clone),
            'max_ref_seconds': self.max_ref_seconds,
            'min_ref_seconds': self.min_ref_seconds,
            'languages': list(self.clone_languages),
        }

    def clone_request_error(self, req: CloneRequest) -> Optional[str]:
        """Return a 400 message if ``req`` carries no input this provider
        accepts, else None. Checked by the route BEFORE create_clone, so a bad
        request spends nothing."""
        label = self.clone_label or self.__class__.__name__
        accepts = set(self.clone_input)
        if 'path' in accepts and req.audio_path:
            return None
        if 'url' in accepts and req.audio_url:
            return None
        if 'path' in accepts:
            return f'{label} requires audio file upload (multipart form)'
        return f'{label} requires audio_url'

    @abstractmethod
    def create_clone(self, req: CloneRequest) -> CloneResult:
        """Create a clone. Sync providers return status 'ready'; async ones
        may return 'pending' + job_id. Raise CloneError for typed failures."""

    def get_clone_status(self, job_id: str) -> CloneResult:
        """Poll an async clone job. Sync providers have no jobs."""
        raise CloneError(
            'internal',
            f'{self.clone_label or self.__class__.__name__} clones synchronously; '
            f'there is no job {job_id!r}',
        )

    def synthesize(self, text: str, voice_id: Optional[str] = None, **kwargs) -> bytes:
        """Alias of generate_speech (spec §2.1) — no new behaviour."""
        if voice_id is not None:
            kwargs['voice'] = voice_id
        return self.generate_speech(text, **kwargs)

    @abstractmethod
    def list_clones(self, tenant_id: str) -> List[CloneInfo]:
        """Clones belonging to ``tenant_id`` only."""

    @abstractmethod
    def disable_clone(self, tenant_id: str, voice_id: str, reason: str = '') -> None:
        """Soft-retire a clone (rename/flag). NEVER a hard delete."""

    def health(self) -> Dict[str, Any]:
        """{'state': 'ok'|'degraded'|'down', 'detail': str, 'load': float|None}."""
        check = getattr(self, 'health_check', None)
        if callable(check):
            try:
                h = check() or {}
            except Exception as e:  # a health probe must never raise
                return {'state': 'down', 'detail': str(e), 'load': None}
            return {'state': 'ok' if h.get('ok') else 'down',
                    'detail': str(h.get('detail', '')), 'load': None}
        ok = self.is_available()
        return {'state': 'ok' if ok else 'down',
                'detail': '' if ok else 'not available', 'load': None}


def soft_retire_clone_dir(base_dir, voice_id: str, reason: str = '') -> 'Path':
    """Rename ``base_dir/voice_id`` to ``<voice_id>.retired`` (never delete).

    Same rule as DELETE /api/tts/voices/<id>: only ``clone_*`` ids, and the
    path must stay inside ``base_dir``. Writes a small ``retired.json`` note
    with the reason into the renamed folder. Returns the new path.
    """
    import json as _json
    import time as _time
    from pathlib import Path

    if not voice_id or not voice_id.startswith('clone_'):
        raise CloneError('internal',
                         f'Can only retire cloned voices (clone_*), got {voice_id!r}')
    base = Path(base_dir)
    voice_dir = base / voice_id
    try:
        voice_dir.resolve().relative_to(base.resolve())
    except ValueError:
        raise CloneError('internal', f'Invalid voice_id {voice_id!r}')
    if not voice_dir.is_dir():
        raise CloneError('internal', f'Voice {voice_id} not found')
    renamed = voice_dir.with_name(voice_dir.name + '.retired')
    voice_dir.rename(renamed)
    try:
        (renamed / 'retired.json').write_text(_json.dumps({
            'voice_id': voice_id, 'reason': reason,
            'retired_at': _time.strftime('%Y-%m-%dT%H:%M:%SZ', _time.gmtime()),
        }, indent=2))
    except OSError:
        pass
    return renamed

__all__ = [
    'TTSProvider',
    'TTSVoice',
    'TTSProviderInfo',
    'TTSProviderError',
    'TTSGenerationError',
    'TTSConfigurationError',
    'TTSVoiceNotFoundError',
    'CLONE_ERROR_CODES',
    'CloneError',
    'CloneRequest',
    'CloneResult',
    'CloneInfo',
    'CloneCapableTTSProvider',
    'soft_retire_clone_dir',
]
