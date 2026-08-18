"""
Tests for the final admin-panel wave (WO-4.1..5.3 + ADMIN-BUG-2).

Covers the NEW admin-gated endpoints added to routes/admin.py, the plugins
update-diff service helpers, and the ADMIN-BUG-2 session-reset route move.

Only cheap / validation-only paths are exercised — we deliberately do NOT call
the update preview/apply/verify paths (they run git fetch + subprocess imports),
nor mutate greetings.json.
"""

import pytest


@pytest.fixture(scope="module")
def admin_client():
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.admin import admin_bp
    app.register_blueprint(admin_bp)
    return app.test_client()


# ── WO-4.2 Update manager wrappers ──────────────────────────────────────────

class TestUpdateManagerPanel:
    def test_status_200_and_shape(self, admin_client):
        r = admin_client.get("/api/admin/update/status")
        assert r.status_code == 200
        d = r.get_json()
        for k in ("commit", "branch", "version", "deployment_type", "is_git_repo"):
            assert k in d

    def test_apply_requires_confirm(self, admin_client):
        # No body → must NOT run a live update.
        r = admin_client.post("/api/admin/update/apply", json={})
        assert r.status_code == 400
        assert r.get_json()["ok"] is False

    def test_apply_rejects_confirm_false(self, admin_client):
        r = admin_client.post("/api/admin/update/apply", json={"confirm": False})
        assert r.status_code == 400

    def test_rollback_requires_target(self, admin_client):
        r = admin_client.post("/api/admin/update/rollback", json={})
        assert r.status_code == 400

    def test_rollback_rejects_non_sha(self, admin_client):
        r = admin_client.post("/api/admin/update/rollback",
                              json={"target_commit": "HEAD; rm -rf /"})
        assert r.status_code == 400
        r2 = admin_client.post("/api/admin/update/rollback",
                               json={"target_commit": "main"})
        assert r2.status_code == 400


# ── WO-5.1 Marketplace update-diff ──────────────────────────────────────────

class TestPluginUpdates:
    def test_updates_list_200(self, admin_client):
        r = admin_client.get("/api/admin/plugins/updates")
        assert r.status_code == 200
        d = r.get_json()
        assert "plugins" in d and isinstance(d["plugins"], list)
        assert "update_count" in d

    def test_update_unknown_plugin_404(self, admin_client):
        r = admin_client.post("/api/admin/plugins/__nope__/update", json={})
        assert r.status_code == 404
        assert r.get_json()["ok"] is False


class TestPluginUpdateService:
    def test_version_tuple_ordering(self):
        from services.plugins import _version_tuple
        assert _version_tuple("1.2.0") > _version_tuple("1.1.9")
        assert _version_tuple("v2.0") > _version_tuple("1.9.9")
        assert _version_tuple("") == (0,)

    def test_get_plugin_updates_returns_rows(self):
        from services.plugins import get_plugin_updates
        rows = get_plugin_updates()
        assert isinstance(rows, list)
        for r in rows:
            assert {"id", "installed_version", "available_version",
                    "update_available", "source"} <= set(r.keys())

    def test_update_plugin_not_installed_returns_none(self):
        from services.plugins import update_plugin
        assert update_plugin("__definitely_not_installed__") is None


# ── WO-4.3 Greetings editor wrappers ────────────────────────────────────────

class TestGreetingsAdmin:
    def test_queue_requires_greeting(self, admin_client):
        r = admin_client.post("/api/admin/greetings/queue", json={})
        assert r.status_code == 400

    def test_queue_rejects_too_long(self, admin_client):
        r = admin_client.post("/api/admin/greetings/queue",
                              json={"greeting": "x" * 400})
        assert r.status_code == 400

    def test_remove_missing_contextual_404(self, admin_client):
        r = admin_client.post("/api/admin/greetings/remove-contextual",
                              json={"greeting": "this greeting does not exist zzz"})
        assert r.status_code == 404


# ── ADMIN-BUG-2 — session-reset route disambiguation ────────────────────────

class TestSessionResetRouteMove:
    def test_conversation_bp_no_longer_owns_session_reset(self):
        from app import create_app
        app, _ = create_app(config_override={"TESTING": True})
        from routes.conversation import conversation_bp
        app.register_blueprint(conversation_bp)
        rules = {r.rule: r.endpoint for r in app.url_map.iter_rules()}
        # The deep-reset handler moved to the explicit path...
        assert "/api/session/reset-openclaw" in rules
        # ...and conversation_bp no longer registers the shadowing /api/session/reset.
        assert rules.get("/api/session/reset") != "conversation.session_reset"


# ── Gate wiring — all new endpoints live under an admin-gated prefix ─────────

class TestAdminPrefixGate:
    def test_new_routes_under_api_admin(self, admin_client):
        from app import create_app
        app, _ = create_app(config_override={"TESTING": True})
        from routes.admin import admin_bp
        app.register_blueprint(admin_bp)
        new = [
            "/api/admin/update/status", "/api/admin/update/preview",
            "/api/admin/update/verify", "/api/admin/update/apply",
            "/api/admin/update/rollback", "/api/admin/plugins/updates",
            "/api/admin/greetings/queue", "/api/admin/greetings/remove-contextual",
        ]
        rules = {r.rule for r in app.url_map.iter_rules()}
        for path in new:
            assert path in rules, f"{path} not registered"
            assert path.startswith("/api/admin/")
