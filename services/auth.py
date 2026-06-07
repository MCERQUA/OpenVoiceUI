"""
Clerk JWT authentication middleware for OpenVoiceUI.

Verifies Clerk session tokens from:
  1. Authorization: Bearer <token> header
  2. __session cookie (set automatically by Clerk for browser requests)

Usage:
    from services.auth import verify_clerk_token, get_token_from_request

    token = get_token_from_request()
    user_id = verify_clerk_token(token)   # returns str or None
"""
import logging
import os
import time
from functools import lru_cache
from typing import Optional

import jwt
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _derive_clerk_domain(key: str) -> str:
    """Derive the Clerk frontend domain from a publishable key (pk_live_/pk_test_)."""
    import base64
    try:
        suffix = key.split('_', 2)[-1]
        padding = (4 - len(suffix) % 4) % 4
        decoded = base64.b64decode(suffix + '=' * padding).decode('utf-8').rstrip('$')
        return decoded
    except Exception:
        return ''

_raw_clerk_key = (os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip()
_CLERK_FRONTEND_DOMAIN = os.getenv('CLERK_FRONTEND_API') or (_derive_clerk_domain(_raw_clerk_key) if _raw_clerk_key else '')
_JWKS_URL = f'https://{_CLERK_FRONTEND_DOMAIN}/.well-known/jwks.json'
_JWKS_CACHE_TTL = 3600  # refresh keys every 60 minutes

# Allowlist of Clerk user IDs permitted to access this deployment.
# Set ALLOWED_USER_IDS=user_abc123,user_xyz789 in .env
# If the env var is empty or unset, the check is SKIPPED (open to any valid Clerk user).
# Always set this in production — agents have full access.
_raw_allowed = os.getenv('ALLOWED_USER_IDS', '')
_ALLOWED_USER_IDS: set[str] = {uid.strip() for uid in _raw_allowed.split(',') if uid.strip()}

# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------

_jwks_cache: dict = {'keys': None, 'fetched_at': 0}


def _get_jwks() -> list:
    """Return cached JWKS key list, refreshing if stale."""
    now = time.time()
    if _jwks_cache['keys'] is None or (now - _jwks_cache['fetched_at']) > _JWKS_CACHE_TTL:
        try:
            resp = requests.get(_JWKS_URL, timeout=10)
            resp.raise_for_status()
            _jwks_cache['keys'] = resp.json().get('keys', [])
            _jwks_cache['fetched_at'] = now
            logger.debug('JWKS refreshed (%d keys)', len(_jwks_cache['keys']))
        except Exception as exc:
            logger.error('Failed to fetch JWKS from %s: %s', _JWKS_URL, exc)
            # Return stale keys if available
            if _jwks_cache['keys']:
                return _jwks_cache['keys']
            return []
    return _jwks_cache['keys']


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_clerk_token(token: str) -> Optional[str]:
    """
    Verify a Clerk JWT and return the user_id (sub claim) if valid.

    Returns None if the token is missing, malformed, or invalid.
    """
    if not token:
        return None

    keys = _get_jwks()
    if not keys:
        logger.warning('No JWKS keys available — cannot verify token')
        return None

    for key_data in keys:
        try:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
            payload = jwt.decode(
                token,
                public_key,
                algorithms=['RS256'],
                options={'verify_aud': False},  # Clerk tokens don't use aud in all configs
            )
            user_id = payload.get('sub')
            if not user_id:
                return None
            # Log user_id on every successful auth so it can be captured for ALLOWED_USER_IDS
            logger.info('Clerk auth: user_id=%s', user_id)
            # Enforce allowlist if configured
            if _ALLOWED_USER_IDS and user_id not in _ALLOWED_USER_IDS:
                logger.warning('Clerk auth: user_id=%s not in ALLOWED_USER_IDS — access denied', user_id)
                return None
            return user_id
        except jwt.ExpiredSignatureError:
            logger.debug('Token expired')
            return None
        except jwt.InvalidTokenError:
            continue  # try next key

    logger.debug('Token did not validate against any JWKS key')
    return None


def get_clerk_user_profile(user_id: str) -> dict:
    """Fetch username/name from Clerk Backend API. Returns {} on failure."""
    import urllib.request, urllib.error, json as _json
    secret = os.getenv('CLERK_SECRET_KEY', '')
    if not secret or not user_id:
        return {}
    try:
        req = urllib.request.Request(
            f'https://api.clerk.com/v1/users/{user_id}',
            headers={'Authorization': f'Bearer {secret}'},
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            data = _json.loads(r.read())
        return {
            'username':   data.get('username') or '',
            'first_name': data.get('first_name') or '',
            'last_name':  data.get('last_name') or '',
            'email':      (data.get('email_addresses') or [{}])[0].get('email_address', ''),
            'user_id':    user_id,
        }
    except Exception:
        return {'user_id': user_id}


def get_token_from_request() -> Optional[str]:
    """
    Extract Clerk session token from the current Flask request.

    Checks in order:
      1. Authorization: Bearer <token>
      2. __session cookie
    """
    from flask import request

    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()

    cookie_token = request.cookies.get('__session')
    if cookie_token:
        return cookie_token

    return None
