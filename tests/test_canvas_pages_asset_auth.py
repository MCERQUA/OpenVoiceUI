"""
/pages/ non-asset auth gate (2026-09-15): with CANVAS_REQUIRE_AUTH on, only embedded-asset types are served
without a session; page backups, .htm, data/doc files and extensionless files need one. Slug-only URLs still
redirect to their .html page, and the self-hosted default (auth off) is unchanged.
"""
import pytest


@pytest.fixture()
def pages(tmp_path, monkeypatch):
    import routes.canvas as canvas
    for name, body in {
        "logo.png": b"\x89PNG\r\n", "app.webmanifest": b"{}", "plan.json": b'{"secret": 1}',
        "report.html.bak-20260626-002747": b"<html>old private page</html>", "legacy.htm": b"<html>x</html>",
        "notes": b"no extension file", "slugpage.html": b"<html>slug</html>",
    }.items():
        (tmp_path / name).write_bytes(body)
    monkeypatch.setattr(canvas, "CANVAS_PAGES_DIR", tmp_path)
    monkeypatch.setattr(canvas, "CANVAS_REQUIRE_AUTH", True)
    return canvas


@pytest.fixture()
def client(pages):
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    if "canvas" not in app.blueprints:
        app.register_blueprint(pages.canvas_bp)
    return app.test_client()


def _as(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, "get_token_from_request", lambda: "tok" if user else None)
    monkeypatch.setattr(auth, "verify_clerk_token", lambda t: user)


@pytest.mark.parametrize("path,expected", [
    ("logo.png", 200), ("app.webmanifest", 200),
    ("plan.json", 401), ("report.html.bak-20260626-002747", 401), ("legacy.htm", 401), ("notes", 401),
])
def test_anonymous(client, monkeypatch, path, expected):
    _as(monkeypatch, None)
    assert client.get(f"/pages/{path}").status_code == expected


def test_slug_only_url_still_redirects(client, monkeypatch):
    _as(monkeypatch, None)
    r = client.get("/pages/slugpage")
    assert r.status_code == 301 and r.headers["Location"].endswith("/pages/slugpage.html")


@pytest.mark.parametrize("path", ["plan.json", "report.html.bak-20260626-002747", "legacy.htm", "notes"])
def test_signed_in_user_gets_non_asset_files(client, monkeypatch, path):
    _as(monkeypatch, "user_123")
    assert client.get(f"/pages/{path}").status_code == 200


def test_auth_off_default_unchanged(client, pages, monkeypatch):
    monkeypatch.setattr(pages, "CANVAS_REQUIRE_AUTH", False)
    _as(monkeypatch, None)
    assert client.get("/pages/plan.json").status_code == 200
