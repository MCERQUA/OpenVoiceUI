"""
pytest fixtures for ai-eyes2-refactor test suite.

ADR-010: pytest + Playwright for backend + E2E testing.

The Flask app (server.py) is a large monolith, so we test modules
in isolation where possible, and use a test client for endpoint tests.
"""

import os
import sys
import pytest

# Ensure project root is on sys.path so imports work
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(scope="session")
def flask_app():
    """Return a configured Flask test app via the create_app() factory."""
    from app import create_app
    test_app, _sock = create_app(config_override={"TESTING": True})
    return test_app


@pytest.fixture(scope="session")
def client(flask_app):
    """Return a Flask test client."""
    return flask_app.test_client()


@pytest.fixture(scope="session")
def health_checker():
    """Return a HealthChecker instance for unit-testing health logic directly."""
    from services.health import HealthChecker
    return HealthChecker()


@pytest.fixture(scope="session")
def config():
    """Return the singleton config for testing config loading."""
    from config.loader import Config
    return Config()


# Review 3 of PR #597: the reviewer's probe files are kept byte-identical (cmp-checked).
# One probe locates the PRE-FIX line `_intel_path.write_text(` in routes/conversation.py by
# text and asserts the guard flags it; the B2 fix removed that line, so on a fixed tree the
# lookup raises StopIteration. It is marked xfail ONLY for that exception: if the by-name
# line comes back and the guard misses it, the probe fails with its own AssertionError.
# The guard's coverage of the pattern is asserted by
# tests/test_agent_writable_io_guard.py::test_scanner_flags_the_review3_b2_pattern.
_PREFIX_LINE_PROBES = {
    'test_zz_review3_conversation_image_intel.py::test_guard_does_not_see_the_sidecar_write',
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if f'{item.path.name}::{item.name}' in _PREFIX_LINE_PROBES:
            item.add_marker(pytest.mark.xfail(
                raises=StopIteration, strict=True,
                reason='probe looks up the pre-fix vulnerable line by text; the B2 fix removed it'))
