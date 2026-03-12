"""
services/tts.py — Unified TTS Service

Consolidates all TTS generation logic from server.py and tts_providers/.
Provides a single entry point for generating speech audio.

Providers:
  - Groq Orpheus TTS (primary, cloud-based)
  - Supertonic TTS (local ONNX, fallback)

Usage:
    from services.tts import generate_tts_b64, generate_tts_chunked

    audio_b64 = generate_tts_b64(text, voice='M1')
"""

import base64
import logging
import os
import re
import struct
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ===== GROQ TTS =====

_groq_client = None


def get_groq_client():
    """Get or initialize Groq client (lazy, cached)."""
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv('GROQ_API_KEY')
        if api_key:
            try:
                from groq import Groq
                _groq_client = Groq(api_key=api_key)
                logger.info("Groq TTS client initialized")
            except ImportError:
                logger.warning("groq package not installed — Groq TTS unavailable")
        else:
            logger.warning("GROQ_API_KEY not set — Groq TTS unavailable")
    return _groq_client


def generate_groq_tts(text: str, voice: str = 'autumn') -> bytes:
    """
    Generate TTS audio using Groq Orpheus (canopylabs/orpheus-v1-english).

    Args:
        text: Text to synthesize.
        voice: Orpheus voice name (default 'autumn').

    Returns:
        MP3 audio bytes.

    Raises:
        RuntimeError: If Groq client unavailable or API call fails.
    """
    groq = get_groq_client()
    if not groq:
        raise RuntimeError("Groq client not available")
    tts_response = groq.audio.speech.create(
        model="canopylabs/orpheus-v1-english",
        input=text,
        voice=voice,
        response_format="mp3"
    )
    audio_bytes = tts_response.content if hasattr(tts_response, 'content') else tts_response.read()
    logger.info(f"Groq Orpheus TTS generated: {len(audio_bytes)} bytes")
    return audio_bytes


# ===== SUPERTONIC TTS =====

from tts_providers import get_provider, list_providers  # noqa: E402 — after stdlib imports


def generate_tts_chunked(provider, text: str, voice: str, max_chars: int = 800) -> bytes:
    """
    Generate TTS audio with chunking to avoid Supertonic ONNX overflow.

    Supertonic ONNX crashes with RUNTIME_EXCEPTION when text exceeds ~1000 tokens.
    Splits long text on sentence boundaries, generates each chunk, then
    concatenates the raw PCM data into a single WAV file.

    Args:
        provider: TTSProvider instance (e.g. SupertonicProvider).
        text: Text to synthesize.
        voice: Voice identifier (e.g. 'M1').
        max_chars: Max characters per chunk. Default 800.

    Returns:
        WAV audio bytes (concatenated from all chunks).
    """
    # Short text — no chunking needed
    if len(text) <= max_chars:
        return provider.generate_speech(text=text, voice=voice, speed=1.05, total_step=40)

    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 > max_chars and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            current_chunk = (current_chunk + " " + sentence).strip()
    if current_chunk:
        chunks.append(current_chunk.strip())

    logger.info(f"TTS chunking: {len(text)} chars -> {len(chunks)} chunks (max {max_chars})")

    all_audio_data = b""
    sample_rate = None
    num_channels = None
    bits_per_sample = None

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        try:
            chunk_audio = provider.generate_speech(text=chunk, voice=voice, speed=1.05, total_step=40)
            if i == 0:
                if chunk_audio[:4] == b'RIFF' and chunk_audio[8:12] == b'WAVE':
                    pos = 12
                    while pos < len(chunk_audio) - 8:
                        chunk_id = chunk_audio[pos:pos + 4]
                        chunk_size = struct.unpack('<I', chunk_audio[pos + 4:pos + 8])[0]
                        if chunk_id == b'fmt ':
                            fmt_data = chunk_audio[pos + 8:pos + 8 + chunk_size]
                            num_channels = struct.unpack('<H', fmt_data[2:4])[0]
                            sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
                            bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
                        elif chunk_id == b'data':
                            all_audio_data += chunk_audio[pos + 8:pos + 8 + chunk_size]
                            break
                        pos += 8 + chunk_size
                else:
                    return chunk_audio
            else:
                if chunk_audio[:4] == b'RIFF':
                    pos = 12
                    while pos < len(chunk_audio) - 8:
                        chunk_id = chunk_audio[pos:pos + 4]
                        chunk_size = struct.unpack('<I', chunk_audio[pos + 4:pos + 8])[0]
                        if chunk_id == b'data':
                            all_audio_data += chunk_audio[pos + 8:pos + 8 + chunk_size]
                            break
                        pos += 8 + chunk_size
            logger.info(f"  Chunk {i + 1}/{len(chunks)}: {len(chunk)} chars OK")
        except Exception as e:
            logger.error(f"  Chunk {i + 1}/{len(chunks)} FAILED: {e}")

    if not all_audio_data or sample_rate is None:
        logger.warning("All TTS chunks failed, trying truncated text")
        return provider.generate_speech(text=text[:max_chars], voice=voice, speed=1.05, total_step=40)

    # Rebuild WAV with concatenated PCM data
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    data_size = len(all_audio_data)
    file_size = 36 + data_size

    wav_header = struct.pack('<4sI4s', b'RIFF', file_size, b'WAVE')
    fmt_chunk = struct.pack('<4sIHHIIHH', b'fmt ', 16, 1,
                            num_channels, sample_rate, byte_rate, block_align, bits_per_sample)
    data_header = struct.pack('<4sI', b'data', data_size)

    return wav_header + fmt_chunk + data_header + all_audio_data


# ===== UNIFIED GENERATE FUNCTION =====

# Fallback order when a provider fails (provider_id → fallback_id)
_FALLBACK_CHAIN = {
    'groq': 'supertonic',
    'qwen3': 'supertonic',
}

_MAX_RETRIES = 2
_RETRY_DELAYS = (0.5, 1.5)  # seconds between retries


def _generate_with_provider(tts_provider: str, text: str, voice: str) -> bytes:
    """Generate audio bytes from a single provider (no retry/fallback)."""
    provider = get_provider(tts_provider)
    provider_info = provider.get_info()
    audio_format = provider_info.get('audio_format', 'wav')

    if audio_format == 'mp3':
        return provider.generate_speech(text=text, voice=voice)
    return generate_tts_chunked(provider, text, voice)


def generate_tts_b64(
    text: str,
    voice: Optional[str] = None,
    tts_provider: str = 'supertonic',
    **kwargs,
) -> Optional[str]:
    """
    Generate TTS audio and return as a base64-encoded string.

    Retries transient failures up to _MAX_RETRIES times, then falls back
    to an alternate provider (e.g. groq → supertonic).

    Args:
        text: Text to synthesize.
        voice: Voice ID (provider-specific). Defaults to provider default.
        tts_provider: Provider ID ('supertonic', 'groq', 'qwen3', etc.).

    Returns:
        Base64-encoded audio string, or None on failure.
    """
    voice = voice or 'M1'

    # ── Try primary provider (single attempt for cloud, retries for local) ──
    last_err = None
    # Cloud providers (groq, qwen3) have their own timeout — don't retry
    # on timeout, fall back immediately. Only retry local providers.
    is_cloud = tts_provider in ('groq', 'qwen3')
    max_attempts = 1 if is_cloud else _MAX_RETRIES + 1
    for attempt in range(max_attempts):
        try:
            audio_bytes = _generate_with_provider(tts_provider, text, voice)
            logger.info(f"TTS generated: provider={tts_provider}, voice={voice}, attempt={attempt + 1}")
            return base64.b64encode(audio_bytes).decode('utf-8')
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(f"TTS attempt {attempt + 1} failed (provider={tts_provider}): {e} — retrying in {delay}s")
                time.sleep(delay)
            else:
                logger.warning(f"TTS failed (provider={tts_provider}): {e} — trying fallback")

    # ── Fallback to alternate provider ───────────────────────────────
    fallback_id = _FALLBACK_CHAIN.get(tts_provider)
    if fallback_id:
        logger.info(f"TTS falling back: {tts_provider} → {fallback_id}")
        try:
            fallback_provider = get_provider(fallback_id)
            fallback_voice = fallback_provider.get_default_voice()
            audio_bytes = _generate_with_provider(fallback_id, text, fallback_voice)
            logger.info(f"TTS fallback OK: provider={fallback_id}, voice={fallback_voice}")
            return base64.b64encode(audio_bytes).decode('utf-8')
        except Exception as fb_err:
            logger.error(f"TTS fallback also failed (provider={fallback_id}): {fb_err}")

    logger.error(f"TTS generation failed — all providers exhausted for: '{text[:60]}'")
    return None


__all__ = [
    'get_groq_client',
    'generate_groq_tts',
    'generate_tts_chunked',
    'generate_tts_b64',
    'get_provider',
    'list_providers',
]
