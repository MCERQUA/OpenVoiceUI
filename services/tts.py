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
                # JamBot Books: record Groq STT/TTS/vision calls (file-drop leg,
                # tailed by the host scraper). Fully guarded — never breaks voice.
                try:
                    from services.jambot_books_hook import attach_groq
                    attach_groq(_groq_client)
                except Exception:
                    pass
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
        response_format="wav"  # Groq Orpheus now ONLY accepts wav; mp3 → 400 (2026-06-15)
    )
    audio_bytes = tts_response.content if hasattr(tts_response, 'content') else tts_response.read()
    logger.info(f"Groq Orpheus TTS generated: {len(audio_bytes)} bytes")
    return audio_bytes


# ===== SUPERTONIC TTS =====

from tts_providers import get_provider, list_providers  # noqa: E402 — after stdlib imports


def generate_tts_chunked(provider, text: str, voice: str, max_chars: int = 800) -> bytes:
    """
    Generate TTS audio with chunking for WAV providers.

    Splits long text on sentence boundaries, generates each chunk, then
    concatenates the raw PCM data into a single WAV file.
    Works with any WAV-output provider (Supertonic, Resemble, etc.).

    Args:
        provider: TTSProvider instance.
        text: Text to synthesize.
        voice: Voice identifier.
        max_chars: Max characters per chunk. Default 800.

    Returns:
        WAV audio bytes (concatenated from all chunks).
    """
    # Supertonic-specific kwargs (ignored by other providers via **kwargs)
    provider_id = provider.get_info().get('provider_id', '')
    extra_kwargs = {'speed': 1.05, 'total_step': 40} if provider_id == 'supertonic' else {}

    # Short text — no chunking needed
    if len(text) <= max_chars:
        return provider.generate_speech(text=text, voice=voice, **extra_kwargs)

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

    # TTS-6: parse the WAV format (fmt chunk) from the FIRST SUCCESSFUL RIFF chunk,
    # not specifically chunk 0. If chunk 0 fails or comes back non-RIFF, we must
    # still stitch the chunks that DID succeed rather than discarding all of them
    # (double-billing) or returning only chunk 0 and dropping the rest.
    non_riff_only = None  # remember a lone non-RIFF payload for the single-chunk case
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        try:
            chunk_audio = provider.generate_speech(text=chunk, voice=voice, **extra_kwargs)
        except Exception as e:
            logger.error(f"  Chunk {i + 1}/{len(chunks)} FAILED: {e}")
            continue

        if chunk_audio[:4] == b'RIFF' and chunk_audio[8:12] == b'WAVE':
            pos = 12
            while pos < len(chunk_audio) - 8:
                chunk_id = chunk_audio[pos:pos + 4]
                chunk_size = struct.unpack('<I', chunk_audio[pos + 4:pos + 8])[0]
                if chunk_id == b'fmt ' and sample_rate is None:
                    fmt_data = chunk_audio[pos + 8:pos + 8 + chunk_size]
                    num_channels = struct.unpack('<H', fmt_data[2:4])[0]
                    sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
                    bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
                elif chunk_id == b'data':
                    all_audio_data += chunk_audio[pos + 8:pos + 8 + chunk_size]
                    break
                pos += 8 + chunk_size
            logger.info(f"  Chunk {i + 1}/{len(chunks)}: {len(chunk)} chars OK")
        else:
            # Non-RIFF payload (unexpected for a WAV provider). Don't let it drop the
            # rest of the reply: if it's the only chunk we have anything from, keep it
            # as a last resort; otherwise skip it and keep stitching the RIFF chunks.
            logger.warning(f"  Chunk {i + 1}/{len(chunks)}: non-RIFF audio — skipping in concat")
            if non_riff_only is None:
                non_riff_only = chunk_audio

    if not all_audio_data or sample_rate is None:
        if non_riff_only is not None:
            logger.warning("No RIFF chunks stitched — returning the non-RIFF chunk as-is")
            return non_riff_only
        logger.warning("All TTS chunks failed, trying truncated text")
        return provider.generate_speech(text=text[:max_chars], voice=voice, **extra_kwargs)

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

# Fallback order when a provider fails (provider_id → next fallback_id).
# The chain is followed TRANSITIVELY with cycle protection (TTS-7): e.g.
# resemble → groq → supertonic, elevenlabs → groq → supertonic, groq →
# supertonic. The chain + voice-gender map now live in config/tts-fallback.json
# (WO-3.1) and are read THROUGH services.tts_fallback at call time so an admin
# edit takes effect on the next utterance with no restart. The inline defaults
# in services.tts_fallback._DEFAULT_FALLBACK are the guaranteed fallback.
from services.tts_fallback import (  # noqa: E402
    get_fallback_chain, get_voice_gender_map,
)

_MAX_RETRIES = 2
_RETRY_DELAYS = (0.5, 1.0, 1.5, 2.0)  # seconds between retries


# Groq structured error codes (from groq_provider "[groq:<code>]") that are
# client-side / non-retryable — treat like an HTTP 4xx and fall back immediately.
_GROQ_4XX_CODES = (
    'invalid_api_key', 'rate_limit_exceeded', 'model_terms_required',
    'insufficient_quota', 'permission_denied', 'model_not_found',
)


def _classify_http_error(err_str: str) -> Optional[str]:
    """Classify a provider error string as '4xx', '5xx', or None.

    Recognizes the distinct shapes each provider raises (TTS-5):
      - Resemble  : "Resemble API error 4xx/5xx"
      - Groq      : "[groq:<code>] ..."  (string codes, mapped to 4xx/5xx)
      - ElevenLabs: "ElevenLabs TTS error 401: ..."
    A 4xx (bad auth / quota / rate-limit) must NEVER be retried — it won't fix
    on retry and the retry sleep just delays the fallback.
    """
    # Numeric HTTP status embedded by Resemble / ElevenLabs
    m = re.search(r'(?:API|TTS) error (\d{3})', err_str)
    if m:
        code = int(m.group(1))
        if 400 <= code < 500:
            return '4xx'
        if 500 <= code < 600:
            return '5xx'
    # Groq structured code
    gm = re.search(r'\[groq:([^\]]+)\]', err_str)
    if gm:
        gcode = gm.group(1)
        if gcode in _GROQ_4XX_CODES or gcode.startswith('4'):
            return '4xx'
        if gcode.startswith('5') or gcode in ('internal_server_error', 'service_unavailable'):
            return '5xx'
    return None


def _map_voice_to_fallback(voice: str, src_provider: str, dst_provider: str) -> str:
    """Map a voice from one provider to the closest equivalent on another."""
    gender = get_voice_gender_map().get(voice, 'M')  # default male if unknown
    if dst_provider == 'supertonic':
        return 'M1' if gender == 'M' else 'F1'
    if dst_provider == 'groq':
        return 'troy' if gender == 'M' else 'autumn'
    return voice  # pass through if we don't know the destination


def _generate_with_provider(tts_provider: str, text: str, voice: str) -> bytes:
    """Generate audio bytes from a single provider (no retry/fallback)."""
    provider = get_provider(tts_provider)
    provider_info = provider.get_info()
    audio_format = provider_info.get('audio_format', 'wav')

    # Resemble returns WAV. Their streaming endpoint accepts up to 2000 chars
    # per request — we chunk at 1500 (with 500 char headroom) to minimize the
    # number of individual API calls. Fewer requests = fewer chances for any
    # single one to hit a transient cluster issue.
    if tts_provider == 'resemble':
        # Resemble's stream DROPS long requests — a ~900+ char / ~36s generation gets
        # "RemoteProtocolError: peer closed connection" → after retries that's total
        # silence. Chunk at 400 chars so every request finishes well under the drop
        # threshold. The chunker splits on sentence boundaries and tolerates a single
        # chunk failing as a small gap rather than dropping the whole reply. (2026-06-07)
        if len(text) > 400:
            return generate_tts_chunked(provider, text, voice, max_chars=400)
        return provider.generate_speech(text=text, voice=voice)
    # Cloud providers returning MP3 (groq, elevenlabs) handle their own limits
    if audio_format == 'mp3':
        return provider.generate_speech(text=text, voice=voice)
    # Local WAV providers (supertonic) need ONNX overflow chunking
    return generate_tts_chunked(provider, text, voice)


def generate_tts_b64(
    text: str,
    voice: Optional[str] = None,
    tts_provider: str = 'groq',
    fallback_state: Optional[dict] = None,
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
        fallback_state: Optional mutable dict for sticky fallback across
            sentences in a single response. When a fallback fires, this dict
            is updated with {'provider': ..., 'voice': ...} so subsequent
            calls use the fallback directly (avoids voice switching mid-response).

    Returns:
        Base64-encoded audio string, or None on failure.
    """
    voice = voice or 'M1'

    # Sticky fallback: if a previous sentence fell back, keep voice consistent —
    # EXCEPT for Resemble, where the user strongly prefers the real custom voice
    # over consistency. Each Resemble sentence retries independently so a single
    # cluster hiccup doesn't doom the whole response to the fallback voice.
    if fallback_state and fallback_state.get('provider') and tts_provider != 'resemble':
        tts_provider = fallback_state['provider']
        voice = fallback_state['voice']
        logger.info(f"TTS using sticky fallback: provider={tts_provider}, voice={voice}")

    # ── Try primary provider ──────────────────────────────────────────────────
    last_err = None
    # Resemble gets 2 attempts (was 6 — TTS-2). Its custom CLONE voice is worth a
    # single retry on an intermittent cluster 500, but 6 attempts × a 120s timeout
    # head-of-line-blocked the ordered flush for MINUTES. Chunking at 400 chars
    # (see _generate_with_provider) keeps each request short, so 2 attempts against
    # the now-30s STREAM_TIMEOUT is the sane ceiling. Other cloud providers
    # (groq/elevenlabs/qwen3) also get 2.
    if tts_provider in ('resemble', 'groq', 'qwen3', 'qwen3-local', 'elevenlabs'):
        max_attempts = 2
    else:
        max_attempts = _MAX_RETRIES + 1
    for attempt in range(max_attempts):
        try:
            audio_bytes = _generate_with_provider(tts_provider, text, voice)
            logger.info(f"TTS generated: provider={tts_provider}, voice={voice}, attempt={attempt + 1}")
            return base64.b64encode(audio_bytes).decode('utf-8')
        except Exception as e:
            last_err = e
            err_str = str(e)
            # 4xx = client error (bad auth/payload/quota/rate-limit) — never retry;
            # it won't fix on retry and the sleep just delays the fallback. 5xx is a
            # real outage → most providers fast-fail. EXCEPTION: Resemble's cluster
            # throws INTERMITTENT 500s (a retry seconds later usually succeeds), and
            # its base clone has no equal-voice fallback, so for Resemble we retry a
            # 5xx once instead of dropping straight to silence. (2026-06-07)
            http_class = _classify_http_error(err_str)
            if http_class == '4xx' or (http_class == '5xx' and tts_provider != 'resemble'):
                logger.warning(f"TTS HTTP {http_class}, no retry (provider={tts_provider}): {e} — falling back")
                break
            if attempt < max_attempts - 1:
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                logger.warning(f"TTS attempt {attempt + 1} failed (provider={tts_provider}): {e} — retrying in {delay}s")
                time.sleep(delay)
            else:
                logger.warning(f"TTS failed (provider={tts_provider}): {e} — trying fallback")

    # ── Fallback through the chain (transitive, cycle-protected — TTS-7) ──────
    # e.g. resemble → groq → supertonic. If groq is also down, we keep walking
    # to supertonic (free, always-on) instead of going silent.
    # Custom CLONED voices (Resemble — e.g. bhb's Kyle) must NEVER be substituted
    # with a different voice. For tenants with RESEMBLE_NO_FALLBACK=1, a brief
    # silence beats a wrong/switching voice. (Mike 2026-06-07, bhb)
    resemble_no_fallback = (
        tts_provider == 'resemble'
        and os.getenv('RESEMBLE_NO_FALLBACK', '').strip().lower() in ('1', 'true', 'yes', 'on')
    )

    tried = {tts_provider}
    current = tts_provider
    while not resemble_no_fallback:
        fallback_id = get_fallback_chain().get(current)
        if not fallback_id or fallback_id in tried:
            break
        tried.add(fallback_id)
        logger.info(f"TTS falling back: {current} → {fallback_id}")
        try:
            # Map the ORIGINAL voice to the closest match on the fallback provider
            # to minimize jarring voice switches mid-response.
            fallback_voice = _map_voice_to_fallback(voice, tts_provider, fallback_id)
            audio_bytes = _generate_with_provider(fallback_id, text, fallback_voice)
            logger.info(f"TTS fallback OK: provider={fallback_id}, voice={fallback_voice} (original: {tts_provider}/{voice})")
            # Lock sticky fallback for non-Resemble originals — keeps voice consistent.
            # For Resemble: don't lock — next sentence should retry the real voice.
            if fallback_state is not None and tts_provider != 'resemble':
                fallback_state['provider'] = fallback_id
                fallback_state['voice'] = fallback_voice
                logger.info(f"TTS sticky fallback locked: {fallback_id}/{fallback_voice} for rest of response")
            return base64.b64encode(audio_bytes).decode('utf-8')
        except Exception as fb_err:
            logger.error(f"TTS fallback failed (provider={fallback_id}): {fb_err} — continuing down chain")
            current = fallback_id  # walk to the next hop

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
