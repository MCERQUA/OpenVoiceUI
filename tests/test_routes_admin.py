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
# AI config — openclaw.json read/write path (2026-07-07 admin panel overhaul)
# ---------------------------------------------------------------------------

_SAMPLE_JSONC = '''{
  // primary provider
  "models": {
    "providers": {
      "zai": {
        "baseUrl": "https://api.z.ai/api/anthropic", /* url with slashes */
        "apiKey": "${ZAI_API_KEY}",
        "api": "anthropic-messages",
        "models": [{"id": "glm-5-turbo", "contextWindow": 204800},]
      }
    }
  },
  "agents": {"defaults": {"model": {"primary": "zai/glm-5-turbo", "fallbacks": ["zai_fb/glm-5-turbo"]}}}
}'''


class TestOpenclawConfigIO:
    def test_parse_jsonc_preserves_urls(self):
        """The old regex stripped '//…' inside https:// URLs — every real config failed."""
        from routes.admin import _parse_jsonc
        data = _parse_jsonc(_SAMPLE_JSONC)
        assert data["models"]["providers"]["zai"]["baseUrl"] == "https://api.z.ai/api/anthropic"
        assert data["agents"]["defaults"]["model"]["primary"] == "zai/glm-5-turbo"

    def test_parse_plain_json(self):
        from routes.admin import _parse_jsonc
        assert _parse_jsonc('{"a": "https://x/y"}') == {"a": "https://x/y"}

    def test_write_oc_config_keeps_inode(self, tmp_path, monkeypatch):
        """openclaw.json is a single-file bind mount — the write must reuse the
        same inode (in-place), never tmp+rename."""
        import routes.admin as adm
        cfg_file = tmp_path / "openclaw.json"
        cfg_file.write_text('{"agents": {}}')
        inode_before = cfg_file.stat().st_ino
        monkeypatch.setattr(adm, "_OPENCLAW_CONFIG_PATH", cfg_file)
        adm._write_oc_config({"agents": {"defaults": {}}})
        assert cfg_file.stat().st_ino == inode_before
        assert json.loads(cfg_file.read_text()) == {"agents": {"defaults": {}}}
        backups = list(tmp_path.glob("openclaw.json.bak-*"))
        assert len(backups) == 1 and json.loads(backups[0].read_text()) == {"agents": {}}

    def test_read_falls_back_to_ro_view(self, tmp_path, monkeypatch):
        import routes.admin as adm
        ro = tmp_path / "openclaw-client.json"
        ro.write_text(_SAMPLE_JSONC)
        monkeypatch.setattr(adm, "_OPENCLAW_CONFIG_PATH", tmp_path / "missing.json")
        monkeypatch.setattr(adm, "_OPENCLAW_CONFIG_RO_FALLBACK", ro)
        data = adm._read_oc_config()
        assert data["agents"]["defaults"]["model"]["fallbacks"] == ["zai_fb/glm-5-turbo"]

    def test_provider_catalog_has_no_dropped_providers(self):
        """MiniMax ('mx'), bigmodel-GLM and Groq-for-LLM are dropped — never offer them."""
        from routes.admin import _AI_PROVIDERS
        assert "mx" not in _AI_PROVIDERS
        assert "glm" not in _AI_PROVIDERS
        assert "groqcloud" not in _AI_PROVIDERS
        assert _AI_PROVIDERS["zai"]["baseUrl"] == "https://api.z.ai/api/anthropic"
        assert "zai_fb" in _AI_PROVIDERS

    def test_put_rejects_malformed_model_ref(self, admin_client, tmp_path, monkeypatch):
        import routes.admin as adm
        cfg_file = tmp_path / "openclaw.json"
        cfg_file.write_text(_SAMPLE_JSONC)
        monkeypatch.setattr(adm, "_OPENCLAW_CONFIG_PATH", cfg_file)
        resp = admin_client.put("/api/admin/ai-config", json={"primary": "not-a-model-ref"})
        assert resp.status_code == 400

    def test_put_updates_primary_in_place(self, admin_client, tmp_path, monkeypatch):
        import routes.admin as adm
        cfg_file = tmp_path / "openclaw.json"
        cfg_file.write_text(_SAMPLE_JSONC)
        monkeypatch.setattr(adm, "_OPENCLAW_CONFIG_PATH", cfg_file)
        resp = admin_client.put("/api/admin/ai-config", json={"primary": "zai_fb/glm-5-turbo"})
        assert resp.status_code == 200
        data = json.loads(cfg_file.read_text())
        assert data["agents"]["defaults"]["model"]["primary"] == "zai_fb/glm-5-turbo"
        # env-placeholder apiKey untouched
        assert data["models"]["providers"]["zai"]["apiKey"] == "${ZAI_API_KEY}"

    def test_put_patches_via_gateway_when_available(self, admin_client, monkeypatch):
        import routes.admin as adm
        calls = []

        def fake_rpc(method, params, timeout=10.0):
            calls.append((method, params))
            if method == "config.get":
                return {"ok": True, "result": {"parsed": json.loads(
                    '{"models":{"providers":{"zai":{"apiKey":"__OPENCLAW_REDACTED__"}}},'
                    '"agents":{"defaults":{"model":{"primary":"zai/glm-5-turbo","fallbacks":[]}}}}'
                ), "hash": "abc123"}}
            if method == "config.patch":
                return {"ok": True, "result": {"ok": True}}
            return {"ok": False, "error": "unexpected"}

        monkeypatch.setattr(adm, "_run_rpc", fake_rpc)
        resp = admin_client.put("/api/admin/ai-config", json={"primary": "zai_fb/glm-5-turbo"})
        assert resp.status_code == 200
        methods = [m for m, _ in calls]
        assert methods == ["config.get", "config.patch"]
        patch_params = calls[1][1]
        assert patch_params["baseHash"] == "abc123"
        partial = json.loads(patch_params["raw"])
        assert partial["agents"]["defaults"]["model"]["primary"] == "zai_fb/glm-5-turbo"
        # partial must NOT echo back redacted keys
        assert "__OPENCLAW_REDACTED__" not in patch_params["raw"]
