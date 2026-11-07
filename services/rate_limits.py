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
    'upload_file':                   '5/minute',
    'groq_stt':                      '60/minute',
    'local_stt':                     '60/minute',
    'external_stt':                  '60/minute',
}


def apply_endpoint_rate_limits(app, table=None):
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
    return applied
