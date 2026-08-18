# Attribution — required, not optional

`jambot-parakeet` runs **NVIDIA Parakeet TDT 0.6B v3**, licensed **CC-BY-4.0**.

CC-BY-4.0 permits commercial use — including in a hosted product — **on the
condition that attribution is given**. That is a licence term we must actually
satisfy, not a courtesy.

## What must appear somewhere user-visible

> Speech recognition powered by [NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3),
> licensed under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).

Acceptable placements (pick one, and keep it): an About/Credits page, a
third-party-licences page, or the OpenVoiceUI docs. It does not need to be on
every screen; it does need to be findable by a user of the product.

**Status: NOT YET PLACED.** Mike to decide the location. Until it is placed we
are using the model outside its licence terms, so this is a real open item —
tracked, not assumed done.

## Components and their licences

| Component | Licence |
|---|---|
| nvidia/parakeet-tdt-0.6b-v3 (model weights) | CC-BY-4.0 |
| istupakov/parakeet-tdt-0.6b-v3-onnx (INT8 ONNX conversion) | CC-BY-4.0 |
| onnx-asr (runtime) | MIT |
| onnxruntime | MIT |
| ffmpeg (in-image, for resampling) | LGPL/GPL depending on build |

## Why this model

Researched 2026-07-25. The repo previously pointed at Whisper `tiny`
(faster-whisper, commented out in requirements.txt) — a 2022 model, weakest
variant. Parakeet TDT 0.6B v3 beats Whisper large-v3 on accuracy at a quarter
the size and runs ~4x faster on CPU, which is what this box has. The current
Open ASR Leaderboard accuracy leaders (Canary-Qwen 2.5B, Granite Speech 4.1 2B,
ARK-ASR-3B) are LLM-decoder hybrids intended for GPUs and are the wrong shape
for CPU-only inference here.
