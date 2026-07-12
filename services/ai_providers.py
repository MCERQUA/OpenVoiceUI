"""
LLM provider catalog loader (WO-1.2).

The provider catalog used to live as a hardcoded dict (_AI_PROVIDERS) inside
routes/admin.py. It is now loadable from config/ai-providers.json so a provider
can be added or edited without a code change — it appears in
/api/services/catalog and the AI-Models admin tab automatically.

Load order:
  1. config/ai-providers.json  (if present + parseable)  → file catalog
  2. _DEFAULT_AI_PROVIDERS      (inline fallback)          → never empty

The inline default is the exact dict that was in routes/admin.py, kept here so
the app still works if the JSON file is missing on a given deployment.

LLM ROUTING itself belongs to the gateway/framework layer (openclaw.json
agents.defaults.model, managed by the config.patch RPC). This catalog only
DESCRIBES providers: their credential env key, base URL, API dialect, and the
models a user may select.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "ai-providers.json"

# Inline fallback — identical to the historical routes/admin.py:_AI_PROVIDERS.
_DEFAULT_AI_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "zai": {
        "name": "Z.AI (Account A)",
        "envKey": "ZAI_API_KEY",
        "baseUrl": "https://api.z.ai/api/anthropic",
        "api": "anthropic-messages",
        "models": [
            {"id": "glm-5-turbo", "name": "GLM-5 Turbo (Z.AI)", "contextWindow": 204000},
            {"id": "glm-4.7", "name": "GLM-4.7 (Z.AI)", "contextWindow": 204000},
        ],
    },
    "zai_fb": {
        "name": "Z.AI (Account B / fallback)",
        "envKey": "ZAI_FALLBACK_API_KEY",
        "baseUrl": "https://api.z.ai/api/anthropic",
        "api": "anthropic-messages",
        "models": [
            {"id": "glm-5-turbo", "name": "GLM-5 Turbo (Z.AI B)", "contextWindow": 204000},
            {"id": "glm-4.7", "name": "GLM-4.7 (Z.AI B)", "contextWindow": 204000},
        ],
    },
    "anthropic": {
        "name": "Anthropic",
        "envKey": "ANTHROPIC_API_KEY",
        "baseUrl": "https://api.anthropic.com",
        "api": "anthropic-messages",
        "models": [
            {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "contextWindow": 200000},
            {"id": "claude-opus-4-8", "name": "Claude Opus 4.8", "contextWindow": 200000},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "contextWindow": 200000},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "envKey": "OPENAI_API_KEY",
        "baseUrl": "https://api.openai.com/v1",
        "api": "openai-responses",
        "models": [
            {"id": "gpt-4.1", "name": "GPT-4.1", "contextWindow": 1047576},
            {"id": "gpt-4o", "name": "GPT-4o", "contextWindow": 128000},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "contextWindow": 128000},
        ],
    },
    "google": {
        "name": "Google Gemini",
        "envKey": "GEMINI_API_KEY",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "api": "google-generative-ai",
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "contextWindow": 1048576},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "contextWindow": 1048576},
        ],
    },
}

# mtime-cached load so an admin edit to the JSON is picked up without a restart,
# while the hot path (catalog build) doesn't re-read disk every call.
_CACHE: Dict[str, Dict[str, Any]] | None = None
_CACHE_MTIME: float = -1.0


def load_ai_providers() -> Dict[str, Dict[str, Any]]:
    """Return the LLM provider catalog dict {provider_id: descriptor}.

    Reads config/ai-providers.json (mtime-cached). Falls back to the inline
    _DEFAULT_AI_PROVIDERS on any error so callers never get an empty catalog.
    """
    global _CACHE, _CACHE_MTIME
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
    except OSError:
        return dict(_DEFAULT_AI_PROVIDERS)

    if _CACHE is not None and mtime == _CACHE_MTIME:
        return _CACHE

    try:
        raw = json.loads(_CONFIG_PATH.read_text())
        providers = raw.get("providers") if isinstance(raw, dict) else None
        if isinstance(providers, dict) and providers:
            _CACHE = providers
            _CACHE_MTIME = mtime
            return _CACHE
        logger.warning("ai-providers.json has no 'providers' object — using inline default")
    except Exception as exc:  # malformed JSON, permission, etc.
        logger.warning("Failed to load ai-providers.json (%s) — using inline default", exc)

    return dict(_DEFAULT_AI_PROVIDERS)


def build_llm_config_schema() -> dict:
    """Build a config_schema.fields[] descriptor for the LLM providers.

    One password field per provider (its API key, tied to a vault credential
    env key). Used by OpenClawGateway.get_config_schema() and the catalog LLM
    descriptors so the panel can render a generic key form.
    """
    fields = []
    for pid, pinfo in load_ai_providers().items():
        env_key = pinfo.get("envKey", "")
        fields.append({
            "id": env_key or pid,
            "type": "password",
            "label": f"{pinfo.get('name', pid)} API key",
            "required": False,
            "credential": env_key,
            "provider": pid,
        })
    return {"fields": fields}


__all__ = ["load_ai_providers", "build_llm_config_schema", "_DEFAULT_AI_PROVIDERS"]
