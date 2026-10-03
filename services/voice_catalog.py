"""
Which voice names belong to which TTS engine — the shared check behind two guards:

  1. profiles/manager.py refuses to SAVE a profile whose voice is not on its engine.
  2. services/tts.py, at speak time, maps a voice that is not on its engine to that
     engine's same-gender voice instead of failing every sentence.

Why both (measured 2026-10-02, foambot): the profile held voice "troy" (a Groq voice) on
the supertonic engine, which only has M1-M5 / F1-F5. Supertonic rejected every sentence,
supertonic is the END of the fallback chain, so every reply was silent: 276 failures in
six hours, about ten days of no audio. The same profile had held groq + "M1" (a supertonic
name) on 09-23. Nothing ever checked that a voice exists on the engine it is paired with.

Only engines with a FIXED voice list are judged. Resemble, Hume, Grok, Qwen and ElevenLabs
accept custom or cloned voices that no static list can enumerate, so for them the only
refusal is positive evidence: the voice is a known name of a DIFFERENT fixed-list engine.
Anything else is "cannot tell" and passes.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, Optional, Set

_SUPERTONIC_FALLBACK = {f"{g}{i}" for g in "MF" for i in range(1, 6)}


@lru_cache(maxsize=1)
def closed_catalogues() -> Dict[str, Set[str]]:
    """Engines whose voices are a fixed list -> that list. Cached: the check runs per spoken sentence."""
    cats: Dict[str, Set[str]] = {}
    try:
        from tts_providers.groq_provider import AVAILABLE_VOICES as _groq
        cats["groq"] = set(_groq)
    except Exception:  # noqa: BLE001
        pass
    try:
        from tts_providers.supertonic_provider import SupertonicProvider
        cats["supertonic"] = set(SupertonicProvider.AVAILABLE_VOICES)
    except Exception:  # noqa: BLE001
        cats["supertonic"] = set(_SUPERTONIC_FALLBACK)
    return cats


def owner_of(voice: str) -> Optional[str]:
    """The fixed-list engine that has this voice name, if exactly one does."""
    owners = [p for p, v in closed_catalogues().items() if voice in v]
    return owners[0] if len(owners) == 1 else None


def voice_mismatch(provider: Optional[str], voice: Optional[str]) -> Optional[str]:
    """A human-readable reason when `voice` is provably not a voice of `provider`, else None."""
    if not provider or not voice:
        return None
    cats = closed_catalogues()
    owner = owner_of(voice)
    if provider in cats:
        if voice in cats[provider]:
            return None
        hint = f" ('{voice}' is a {owner} voice)" if owner else ""
        return (f"voice '{voice}' is not available on {provider}{hint}; "
                f"{provider} voices: {', '.join(sorted(cats[provider]))}")
    if owner and owner != provider:
        return f"voice '{voice}' belongs to {owner}, not {provider}"
    return None
