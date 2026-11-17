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


# Gate-4 re-check #5 probes are committed verbatim as tests/test_claimcheck5_probes{,_b}.py. Part b imports its
# helpers from the name the re-checker used (tests.test_recheck5_probes); alias that name, never a second copy.
class _Recheck5Alias:
    _NAME = 'tests.test_recheck5_probes'

    def find_spec(self, name, path=None, target=None):
        if name != self._NAME:
            return None
        import importlib.util
        return importlib.util.spec_from_loader(name, self)

    def create_module(self, spec):
        import importlib
        return importlib.import_module('tests.test_claimcheck5_probes')

    def exec_module(self, module):
        return None


sys.meta_path.append(_Recheck5Alias())


# Review 3 of PR #597: the reviewer's probe files are kept byte-identical (cmp-checked).
# One probe locates the PRE-FIX line `_intel_path.write_text(` in routes/conversation.py by
# text and asserts the guard flags it; the B2 fix removed that line, so on a fixed tree the
# lookup raises StopIteration. It is marked xfail ONLY for that exception AND ONLY while the
# FIXED line is present (review 4 of PR #597): if the fix is reverted or renamed, the
# marker is not applied and the probe runs as a plain test, so a missing fix FAILS instead
# of xfailing. If the by-name line comes back and the guard misses it, the probe fails
# with its own AssertionError either way.
# The guard's coverage of the pattern is asserted by
# tests/test_agent_writable_io_guard.py::test_scanner_flags_the_review3_b2_pattern.
_PREFIX_LINE_PROBES = {
    'test_zz_review3_conversation_image_intel.py::test_guard_does_not_see_the_sidecar_write',
}
_B2_FIXED_LINE = "_img_file.sibling('.image-intel')"


def _b2_fix_present():
    try:
        with open(os.path.join(PROJECT_ROOT, 'routes', 'conversation.py'), encoding='utf-8') as f:
            return _B2_FIXED_LINE in f.read()
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    fixed = None
    for item in items:
        if f'{item.path.name}::{item.name}' in _PREFIX_LINE_PROBES:
            if fixed is None:
                fixed = _b2_fix_present()
            if fixed:
                item.add_marker(pytest.mark.xfail(
                    raises=StopIteration, strict=True,
                    reason='probe looks up the pre-fix vulnerable line by text; the B2 fix '
                           f'removed it (fixed line {_B2_FIXED_LINE!r} is present)'))
