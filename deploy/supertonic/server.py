"""Supertonic TTS microservice — thin FastAPI wrapper around `supertonic`."""

import logging
from contextlib import asynccontextmanager
from io import BytesIO

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from supertonic import TTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("supertonic-service")

VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]
LANGUAGES = ["en", "ko", "es", "pt", "fr"]

# Pre-loaded voice styles keyed by name
_styles: dict = {}
_tts: TTS | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Load TTS engine and all voice styles once at startup."""
    global _tts
    logger.info("Loading Supertonic TTS engine …")
    _tts = TTS(auto_download=True)
    for voice in VOICES:
        logger.info("Loading voice style %s …", voice)
        _styles[voice] = _tts.get_voice_style(voice)
    logger.info("All %d voices loaded — ready to serve.", len(_styles))
    yield


app = FastAPI(title="Supertonic TTS", lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    voice: str = "M1"
    speed: float = Field(default=1.05, gt=0, le=3)
    steps: int = Field(default=5, ge=1, le=50)
    lang: str = "en"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/tts")
async def tts(req: TTSRequest):
    if req.voice not in _styles:
        raise HTTPException(400, f"Unknown voice '{req.voice}'. Available: {VOICES}")
    if req.lang not in LANGUAGES:
        raise HTTPException(400, f"Unsupported lang '{req.lang}'. Available: {LANGUAGES}")
    if not req.text.strip():
        raise HTTPException(400, "Text cannot be empty")

    try:
        wav, duration = _tts(
            text=req.text,
            lang=req.lang,
            style=_styles[req.voice],
            speed=req.speed,
            total_step=req.steps,
        )
        audio = wav[0, : int(_tts.sample_rate * duration[0].item())]
        buf = BytesIO()
        sf.write(buf, audio, _tts.sample_rate, format="WAV")
        return Response(content=buf.getvalue(), media_type="audio/wav")
    except Exception as exc:
        logger.exception("TTS generation failed")
        raise HTTPException(500, str(exc))
