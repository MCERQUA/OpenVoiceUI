"""
Generic retry-until-success loop, used by server.py's gateway boot warm-up.

Split into its own module (no side effects on import) so it can be unit
tested without pulling in server.py's module-level app/thread setup or
gateway_manager's module-level gateway registration.
"""


def retry_until_success(connect_fn, sleep_fn, log, delay_seconds=30):
    """Call `connect_fn()` repeatedly until it does not raise, sleeping
    `delay_seconds` (via `sleep_fn`) between attempts and reporting each
    failure through `log`. Returns the 1-based attempt number that
    succeeded. Never gives up — callers that need a cap should wrap this
    in their own bounded loop.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            connect_fn()
            return attempt
        except Exception as e:
            log(f"attempt {attempt} failed ({e}) — retrying in {delay_seconds}s")
            sleep_fn(delay_seconds)
