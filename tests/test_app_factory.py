"""
Tests for app.py — Flask application factory (P7-T1, ADR-010)
"""

import pytest
from flask import Flask
from flask_sock import Sock


class TestCreateApp:
    def test_create_app_returns_tuple(self):
        from app import create_app
        result = create_app(config_override={"TESTING": True})
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_create_app_returns_flask_instance(self):
        from app import create_app
        app, sock = create_app(config_override={"TESTING": True})
        assert isinstance(app, Flask)

    def test_create_app_returns_sock_instance(self):
        from app import create_app
        app, sock = create_app(config_override={"TESTING": True})
        assert isinstance(sock, Sock)

    def test_testing_flag_set(self):
        from app import create_app
        app, _ = create_app(config_override={"TESTING": True})
        assert app.config["TESTING"] is True

    def test_secret_key_set(self):
        from app import create_app
        app, _ = create_app(config_override={"TESTING": True})
        assert app.config["SECRET_KEY"]

    def test_max_content_length_set(self):
        from app import create_app
        app, _ = create_app(config_override={"TESTING": True})
        # 100 MB upload limit
        assert app.config["MAX_CONTENT_LENGTH"] == 100 * 1024 * 1024

    def test_config_override_applies(self):
        from app import create_app
        app, _ = create_app(config_override={"TESTING": True, "MY_CUSTOM": "hello"})
        assert app.config["MY_CUSTOM"] == "hello"

    def test_no_override_still_works(self):
        from app import create_app
        app, sock = create_app()
        assert app is not None
        assert sock is not None

    def test_test_client_can_be_created(self):
        from app import create_app
        app, _ = create_app(config_override={"TESTING": True})
        client = app.test_client()
        assert client is not None
