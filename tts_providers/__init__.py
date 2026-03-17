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
from typing import Optional, Dict, Any, List

from .base_provider import TTSProvider
from .hume_provider import HumeProvider
from .supertonic_provider import SupertonicProvider
from .groq_provider import GroqProvider
from .qwen3_provider import Qwen3Provider
from .resemble_provider import ResembleProvider

# Provider registry
_PROVIDERS = {
    'hume': HumeProvider,
    'supertonic': SupertonicProvider,
    'groq': GroqProvider,
    'qwen3': Qwen3Provider,
    'resemble': ResembleProvider,
}

def _load_config() -> Dict[str, Any]:
    """Load providers configuration from JSON file."""
    config_path = os.path.join(os.path.dirname(__file__), 'providers_config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'providers': {}, 'default_provider': 'supertonic'}

def get_provider(provider_id: Optional[str] = None) -> TTSProvider:
    """
    Get a TTS provider instance.

    Args:
        provider_id: Provider identifier ('hume', 'supertonic'). If None, uses default.

    Returns:
        TTSProvider instance

    Raises:
        ValueError: If provider_id is unknown

    Example:
        >>> provider = get_provider('supertonic')
        >>> audio = provider.generate_speech("Hello", voice='M1')
    """
    config = _load_config()
    if provider_id is None:
        provider_id = config.get('default_provider', 'supertonic')

    if provider_id not in _PROVIDERS:
        available = ', '.join(_PROVIDERS.keys())
        raise ValueError(f"Unknown provider '{provider_id}'. Available: {available}")

    return _PROVIDERS[provider_id]()

def list_providers(include_inactive: bool = True) -> List[Dict[str, Any]]:
    """
    List all TTS providers with metadata.

    Args:
        include_inactive: If True, include inactive providers. Default True.

    Returns:
        List of provider metadata dictionaries

    Example:
        >>> for p in list_providers():
        ...     print(f"{p['name']}: ${p['cost_per_minute']}/min")
    """
    config = _load_config()
    providers = []

    for provider_id, provider_class in _PROVIDERS.items():
        try:
            instance = provider_class()
            info = instance.get_info()

            # Merge with config metadata
            if provider_id in config.get('providers', {}):
                config_data = config['providers'][provider_id]
                info.update({
                    'provider_id': provider_id,
                    'cost_per_minute': config_data.get('cost_per_minute', 0.0),
                    'quality': config_data.get('quality', 'unknown'),
                    'latency': config_data.get('latency', 'unknown'),
                    'features': config_data.get('features', []),
                    'requires_api_key': config_data.get('requires_api_key', False),
                    'languages': config_data.get('languages', []),
                    'notes': config_data.get('notes', ''),
                })

            # Filter inactive if requested
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
    'ResembleProvider',
    'get_provider',
    'list_providers',
]
