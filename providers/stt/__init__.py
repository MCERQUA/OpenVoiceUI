"""STT provider package.

Importing this package registers all STT providers with the registry.
"""

from providers.stt.base import STTProvider, TranscriptionResult, STTError

# Import concrete providers so their registry.register() calls fire
from providers.stt import webspeech_provider  # noqa: F401
from providers.stt import whisper_provider  # noqa: F401
from providers.stt import external_provider  # noqa: F401

__all__ = ["STTProvider", "TranscriptionResult", "STTError"]
