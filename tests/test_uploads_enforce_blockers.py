"""
/uploads/ enforce blockers (phase 3 prep). Three things had to be true before any instance can
run UPLOADS_AUTH_MODE=enforce, and these tests pin each one:

1. A browser that HAS a login but sends a stale session must not be told its session is
   "invalid" with no way back. The __session cookie lives 30 days while the Clerk token in it
   lives ~60 s and is refreshed only by the app shell (src/app.js _syncSessionCookie), so any
   navigation made after leaving the shell carries an expired token. The would-deny row now
   says session-expired (a token this Clerk instance signed) rather than session-invalid, and
   enforce sends a browser navigation through the login shell, which refreshes the session and
   returns. Real RS256 tokens, not mocks: a forged token that is ALSO expired must still read
   invalid, because PyJWT checks the signature before exp.
2. /uploads/albums/<id>/<file> is a more specific rule than /uploads/<path:filename>, so it wins
   the route match and never reached the uploads gate. It now runs the gate itself.
3. GET /api/uploads (the listing: every filename + size) sits under app.require_auth's public
   '/api/upload' prefix. It now runs the gate as a listing: no signed-link grant, JSON 401.
"""
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15"
NAV = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
KEY = "k" * 40
AGENT = "agent-key-for-tests"


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def up(tmp_path, monkeypatch):
    import routes.static_files as sf
    import routes.albums as al
    (tmp_path / "package.html").write_text("<p>x</p>")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    album = tmp_path / "albums" / "abc123"
    album.mkdir(parents=True)
    (album / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (album / "album.json").write_text(json.dumps({"photos": [{"lat": 1.0, "lng": 2.0}]}))
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_SHADOW_LOG", tmp_path / ".uploads-auth-shadow.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", True)
    monkeypatch.setattr(sf, "UPLOADS_AUTH_MODE", "shadow")
    monkeypatch.setattr(al, "ALBUMS_DIR", tmp_path / "albums")
    monkeypatch.setenv("UPLOADS_SIGNING_KEY", KEY)
    monkeypatch.setenv("AGENT_API_KEY", AGENT)
    return sf


@pytest.fixture()
def app(up):
    from app import create_app
    import routes.albums as al
    a, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in a.blueprints:
        a.register_blueprint(up.static_files_bp)
    if "albums" not in a.blueprints:
        a.register_blueprint(al.albums_bp)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def clerk(monkeypatch):
    """A real Clerk-shaped signer: one RS256 key in the JWKS cache, one allowlisted user."""
    import services.auth as auth
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "k1"
    monkeypatch.setattr(auth, "_jwks_cache", {"keys": [jwk], "fetched_at": time.time()})
    monkeypatch.setattr(auth, "_ALLOWED_USER_IDS", {"user_owner"})
    monkeypatch.setattr(auth, "_CLERK_CONFIGURED", True)

    def mint(age, life=60, sub="user_owner", signer=key):
        iat = int(time.time()) - age
        return jwt.encode({"sub": sub, "iat": iat, "nbf": iat - 10, "exp": iat + life},
                          signer, algorithm="RS256", headers={"kid": "k1"})
    return mint


def _rows(up):
    p = up.UPLOADS_SHADOW_LOG
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def _cookie(client, token):
    client.set_cookie("__session", token, domain="localhost")


def _as(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, "get_token_from_request", lambda: "tok" if user else None)
    monkeypatch.setattr(auth, "verify_clerk_token", lambda t: user)


def _signed(path, ttl=600):
    from services import upload_links
    return upload_links.signed_url(path, ttl, key=KEY)


# ---------------------------------------------------------------- 1. stale session

def test_fresh_session_is_granted(client, up, clerk):
    _cookie(client, clerk(age=5))
    r = client.get("/uploads/package.html", headers={"User-Agent": IPHONE, "Accept": NAV})
    assert r.status_code == 200
    assert _rows(up) == []


def test_stale_session_is_recorded_as_expired_not_invalid(client, up, clerk):
    # Minted 2 minutes ago with Clerk's ~60 s life: what a phone sends after leaving the shell.
    _cookie(client, clerk(age=120))
    r = client.get("/uploads/package.html", headers={"User-Agent": IPHONE, "Accept": NAV})
    assert r.status_code == 200                      # shadow still serves
    rows = _rows(up)
    assert len(rows) == 1
    assert rows[0]["grants"] == {"agent_key": "absent", "signature": "absent", "session": "expired"}
    assert rows[0]["reason"] == "session-expired"
    assert rows[0]["enforce_action"] == "login-redirect"


def test_forged_and_expired_token_is_invalid_never_expired(client, up, clerk):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _cookie(client, clerk(age=120, signer=other))
    client.get("/uploads/package.html", headers={"Accept": NAV})
    assert _rows(up)[0]["reason"] == "session-invalid"


def test_current_token_for_a_user_not_allowlisted(client, up, clerk):
    _cookie(client, clerk(age=5, sub="user_stranger"))
    client.get("/uploads/package.html", headers={"Accept": NAV})
    assert _rows(up)[0]["reason"] == "session-not-allowed"


def test_enforce_stale_navigation_goes_through_login_shell(client, up, clerk, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _cookie(client, clerk(age=120))
    r = client.get("/uploads/package.html", headers={"User-Agent": IPHONE, "Accept": NAV})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/?redirect=/uploads/package.html")


def test_enforce_stale_non_navigation_is_401(client, up, clerk, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _cookie(client, clerk(age=120))
    r = client.get("/uploads/logo.png", headers={"Accept": "image/*"})
    assert r.status_code == 401
    assert _rows(up)[0]["enforce_action"] == "401"


def test_failure_reason_without_keys(monkeypatch):
    import services.auth as auth
    monkeypatch.setattr(auth, "_get_jwks", lambda: [])
    assert auth.token_failure_reason("a.b.c") == auth.TOKEN_NO_KEYS
    assert auth.token_failure_reason("") == auth.TOKEN_ABSENT


def test_verify_clerk_token_decision_unchanged(clerk):
    import services.auth as auth
    assert auth.verify_clerk_token(clerk(age=5)) == "user_owner"
    assert auth.verify_clerk_token(clerk(age=120)) is None
    assert auth.verify_clerk_token(clerk(age=5, sub="user_stranger")) is None


# ---------------------------------------------------------------- 2. albums

def test_albums_rule_wins_the_route_match(app):
    endpoint, _ = app.url_map.bind("localhost").match("/uploads/albums/abc123/photo.jpg")
    assert endpoint == "albums.serve_album_photo"


def test_albums_shadow_serves_and_logs(client, up, monkeypatch):
    _as(monkeypatch, None)
    r = client.get("/uploads/albums/abc123/photo.jpg")
    assert r.status_code == 200
    assert r.headers["X-Robots-Tag"].startswith("noindex")
    rows = _rows(up)
    assert len(rows) == 1 and rows[0]["file"] == "albums/abc123/photo.jpg"


@pytest.mark.parametrize("path", [
    "/uploads/albums/abc123/photo.jpg",
    "/uploads/albums/abc123/album.json",
    "/uploads/albums/abc123/missing.jpg",            # no existence oracle: 401, not 404
])
def test_albums_enforce_anonymous_denied(client, up, monkeypatch, path):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, None)
    assert client.get(path).status_code == 401


def test_albums_enforce_grants(client, up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, None)
    p = "/uploads/albums/abc123/photo.jpg"
    assert client.get(_signed(p)).status_code == 200
    assert client.get(p, headers={"X-Agent-Key": AGENT}).status_code == 200
    assert client.get(p + "?exp=9999999999&sig=" + "0" * 64).status_code == 401
    # a signature for one album file does not open another
    assert client.get(_signed("/uploads/albums/abc123/photo.jpg").replace("photo.jpg", "album.json")
                      ).status_code == 401
    _as(monkeypatch, "user_owner")
    assert client.get(p).status_code == 200


# ---------------------------------------------------------------- 3. listing

def test_listing_served_by_the_gated_blueprint_route(app):
    endpoint, _ = app.url_map.bind("localhost").match("/api/uploads")
    assert endpoint == "static_files.list_uploads"
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text()
    assert '@app.route("/api/uploads"' not in src


def test_listing_shadow_serves_and_logs(client, up, monkeypatch):
    _as(monkeypatch, None)
    r = client.get("/api/uploads")
    assert r.status_code == 200 and r.get_json()["count"] == 2
    rows = _rows(up)
    assert len(rows) == 1 and rows[0]["file"] == "(listing) /api/uploads"
    assert rows[0]["enforce_action"] == "401" and "signature" not in rows[0]["grants"]


def test_listing_enforce(client, up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, None)
    r = client.get("/api/uploads", headers={"Accept": NAV})
    assert r.status_code == 401 and r.get_json()["code"] == "auth_required"   # JSON, never a redirect
    # a signed link opens one file, never the index
    assert client.get("/api/uploads?" + _signed("/uploads/logo.png").split("?", 1)[1]).status_code == 401
    assert client.get("/api/uploads", headers={"X-Agent-Key": AGENT}).status_code == 200
    _as(monkeypatch, "user_owner")
    names = {f["filename"] for f in client.get("/api/uploads").get_json()["files"]}
    assert names == {"package.html", "logo.png"}     # dotfiles and dirs never listed


def test_listing_off_when_auth_not_required(client, up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_REQUIRE_AUTH", False)
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    _as(monkeypatch, None)
    assert client.get("/api/uploads").status_code == 200
    assert _rows(up) == []
