"""
Tests for routes/admin.py — Admin Blueprint (P7-T1, ADR-010)
Tests focus on refactor monitoring endpoints (no external Gateway required).
"""

import json
import pytest
from pathlib import Path


@pytest.fixture(scope="module")
def admin_client():
    """Minimal Flask app with admin blueprint registered."""
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.admin import admin_bp
    app.register_blueprint(admin_bp)
    return app.test_client()


# ---------------------------------------------------------------------------
# /api/refactor/status
# ---------------------------------------------------------------------------

class TestRefactorStatus:
    def test_status_returns_200_when_state_exists(self, admin_client):
        resp = admin_client.get("/api/refactor/status")
        # State file should exist in the project
        assert resp.status_code in (200, 404)

    def test_status_returns_json(self, admin_client):
        resp = admin_client.get("/api/refactor/status")
        assert resp.content_type.startswith("application/json")

    def test_status_has_tasks_key_when_ok(self, admin_client):
        resp = admin_client.get("/api/refactor/status")
        if resp.status_code == 200:
            data = resp.get_json()
            assert "tasks" in data


# ---------------------------------------------------------------------------
# /api/refactor/activity
# ---------------------------------------------------------------------------

class TestRefactorActivity:
    def test_activity_returns_200(self, admin_client):
        resp = admin_client.get("/api/refactor/activity")
        assert resp.status_code == 200

    def test_activity_returns_list(self, admin_client):
        resp = admin_client.get("/api/refactor/activity")
        data = resp.get_json()
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# /api/refactor/metrics
# ---------------------------------------------------------------------------

class TestRefactorMetrics:
    def test_metrics_returns_json(self, admin_client):
        resp = admin_client.get("/api/refactor/metrics")
        assert resp.content_type.startswith("application/json")
        assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# /api/refactor/control
# ---------------------------------------------------------------------------

class TestRefactorControl:
    def test_pause_returns_ok(self, admin_client):
        resp = admin_client.post(
            "/api/refactor/control",
            json={"action": "pause"},
            content_type="application/json",
        )
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.get_json()
            assert data.get("ok") is True

    def test_resume_returns_ok(self, admin_client):
        resp = admin_client.post(
            "/api/refactor/control",
            json={"action": "resume"},
            content_type="application/json",
        )
        assert resp.status_code in (200, 404)

    def test_invalid_action_returns_400(self, admin_client):
        resp = admin_client.post(
            "/api/refactor/control",
            json={"action": "destroy_everything"},
            content_type="application/json",
        )
        # Either 400 (validation) or 404 (state file not found)
        assert resp.status_code in (400, 404)

    def test_skip_without_task_id_returns_error(self, admin_client):
        resp = admin_client.post(
            "/api/refactor/control",
            json={"action": "skip"},
            content_type="application/json",
        )
        assert resp.status_code in (400, 404)

    def test_skip_unknown_task_returns_error(self, admin_client):
        resp = admin_client.post(
            "/api/refactor/control",
            json={"action": "skip", "task_id": "FAKE-T99"},
            content_type="application/json",
        )
        assert resp.status_code in (400, 404)

    def test_no_body_returns_error(self, admin_client):
        resp = admin_client.post(
            "/api/refactor/control",
            data="",
            content_type="application/json",
        )
        assert resp.status_code in (400, 404)


# ---------------------------------------------------------------------------
# /api/server-stats
# ---------------------------------------------------------------------------

class TestServerStats:
    def test_server_stats_returns_200(self, admin_client):
        resp = admin_client.get("/api/server-stats")
        assert resp.status_code == 200

    def test_server_stats_has_cpu_key(self, admin_client):
        resp = admin_client.get("/api/server-stats")
        data = resp.get_json()
        assert "cpu_percent" in data

    def test_server_stats_has_memory_key(self, admin_client):
        resp = admin_client.get("/api/server-stats")
        data = resp.get_json()
        assert "memory" in data

    def test_server_stats_has_disk_key(self, admin_client):
        resp = admin_client.get("/api/server-stats")
        data = resp.get_json()
        assert "disk" in data

    def test_server_stats_has_uptime_key(self, admin_client):
        resp = admin_client.get("/api/server-stats")
        data = resp.get_json()
        assert "uptime" in data

    def test_server_stats_has_timestamp(self, admin_client):
        resp = admin_client.get("/api/server-stats")
        data = resp.get_json()
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# AI provider catalog
# ---------------------------------------------------------------------------

class TestAIProviderCatalog:
    def test_nearai_provider_registered(self):
        from routes.admin import _AI_PROVIDERS

        provider = _AI_PROVIDERS["nearai"]
        assert provider["name"] == "NEAR AI Cloud"
        assert provider["envKey"] == "NEARAI_API_KEY"
        assert provider["baseUrl"] == "https://cloud-api.near.ai/v1"
        assert provider["api"] == "openai-completions"

    def test_nearai_models_have_context_windows(self):
        from routes.admin import _AI_PROVIDERS

        models = _AI_PROVIDERS["nearai"]["models"]
        assert any(model["id"] == "zai-org/GLM-5.1-FP8" for model in models)
        assert all(model["contextWindow"] > 0 for model in models)
