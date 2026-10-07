"""
/uploads/ auth (2026-10-06). With CANVAS_REQUIRE_AUTH on:
  shadow  (default) — serve as before, and log every request that WOULD be denied;
  enforce           — deny without a grant (401, or a login redirect for browsers),
                      decided BEFORE revealing whether the file exists;
  off / auth off    — unchanged self-hosted behaviour.
Grants (phase 2): a Clerk session, a signed link (?exp=&sig=, services/upload_links.py),
or the internal X-Agent-Key. Every served upload carries X-Robots-Tag noindex. Cases come
from the design's control list, not from the implementation.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Phase 2 — signed links + agent key
# ---------------------------------------------------------------------------

KEY = "test-signing-key-0123456789abcdef"
AGENT = "test-agent-key-xyz"


@pytest.fixture()
def enforce(up, monkeypatch):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", "enforce")
    monkeypatch.setenv("UPLOADS_SIGNING_KEY", KEY)
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    _as(monkeypatch, None)
    return up


def _links():
    from services import upload_links
    return upload_links


def test_enforce_valid_signature_served(client, enforce):
    url = _links().signed_url("logo.png", "7d")
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers.get("X-Robots-Tag") == "noindex, nofollow, noarchive"
    assert _shadow_rows(enforce) == []


def test_enforce_expired_signature_denied(client, enforce):
    url = _links().signed_url("logo.png", 60, now=int(time.time()) - 3600)
    assert client.get(url).status_code == 401


def test_enforce_forged_signature_denied(client, enforce):
    url = _links().signed_url("logo.png", "1d", key="some-other-secret-0123456789")
    assert client.get(url).status_code == 401
    good = _links().sign("logo.png", "1d")
    assert client.get(f"/uploads/logo.png?exp={good['exp'] + 1}&sig={good['sig']}").status_code == 401


def test_enforce_signature_for_other_path_denied(client, enforce):
    s = _links().sign("logo.png", "1d")
    assert client.get(f"/uploads/dump.zip?exp={s['exp']}&sig={s['sig']}").status_code == 401


def test_enforce_exp_beyond_cap_denied_even_if_correctly_signed(client, enforce):
    links = _links()
    exp = int(time.time()) + links.MAX_TTL_SECONDS + 86400
    sig = links._signature(KEY.encode(), "/uploads/logo.png", exp)
    assert client.get(f"/uploads/logo.png?exp={exp}&sig={sig}").status_code == 401


def test_mint_refuses_ttl_above_cap(enforce):
    links = _links()
    with pytest.raises(links.UploadLinkError):
        links.sign("logo.png", "31d")
    with pytest.raises(links.UploadLinkError):
        links.sign("logo.png", links.MAX_TTL_SECONDS + 1)
    with pytest.raises(links.UploadLinkError):
        links.sign("logo.png", 0)
    assert links.sign("logo.png", "30d")["sig"]


def test_mint_refuses_traversal_and_dotfiles(enforce):
    links = _links()
    for bad in ("../secret.txt", "a/../../x", ".uploads-auth-shadow.jsonl", "/etc/passwd", ""):
        with pytest.raises(links.UploadLinkError):
            links.sign(bad, "1d")


@pytest.mark.parametrize("key", [None, "", "short"])
def test_no_signing_key_signature_ignored_not_crash(client, enforce, monkeypatch, key):
    links = _links()
    s = links.sign("logo.png", "1d")  # minted while a key was set
    if key is None:
        monkeypatch.delenv("UPLOADS_SIGNING_KEY", raising=False)
    else:
        monkeypatch.setenv("UPLOADS_SIGNING_KEY", key)
    with pytest.raises(links.UploadLinkError):
        links.sign("logo.png", "1d")
    url = f"/uploads/logo.png?exp={s['exp']}&sig={s['sig']}"
    assert client.get(url).status_code == 401
    monkeypatch.setattr(enforce, "UPLOADS_AUTH_MODE", "shadow")
    assert client.get(url).status_code == 200
    rows = _shadow_rows(enforce)
    assert rows[-1]["reason"] == "signature-unconfigured"


def test_enforce_agent_key_served(client, enforce, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", AGENT)
    assert client.get("/uploads/dump.zip", headers={"X-Agent-Key": AGENT}).status_code == 200
    assert client.get("/uploads/dump.zip", headers={"X-Agent-Key": AGENT + "x"}).status_code == 401


def test_agent_key_not_a_grant_when_unset(client, enforce):
    assert client.get("/uploads/dump.zip", headers={"X-Agent-Key": ""}).status_code == 401
    assert client.get("/uploads/dump.zip", headers={"X-Agent-Key": "anything"}).status_code == 401


def test_enforce_session_still_served_despite_bad_signature(client, enforce, monkeypatch):
    _as(monkeypatch, "user_123")
    assert client.get("/uploads/logo.png").status_code == 200
    assert client.get("/uploads/logo.png?exp=1&sig=" + "0" * 64).status_code == 200


def test_signed_link_round_trips_encoded_filename(client, enforce, up):
    (up.UPLOADS_DIR / "sub").mkdir()
    (up.UPLOADS_DIR / "sub" / "my mockup.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    url = _links().signed_url("sub/my mockup.png", "1h")
    assert "%20" in url
    assert client.get(url).status_code == 200
    assert _links()._verify_url("https://example.test" + url) == "ok"


def test_shadow_records_which_grant_was_missing(client, enforce, monkeypatch):
    monkeypatch.setattr(enforce, "UPLOADS_AUTH_MODE", "shadow")
    monkeypatch.setenv("AGENT_API_KEY", AGENT)
    expired = _links().signed_url("logo.png", 60, now=int(time.time()) - 3600)
    forged = "/uploads/logo.png?exp=%d&sig=%s" % (int(time.time()) + 600, "a" * 64)
    assert client.get(expired).status_code == 200
    assert client.get(forged).status_code == 200
    assert client.get("/uploads/logo.png", headers={"X-Agent-Key": "wrong"}).status_code == 200
    assert client.get("/uploads/logo.png").status_code == 200
    rows = _shadow_rows(enforce)
    assert [r["reason"] for r in rows] == [
        "signature-expired", "signature-invalid", "agent_key-invalid", "no-credential"]
    assert rows[-1]["grants"] == {"agent_key": "absent", "signature": "absent", "session": "absent"}
    assert rows[0]["grants"]["signature"] == "expired"
    # a granted request leaves no row
    assert client.get("/uploads/logo.png", headers={"X-Agent-Key": AGENT}).status_code == 200
    assert client.get(_links().signed_url("logo.png", "1h")).status_code == 200
    assert len(_shadow_rows(enforce)) == 4


def test_cli_sign_and_verify(enforce, capsys):
    links = _links()
    assert links.main(["sign", "logo.png", "--ttl", "7d", "--base-url", "https://example.test/"]) == 0
    url = capsys.readouterr().out.strip()
    assert url.startswith("https://example.test/uploads/logo.png?exp=")
    assert links.main(["verify", url]) == 0
    assert links.main(["verify", url.replace("logo.png", "dump.zip")]) == 1
    assert links.main(["sign", "logo.png", "--ttl", "31d"]) == 2


def test_cli_runs_as_module(enforce):
    repo = Path(__file__).resolve().parent.parent
    env = {**os.environ, "UPLOADS_SIGNING_KEY": KEY}
    out = subprocess.run([sys.executable, "-m", "services.upload_links", "sign", "logo.png", "--ttl", "12h"],
                         cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("/uploads/logo.png?exp=")
