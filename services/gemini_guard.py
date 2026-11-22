"""Gemini is for LOOKING at images (vision) and text. Never for GENERATING images.

Operator rule, 2026-11-22: "make sure that we can use gemini for VISION and never use it
for generation ... restrict the tools so that it wouldnt be even possible to generate and
only to use it to look at images." There is NO setting that re-enables generation.

Two layers, both deny-by-default for Google's generative-AI hosts:

1. check_gemini_request(url, payload) — called explicitly by every Gemini caller in this
   codebase (routes/vision.py, routes/conversation.py, routes/image_gen.py, routes/icons.py)
   before it sends anything.
2. A process-wide hook on requests.Session.send (install_transport_guard(), run at import)
   that applies the same check to EVERY request this process sends through `requests` to a
   Google generative-AI host — so a caller added later, or one that forgets layer 1, is
   still refused. `requests.post(...)` goes through Session.send.

A request to a Google generative-AI host is allowed only when ALL of these hold:
  * the path is /models/<model>:<method> with <method> in ALLOWED_METHODS
    (generateContent / streamGenerateContent / countTokens). Imagen's :predict, Veo's
    :predictLongRunning and every other method are refused;
  * the model id contains none of BLOCKED_MODEL_TOKENS ("image", "imagen", "veo");
  * the JSON body does not ask for IMAGE output (generationConfig.responseModalities /
    response_modalities containing IMAGE, or an imageConfig block).

A refusal logs at ERROR and raises GeminiGenerationBlocked. It never fails silently and
no bytes leave the process.

LIMITS (stated, not hidden): this guards THIS process. It cannot stop a different program
that holds the same GEMINI_API_KEY (an agent shell, curl, another container) from calling
Google directly. The complete restriction is that only a server-side proxy holds the key.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

GOOGLE_GENAI_HOSTS = (
    'generativelanguage.googleapis.com',
    'aiplatform.googleapis.com',          # Vertex AI (incl. <region>-aiplatform.googleapis.com)
)
ALLOWED_METHODS = frozenset({'generateContent', 'streamGenerateContent', 'countTokens'})
BLOCKED_MODEL_TOKENS = ('image', 'imagen', 'veo')

_MODEL_PATH_RE = re.compile(r'/models/([^/:?]+):([A-Za-z]+)')


class GeminiGenerationBlocked(PermissionError):
    """A request to Google generative AI that could generate media was refused."""


def is_google_genai_host(host: str) -> bool:
    host = (host or '').lower().split(':')[0]
    return any(host == h or host.endswith('-' + h) or host.endswith('.' + h)
               for h in GOOGLE_GENAI_HOSTS)


def _asks_for_image_output(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    for cfg_key in ('generationConfig', 'generation_config', 'config'):
        cfg = payload.get(cfg_key)
        if not isinstance(cfg, dict):
            continue
        for mod_key in ('responseModalities', 'response_modalities'):
            mods = cfg.get(mod_key) or []
            if isinstance(mods, str):
                mods = [mods]
            if any(str(m).strip().upper() == 'IMAGE' for m in mods):
                return True
        if 'imageConfig' in cfg or 'image_config' in cfg:
            return True
    return False


def _refuse(reason: str, url: str) -> None:
    # Never log the query string: it can carry ?key=<secret>.
    p = urlparse(url)
    safe = f'{p.scheme}://{p.netloc}{p.path}'
    logger.error('gemini_guard: REFUSED Google GenAI request (%s): %s', reason, safe)
    raise GeminiGenerationBlocked(
        f'Gemini may only be used to look at images or for text, never to generate images '
        f'({reason}).')


def check_gemini_request(url: str, payload=None) -> None:
    """Raise GeminiGenerationBlocked unless this is an allowed vision/text request.

    Non-Google URLs pass untouched. `payload` may be a dict or the raw JSON body
    (bytes/str); an undecodable body on a generate call is refused."""
    try:
        p = urlparse(url or '')
    except Exception:
        return
    if not is_google_genai_host(p.hostname or ''):
        return

    m = _MODEL_PATH_RE.search(p.path or '')
    if not m:
        _refuse('not a /models/<model>:<method> call', url)
    model, method = m.group(1), m.group(2)
    if method not in ALLOWED_METHODS:
        _refuse(f'method :{method} is not allowed (only {sorted(ALLOWED_METHODS)})', url)
    low = model.lower()
    for tok in BLOCKED_MODEL_TOKENS:
        if tok in low:
            _refuse(f'model {model!r} is an image/video generation model', url)

    body = payload
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode('utf-8')
        except UnicodeDecodeError:
            _refuse('request body is not UTF-8 JSON', url)
    if isinstance(body, str):
        if not body.strip():
            body = None
        else:
            try:
                body = json.loads(body)
            except ValueError:
                _refuse('request body is not JSON', url)
    if _asks_for_image_output(body):
        _refuse(f'model {model!r} was asked for IMAGE output', url)


# ── layer 2: process-wide transport hook ──────────────────────────────────────

_install_lock = threading.Lock()
_installed = False


def install_transport_guard() -> None:
    """Wrap requests.Session.send once per process. Idempotent."""
    global _installed
    with _install_lock:
        if _installed:
            return
        import requests

        original_send = requests.Session.send

        def guarded_send(self, request, **kwargs):
            url = getattr(request, 'url', '') or ''
            try:
                host = urlparse(url).hostname or ''
            except Exception:
                host = ''
            if is_google_genai_host(host):
                check_gemini_request(url, getattr(request, 'body', None))
            return original_send(self, request, **kwargs)

        guarded_send.__wrapped__ = original_send
        guarded_send._gemini_guard = True
        requests.Session.send = guarded_send
        _installed = True


def transport_guard_installed() -> bool:
    import requests
    return bool(getattr(requests.Session.send, '_gemini_guard', False))


install_transport_guard()
