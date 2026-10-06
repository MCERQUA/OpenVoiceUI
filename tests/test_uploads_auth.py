"""
/uploads/ auth (2026-10-06). With CANVAS_REQUIRE_AUTH on:
  shadow  (default) — serve as before, and log every request that WOULD be denied;
  enforce           — deny without a Clerk session (401, or a login redirect for browsers),
                      decided BEFORE revealing whether the file exists;
  off / auth off    — unchanged self-hosted behaviour.
Every served upload carries X-Robots-Tag noindex. Cases come from the design's control list,
not from the implementation.
"""
import json

import pytest


@pytest.fixture()
def up(tmp_path, monkeypatch):
    import routes.static_files as sf
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "dump.zip").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_SHADOW_LOG", tmp_path / ".uploads-auth-shadow.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", True)
    monkeypatch.setattr(sf, "UPLOADS_AUTH_MODE", "shadow")
    return sf


@pytest.fixture()
def client(up):
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in app.blueprints:
        app.register_blueprint(up.static_files_bp)
    return app.test_client()


def _as(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, "get_token_from_request", lambda: "tok" if user else None)
    monkeypatch.setattr(auth, "verify_clerk_token", lambda t: user)


def _shadow_rows(up):
    p = up.UPLOADS_SHADOW_LOG
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_shadow_anonymous_still_served_and_logged(client, up, monkeypatch):
    _as(monkeypatch, None)
    r = client.get("/uploads/dump.zip", headers={"User-Agent": "Apache-HttpClient/4.5.14 (Java/17)"})
    assert r.status_code == 200
    rows = _shadow_rows(up)
    assert len(rows) == 1 and rows[0]["file"] == "dump.zip" and rows[0]["exists"] is True
    assert rows[0]["ua"].startswith("Apache-HttpClient") and rows[0]["mode"] == "shadow"


def test_shadow_signed_in_not_logged(client, up, monkeypatch):
    _as(monkeypatch, "user_123")
    assert client.get("/uploads/logo.png").status_code == 200
    assert _shadow_rows(up) == []


def test_enforce_anonymous_denied(client, up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, None)
    assert client.get("/uploads/dump.zip").status_code == 401


def test_enforce_browser_redirected_to_login(client, up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, None)
    r = client.get("/uploads/logo.png", headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 302 and "/?redirect=/uploads/logo.png" in r.headers["Location"]


def test_enforce_no_existence_oracle(client, up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, None)
    assert client.get("/uploads/does-not-exist.sql").status_code == 401


def test_enforce_signed_in_served(client, up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, "user_123")
    r = client.get("/uploads/logo.png")
    assert r.status_code == 200


@pytest.mark.parametrize("mode,require", [("off", True), ("shadow", False), ("enforce", False)])
def test_off_or_auth_disabled_unchanged(client, up, monkeypatch, mode, require):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", mode)
    monkeypatch.setattr(up, "UPLOADS_REQUIRE_AUTH", require)
    _as(monkeypatch, None)
    assert client.get("/uploads/dump.zip").status_code == 200
    assert _shadow_rows(up) == []


def test_noindex_header_on_served_upload(client, up, monkeypatch):
    _as(monkeypatch, "user_123")
    assert client.get("/uploads/logo.png").headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"


def test_shadow_log_itself_never_served(client, up, monkeypatch):
    _as(monkeypatch, None)
    client.get("/uploads/dump.zip")
    _as(monkeypatch, "user_123")
    assert client.get("/uploads/.uploads-auth-shadow.jsonl").status_code == 404


def test_shadow_log_failure_does_not_break_serving(client, up, monkeypatch, tmp_path):
    monkeypatch.setattr(up, "UPLOADS_SHADOW_LOG", tmp_path / "no-such-dir" / "x.jsonl")
    _as(monkeypatch, None)
    assert client.get("/uploads/logo.png").status_code == 200
