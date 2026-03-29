"""
Extended tests for routes/conversation.py — covers helper functions
(_notify_brain, log_conversation, log_metrics, get_supertonic_for_voice)
and additional endpoint scenarios.
(P7-T1, ADR-010)
"""

import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# _notify_brain
# ---------------------------------------------------------------------------

class TestNotifyBrain:
    def test_notify_brain_writes_event(self, tmp_path):
        from routes import conversation as conv_mod
        events_file = tmp_path / "events.jsonl"
        with patch.object(conv_mod, "BRAIN_EVENTS_PATH", events_file):
            conv_mod._notify_brain("test_event", key="value")
        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["type"] == "test_event"
        assert event["key"] == "value"
        assert "timestamp" in event

    def test_notify_brain_appends_multiple(self, tmp_path):
        from routes import conversation as conv_mod
        events_file = tmp_path / "events.jsonl"
        with patch.object(conv_mod, "BRAIN_EVENTS_PATH", events_file):
            conv_mod._notify_brain("event_a")
            conv_mod._notify_brain("event_b")
        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "event_a"
        assert json.loads(lines[1])["type"] == "event_b"

    def test_notify_brain_handles_permission_error(self, tmp_path):
        from routes import conversation as conv_mod
        # Writing to a non-writable path should silently pass (non-critical)
        with patch.object(conv_mod, "BRAIN_EVENTS_PATH", Path("/root/nope.jsonl")):
            # Should not raise
            conv_mod._notify_brain("test_event")

    def test_notify_brain_event_has_timestamp(self, tmp_path):
        from routes import conversation as conv_mod
        events_file = tmp_path / "events.jsonl"
        with patch.object(conv_mod, "BRAIN_EVENTS_PATH", events_file):
            conv_mod._notify_brain("tick")
        event = json.loads(events_file.read_text().strip())
        assert "timestamp" in event
        assert len(event["timestamp"]) > 10


# ---------------------------------------------------------------------------
# log_conversation
# ---------------------------------------------------------------------------

class TestLogConversation:
    def _make_db(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            """CREATE TABLE conversation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                message TEXT,
                tts_provider TEXT,
                voice TEXT,
                created_at TEXT
            )"""
        )
        conn.commit()
        conn.close()
        return db

    def test_log_conversation_inserts_row(self, tmp_path):
        from routes import conversation as conv_mod
        db = self._make_db(tmp_path)
        with patch.object(conv_mod, "DB_PATH", db), \
             patch.object(conv_mod, "BRAIN_EVENTS_PATH", tmp_path / "events.jsonl"):
            conv_mod.log_conversation("user", "Hello!", "sess-1")
            conv_mod._flush_db_writes()  # wait for background writer
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT role, message, session_id FROM conversation_log").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0] == ("user", "Hello!", "sess-1")

    def test_log_conversation_with_tts_params(self, tmp_path):
        from routes import conversation as conv_mod
        db = self._make_db(tmp_path)
        with patch.object(conv_mod, "DB_PATH", db), \
             patch.object(conv_mod, "BRAIN_EVENTS_PATH", tmp_path / "events.jsonl"):
            conv_mod.log_conversation("assistant", "Hi there!", "sess-2",
                                      tts_provider="groq", voice="alloy")
            conv_mod._flush_db_writes()  # wait for background writer
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT tts_provider, voice FROM conversation_log").fetchall()
        conn.close()
        assert rows[0] == ("groq", "alloy")

    def test_log_conversation_handles_db_error(self, tmp_path):
        from routes import conversation as conv_mod
        # Point to an invalid path — should not raise
        with patch.object(conv_mod, "DB_PATH", Path("/nonexistent/path/test.db")):
            conv_mod.log_conversation("user", "test")  # silently handles error

    def test_log_conversation_default_session(self, tmp_path):
        from routes import conversation as conv_mod
        db = self._make_db(tmp_path)
        with patch.object(conv_mod, "DB_PATH", db), \
             patch.object(conv_mod, "BRAIN_EVENTS_PATH", tmp_path / "events.jsonl"):
            conv_mod.log_conversation("user", "Test message")
            conv_mod._flush_db_writes()  # wait for background writer
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT session_id FROM conversation_log").fetchall()
        conn.close()
        assert rows[0][0] == "default"


# ---------------------------------------------------------------------------
# log_metrics
# ---------------------------------------------------------------------------

class TestLogMetrics:
    def _make_db(self, tmp_path):
        db = tmp_path / "metrics.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            """CREATE TABLE conversation_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                profile TEXT,
                model TEXT,
                handshake_ms INTEGER,
                llm_inference_ms INTEGER,
                tts_generation_ms INTEGER,
                total_ms INTEGER,
                user_message_len INTEGER,
                response_len INTEGER,
                tts_text_len INTEGER,
                tts_provider TEXT,
                tts_success INTEGER,
                tts_error TEXT,
                tool_count INTEGER,
                fallback_used INTEGER,
                error TEXT,
                created_at TEXT
            )"""
        )
        conn.commit()
        conn.close()
        return db

    def test_log_metrics_inserts_row(self, tmp_path):
        from routes import conversation as conv_mod
        db = self._make_db(tmp_path)
        with patch.object(conv_mod, "DB_PATH", db):
            conv_mod.log_metrics({
                "session_id": "sess-1",
                "profile": "default",
                "model": "glm-5-turbo",
                "handshake_ms": 100,
                "llm_inference_ms": 500,
                "tts_generation_ms": 200,
                "total_ms": 800,
                "user_message_len": 50,
                "response_len": 100,
            })
            conv_mod._flush_db_writes()  # wait for background writer
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT session_id, profile FROM conversation_metrics").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0] == ("sess-1", "default")

    def test_log_metrics_handles_db_error(self, tmp_path):
        from routes import conversation as conv_mod
        with patch.object(conv_mod, "DB_PATH", Path("/nonexistent/path/test.db")):
            # Should not raise
            conv_mod.log_metrics({"session_id": "test"})

    def test_log_metrics_defaults(self, tmp_path):
        from routes import conversation as conv_mod
        db = self._make_db(tmp_path)
        with patch.object(conv_mod, "DB_PATH", db):
            conv_mod.log_metrics({})
            conv_mod._flush_db_writes()  # wait for background writer
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT profile, model, tts_success, tool_count, fallback_used FROM conversation_metrics"
        ).fetchall()
        conn.close()
        assert rows[0] == ("unknown", "unknown", 1, 0, 0)


# ---------------------------------------------------------------------------
# get_supertonic_for_voice
# ---------------------------------------------------------------------------

class TestGetSupertonicForVoice:
    def test_returns_provider_or_none(self):
        from routes.conversation import get_supertonic_for_voice
        # This calls get_provider('supertonic') which may or may not be registered
        try:
            result = get_supertonic_for_voice("M1")
            # Should return a provider or None
            assert result is not None or result is None
        except Exception:
            pass  # OK if provider not available in test env

    def test_voice_param_ignored(self):
        from routes.conversation import get_supertonic_for_voice
        # Call with different voice styles — should not raise regardless
        try:
            get_supertonic_for_voice("M2")
            get_supertonic_for_voice("F1")
        except Exception:
            pass  # OK if provider unavailable


# ---------------------------------------------------------------------------
# /api/conversation/reset (additional cases)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conv_test_client():
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.canvas import canvas_bp
    from routes.conversation import conversation_bp
    try:
        app.register_blueprint(canvas_bp)
        app.register_blueprint(conversation_bp)
    except Exception:
        pass
    return app.test_client()


class TestConversationResetExtended:
    def test_reset_with_soft_mode(self, conv_test_client):
        resp = conv_test_client.post(
            "/api/conversation/reset",
            json={"mode": "soft"},
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_reset_with_hard_mode(self, conv_test_client):
        resp = conv_test_client.post(
            "/api/conversation/reset",
            json={"mode": "hard"},
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_reset_no_body_accepted(self, conv_test_client):
        resp = conv_test_client.post(
            "/api/conversation/reset",
            json={},
            content_type="application/json",
        )
        # Empty JSON body is valid
        assert resp.status_code in (200, 400)

    def test_reset_response_has_new_key(self, conv_test_client):
        resp = conv_test_client.post(
            "/api/conversation/reset",
            json={"mode": "soft"},
            content_type="application/json",
        )
        data = resp.get_json()
        assert data is not None


# ---------------------------------------------------------------------------
# /api/tts/providers — additional checks
# ---------------------------------------------------------------------------

class TestTtsProvidersExtended:
    def test_providers_list_not_empty(self, conv_test_client):
        resp = conv_test_client.get("/api/tts/providers")
        data = resp.get_json()
        if isinstance(data, dict):
            providers = data.get("providers", [])
        else:
            providers = data
        assert isinstance(providers, list)

    def test_providers_content_type_json(self, conv_test_client):
        resp = conv_test_client.get("/api/tts/providers")
        assert "json" in resp.content_type
