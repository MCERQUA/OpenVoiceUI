"""
services/zai_direct.py — direct Z.AI Anthropic-dialect call with A/B key fallback.

Two call sites (routes/chat.py, routes/conversation.py) hit Z.AI directly with
only ZAI_API_KEY (account A) and no fallback to ZAI_FALLBACK_API_KEY (account
B / zai_fb in services/ai_providers.py) when account A hits its weekly/monthly
cap (Z.AI error 1310, HTTP 429). This module centralizes that call so both
sites retry once against the fallback key on a cap-exhaustion response.

NEVER Groq — Groq is TTS only, never for LLM. NEVER log key values.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

ZAI_MESSAGES_URL = 'https://api.z.ai/api/anthropic/v1/messages'


def _is_cap_exhausted(resp: requests.Response) -> bool:
    """True if this response indicates the Z.AI account hit its weekly/monthly cap."""
    if resp.status_code == 429:
        return True
    try:
        body_text = resp.text or ''
    except Exception:
        body_text = ''
    return '1310' in body_text or 'Limit Exhausted' in body_text


def zai_messages_post(
    payload: Dict[str, Any],
    timeout: float = 30,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """
    POST to the Z.AI Anthropic-messages endpoint using ZAI_API_KEY (account A).
    On a cap-exhaustion response (HTTP 429 / body contains "1310" or "Limit
    Exhausted"), retries ONCE using ZAI_FALLBACK_API_KEY (account B) if that
    env var is set. Returns whichever requests.Response came back last —
    callers keep their existing status-code / raise_for_status handling.

    Raises the same way the two callers already do today if neither key is
    configured: a RuntimeError naming the missing env var (never a key value).
    """
    poster = session.post if session is not None else requests.post

    primary_key = os.environ.get('ZAI_API_KEY', '')
    fallback_key = os.environ.get('ZAI_FALLBACK_API_KEY', '')

    if not primary_key and not fallback_key:
        raise RuntimeError('ZAI_API_KEY not configured')

    headers_base = {
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }

    if primary_key:
        resp = poster(
            ZAI_MESSAGES_URL,
            headers={**headers_base, 'x-api-key': primary_key},
            json=payload,
            timeout=timeout,
        )
        if not _is_cap_exhausted(resp):
            return resp
        if not fallback_key:
            return resp
        logger.warning('### Z.AI account A cap exhausted — retrying with fallback key (zai_fb)')
    else:
        logger.warning('### ZAI_API_KEY not set — using fallback key (zai_fb) directly')

    resp = poster(
        ZAI_MESSAGES_URL,
        headers={**headers_base, 'x-api-key': fallback_key},
        json=payload,
        timeout=timeout,
    )
    return resp
