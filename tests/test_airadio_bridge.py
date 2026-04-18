"""
Tests for routes/airadio_bridge.py — AI-Radio Bridge Blueprint

Smoke-test surface only:
  * Blueprint imports + registers cleanly.
  * Without AIRADIO_URL / AIRADIO_AGENT_KEY, every route returns 503 with the
    BRIDGE_NOT_CONFIGURED contract.
  * With both env vars set and `requests` mocked, push-song happy-path
    returns the normalized `{ ok, songId, url }` shape.
"""

import importlib
import io
import os
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


# ---------------------------------------------------------------------------
# Fixture: app with bridge unconfigured (env vars missing)
# ---------------------------------------------------------------------------


@pytest.fixture
def unconfigured_client(monkeypatch):
    """Flask client where AIRADIO_URL/AGENT_KEY are intentionally unset."""
    monkeypatch.setenv("AIRADIO_URL", "")
    monkeypatch.setenv("AIRADIO_AGENT_KEY", "")
    monkeypatch.setenv("AIRADIO_JAMBOT_USER", "")

    # Re-import so module-level config picks up empty env.
    import routes.airadio_bridge as airadio_bridge
    importlib.reload(airadio_bridge)

    app = Flask(__name__)
    app.register_blueprint(airadio_bridge.airadio_bp)
    return app.test_client()


# ---------------------------------------------------------------------------
# Fixture: app with bridge configured + outbound `requests` mocked
# ---------------------------------------------------------------------------


@pytest.fixture
def configured_client(monkeypatch, tmp_path):
    """Flask client where AIRADIO_URL/AGENT_KEY are set; HTTP is patched."""
    monkeypatch.setenv("AIRADIO_URL", "http://ai-radio:3000")
    monkeypatch.setenv("AIRADIO_AGENT_KEY", "fake-shared-secret")
    monkeypatch.setenv("AIRADIO_JAMBOT_USER", "test-dev")

    # Reload so the module captures the env vars and re-imports paths.
    import routes.airadio_bridge as airadio_bridge
    importlib.reload(airadio_bridge)

    app = Flask(__name__)
    app.register_blueprint(airadio_bridge.airadio_bp)
    return app, airadio_bridge


# ---------------------------------------------------------------------------
# Test: blueprint can be imported and registered
# ---------------------------------------------------------------------------


def test_blueprint_imports_and_registers():
    from routes.airadio_bridge import airadio_bp
    app = Flask(__name__)
    app.register_blueprint(airadio_bp)
    # Blueprint should contribute multiple endpoints under /api/airadio/.
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert any(r.startswith("/api/airadio/") for r in rules)
    # Spot-check the canonical routes are present.
    assert "/api/airadio/push-song" in rules
    assert "/api/airadio/resolve" in rules
    assert "/api/airadio/inbox" in rules


# ---------------------------------------------------------------------------
# Tests: bridge unconfigured → every route returns 503
# ---------------------------------------------------------------------------


class TestUnconfiguredReturns503:
    """Every route must return the BRIDGE_NOT_CONFIGURED contract."""

    def test_push_song_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/push-song",
            json={"filename": "x.mp3", "playlist": "library"},
        )
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_push_playlist_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/push-playlist",
            json={"name": "x", "playlist": "library"},
        )
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_set_image_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/set-image",
            json={"target": "avatar", "path": "/uploads/x.png"},
        )
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_resolve_503(self, unconfigured_client):
        resp = unconfigured_client.get("/api/airadio/resolve?type=song&query=test")
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_play_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/play",
            json={"type": "song", "id": "abc"},
        )
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_inbox_503(self, unconfigured_client):
        resp = unconfigured_client.get("/api/airadio/inbox")
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_send_to_friend_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/send-to-friend",
            json={"song_id": "x", "receiver_handle": "@nick"},
        )
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_vote_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/vote",
            json={"song_id": "x", "value": 1},
        )
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_friend_request_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/friend-request",
            json={"handle": "@nick"},
        )
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"

    def test_friend_accept_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/friend-accept",
            json={"handle": "@nick"},
        )
        assert resp.status_code == 503

    def test_friend_decline_503(self, unconfigured_client):
        resp = unconfigured_client.post(
            "/api/airadio/friend-decline",
            json={"handle": "@nick"},
        )
        assert resp.status_code == 503

    def test_library_503(self, unconfigured_client):
        resp = unconfigured_client.get("/api/airadio/library")
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "BRIDGE_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# Tests: happy path with `requests` mocked
# ---------------------------------------------------------------------------


def _make_mock_response(status_code=200, json_body=None):
    """Build a MagicMock that quacks like requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = "" if json_body is None else str(json_body)
    return resp


class TestPushSongHappyPath:
    def test_push_song_returns_normalized_shape(self, configured_client, tmp_path, monkeypatch):
        app, airadio_bridge = configured_client
        client = app.test_client()

        # Plant a fake song file inside MUSIC_DIR so _resolve_local_song finds it.
        fake_song = airadio_bridge.MUSIC_DIR / "ai-radio-test-song.mp3"
        airadio_bridge.MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        fake_song.write_bytes(b"\x00\x00fake-mp3-bytes")

        try:
            mock_resp = _make_mock_response(
                status_code=200,
                json_body={
                    "ok": True,
                    "data": {
                        "songId": "song_abc123",
                        "url": "https://radio.jam-bot.com/audio/u1/song_abc123.mp3",
                    },
                },
            )
            with patch.object(
                airadio_bridge.http_requests,
                "request",
                return_value=mock_resp,
            ) as mock_req:
                resp = client.post(
                    "/api/airadio/push-song",
                    json={"filename": "ai-radio-test-song.mp3", "playlist": "library"},
                )

            assert resp.status_code == 200
            body = resp.get_json()
            assert body["ok"] is True
            assert body["songId"] == "song_abc123"
            assert body["url"] == "https://radio.jam-bot.com/audio/u1/song_abc123.mp3"

            # Verify the outbound request carried the correct headers + path.
            assert mock_req.call_count == 1
            args, kwargs = mock_req.call_args
            assert args[0] == "POST"
            assert args[1].endswith("/api/agent/push-song")
            assert kwargs["headers"]["X-JamBot-Agent-Key"] == "fake-shared-secret"
            assert kwargs["headers"]["X-JamBot-User"] == "test-dev"
            assert "files" in kwargs and "file" in kwargs["files"]
        finally:
            if fake_song.exists():
                fake_song.unlink()

    def test_push_song_404_when_file_missing(self, configured_client):
        app, airadio_bridge = configured_client
        client = app.test_client()
        # No HTTP mock needed — we should bail out before any outbound call.
        resp = client.post(
            "/api/airadio/push-song",
            json={"filename": "does-not-exist-anywhere.mp3", "playlist": "library"},
        )
        assert resp.status_code == 404
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"]["code"] == "NOT_FOUND"


class TestSetImageWhitelist:
    def test_set_image_rejects_path_traversal(self, configured_client):
        app, airadio_bridge = configured_client
        client = app.test_client()
        resp = client.post(
            "/api/airadio/set-image",
            json={"target": "avatar", "path": "/uploads/../../etc/passwd"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"]["code"] == "VALIDATION_FAILED"

    def test_set_image_rejects_absolute_outside_whitelist(self, configured_client):
        app, airadio_bridge = configured_client
        client = app.test_client()
        resp = client.post(
            "/api/airadio/set-image",
            json={"target": "avatar", "path": "/etc/passwd"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "VALIDATION_FAILED"

    def test_set_image_rejects_unknown_target(self, configured_client):
        app, airadio_bridge = configured_client
        client = app.test_client()
        resp = client.post(
            "/api/airadio/set-image",
            json={"target": "everything", "path": "/uploads/x.png"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "VALIDATION_FAILED"


class TestResolveProxy:
    def test_resolve_passes_through(self, configured_client):
        app, airadio_bridge = configured_client
        client = app.test_client()
        mock_resp = _make_mock_response(
            status_code=200,
            json_body={
                "ok": True,
                "data": {
                    "id": "song_xyz",
                    "url": "https://radio.jam-bot.com/audio/u1/x.mp3",
                    "title": "Code Block Cartel",
                    "artist": "Clawdbot",
                },
            },
        )
        with patch.object(
            airadio_bridge.http_requests,
            "request",
            return_value=mock_resp,
        ) as mock_req:
            resp = client.get("/api/airadio/resolve?type=song&query=Code+Block+Cartel")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["id"] == "song_xyz"

        args, kwargs = mock_req.call_args
        assert args[0] == "GET"
        assert args[1].endswith("/api/agent/resolve")
        assert kwargs["params"] == {"type": "song", "query": "Code Block Cartel"}


class TestVoteValidation:
    def test_vote_rejects_invalid_value(self, configured_client):
        app, airadio_bridge = configured_client
        client = app.test_client()
        resp = client.post(
            "/api/airadio/vote",
            json={"song_id": "abc", "value": 99},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "VALIDATION_FAILED"
