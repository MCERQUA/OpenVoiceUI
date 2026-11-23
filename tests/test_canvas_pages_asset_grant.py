"""
Asset grant for a private page opened top-level (follow-up to SEC-094 / PR #612).

A private canvas page opened straight from a texted link runs top-level, outside the app shell, so nothing refreshes
the 60 s Clerk __session cookie. Under the default-deny asset gate, any asset fetched after that token expires (a lazy
<img>, a video seek) would 401. When the private-page gate verifies a viewer it now also sets a signed, tenant-bound,
HttpOnly cookie scoped to /pages/ that the ASSET gate (and only the asset gate) accepts.
"""
import json
import time

import pytest

ASSETS = ["private.png", "clip.mp4", "lazy.webp", "secret.css"]


@pytest.fixture()
def pages(tmp_path, monkeypatch):
    import routes.canvas as canvas
    import routes.static_files as sf
    root = tmp_path / "canvas-pages"
    (root / "_data").mkdir(parents=True)
    for name in ASSETS:
        (root / name).write_bytes(b"asset-bytes")
    (root / "notes.json").write_bytes(b"{}")
    (root / "_data" / "state.json").write_bytes(b"{}")
    (root / "private-page.html").write_bytes(
        b'<html><body><img src="private.png"><img loading="lazy" src="lazy.webp"><video src="clip.mp4"></video>'
        b'</body></html>')
    (root / "other-private.html").write_bytes(b"<html><body>other</body></html>")
    (root / "public-page.html").write_bytes(b"<html><body>public</body></html>")
    manifest = tmp_path / "canvas-manifest.json"
    manifest.write_text(json.dumps({"pages": {
        "private-page": {"is_public": False}, "other-private": {"is_public": False},
        "public-page": {"is_public": True},
    }}))
    monkeypatch.setattr(canvas, "CANVAS_PAGES_DIR", root)
    monkeypatch.setattr(canvas, "CANVAS_MANIFEST_PATH", manifest)
    monkeypatch.setattr(canvas, "_manifest_cache", {"data": None, "mtime": 0})
    monkeypatch.setattr(canvas, "CANVAS_REQUIRE_AUTH", True)
    monkeypatch.setattr(sf, "_chunk_key", lambda: b"k" * 64)
    monkeypatch.setenv("JAMBOT_TENANT", "tenant-a")
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


def _grant_header(resp):
    return [h for h in resp.headers.getlist("Set-Cookie") if h.startswith("ovu_canvas_assets=")]


def _open_private_page(client, monkeypatch):
    _as(monkeypatch, "user_1")
    r = client.get("/pages/private-page.html")
    assert r.status_code == 200
    return r


def test_signed_in_private_page_serve_sets_scoped_httponly_grant(client, monkeypatch):
    hdr = _grant_header(_open_private_page(client, monkeypatch))
    assert len(hdr) == 1
    h = hdr[0]
    assert "Path=/pages/" in h and "HttpOnly" in h and "Secure" in h and "SameSite=Strict" in h
    assert "Max-Age=43200" in h


def test_assets_keep_loading_after_clerk_token_expires(client, monkeypatch):
    _open_private_page(client, monkeypatch)
    _as(monkeypatch, None)          # 60 s later: the __session token the page arrived with no longer verifies
    for name in ASSETS:
        r = client.get(f"/pages/{name}")
        assert r.status_code == 200 and r.data == b"asset-bytes", name
    r = client.get("/pages/clip.mp4", headers={"Range": "bytes=0-3"})   # video seek = a fresh Range request
    assert r.status_code == 206


def test_grant_never_opens_pages_data_or_non_asset_files(client, monkeypatch):
    _open_private_page(client, monkeypatch)
    _as(monkeypatch, None)
    assert client.get("/pages/private-page.html").status_code == 401
    assert client.get("/pages/other-private.html").status_code == 401
    assert client.get("/pages/notes.json").status_code == 401
    assert client.get("/pages/_data/state.json").status_code == 401


def test_without_the_grant_expired_session_still_401s(client, monkeypatch):
    _as(monkeypatch, None)          # the regression this fixes, and the SEC-094 default-deny, both still hold
    for name in ASSETS:
        assert client.get(f"/pages/{name}").status_code == 401, name


def test_anonymous_and_public_serves_never_set_the_grant(client, monkeypatch):
    _as(monkeypatch, None)
    assert _grant_header(client.get("/pages/public-page.html")) == []
    assert _grant_header(client.get("/pages/private-page.html")) == []
    _as(monkeypatch, "user_1")      # signed in, but a PUBLIC page: the page gate verified nobody, so no grant
    assert _grant_header(client.get("/pages/public-page.html")) == []


def test_auth_off_never_sets_the_grant(client, pages, monkeypatch):
    monkeypatch.setattr(pages, "CANVAS_REQUIRE_AUTH", False)
    _as(monkeypatch, "user_1")
    assert _grant_header(client.get("/pages/private-page.html")) == []


@pytest.mark.parametrize("value", [
    "", "garbage", "a1.1.0123456789abcdef." + "0" * 64,                      # malformed / wrong signature
])
def test_forged_grants_rejected(client, monkeypatch, value):
    _as(monkeypatch, None)
    client.set_cookie("ovu_canvas_assets", value, path="/pages/")
    assert client.get("/pages/private.png").status_code == 401


def test_tampered_expired_cross_tenant_and_upload_grants_rejected(client, pages, monkeypatch):
    import routes.static_files as sf
    _as(monkeypatch, None)
    good = pages._mint_asset_grant()
    assert pages._asset_grant_ok(good)
    _, exp, nonce, sig = good.split(".")
    assert not pages._asset_grant_ok(f"a1.{int(exp) + 1}.{nonce}.{sig}")            # tampered expiry
    old = int(time.time()) - 5
    assert not pages._asset_grant_ok(f"a1.{old}.{nonce}.{sf._chunk_sig('canvas-assets', old, nonce, 'tenant-a')}")
    far = int(time.time()) + 10 * 24 * 3600                                         # beyond TTL: not ours
    assert not pages._asset_grant_ok(f"a1.{far}.{nonce}.{sf._chunk_sig('canvas-assets', far, nonce, 'tenant-a')}")
    monkeypatch.setenv("JAMBOT_TENANT", "tenant-b")
    assert not pages._asset_grant_ok(good)                                          # minted for another tenant
    monkeypatch.setenv("JAMBOT_TENANT", "tenant-a")
    up = sf.mint_upload_grant()                                                     # an upload grant is not one
    assert not pages._asset_grant_ok("a1." + up.split(".", 1)[1])
    client.set_cookie("ovu_canvas_assets", good, path="/pages/")
    assert client.get("/pages/private.png").status_code == 200
