"""
jambot-parakeet — shared on-box speech-to-text for the JamBot fleet.

  POST /transcribe   multipart 'audio'  -> {"text": ..., "duration_s": ..., "rtf": ...}
  GET  /health                          -> {"ok": true, "model": ..., "loaded": bool}

NVIDIA Parakeet TDT 0.6B v3 (INT8 ONNX) via onnx-asr. CPU-only by design — this
box has no GPU. No API key, no per-call cost, nothing leaves the server.

MODEL LICENCE: CC-BY-4.0. Commercial use is allowed WITH ATTRIBUTION.
See ATTRIBUTION.md beside this file.
"""
import logging
import os
import shutil
import subprocess
import tempfile
import time

from fastapi import FastAPI, File, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [parakeet] %(message)s")
logger = logging.getLogger(__name__)

MODEL_ID = os.getenv("PARAKEET_MODEL", "istupakov/parakeet-tdt-0.6b-v3-onnx")
MAX_BYTES = int(os.getenv("PARAKEET_MAX_BYTES", str(60 * 1024 * 1024)))

app = FastAPI(title="jambot-parakeet")
_model = None


def get_model():
    """Load once, lazily. The weights are baked into the image at build time, so
    this is a local read (~seconds), not a download."""
    global _model
    if _model is None:
        import onnx_asr
        t0 = time.time()
        logger.info("loading %s ...", MODEL_ID)
        _model = onnx_asr.load_model(MODEL_ID)
        logger.info("model ready in %.1fs", time.time() - t0)
    return _model


@app.on_event("startup")
def _warm():
    # Load at startup rather than on the first caller. A cold first request that
    # blocks for the model load looks like a hang to whoever asked for it — the
    # same "ready doesn't mean ready" trap that bit the webdev wake path.
    try:
        get_model()
    except Exception as e:  # never crash the container over a warm failure
        logger.error("startup warm failed (will retry on first request): %s", e)


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_ID, "loaded": _model is not None}


def _to_wav16k(src: str, dst: str) -> bool:
    """Normalise anything ffmpeg understands to 16 kHz mono PCM.

    Browsers send webm/opus; phones send m4a; the recorder may send wav already.
    Parakeet wants 16 kHz mono, so normalise rather than trusting the caller.
    """
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1", "-f", "wav", dst],
        capture_output=True, timeout=120,
    )
    if r.returncode != 0:
        logger.warning("ffmpeg failed: %s", r.stderr.decode()[:300])
        return False
    return True


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"audio exceeds {MAX_BYTES} bytes")

    tmpdir = tempfile.mkdtemp(prefix="parakeet-")
    try:
        src = os.path.join(tmpdir, "in" + (os.path.splitext(audio.filename or "")[1] or ".bin"))
        wav = os.path.join(tmpdir, "in16k.wav")
        with open(src, "wb") as f:
            f.write(raw)

        if not _to_wav16k(src, wav):
            # Fall back to the original bytes — some inputs are already correct
            # and ffmpeg's failure may be about metadata, not audio.
            wav = src

        t0 = time.time()
        try:
            text = get_model().recognize(wav)
        except Exception as e:
            logger.exception("recognition failed")
            raise HTTPException(status_code=500, detail=f"recognition failed: {e}")
        elapsed = time.time() - t0

        # Report real-time factor so callers can see it degrade under load
        # instead of guessing. rtf < 1 means faster than realtime.
        dur = None
        try:
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", wav],
                capture_output=True, timeout=20,
            )
            dur = round(float(p.stdout.decode().strip()), 2)
        except Exception:
            pass

        out = {"text": (text or "").strip(), "elapsed_s": round(elapsed, 2)}
        if dur:
            out["duration_s"] = dur
            out["rtf"] = round(elapsed / dur, 3) if dur > 0 else None
        logger.info("transcribed %.1fs audio in %.2fs (rtf=%s)", dur or -1, elapsed, out.get("rtf"))
        return out
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
