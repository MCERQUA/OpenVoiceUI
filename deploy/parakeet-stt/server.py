"""
jambot-parakeet — shared on-box speech-to-text for the JamBot fleet.

  POST /transcribe   multipart 'audio'  -> {"text": ..., "duration_s": ..., "rtf": ...}
  GET  /health                          -> {"ok": true, "model": ..., "loaded": bool}

NVIDIA Parakeet TDT 0.6B v3 (INT8 ONNX) via onnx-asr. CPU-only by design — this
box has no GPU. No API key, no per-call cost, nothing leaves the server.

MODEL LICENCE: CC-BY-4.0. Commercial use is allowed WITH ATTRIBUTION.
See ATTRIBUTION.md beside this file.
"""
import asyncio
import gc
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

from fastapi import FastAPI, File, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [parakeet] %(message)s")
logger = logging.getLogger(__name__)

MODEL_ID = os.getenv("PARAKEET_MODEL", "istupakov/parakeet-tdt-0.6b-v3-onnx")
MAX_BYTES = int(os.getenv("PARAKEET_MAX_BYTES", str(60 * 1024 * 1024)))

# IDLE UNLOAD — why this exists (2026-07-25 incident).
# This service is used occasionally but held the ONNX model resident 24/7:
# 2,258 MB RSS + 2,052 MB swap = 4.3 GB, the single largest memory AND swap
# consumer on a 30 GB box that was already full. It pushed the host into
# sustained swapping (21/24 GB swap used, vmstat si/so non-zero), which made
# EVERYTHING slow at once — the desktop stream stuttered, agents crawled — while
# every component still passed its health check. Holding 4.3 GB permanently for a
# service that is idle at 0.14% CPU is the wrong trade.
#
# So: unload the model after IDLE_UNLOAD_S with no traffic and let the next
# caller pay a few seconds to reload it. Set IDLE_UNLOAD_S=0 to disable.
IDLE_UNLOAD_S = int(os.getenv("PARAKEET_IDLE_UNLOAD_S", "900"))   # 15 min
REAP_EVERY_S = int(os.getenv("PARAKEET_REAP_EVERY_S", "60"))
WARM_ON_START = os.getenv("PARAKEET_WARM_ON_START", "1") not in ("0", "false", "no")

app = FastAPI(title="jambot-parakeet")
_model = None
_model_lock = threading.Lock()      # guards load/unload
_inflight = 0                       # transcriptions currently using the model
_inflight_lock = threading.Lock()
_last_used = time.time()


def get_model():
    """Load once, lazily. The weights are baked into the image at build time, so
    this is a local read (~seconds), not a download."""
    global _model, _last_used
    _last_used = time.time()
    with _model_lock:
        if _model is None:
            import onnx_asr
            t0 = time.time()
            logger.info("loading %s ...", MODEL_ID)
            _model = onnx_asr.load_model(MODEL_ID)
            logger.info("model ready in %.1fs", time.time() - t0)
        return _model


def _unload_if_idle():
    """Drop the model if nothing has used it for IDLE_UNLOAD_S.

    Refuses while any transcription is in flight — freeing a model mid-recognize
    would crash the request that is using it. The in-flight counter is what makes
    this safe; an idle TIMER alone is not, because a long transcription can span
    several reap ticks without updating _last_used until it finishes.
    """
    global _model
    if _model is None or IDLE_UNLOAD_S <= 0:
        return
    with _inflight_lock:
        if _inflight > 0:
            return
    idle = time.time() - _last_used
    if idle < IDLE_UNLOAD_S:
        return
    with _model_lock:
        if _model is None:
            return
        # Re-check under the lock — a request may have arrived since the check above.
        with _inflight_lock:
            if _inflight > 0:
                return
        _model = None
        gc.collect()
    logger.info("model unloaded after %.0fs idle — memory released, next request reloads", idle)


@app.on_event("startup")
async def _startup():
    if WARM_ON_START:
        # Warm in a worker thread so it cannot block the event loop (and the
        # health endpoint) during the multi-second load.
        def _warm():
            try:
                get_model()
            except Exception as e:  # never crash the container over a warm failure
                logger.error("startup warm failed (will retry on first request): %s", e)
        threading.Thread(target=_warm, daemon=True).start()

    async def _reaper():
        while True:
            await asyncio.sleep(REAP_EVERY_S)
            try:
                await asyncio.to_thread(_unload_if_idle)
            except Exception:
                logger.exception("idle reaper tick failed")   # never kill the loop
    if IDLE_UNLOAD_S > 0:
        asyncio.create_task(_reaper())
        logger.info("idle-unload armed: %ss idle -> release model (~2GB)", IDLE_UNLOAD_S)


@app.get("/health")
def health():
    """`loaded:false` is NORMAL here — it means the model was released after idle,
    not that the service is broken. `ok` is the liveness signal; a caller that
    treats loaded:false as "down" would be reading a memory optimisation as an
    outage."""
    return {
        "ok": True,
        "model": MODEL_ID,
        "loaded": _model is not None,
        "idle_unload_s": IDLE_UNLOAD_S,
        "idle_s": round(time.time() - _last_used, 1),
    }


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

        global _inflight, _last_used
        t0 = time.time()
        # Mark in-flight BEFORE resolving the model so the idle reaper cannot free
        # it between get_model() returning and recognize() using it.
        with _inflight_lock:
            _inflight += 1
        try:
            text = get_model().recognize(wav)
        except Exception as e:
            logger.exception("recognition failed")
            raise HTTPException(status_code=500, detail=f"recognition failed: {e}")
        finally:
            with _inflight_lock:
                _inflight -= 1
            # Stamp on COMPLETION too: a long transcription would otherwise let
            # _last_used age past the idle window while it was still running.
            _last_used = time.time()
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
