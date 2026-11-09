"""
Uploads listing + always-private areas (2026-11-09).

Measured on the live fleet the same day, unauthenticated: a tenant's /api/uploads answered 200 with every
upload filename, and /uploads/field-dock/<photo>.jpg answered 200 with a Field Dock photo (receipts, work
orders, invoices carrying a Bill-To name / address / phone). Cause: app.py listed '/uploads/' and
'/api/uploads' (and '/api/upload' as a PREFIX, which also matched '/api/uploads') as public, and the
uploads gate in routes/static_files.py only ever logs in the default shadow mode.

Now, whenever CANVAS_REQUIRE_AUTH is on, in EVERY UPLOADS_AUTH_MODE:
  - the listing needs a Clerk session (Bearer or __session cookie) or the agent key, at app.require_auth
    AND at the route;
  - /uploads/field-dock/ and /uploads/sms/ (plus any UPLOADS_PRIVATE_PREFIXES) need a session, a signed
    link or the agent key, judged on the RESOLVED path;
  - every other upload is unchanged (shadow: served + logged), so a public canvas page's images still load.

These run the real gate: Clerk key set (require_auth registered), real RS256 tokens, the real canvas route.
"""
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

AGENT = "agent-key-private-areas"
SIGN_KEY = "s" * 40
PHOTO = "field-dock/2026-09-14T03-24-30-377797Z_mms0.jpg"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import routes.static_files as sf
    import routes.canvas as canvas
    up = tmp_path / "uploads"
    (up / "field-dock").mkdir(parents=True)
    (up / "field-dock" / PHOTO.split("/", 1)[1]).write_bytes(b"\xff\xd8\xff\xe0receipt")
    (up / "sms").mkdir()
    (up / "sms" / "2026-06-26T15-58-inbound-1.jpg").write_bytes(b"\xff\xd8\xff\xe0mms")
    (up / "receipts").mkdir()
    (up / "receipts" / "r1.jpg").write_bytes(b"\xff\xd8\xff\xe0r")
    (up / "ai-gen-hero.png").write_bytes(b"\x89PNG\r\n\x1a\nhero")
    (up / "sms-d6a6940a.jpg").write_bytes(b"\xff\xd8\xff\xe0top")   # sms-router save: public by design
    (up / "fd-link.jpg").symlink_to(up / "field-dock" / PHOTO.split("/", 1)[1])
    monkeypatch.setattr(sf, "UPLOADS_DIR", up)
    monkeypatch.setattr(sf, "UPLOADS_SHADOW_LOG", up / ".uploads-auth-shadow.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", True)
    monkeypatch.setattr(sf, "UPLOADS_AUTH_MODE", "shadow")      # the live fleet's mode
    monkeypatch.delenv("UPLOADS_PRIVATE_PREFIXES", raising=False)
    monkeypatch.setenv("UPLOADS_SIGNING_KEY", SIGN_KEY)

    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "promo.html").write_text(
        '<html><head></head><body><img src="/uploads/ai-gen-hero.png">'
        '<img src="/uploads/sms-d6a6940a.jpg"></body></html>')
    (pages / "field-dock.html").write_text(
        '<html><head></head><body><img src="/uploads/%s"></body></html>' % PHOTO)
    manifest = tmp_path / "canvas-manifest.json"
    manifest.write_text(json.dumps({"pages": {
        "promo": {"filename": "promo.html", "is_public": True},
        "field-dock": {"filename": "field-dock.html", "is_public": False},
    }}))
    monkeypatch.setattr(canvas, "CANVAS_PAGES_DIR", pages)
    monkeypatch.setattr(canvas, "CANVAS_MANIFEST_PATH", manifest)
    monkeypatch.setattr(canvas, "CANVAS_REQUIRE_AUTH", True)
    monkeypatch.setattr(canvas, "_manifest_cache", {"data": None, "mtime": 0})
    return sf


@pytest.fixture()
def clerk(monkeypatch):
    import services.auth as auth
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "k1"
    monkeypatch.setattr(auth, "_jwks_cache", {"keys": [jwk], "fetched_at": time.time()})
    monkeypatch.setattr(auth, "_ALLOWED_USER_IDS", {"user_owner"})
    monkeypatch.setattr(auth, "_CLERK_CONFIGURED", True)

    def mint(age=0, life=60, sub="user_owner"):
        iat = int(time.time()) - age
        return jwt.encode({"sub": sub, "iat": iat, "nbf": iat - 10, "exp": iat + life},
                          key, algorithm="RS256", headers={"kid": "k1"})
    return mint


@pytest.fixture()
def client(env, clerk, monkeypatch):
    # Clerk configured => app.require_auth is registered, exactly as on a hosted tenant.
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_private_areas")
    monkeypatch.setenv("AGENT_API_KEY", AGENT)
    from app import create_app
    import routes.canvas as canvas
    app, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in app.blueprints:
        app.register_blueprint(env.static_files_bp)
    if "canvas" not in app.blueprints:
        app.register_blueprint(canvas.canvas_bp)
    return app.test_client()


def _rows(sf):
    p = sf.UPLOADS_SHADOW_LOG
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def _signed(path, ttl=600):
    from services import upload_links
    return upload_links.signed_url(path, ttl, key=SIGN_KEY)


# ------------------------------------------------------------------ the listing

def test_listing_anonymous_is_401_in_shadow_mode(client):
    r = client.get("/api/uploads")
    assert r.status_code == 401 and r.get_json()["code"] == "auth_required"
    for q in ("?type=image", "?type=text", "?type=all", "?limit=400"):
        assert client.get("/api/uploads" + q).status_code == 401, q


def test_listing_trailing_slash_and_head_are_not_public(client):
    assert client.head("/api/uploads").status_code == 401
    assert client.get("/api/uploads/").status_code in (401, 404)


def test_listing_bearer_cookie_and_agent_key_are_granted(client, clerk):
    assert client.get("/api/uploads", headers={"Authorization": "Bearer " + clerk()}).status_code == 200
    client.set_cookie("__session", clerk(), domain="localhost")
    r = client.get("/api/uploads")
    assert r.status_code == 200
    names = {f["filename"] for f in r.get_json()["files"]}
    assert "ai-gen-hero.png" in names and not any(n.startswith(".") for n in names)
    client.delete_cookie("__session", domain="localhost")
    assert client.get("/api/uploads", headers={"X-Agent-Key": AGENT}).status_code == 200


def test_listing_rejects_bad_credentials(client, clerk):
    assert client.get("/api/uploads", headers={"X-Agent-Key": AGENT + "x"}).status_code == 401
    assert client.get("/api/uploads", headers={"Authorization": "Bearer " + clerk(age=600)}).status_code == 401
    assert client.get("/api/uploads", headers={"Authorization": "Bearer " + clerk(sub="user_other")}).status_code == 401
    # a signed link opens one file, never the index
    assert client.get("/api/uploads?" + _signed("/uploads/ai-gen-hero.png").split("?", 1)[1]).status_code == 401


def test_listing_gated_at_the_route_even_without_require_auth(env, monkeypatch):
    # Defense in depth: the route gate alone (no Clerk key, so require_auth is not registered).
    monkeypatch.delenv("CLERK_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("VITE_CLERK_PUBLISHABLE_KEY", raising=False)
    monkeypatch.setenv("CANVAS_REQUIRE_AUTH", "false")   # keep create_app out of the fail-closed posture
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in app.blueprints:
        app.register_blueprint(env.static_files_bp)
    c = app.test_client()
    for mode in ("off", "shadow", "enforce"):
        monkeypatch.setattr(env, "UPLOADS_AUTH_MODE", mode)
        assert c.get("/api/uploads").status_code == 401, mode
    rows = [r for r in _rows(env) if r["file"] == "(listing) /api/uploads"]
    assert rows and all(r["mode"] == "always-enforce" for r in rows)


def test_upload_post_stays_public(client):
    # '/api/upload' is public as an EXACT path (the POST); only the prefix match was removed.
    r = client.post("/api/upload", data={})
    assert r.status_code != 401


# ------------------------------------------------------------------ private areas

@pytest.mark.parametrize("path", [
    "/uploads/" + PHOTO,
    "/uploads/sms/2026-06-26T15-58-inbound-1.jpg",
    "/uploads/field-dock/does-not-exist.jpg",       # same answer as an existing file: no existence oracle
    "/uploads/./" + PHOTO,
    "/uploads/fd-link.jpg",                          # symlink into the private area
])
def test_private_anonymous_is_401_in_shadow_mode(client, path):
    r = client.get(path, headers={"Accept": "image/avif,image/webp,*/*"})
    assert r.status_code == 401, (path, r.status_code)


def test_dotdot_walk_into_private_area_serves_nothing(client):
    # '..' is refused before the gate (dot-part rule, 404 for every such path, no oracle); never the bytes.
    r = client.get("/uploads/receipts/../" + PHOTO)
    assert r.status_code in (401, 404) and b"receipt" not in r.data


def test_private_browser_navigation_goes_to_login(client):
    r = client.get("/uploads/" + PHOTO, headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 302 and r.headers["Location"].startswith("/?redirect=/uploads/field-dock/")


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_private_enforced_in_every_mode(client, env, monkeypatch, mode):
    monkeypatch.setattr(env, "UPLOADS_AUTH_MODE", mode)
    assert client.get("/uploads/" + PHOTO).status_code == 401


def test_private_grants(client, clerk):
    assert client.get("/uploads/" + PHOTO, headers={"Authorization": "Bearer " + clerk()}).status_code == 200
    assert client.get("/uploads/" + PHOTO, headers={"X-Agent-Key": AGENT}).status_code == 200
    assert client.get(_signed("/uploads/" + PHOTO)).status_code == 200
    # the owner's browser: an <img> inside the app shell carries the __session cookie
    client.set_cookie("__session", clerk(), domain="localhost")
    r = client.get("/uploads/" + PHOTO, headers={"Accept": "image/*"})
    assert r.status_code == 200 and r.data.endswith(b"receipt")


def test_private_rejects_bad_credentials(client, clerk):
    assert client.get("/uploads/" + PHOTO, headers={"X-Agent-Key": "wrong"}).status_code == 401
    assert client.get("/uploads/" + PHOTO, headers={"Authorization": "Bearer " + clerk(age=600)}).status_code == 401
    forged = "/uploads/%s?exp=%d&sig=%s" % (PHOTO, int(time.time()) + 600, "a" * 64)
    assert client.get(forged).status_code == 401
    # a signature for an ordinary file does not open a private one
    s = _signed("/uploads/ai-gen-hero.png").split("?", 1)[1]
    assert client.get("/uploads/" + PHOTO + "?" + s).status_code == 401


def test_private_denials_logged_as_real_denials(client, env):
    client.get("/uploads/" + PHOTO)
    rows = [r for r in _rows(env) if r["file"].startswith("field-dock/")]
    assert rows and rows[-1]["mode"] == "always-enforce" and rows[-1]["reason"] == "no-credential"


def test_instance_can_add_private_prefixes(client, monkeypatch):
    assert client.get("/uploads/receipts/r1.jpg").status_code == 200
    monkeypatch.setenv("UPLOADS_PRIVATE_PREFIXES", " receipts/ , ../evil, .hidden")
    assert client.get("/uploads/receipts/r1.jpg").status_code == 401
    assert client.get("/uploads/ai-gen-hero.png").status_code == 200


def test_auth_off_self_hosted_unchanged(client, env, monkeypatch):
    monkeypatch.setattr(env, "UPLOADS_REQUIRE_AUTH", False)
    assert client.get("/uploads/" + PHOTO).status_code == 200


# ------------------------------------------------------------------ what must NOT break

def test_ordinary_generated_image_still_public(client, env):
    for p in ("/uploads/ai-gen-hero.png", "/uploads/sms-d6a6940a.jpg"):
        r = client.get(p, headers={"Accept": "image/*"})
        assert r.status_code == 200, p
    # shadow behaviour kept for ordinary files: served AND logged as a would-deny
    assert any(r["file"] == "ai-gen-hero.png" and r["mode"] == "shadow" for r in _rows(env))


def test_public_page_and_its_images_render_anonymously(client):
    r = client.get("/pages/promo.html")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    srcs = [s.split('"', 1)[0] for s in html.split('src="')[1:] if s.startswith("/uploads/")]
    assert srcs == ["/uploads/ai-gen-hero.png", "/uploads/sms-d6a6940a.jpg"]
    for s in srcs:
        assert client.get(s, headers={"Accept": "image/*"}).status_code == 200, s


def test_private_page_images_load_for_the_signed_in_owner(client, clerk):
    assert client.get("/pages/field-dock.html").status_code in (302, 401)
    client.set_cookie("__session", clerk(), domain="localhost")
    assert client.get("/pages/field-dock.html").status_code == 200
    assert client.get("/uploads/" + PHOTO, headers={"Accept": "image/*"}).status_code == 200


def test_other_upload_enumerators_are_not_public(client):
    for p in ("/api/grid-gen/uploads", "/api/image-gen/saved", "/api/albums", "/api/audio-clips/list"):
        assert client.get(p).status_code == 401, p


# ------------------------------------------------------------------ path tricks (review 2026-11-09)

@pytest.mark.parametrize("path", [
    "/uploads/field-dock",                                   # the area's own directory, no slash
    "/uploads/field-dock/",
    "/uploads/FIELD-DOCK/" + PHOTO.split("/", 1)[1],         # case
    "/uploads/Field-Dock/" + PHOTO.split("/", 1)[1],
    "/uploads/field%2Ddock/" + PHOTO.split("/", 1)[1],       # percent-encoded '-'
    "/uploads/field-dock%2F" + PHOTO.split("/", 1)[1],       # percent-encoded '/'
    "/uploads/%66ield-dock/" + PHOTO.split("/", 1)[1],       # percent-encoded 'f'
    "/uploads/sms",
])
def test_private_path_tricks_are_401(client, path):
    r = client.get(path, headers={"Accept": "image/*"})
    assert r.status_code == 401, (path, r.status_code)


@pytest.mark.parametrize("path", [
    "/uploads//" + PHOTO,
    "/uploads/./field-dock/./" + PHOTO.split("/", 1)[1],
    "/uploads/receipts/%2E%2E/" + PHOTO,
    "/uploads/field-dock/%2E%2E/field-dock/" + PHOTO.split("/", 1)[1],
])
def test_private_path_tricks_never_serve_bytes(client, path):
    r = client.get(path, headers={"Accept": "image/*"}, follow_redirects=True)
    assert r.status_code != 200 and b"receipt" not in r.data, (path, r.status_code)


def test_album_symlink_into_private_area_is_401(client, env, monkeypatch):
    import routes.albums as albums
    adir = env.UPLOADS_DIR / "albums"
    (adir / "a1").mkdir(parents=True)
    (adir / "a1" / "p.jpg").symlink_to(env.UPLOADS_DIR / PHOTO)
    (adir / "a1" / "ok.jpg").write_bytes(b"\xff\xd8\xff\xe0album")
    monkeypatch.setattr(albums, "ALBUMS_DIR", adir)
    if "albums" not in client.application.blueprints:
        client.application.register_blueprint(albums.albums_bp)
    assert client.get("/uploads/albums/a1/p.jpg").status_code == 401
    assert client.get("/uploads/albums/a1/ok.jpg").status_code == 200      # ordinary album photo: shadow


def test_album_route_honours_instance_private_prefixes(client, env, monkeypatch):
    import routes.albums as albums
    adir = env.UPLOADS_DIR / "albums"
    (adir / "a2").mkdir(parents=True)
    (adir / "a2" / "ok.jpg").write_bytes(b"\xff\xd8\xff\xe0album")
    monkeypatch.setattr(albums, "ALBUMS_DIR", adir)
    if "albums" not in client.application.blueprints:
        client.application.register_blueprint(albums.albums_bp)
    assert client.get("/uploads/albums/a2/ok.jpg").status_code == 200
    monkeypatch.setenv("UPLOADS_PRIVATE_PREFIXES", "albums/")
    assert client.get("/uploads/albums/a2/ok.jpg").status_code == 401
