"""
TTS fallback-policy loader (WO-3.1).

The TTS fallback chain + voice-gender map used to live as hardcoded dicts
(_FALLBACK_CHAIN / _VOICE_GENDER) inside services/tts.py. They are now loadable
from config/tts-fallback.json so the operator can reorder the fallback chain or
extend voice-gender coverage from the admin 'Voice: TTS' tab without a code
change — the runtime (services/tts.py) reads THROUGH this loader, so an edit
takes effect on the next utterance with no restart.

Load order (mirrors services/ai_providers.py exactly):
  1. config/tts-fallback.json  (if present + parseable)  → file policy
  2. _DEFAULT_FALLBACK          (inline fallback)          → never empty

Shape:
  {
    "chain": { "<provider_id>": "<next_provider_id>", ... },
    "voice_gender": { "<voice_id>": "M"|"F", ... }
  }
"chain" maps each provider to its SINGLE next-hop fallback; services/tts.py
walks it transitively with cycle protection. "voice_gender" keeps a fallback on
the same-gender voice.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tts-fallback.json"

# Inline fallback — the historical services/tts.py dicts, with voice_gender
# coverage extended for qwen3/resemble/custom voices where determinable (TTS-12).
_DEFAULT_FALLBACK: Dict[str, Any] = {
    "chain": {
        "groq": "supertonic",
        "qwen3": "groq",
        "qwen3-local": "groq",
        "resemble": "groq",
        "elevenlabs": "groq",
    },
    "voice_gender": {
        # Groq Orpheus voices
        "autumn": "F", "diana": "F", "hannah": "F",
        "austin": "M", "daniel": "M", "troy": "M",
        # ElevenLabs voices (common ones)
        "rachel": "F", "drew": "M", "clyde": "M", "paul": "M",
        "domi": "F", "bella": "F", "antoni": "M", "elli": "F",
        "josh": "M", "arnold": "M", "adam": "M", "sam": "M",
        # Supertonic voices
        "M1": "M", "M2": "M", "M3": "M", "M4": "M", "M5": "M",
        "F1": "F", "F2": "F", "F3": "F", "F4": "F", "F5": "F",
        # Resemble custom clones (bhb Kyle Valhalla + friends) — TTS-12 partial
        "kyle": "M", "valhalla": "M", "bhb": "M",
        # Qwen3 preset voices (Fal.ai) — TTS-12 partial
        "ethan": "M", "chelsie": "F", "cherry": "F", "serena": "F",
        "dylan": "M", "jada": "F", "sunny": "F",
    },
}

# mtime-cached load so an admin edit to the JSON is picked up without a restart,
# while the hot path (each utterance) doesn't re-read disk every call.
_CACHE: Dict[str, Any] | None = None
_CACHE_MTIME: float = -1.0


def load_tts_fallback() -> Dict[str, Any]:
    """Return the TTS fallback policy {'chain': {...}, 'voice_gender': {...}}.

    Reads config/tts-fallback.json (mtime-cached). Falls back to the inline
    _DEFAULT_FALLBACK on any error so callers never get an empty policy.
    """
    global _CACHE, _CACHE_MTIME
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
    except OSError:
        return {k: dict(v) for k, v in _DEFAULT_FALLBACK.items()}

    if _CACHE is not None and mtime == _CACHE_MTIME:
        return _CACHE

    try:
        raw = json.loads(_CONFIG_PATH.read_text())
        if isinstance(raw, dict):
            chain = raw.get("chain")
            vg = raw.get("voice_gender")
            if isinstance(chain, dict) and isinstance(vg, dict):
                _CACHE = {"chain": chain, "voice_gender": vg}
                _CACHE_MTIME = mtime
                return _CACHE
        logger.warning("tts-fallback.json missing chain/voice_gender — using inline default")
    except Exception as exc:  # malformed JSON, permission, etc.
        logger.warning("Failed to load tts-fallback.json (%s) — using inline default", exc)

    return {k: dict(v) for k, v in _DEFAULT_FALLBACK.items()}


def get_fallback_chain() -> Dict[str, str]:
    """The provider_id -> next-hop provider_id fallback map (live)."""
    return load_tts_fallback().get("chain", {})


def get_voice_gender_map() -> Dict[str, str]:
    """The voice_id -> 'M'|'F' map (live)."""
    return load_tts_fallback().get("voice_gender", {})


def invalidate() -> None:
    """Drop the mtime cache so the next read re-loads from disk immediately."""
    global _CACHE, _CACHE_MTIME
    _CACHE = None
    _CACHE_MTIME = -1.0


def find_cycle(chain: Dict[str, str]) -> list | None:
    """Return the first cycle found in the chain as a list of ids, else None.

    The chain is single-successor, so a cycle is any node from which walking
    `next` revisits a node already on the current path.
    """
    for start in chain:
        seen = []
        node = start
        while node in chain:
            if node in seen:
                return seen[seen.index(node):] + [node]
            seen.append(node)
            node = chain[node]
    return None


def validate_fallback_policy(policy: Dict[str, Any], valid_ids: set) -> str | None:
    """Validate a candidate fallback policy before it is written.

    Checks:
      - shape: 'chain' + 'voice_gender' are dicts
      - every provider id referenced in the chain (source AND destination) is a
        registered TTS provider
      - the chain has no cycle (would loop the transitive walk)
      - voice_gender values are 'M' or 'F'

    Returns an error string on the first problem, or None if the policy is OK.
    """
    if not isinstance(policy, dict):
        return "policy must be an object"
    chain = policy.get("chain")
    vg = policy.get("voice_gender")
    if not isinstance(chain, dict):
        return "chain must be an object of provider_id -> next_provider_id"
    if not isinstance(vg, dict):
        return "voice_gender must be an object of voice_id -> 'M'|'F'"

    for src, dst in chain.items():
        if not isinstance(dst, str):
            return f"chain['{src}'] must be a provider id string"
        if src not in valid_ids:
            return f"unknown provider in chain (source): {src}"
        if dst not in valid_ids:
            return f"unknown provider in chain (destination): {dst}"
        if src == dst:
            return f"provider '{src}' cannot fall back to itself"

    cycle = find_cycle(chain)
    if cycle:
        return "fallback chain has a cycle: " + " -> ".join(cycle)

    for voice, gender in vg.items():
        if gender not in ("M", "F"):
            return f"voice_gender['{voice}'] must be 'M' or 'F', got: {gender}"

    return None


__all__ = [
    "load_tts_fallback",
    "get_fallback_chain",
    "get_voice_gender_map",
    "invalidate",
    "find_cycle",
    "validate_fallback_policy",
    "_DEFAULT_FALLBACK",
]
