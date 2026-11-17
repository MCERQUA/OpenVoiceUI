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
