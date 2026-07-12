#!/usr/bin/env python3
"""
TTS Providers Package.

This package provides a unified interface for multiple Text-to-Speech backends.
All providers inherit from TTSProvider base class and implement the same API.

Available Providers:
    - HumeProvider: Hume EVI WebSocket TTS (INACTIVE - placeholder only)
    - SupertonicProvider: Local ONNX-based TTS (active, recommended)

Usage:
    >>> from tts_providers import get_provider, list_providers
    >>> # Get default provider (Supertonic)
    >>> provider = get_provider()
    >>> audio = provider.generate_speech("Hello world", voice='M1')
    >>>
    >>> # List all providers
    >>> providers = list_providers()

Author: OpenVoiceUI
Date: 2026-02-11
"""

import json
import os
import threading
from typing import Optional, Dict, Any, List

from .base_provider import TTSProvider
from .hume_provider import HumeProvider
from .supertonic_provider import SupertonicProvider
from .groq_provider import GroqProvider
from .qwen3_provider import Qwen3Provider
from .qwen3_local_provider import Qwen3LocalProvider
from .resemble_provider import ResembleProvider
from .elevenlabs_provider import ElevenLabsProvider

# Provider registry
_PROVIDERS = {
    'hume': HumeProvider,
    'supertonic': SupertonicProvider,
    'groq': GroqProvider,
    'qwen3': Qwen3Provider,
    'qwen3-local': Qwen3LocalProvider,
    'resemble': ResembleProvider,
    'elevenlabs': ElevenLabsProvider,
}

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'providers_config.json')

# ── Module-level singleton caches (perf: TTS-1) ────────────────────────────────
# get_provider() used to build a FRESH provider instance AND re-read the config
# from disk on every call — which, on the voice hot path, happened once per
# sentence. That defeated every instance-level cache (ElevenLabs voices,
# Supertonic ONNX/voice cache) and re-read JSON per utterance. We now hold one
# instance per provider id for the process lifetime, guarded by a lock so two
# threads racing the first generate can't build two instances. Config is cached
# with an mtime check so an admin edit is still picked up without a restart.
_INSTANCE_CACHE: Dict[str, TTSProvider] = {}
_INSTANCE_LOCK = threading.Lock()

_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_CONFIG_MTIME: float = -1.0
_CONFIG_LOCK = threading.Lock()


def _load_config() -> Dict[str, Any]:
    """Load providers configuration from JSON file (cached, mtime-reloaded)."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return {'providers': {}, 'default_provider': 'supertonic'}
    # Fast path — cache still valid
    if _CONFIG_CACHE is not None and mtime == _CONFIG_MTIME:
        return _CONFIG_CACHE
    with _CONFIG_LOCK:
        # Re-check under the lock (another thread may have just loaded it)
        if _CONFIG_CACHE is not None and mtime == _CONFIG_MTIME:
            return _CONFIG_CACHE
        try:
            with open(_CONFIG_PATH, 'r') as f:
                _CONFIG_CACHE = json.load(f)
            _CONFIG_MTIME = mtime
        except (FileNotFoundError, ValueError):
            _CONFIG_CACHE = {'providers': {}, 'default_provider': 'supertonic'}
            _CONFIG_MTIME = mtime
    return _CONFIG_CACHE


def invalidate(provider_id: Optional[str] = None) -> None:
    """Drop cached provider instance(s) and the config cache.

    Call this whenever providers_config.json is rewritten (default provider /
    default voice change) so the next get_provider() rebuilds against fresh
    config. Pass a provider_id to evict just that instance, or None for all.
    """
    global _CONFIG_CACHE, _CONFIG_MTIME
    with _INSTANCE_LOCK:
        if provider_id is None:
            _INSTANCE_CACHE.clear()
        else:
            _INSTANCE_CACHE.pop(provider_id, None)
    with _CONFIG_LOCK:
        _CONFIG_CACHE = None
        _CONFIG_MTIME = -1.0


def get_provider(provider_id: Optional[str] = None) -> TTSProvider:
    """
    Get a cached TTS provider instance (one per provider id, process lifetime).

    Args:
        provider_id: Provider identifier ('hume', 'supertonic', ...). If None,
            uses the configured default_provider.

    Returns:
        TTSProvider instance (shared singleton — do not mutate per-request state).

    Raises:
        ValueError: If provider_id is unknown

    Example:
        >>> provider = get_provider('supertonic')
        >>> audio = provider.generate_speech("Hello", voice='M1')
    """
    if provider_id is None:
        provider_id = _load_config().get('default_provider', 'supertonic')

    if provider_id not in _PROVIDERS:
        available = ', '.join(_PROVIDERS.keys())
        raise ValueError(f"Unknown provider '{provider_id}'. Available: {available}")

    inst = _INSTANCE_CACHE.get(provider_id)
    if inst is not None:
        return inst
    with _INSTANCE_LOCK:
        inst = _INSTANCE_CACHE.get(provider_id)
        if inst is None:
            inst = _PROVIDERS[provider_id]()
            _INSTANCE_CACHE[provider_id] = inst
        return inst


def _catalog_info(provider_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Build a provider info dict from the config catalog (no instantiation)."""
    cfg = dict(config.get('providers', {}).get(provider_id, {}))
    cfg['provider_id'] = provider_id
    cfg.setdefault('name', provider_id)
    cfg.setdefault('status', 'active')
    cfg.setdefault('voices', [])
    # Reflect the live status of an ALREADY-cached instance (network-free —
    # the instance exists so reading its _status touches no vendor API).
    cached = _INSTANCE_CACHE.get(provider_id)
    if cached is not None:
        cfg['status'] = getattr(cached, '_status', cfg['status'])
    return cfg


def list_providers(include_inactive: bool = True,
                   probe: bool = False) -> List[Dict[str, Any]]:
    """
    List all TTS providers with metadata.

    Args:
        include_inactive: If True, include inactive providers. Default True.
        probe: If False (default — used by the readiness/health path), build
            metadata from the config catalog WITHOUT instantiating providers or
            calling any vendor API. If True (used by the admin/UI endpoint),
            instantiate cached singletons and call get_info() for live detail
            (cloned voices, dynamic status). Never make the health probe hit a
            third-party API (HEALTH-1 / TTS-10).

    Returns:
        List of provider metadata dictionaries.
    """
    config = _load_config()
    providers = []

    for provider_id in _PROVIDERS:
        try:
            if probe:
                info = get_provider(provider_id).get_info()
                # Merge catalog metadata (cost/quality/latency/etc.)
                cfg = config.get('providers', {}).get(provider_id)
                if cfg:
                    for k in ('cost_per_minute', 'quality', 'latency', 'features',
                              'requires_api_key', 'languages', 'notes'):
                        if k in cfg:
                            info[k] = cfg[k]
                info['provider_id'] = provider_id
            else:
                info = _catalog_info(provider_id, config)

            if not include_inactive and info.get('status') != 'active':
                continue
            providers.append(info)
        except Exception as e:
            print(f"Warning: Failed to load provider {provider_id}: {e}")

    return providers


__all__ = [
    'TTSProvider',
    'HumeProvider',
    'SupertonicProvider',
    'GroqProvider',
    'Qwen3Provider',
    'Qwen3LocalProvider',
    'ResembleProvider',
    'ElevenLabsProvider',
    'get_provider',
    'list_providers',
    'invalidate',
]
