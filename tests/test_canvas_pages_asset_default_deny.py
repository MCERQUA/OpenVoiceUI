"""
/pages/ asset default-deny (2026-11-21, mesh SEC-094). With CANVAS_REQUIRE_AUTH on, an image/style/script/media file
under /pages/ is served without a session ONLY when a page that is itself served without a session (an is_public
page, or an OS page) references it. Before this, every file of those types was open to anyone with the URL, so a
private page's images were readable signed-out (measured on test-dev and :latest: png/jpg/webp/svg/js/css/mp4 200).
"""
import json

import pytest

PRIVATE_HTML = b'<html><img src="private.png"><img src="/pages/img/private-hero.webp"></html>'
PUBLIC_HTML = (
    b'<html><head><meta property="og:image" content="https://test.jam-bot.com/pages/og.png">'
    b'<link rel="stylesheet" href="style.css"></head><body>'
    b'<img src="pub.png?v=3"><img srcset="a.png 1x, b.png 2x"><img src="/pages/img/hero.webp">'
    b'<img src="../escape.png"><video poster="sub/poster.jpg" src="clip.mp4"></video></body></html>'
)
ASSETS = [
    "private.png", "img/private-hero.webp", "secret.png", "secret.jpg", "secret.webp", "secret.svg",
    "secret.js", "secret.css", "secret.mp4", "pub.png", "a.png", "b.png", "img/hero.webp", "og.png",
    "bg.jpg", "font.woff2", "sub/poster.jpg", "clip.mp4", "desk-icon.svg",
]


@pytest.fixture()
def pages(tmp_path, monkeypatch):
    import routes.canvas as canvas
    root = tmp_path / "canvas-pages"
    (root / "img").mkdir(parents=True)
    (root / "sub").mkdir()
    for name in ASSETS:
        (root / name).write_bytes(b"asset-bytes")
    (root / "style.css").write_bytes(b"body{background:url('bg.jpg')} @font-face{src:url(font.woff2)}")
    (root / "private-page.html").write_bytes(PRIVATE_HTML)
    (root / "public-page.html").write_bytes(PUBLIC_HTML)
    (root / "desktop.html").write_bytes(b'<html><img src="desk-icon.svg"></html>')
    manifest = tmp_path / "canvas-manifest.json"
    manifest.write_text(json.dumps({"pages": {
        "private-page": {"is_public": False}, "public-page": {"is_public": True},
    }}))
    monkeypatch.setattr(canvas, "CANVAS_PAGES_DIR", root)
    monkeypatch.setattr(canvas, "CANVAS_MANIFEST_PATH", manifest)
    monkeypatch.setattr(canvas, "_manifest_cache", {"data": None, "mtime": 0})
    monkeypatch.setattr(canvas, "CANVAS_REQUIRE_AUTH", True)
    canvas._manifest_path_for_test = manifest
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


# The finding: assets of a PRIVATE page, and assets no public page names, must not load signed-out.
@pytest.mark.parametrize("path", [
    "private.png", "img/private-hero.webp", "secret.png", "secret.jpg", "secret.webp", "secret.svg",
    "secret.js", "secret.css", "secret.mp4",
])
def test_anonymous_private_assets_denied(client, monkeypatch, path):
    _as(monkeypatch, None)
    assert client.get(f"/pages/{path}").status_code == 401


def test_anonymous_missing_asset_is_401_not_404(client, monkeypatch):
    _as(monkeypatch, None)   # the gate must not reveal which asset names exist
    assert client.get("/pages/does-not-exist.png").status_code == 401


def test_escape_reference_does_not_open_anything(client, monkeypatch):
    _as(monkeypatch, None)   # "../escape.png" in a public page resolves outside /pages/ -> ignored
    assert client.get("/pages/escape.png").status_code == 401


# The trap: a PUBLIC page's own images, styles, fonts and media must still load signed-out.
@pytest.mark.parametrize("path", [
    "pub.png", "a.png", "b.png", "img/hero.webp", "og.png", "style.css", "bg.jpg", "font.woff2",
    "sub/poster.jpg", "clip.mp4", "desk-icon.svg",
])
def test_anonymous_public_page_assets_served(client, monkeypatch, path):
    _as(monkeypatch, None)
    assert client.get(f"/pages/{path}").status_code == 200


def test_asset_follows_page_when_page_goes_private(client, pages, monkeypatch):
    _as(monkeypatch, None)
    assert client.get("/pages/pub.png").status_code == 200
    m = pages._manifest_path_for_test
    import os
    m.write_text(json.dumps({"pages": {"public-page": {"is_public": False}}}))
    st = os.stat(m)
    os.utime(m, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert client.get("/pages/pub.png").status_code == 401


@pytest.mark.parametrize("path", ["private.png", "secret.mp4", "pub.png"])
def test_signed_in_user_gets_every_asset(client, monkeypatch, path):
    _as(monkeypatch, "user_123")
    assert client.get(f"/pages/{path}").status_code == 200


def test_auth_off_default_unchanged(client, pages, monkeypatch):
    monkeypatch.setattr(pages, "CANVAS_REQUIRE_AUTH", False)
    _as(monkeypatch, None)
    assert client.get("/pages/secret.png").status_code == 200


def test_signed_out_navigation_to_private_image_goes_to_sign_in(client, monkeypatch):
    _as(monkeypatch, None)   # owner taps a texted image link: same sign-in redirect as a private page
    r = client.get("/pages/private.png", headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/?redirect=/pages/private.png")
