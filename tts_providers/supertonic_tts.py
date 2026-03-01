#!/usr/bin/env python3
"""
Supertonic TTS wrapper for OpenVoiceUI.

This module provides a clean interface to the Supertonic Text-to-Speech engine,
wrapping the helper.py functionality for use in Flask applications.

Author: OpenVoiceUI
Date: 2026-02-11
"""

import os
import sys
import logging
from io import BytesIO
from typing import Optional

import numpy as np
import soundfile as sf

# Add the Supertonic helper.py directory to the path
SUPERTONIC_HELPER_PATH = os.environ.get("SUPERTONIC_HELPER_PATH", os.path.expanduser("~/supertonic/py"))
if SUPERTONIC_HELPER_PATH not in sys.path:
    sys.path.insert(0, SUPERTONIC_HELPER_PATH)

try:
    from helper import (
        load_text_to_speech,
        load_voice_style,
        Style,
    )
except ImportError as e:
    logging.error(f"Failed to import Supertonic helper: {e}")
    logging.error(f"Make sure {SUPERTONIC_HELPER_PATH}/helper.py exists")
    raise


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SupertonicTTS:
    """
    Wrapper class for Supertonic Text-to-Speech engine.

    This class provides a simple interface for generating speech from text
    using the Supertonic ONNX models. It handles model loading, voice style
    management, and audio generation.

    Example:
        >>> tts = SupertonicTTS(
        ...     onnx_dir="~/supertonic/assets/onnx",
        ...     voice_style_path="~/supertonic/assets/voice_styles/M1.json"
        ... )
        >>> audio_bytes = tts.generate_speech("Hello world, this is a test")
        >>> # audio_bytes contains WAV format audio data
    """

    # Default paths (use SUPERTONIC_MODEL_PATH env var or ~/supertonic)
    DEFAULT_ONNX_DIR = os.environ.get("SUPERTONIC_ONNX_DIR", os.path.expanduser("~/supertonic/assets/onnx"))
    DEFAULT_VOICE_STYLES_DIR = os.environ.get("SUPERTONIC_VOICE_STYLES_DIR", os.path.expanduser("~/supertonic/assets/voice_styles"))

    # Available voice styles
    AVAILABLE_VOICE_STYLES = {
        'M1': 'M1.json',  # Male voice 1
        'M2': 'M2.json',  # Male voice 2
        'F1': 'F1.json',  # Female voice 1
        'F2': 'F2.json',  # Female voice 2
    }

    def __init__(
        self,
        onnx_dir: Optional[str] = None,
        voice_style_path: Optional[str] = None,
        voice_style_name: str = 'M1',
        use_gpu: bool = False
    ):
        """
        Initialize the Supertonic TTS engine.

        Args:
            onnx_dir: Path to the ONNX models directory. If None, uses DEFAULT_ONNX_DIR.
            voice_style_path: Full path to the voice style JSON file. If None,
                             constructs path from voice_style_name.
            voice_style_name: Name of the voice style (M1, M2, F1, F2). Used only
                             if voice_style_path is None.
            use_gpu: Whether to use GPU for inference. Default is False (CPU only).

        Raises:
            FileNotFoundError: If onnx_dir or voice_style file doesn't exist.
            RuntimeError: If model loading fails.
        """
        # Set paths
        self.onnx_dir = onnx_dir or self.DEFAULT_ONNX_DIR
        self.voice_style_name = voice_style_name

        # Validate onnx directory
        if not os.path.exists(self.onnx_dir):
            raise FileNotFoundError(
                f"ONNX models directory not found: {self.onnx_dir}"
            )
        logger.info(f"Using ONNX models from: {self.onnx_dir}")

        # Set voice style path
        if voice_style_path:
            self.voice_style_path = voice_style_path
        else:
            # Construct path from voice style name
            if voice_style_name not in self.AVAILABLE_VOICE_STYLES:
                raise ValueError(
                    f"Invalid voice_style_name: {voice_style_name}. "
                    f"Available: {list(self.AVAILABLE_VOICE_STYLES.keys())}"
                )
            voice_style_file = self.AVAILABLE_VOICE_STYLES[voice_style_name]
            self.voice_style_path = os.path.join(
                self.DEFAULT_VOICE_STYLES_DIR, voice_style_file
            )

        # Validate voice style file
        if not os.path.exists(self.voice_style_path):
            raise FileNotFoundError(
                f"Voice style file not found: {self.voice_style_path}"
            )
        logger.info(f"Using voice style: {self.voice_style_path}")

        # Initialize models
        try:
            logger.info("Loading Supertonic TTS models...")
            self.text_to_speech = load_text_to_speech(self.onnx_dir, use_gpu=use_gpu)
            self.style = load_voice_style([self.voice_style_path], verbose=True)
            self.sample_rate = self.text_to_speech.sample_rate
            logger.info(f"TTS models loaded successfully (sample rate: {self.sample_rate}Hz)")
        except Exception as e:
            logger.error(f"Failed to load TTS models: {e}")
            raise RuntimeError(f"TTS model loading failed: {e}")

    def generate_speech(
        self,
        text: str,
        lang: str = 'en',
        speed: float = 1.0,
        total_step: int = 15
    ) -> bytes:
        """
        Generate speech from text.

        Args:
            text: The text to synthesize into speech.
            lang: Language code ('en', 'ko', 'es', 'pt', 'fr'). Default is 'en'.
            speed: Speech speed multiplier. Higher values = faster speech.
                   Recommended range: 0.8 to 1.3. Default is 1.05.
            total_step: Number of denoising steps for generation. More steps =
                        better quality but slower. Recommended range: 3-10.
                        Default is 5 (good balance).

        Returns:
            bytes: Raw WAV audio data (can be written directly to file or sent
                   via HTTP with Content-Type: audio/wav).

        Raises:
            ValueError: If lang is not supported or parameters are invalid.
            RuntimeError: If speech generation fails.

        Example:
            >>> audio = tts.generate_speech("Hello world", lang='en', speed=1.05)
            >>> with open('output.wav', 'wb') as f:
            ...     f.write(audio)
        """
        # Validate inputs
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        supported_langs = ['en', 'ko', 'es', 'pt', 'fr']
        if lang not in supported_langs:
            raise ValueError(
                f"Unsupported language: {lang}. Supported: {supported_langs}"
            )

        if speed <= 0 or speed > 3:
            raise ValueError(f"Invalid speed: {speed}. Must be between 0 and 3")

        if total_step < 1 or total_step > 50:
            raise ValueError(f"Invalid total_step: {total_step}. Must be between 1 and 50")

        logger.info(f"Generating speech: '{text[:50]}...' (lang={lang}, speed={speed}, steps={total_step})")

        # Maximum character length per chunk to stay under ONNX token limit (~1000 tokens)
        MAX_CHUNK_LENGTH = 500

        def split_text_into_chunks(text: str, max_length: int) -> list:
            """Split text into chunks at sentence boundaries."""
            if len(text) <= max_length:
                return [text]

            chunks = []
            # Split on sentence boundaries
            sentence_endings = ['. ', '! ', '? ', '\n']

            current_chunk = ""
            # Split by sentences first
            sentences = [text]
            for ending in sentence_endings:
                new_sentences = []
                for s in sentences:
                    parts = s.split(ending)
                    for i, part in enumerate(parts):
                        if i < len(parts) - 1:
                            new_sentences.append(part + ending.strip())
                        elif part.strip():
                            new_sentences.append(part)
                sentences = new_sentences if new_sentences else sentences

            # Combine sentences into chunks up to max_length
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue

                if len(current_chunk) + len(sentence) + 1 <= max_length:
                    current_chunk += (" " if current_chunk else "") + sentence
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    # If single sentence is too long, just use it (will be truncated by tokenizer)
                    if len(sentence) > max_length:
                        chunks.append(sentence)
                        current_chunk = ""
                    else:
                        current_chunk = sentence

            if current_chunk.strip():
                chunks.append(current_chunk.strip())

            return chunks

        try:
            chunks = split_text_into_chunks(text, MAX_CHUNK_LENGTH)
            logger.info(f"Text split into {len(chunks)} chunk(s)")

            all_audio_chunks = []

            for i, chunk in enumerate(chunks):
                logger.info(f"Processing chunk {i+1}/{len(chunks)}: '{chunk[:30]}...'")

                # Generate speech using the Supertonic TextToSpeech instance
                wav, duration = self.text_to_speech(
                    text=chunk,
                    lang=lang,
                    style=self.style,
                    total_step=total_step,
                    speed=speed
                )

                # Extract the audio data (first batch item, trim to actual duration)
                audio_data = wav[0, :int(self.sample_rate * duration[0].item())]
                all_audio_chunks.append(audio_data)

            # Concatenate all audio chunks
            if len(all_audio_chunks) == 1:
                final_audio = all_audio_chunks[0]
            else:
                final_audio = np.concatenate(all_audio_chunks)

            # Write to BytesIO buffer to get raw bytes
            buffer = BytesIO()
            sf.write(buffer, final_audio, self.sample_rate, format='WAV')
            audio_bytes = buffer.getvalue()

            total_duration = len(final_audio) / self.sample_rate
            logger.info(f"Generated {len(audio_bytes)} bytes of audio ({total_duration:.2f}s)")
            return audio_bytes

        except Exception as e:
            logger.error(f"Speech generation failed: {e}")
            raise RuntimeError(f"Failed to generate speech: {e}")

    def set_voice_style(self, voice_style_name: str) -> None:
        """
        Change the voice style.

        Args:
            voice_style_name: Name of the new voice style (M1, M2, F1, F2).

        Raises:
            ValueError: If voice_style_name is not available.
            FileNotFoundError: If the voice style file doesn't exist.
            RuntimeError: If loading the new style fails.
        """
        if voice_style_name == self.voice_style_name:
            logger.info(f"Already using voice style: {voice_style_name}")
            return

        if voice_style_name not in self.AVAILABLE_VOICE_STYLES:
            raise ValueError(
                f"Invalid voice_style_name: {voice_style_name}. "
                f"Available: {list(self.AVAILABLE_VOICE_STYLES.keys())}"
            )

        voice_style_file = self.AVAILABLE_VOICE_STYLES[voice_style_name]
        new_voice_style_path = os.path.join(
            self.DEFAULT_VOICE_STYLES_DIR, voice_style_file
        )

        if not os.path.exists(new_voice_style_path):
            raise FileNotFoundError(
                f"Voice style file not found: {new_voice_style_path}"
            )

        try:
            self.style = load_voice_style([new_voice_style_path], verbose=True)
            self.voice_style_name = voice_style_name
            self.voice_style_path = new_voice_style_path
            logger.info(f"Voice style changed to: {voice_style_name}")
        except Exception as e:
            logger.error(f"Failed to load voice style: {e}")
            raise RuntimeError(f"Failed to load voice style: {e}")


# Singleton instance for use in Flask app
_tts_instance: Optional[SupertonicTTS] = None


def get_tts_instance() -> Optional[SupertonicTTS]:
    """
    Get the global TTS instance (singleton).

    Returns:
        The global SupertonicTTS instance, or None if not initialized.

    This is useful for Flask apps where you want to initialize TTS once
    at startup and reuse the instance across requests.
    """
    global _tts_instance
    return _tts_instance


def initialize_tts(
    onnx_dir: Optional[str] = None,
    voice_style_name: str = 'M1',
    use_gpu: bool = False
) -> Optional[SupertonicTTS]:
    """
    Initialize the global TTS instance.

    Args:
        onnx_dir: Path to ONNX models directory.
        voice_style_name: Default voice style to use.
        use_gpu: Whether to use GPU for inference.

    Returns:
        The initialized SupertonicTTS instance, or None if initialization fails.
    """
    global _tts_instance
    try:
        _tts_instance = SupertonicTTS(
            onnx_dir=onnx_dir,
            voice_style_name=voice_style_name,
            use_gpu=use_gpu
        )
        logger.info("Global TTS instance initialized")
        return _tts_instance
    except Exception as e:
        logger.error(f"Failed to initialize TTS: {e}")
        _tts_instance = None
        return None


if __name__ == "__main__":
    # Simple test when run directly
    print("Supertonic TTS Wrapper - Direct Test")
    print("=" * 50)

    try:
        # Initialize TTS
        tts = SupertonicTTS(
            onnx_dir=os.environ.get("SUPERTONIC_ONNX_DIR", os.path.expanduser("~/supertonic/assets/onnx")),
            voice_style_name="M1"
        )

        # Generate speech
        test_text = "Hello world, this is a test of the Supertonic TTS system."
        audio = tts.generate_speech(test_text, lang='en', speed=1.05)

        # Save to file
        output_path = "/tmp/supertonic_test_output.wav"
        with open(output_path, 'wb') as f:
            f.write(audio)

        print(f"Success! Audio saved to: {output_path}")
        print(f"Generated {len(audio)} bytes of audio data")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
