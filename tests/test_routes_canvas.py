"""
Tests for routes/canvas.py — Canvas Blueprint helpers and endpoints (P7-T1, ADR-010)
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture(scope="module")
def canvas_client():
    """Minimal Flask app with canvas blueprint registered."""
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.canvas import canvas_bp
    app.register_blueprint(canvas_bp)
    return app.test_client()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class TestCanvasHelpers:
    def test_update_canvas_context_does_not_crash(self):
        from routes.canvas import update_canvas_context
        # Should not raise even if SSE server is not running
        update_canvas_context("/pages/test.html", title="Test Page")

    def test_get_canvas_context_returns_string(self):
        from routes.canvas import get_canvas_context
        result = get_canvas_context()
        assert isinstance(result, str)

    def test_get_current_canvas_page_for_worker(self):
        from routes.canvas import get_current_canvas_page_for_worker
        result = get_current_canvas_page_for_worker()
        # Returns either a path string or None
        assert result is None or isinstance(result, str)

    def test_suggest_category_dashboard(self):
        from routes.canvas import suggest_category
        cat = suggest_category("Performance Dashboard")
        assert cat == "dashboards"

    def test_suggest_category_weather(self):
        from routes.canvas import suggest_category
        cat = suggest_category("Weather Forecast Today")
        assert cat == "weather"

    def test_suggest_category_unknown(self):
        from routes.canvas import suggest_category
        cat = suggest_category("Random Title XYZ")
        assert cat == "uncategorized"

    def test_suggest_category_with_content(self):
        from routes.canvas import suggest_category
        cat = suggest_category("Overview", content="This is a dashboard for monitoring")
        assert cat == "dashboards"

    def test_generate_voice_aliases_returns_list(self):
        from routes.canvas import generate_voice_aliases
        aliases = generate_voice_aliases("Voice Agent Dashboard")
        assert isinstance(aliases, list)
        assert len(aliases) > 0

    def test_generate_voice_aliases_includes_lowercase(self):
        from routes.canvas import generate_voice_aliases
        aliases = generate_voice_aliases("Weather Report")
        lc = [a.lower() for a in aliases]
        assert any("weather" in a for a in lc)

    def test_load_canvas_manifest_returns_dict(self):
        from routes.canvas import load_canvas_manifest
        manifest = load_canvas_manifest()
        assert isinstance(manifest, dict)

    def test_extract_canvas_page_content_nonexistent(self):
        from routes.canvas import extract_canvas_page_content
        result = extract_canvas_page_content("/nonexistent/page.html")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# API: /api/canvas/manifest GET — ETag / conditional fetch (#235)
# ---------------------------------------------------------------------------

class TestCanvasManifestEtag:
    def test_manifest_sends_etag(self, canvas_client):
        resp = canvas_client.get("/api/canvas/manifest")
        assert resp.status_code == 200
        assert resp.headers.get("ETag", "").startswith('"')

    def test_matching_if_none_match_returns_304(self, canvas_client):
        first = canvas_client.get("/api/canvas/manifest")
        etag = first.headers["ETag"]
        second = canvas_client.get(
            "/api/canvas/manifest", headers={"If-None-Match": etag}
        )
        assert second.status_code == 304
        assert second.headers["ETag"] == etag
        assert second.get_data() == b""

    def test_stale_if_none_match_returns_full_body(self, canvas_client):
        resp = canvas_client.get(
            "/api/canvas/manifest", headers={"If-None-Match": '"bogus-etag"'}
        )
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), dict)


# ---------------------------------------------------------------------------
# API: /api/canvas/context GET
# ---------------------------------------------------------------------------

class TestCanvasContextGet:
    def test_get_context_returns_200(self, canvas_client):
        resp = canvas_client.get("/api/canvas/context")
        assert resp.status_code == 200

    def test_get_context_returns_json(self, canvas_client):
        resp = canvas_client.get("/api/canvas/context")
        data = resp.get_json()
        assert data is not None


# ---------------------------------------------------------------------------
# API: /api/canvas/context POST
# ---------------------------------------------------------------------------

class TestCanvasContextPost:
    def test_post_context_returns_200(self, canvas_client):
        resp = canvas_client.post(
            "/api/canvas/context",
            json={"page_path": "/pages/test.html", "title": "Test"},
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_post_context_missing_page_path(self, canvas_client):
        resp = canvas_client.post(
            "/api/canvas/context",
            json={"title": "No Path"},
            content_type="application/json",
        )
        # Should handle gracefully
        assert resp.status_code in (200, 400)


# ---------------------------------------------------------------------------
# API: /api/canvas/update POST
# ---------------------------------------------------------------------------

class TestCanvasUpdate:
    def test_update_no_body_returns_error(self, canvas_client):
        resp = canvas_client.post(
            "/api/canvas/update",
            json={},
            content_type="application/json",
        )
        # No type → should return 400 or handle gracefully
        assert resp.status_code in (200, 400)

    def test_update_with_display_output(self, canvas_client):
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp
            resp = canvas_client.post(
                "/api/canvas/update",
                json={
                    "displayOutput": {
                        "type": "page",
                        "path": "/pages/test.html",
                        "title": "Test Update",
                    }
                },
                content_type="application/json",
            )
        assert resp.status_code in (200, 500)


# ---------------------------------------------------------------------------
# save_canvas_manifest() return value — a write failure must surface as a
# result the caller can check, not a silent None (fleet-wide bug 2026-09-08:
# [Errno 13] Permission denied on the bind-mounted manifest was swallowed and
# every route still returned HTTP 200/'ok').
# ---------------------------------------------------------------------------

class TestSaveCanvasManifestReturnValue:
    def test_returns_true_on_success(self, tmp_path, monkeypatch):
        from routes import canvas
        manifest_path = tmp_path / "canvas-manifest.json"
        monkeypatch.setattr(canvas, "CANVAS_MANIFEST_PATH", manifest_path)
        result = canvas.save_canvas_manifest({"pages": {}, "categories": {}})
        assert result is True
        assert manifest_path.exists()

    def test_returns_false_on_write_failure(self, tmp_path, monkeypatch):
        from routes import canvas
        # Parent directory doesn't exist -> open() raises, caught by save_canvas_manifest
        manifest_path = tmp_path / "missing-dir" / "canvas-manifest.json"
        monkeypatch.setattr(canvas, "CANVAS_MANIFEST_PATH", manifest_path)
        result = canvas.save_canvas_manifest({"pages": {}, "categories": {}})
        assert result is False


# ---------------------------------------------------------------------------
# API: PATCH /api/canvas/manifest/page/<page_id> — manifest write failure
# must return 500, never a false 200/'ok'.
# ---------------------------------------------------------------------------

class TestHandlePageMetadataManifestWrite:
    def test_patch_returns_500_when_manifest_write_fails(self, canvas_client):
        manifest = {"pages": {"test-page": {"display_name": "Test"}}, "categories": {}}
        with patch("routes.canvas.load_canvas_manifest", return_value=manifest), \
             patch("routes.canvas.save_canvas_manifest", return_value=False):
            resp = canvas_client.patch(
                "/api/canvas/manifest/page/test-page",
                json={"display_name": "New Name"},
            )
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["status"] == "error"

    def test_patch_returns_200_when_manifest_write_succeeds(self, canvas_client):
        manifest = {"pages": {"test-page": {"display_name": "Test"}}, "categories": {}}
        with patch("routes.canvas.load_canvas_manifest", return_value=manifest), \
             patch("routes.canvas.save_canvas_manifest", return_value=True):
            resp = canvas_client.patch(
                "/api/canvas/manifest/page/test-page",
                json={"display_name": "New Name"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
