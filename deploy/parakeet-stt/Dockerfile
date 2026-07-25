# jambot-parakeet — shared on-box speech-to-text for the whole fleet.
#
# WHY A SHARED SERVICE, not baked into jambot/openvoiceui:
# The model is ~680MB. There are 24+ OVU containers; baking it in would mean 24
# copies resident on a box whose swap is already heavily used. Same reasoning as
# jambot-browse (Chromium) and supertonic-tts — one model, one process, reached
# over the jambot-shared network.
#
# WHY PARAKEET AND NOT WHISPER (researched 2026-07-25):
# The repo's requirements.txt carried a commented-out `faster-whisper==1.2.1`,
# and /api/stt/local was written against Whisper `tiny`. Whisper is a 2022 model
# and `tiny` is its weakest variant. NVIDIA Parakeet TDT 0.6B v3 (Aug 2025,
# updated Jun 2026) beats Whisper large-v3 on accuracy at a quarter the size and
# runs roughly 4x faster on CPU — which is what this box has (8-core EPYC, NO
# GPU). The accuracy leaders on the Open ASR Leaderboard (Canary-Qwen 2.5B,
# Granite Speech 4.1 2B) are LLM-decoder hybrids built for GPUs and are the
# wrong shape here.
#
# Runtime is onnx-asr over INT8 ONNX weights: no PyTorch, no NeMo, no CUDA.
# That keeps the image small and the resident set predictable.
#
# LICENSE: the model is CC-BY-4.0 (nvidia/parakeet-tdt-0.6b-v3). Commercial use
# is permitted WITH ATTRIBUTION — see ATTRIBUTION.md next to this file. That is
# a real obligation, not a formality.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# onnx-asr[cpu] pulls onnxruntime (CPU build) + huggingface_hub for the weights.
# Versions verified against PyPI 2026-07-25 — do not bump from memory.
# (I first guessed onnx-asr 0.7.0; actual latest is 0.12.0. Check, don't recall.)
RUN pip install --no-cache-dir \
        "onnx-asr[cpu,hub]==0.12.0" \
        fastapi==0.140.0 \
        "uvicorn[standard]==0.51.0" \
        python-multipart==0.0.32

RUN useradd -r -m appuser
WORKDIR /app
COPY server.py .
RUN chown -R appuser:appuser /app
USER appuser

# Pre-download the weights at BUILD time, as appuser so the cache path matches
# runtime. Without this the first request after every recreate pays a ~680MB
# download, which reads to the caller as "the service is broken".
ENV HF_HOME=/home/appuser/.cache/huggingface
RUN python3 -c "import onnx_asr; onnx_asr.load_model('istupakov/parakeet-tdt-0.6b-v3-onnx')" \
    && du -sh /home/appuser/.cache/huggingface || true

EXPOSE 8770

# Single worker on purpose: one process owns the loaded model. Extra workers
# would each load their own ~680MB copy.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8770", "--workers", "1"]
