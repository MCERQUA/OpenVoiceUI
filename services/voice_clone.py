"""Voice-clone PROVIDER-SEAM: per-tenant policy + provider selection.

Spec: docs/jambot/ovu-voice-clone-seam-spec.md §2.5.

Selection for POST /api/tts/clone =
    policy.allowed_providers  (ordered; per tenant)
  ∩ tts_providers.get_clone_provider_ids()  (catalog "voice-cloning" feature
    + catalog status active + class implements CloneCapableTTSProvider)
and, at request time, the chosen provider's is_available().

Policy file: VOICE_CLONE_SEAM_DIR/policy.json (services/paths.py), read
mtime-cached the same way services/tts_fallback.py reads tts-fallback.json.

THIS RELEASE honours only ``allowed_providers`` and ``default_provider``.
Everything else in the spec's policy shape is ignored on purpose:
  TODO spec §1   clone_requires_code / access codes
  TODO spec §3   consent records, require_recitation, max_ref_seconds
  TODO spec §2.3 async jobs route (runtime jobs/<id>.json, GET poll)
  TODO spec §2.5 clone_fallback (OFFERED to the user, never silent), max_clones

ABSENT policy file => EXACTLY the four providers the route hard-coded before
the seam (DEFAULT_ALLOWED_PROVIDERS), default "qwen3". So every existing
tenant sees no change, and a NEW clone-capable provider (e.g. the GPU lane's
qwen3-gpu) is opt-in per tenant via policy.json, never fleet-wide by default.

A policy file that EXISTS but cannot be read as a JSON object (or whose
allowed_providers is not a list of strings) FAILS CLOSED: no provider is
allowed and the route answers 400. A restriction file must never silently
widen to the defaults because of a typo.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Today's (pre-seam) allowlist, in the order its 400 message listed them.
DEFAULT_ALLOWED_PROVIDERS = ('qwen3', 'qwen3-local', 'elevenlabs', 'resemble')
#: Today's default when the request omits "provider".
DEFAULT_CLONE_PROVIDER = 'qwen3'

_CACHE: Optional[Dict[str, Any]] = None
_CACHE_KEY: Optional[tuple] = None
_LOCK = threading.Lock()


def policy_path() -> Path:
    from services.paths import VOICE_CLONE_SEAM_DIR
    return Path(VOICE_CLONE_SEAM_DIR) / 'policy.json'


def _default_policy() -> Dict[str, Any]:
    return {
        'present': False,
        'allowed_providers': list(DEFAULT_ALLOWED_PROVIDERS),
        'default_provider': DEFAULT_CLONE_PROVIDER,
        'error': None,
    }


def _parse(raw: Any) -> Dict[str, Any]:
    closed = {'present': True, 'allowed_providers': [], 'default_provider': None}
    if not isinstance(raw, dict):
        return {**closed, 'error': 'policy.json is not a JSON object'}

    allowed = raw.get('allowed_providers', None)
    if allowed is None:
        allowed_list = list(DEFAULT_ALLOWED_PROVIDERS)
    elif isinstance(allowed, list) and all(isinstance(p, str) for p in allowed):
        allowed_list = []
        for p in allowed:
            p = p.strip()
            if p and p not in allowed_list:
                allowed_list.append(p)
    else:
        return {**closed, 'error': 'allowed_providers must be a list of strings'}

    default = raw.get('default_provider')
    if not isinstance(default, str) or not default.strip():
        default = None
    else:
        default = default.strip()

    return {'present': True, 'allowed_providers': allowed_list,
            'default_provider': default, 'error': None}


def load_policy() -> Dict[str, Any]:
    """Return {'present', 'allowed_providers', 'default_provider', 'error'}.

    mtime-cached (keyed on path + mtime + size so a same-second rewrite with
    different content is still picked up).
    """
    global _CACHE, _CACHE_KEY
    path = policy_path()
    try:
        st = path.stat()
    except FileNotFoundError:
        return _default_policy()
    except OSError as exc:
        logger.error("voice-clone policy %s unreadable (%s) — failing closed", path, exc)
        return {'present': True, 'allowed_providers': [], 'default_provider': None,
                'error': f'policy unreadable: {exc}'}

    key = (str(path), st.st_mtime_ns, st.st_size)
    if _CACHE is not None and _CACHE_KEY == key:
        return _CACHE
    with _LOCK:
        if _CACHE is not None and _CACHE_KEY == key:
            return _CACHE
        try:
            parsed = _parse(json.loads(path.read_text()))
        except Exception as exc:  # malformed JSON, permission, decode
            parsed = {'present': True, 'allowed_providers': [],
                      'default_provider': None, 'error': f'policy unreadable: {exc}'}
        if parsed.get('error'):
            logger.error("voice-clone policy %s: %s — failing closed (no clone provider allowed)",
                         path, parsed['error'])
        _CACHE, _CACHE_KEY = parsed, key
        return parsed


def invalidate_policy_cache() -> None:
    global _CACHE, _CACHE_KEY
    with _LOCK:
        _CACHE, _CACHE_KEY = None, None


def _is_available(provider_id: str) -> bool:
    from tts_providers import get_provider
    try:
        return bool(get_provider(provider_id).is_available())
    except Exception as exc:
        logger.warning("voice-clone: availability probe for %s failed: %s", provider_id, exc)
        return False


def clone_provider_ids(available_only: bool = False) -> List[str]:
    """Allowed + eligible clone providers, in the tenant policy's order.

    available_only=True additionally drops providers whose is_available() is
    False (used to pick a default; the route still answers an explicit
    request for an unavailable provider with today's 503).
    """
    from tts_providers import get_clone_provider_ids
    eligible = set(get_clone_provider_ids())
    ids = [p for p in load_policy()['allowed_providers'] if p in eligible]
    if available_only:
        ids = [p for p in ids if _is_available(p)]
    return ids


def default_clone_provider() -> str:
    """Provider used when a clone request omits "provider".

    No policy file -> "qwen3" (today). A policy's explicit default_provider is
    returned as-is, even if it is not allowed or not available, so the caller
    gets an explicit 400/503 rather than a silent switch to another provider
    (spec §2.4: a clone is never quietly moved to a provider the user did not
    pick). With no default_provider, the first AVAILABLE allowed provider is
    chosen, else the first allowed one, else "".
    """
    policy = load_policy()
    if not policy['present']:
        return DEFAULT_CLONE_PROVIDER
    if policy.get('default_provider'):
        return policy['default_provider']
    available = clone_provider_ids(available_only=True)
    if available:
        return available[0]
    allowed = clone_provider_ids()
    return allowed[0] if allowed else ''


def tenant_id() -> str:
    """This container's tenant (never taken from a request body)."""
    return os.environ.get('JAMBOT_TENANT') or os.environ.get('HOST_TENANT') or ''


__all__ = [
    'DEFAULT_ALLOWED_PROVIDERS',
    'DEFAULT_CLONE_PROVIDER',
    'policy_path',
    'load_policy',
    'invalidate_policy_cache',
    'clone_provider_ids',
    'default_clone_provider',
    'tenant_id',
]
