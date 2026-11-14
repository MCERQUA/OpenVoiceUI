"""Per-endpoint rate limits, applied after every route is registered.

Kept out of server.py so the table is importable and testable without
booting the whole monolith. server.py calls apply_endpoint_rate_limits(app)
at the very end of route registration.

Keys are Flask ENDPOINT names (the keys of app.view_functions): a blueprint
route is "<blueprint name>.<function name>", a route on the app itself is the
bare function name. A key that matches nothing is skipped with a warning —
i.e. a typo silently leaves the endpoint UNLIMITED, which is why
tests/test_voice_clone_seam.py asserts the clone key resolves to a real
endpoint and actually returns 429.

A key that resolves to the WRONG endpoint is just as silent: 'upload_file' named
server.py's shadowed duplicate /api/upload route (the blueprint rule matches first),
so the real upload handler ran at the 200/minute default (#587).
tests/test_rate_limit_keys.py asserts every key is the endpoint url_map actually
matches for its path.
"""

import logging

logger = logging.getLogger(__name__)

ENDPOINT_RATE_LIMITS = {
    'conversation.conversation':     '30/minute',
    'conversation.tts_generate':     '10/minute',
    'conversation.tts_preview':      '10/minute',
    # Voice cloning spends real money (fal/ElevenLabs/Resemble) or GPU time
    # per call and was uncapped before the clone seam (spec S3).
    # NOT lower than 5/minute: default-pages/voice-studio.html fires ONE POST
    # PER SELECTED PROVIDER CARD in parallel (5 cards), so a per-minute cap
    # below the card count 429s part of a single legitimate clone action.
    # 10/minute = one full fan-out + one retry; 30/hour = 6 full fan-outs.
    'conversation.tts_clone_voice':  '10/minute;30/hour',
    # POST /api/upload. The blueprint handler, NOT server.py's bare upload_file (a shadowed
    # duplicate rule that never serves a request, #587). 200/minute is the limit the route has
    # really run at; the tight cap is for anonymous callers only (ANONYMOUS_RATE_LIMITS).
    'static_files.upload_file':      '200/minute',
    'groq_stt':                      '60/minute',
    'local_stt':                     '60/minute',
    'external_stt':                  '60/minute',
}


# Limits that apply only to callers WITHOUT a verified credential (Clerk session or the tenant
# agent key). /api/upload is public in the auth gate (canvas pages lose the Clerk JWT), so an
# anonymous client is the abuse case. A signed-in owner on bulk-image-uploader.html sends one
# POST per file, three at a time, so 5/minute for owners would fail most of a 10-photo batch;
# behind the proxy every caller of a tenant shares one remote address, so it would also be
# one bucket for the whole tenant.
ANONYMOUS_RATE_LIMITS = {
    'static_files.upload_file':      '5/minute',
}


def caller_has_verified_credential() -> bool:
    """True when the request carries a credential the app's auth gate accepts. An install with
    no login configured has no anonymous/owner distinction: everyone counts as the owner."""
    import hmac
    import os
    from flask import request
    if not (os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip():
        return True
    agent_key = os.getenv('AGENT_API_KEY', '').strip()
    presented = request.headers.get('X-Agent-Key', '')
    if agent_key and presented and hmac.compare_digest(presented.encode('utf-8'), agent_key.encode('utf-8')):
        return True
    from services.auth import verify_clerk_token
    auth = request.headers.get('Authorization', '')
    for tok in (auth[7:].strip() if auth.startswith('Bearer ') else '', request.cookies.get('__session', '')):
        if tok and verify_clerk_token(tok):
            return True
    return False


def apply_endpoint_rate_limits(app, table=None, anonymous_table=None):
    """Wrap each listed endpoint's view function with its limit.

    limiter.limit() returns a wrapped function — it must be assigned back into
    app.view_functions or the limit is silently discarded.

    Returns the list of endpoint names that were limited.
    """
    limiter = getattr(app, 'limiter', None)
    if not limiter:
        return []
    applied = []
    for endpoint, rate in (table or ENDPOINT_RATE_LIMITS).items():
        view_fn = app.view_functions.get(endpoint)
        if view_fn:
            app.view_functions[endpoint] = limiter.limit(rate)(view_fn)
            applied.append(endpoint)
        else:
            logger.warning("Rate limit: endpoint %r not found — skipping", endpoint)
    for endpoint, rate in (ANONYMOUS_RATE_LIMITS if anonymous_table is None else anonymous_table).items():
        view_fn = app.view_functions.get(endpoint)
        if view_fn:
            app.view_functions[endpoint] = limiter.limit(
                rate, exempt_when=caller_has_verified_credential)(view_fn)
            applied.append(endpoint + ' (anonymous)')
        else:
            logger.warning("Rate limit: endpoint %r not found — skipping", endpoint)
    return applied
