"""Image-generation provider defaults.

Gemini / Imagen / Nano Banana image GENERATION is impossible in this codebase (operator
rule 2026-11-22): services/gemini_guard.py refuses every Google generative-AI request that
could produce an image, and there is no setting that turns it back on. Gemini VISION
(routes/vision.py) and Gemini text are unaffected.
"""
from __future__ import annotations

from services.gemini_guard import GeminiGenerationBlocked  # noqa: F401  (re-export)

# The text-to-image default: FLUX.1-schnell on HF's nscale router
# (routes/image_gen.py _generate_huggingface), keyed by HF_TOKEN.
DEFAULT_IMAGE_MODEL = 'hf:black-forest-labs/FLUX.1-schnell'

GEMINI_DISABLED_MESSAGE = (
    'Gemini is used here only to look at images, never to generate them. '
    f'Use an hf: model such as {DEFAULT_IMAGE_MODEL}.'
)
