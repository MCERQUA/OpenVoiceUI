"""
SEC-062: the upload URL OVU hands the AGENT (`url_full`) is a signed link under enforce.

The chat composer writes `url_full` into the message (src/app.js `[IMAGE SAVED: ... (public URL:
<url_full>)]`). The agent passes that URL to Z.AI's analyze_image, and Z.AI's servers fetch it
anonymously (Apache-HttpClient, no session, no X-Agent-Key). Measured 2026-11-19 on hrsf: under
UPLOADS_AUTH_MODE=enforce a bare /uploads/ URL is 401 for them. So under enforce url_full is
signed for that one file (services/upload_links.py, instance key, DEFAULT_TTL_SECONDS); in shadow
it is the bare URL, byte for byte, as before. The UI's own display URL ('url') is never signed.
"""
import io
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

KEY = "k" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture()
def up(tmp_path, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_INDEX", tmp_path / ".uploads-index.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_SHADOW_LOG", tmp_path / ".uploads-auth-shadow.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", True)
    monkeypatch.setattr(sf, "UPLOADS_AUTH_MODE", "enforce")
    monkeypatch.setenv("UPLOADS_SIGNING_KEY", KEY)
    monkeypatch.setenv("AGENT_API_KEY", "agent-key-for-tests")
    for k in ("CLERK_PUBLISHABLE_KEY", "VITE_CLERK_PUBLISHABLE_KEY", "JAMBOT_TENANT", "TENANT_NAME"):
        monkeypatch.delenv(k, raising=False)
    return sf


@pytest.fixture()
def client(up):
    from app import create_app
    a, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in a.blueprints:
        a.register_blueprint(up.static_files_bp)
    return a.test_client()


def _upload(client, name="shot.png", data=PNG):
    r = client.post("/api/upload", data={"file": (io.BytesIO(data), name)},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _signed_parts(url_full):
    """(path, exp, sig) of a signed url_full; fails when url_full is bare."""
    parts = urlsplit(url_full)
    q = parse_qs(parts.query)
    assert q.get("exp") and q.get("sig"), f"url_full is not a signed link: {url_full}"
    return parts.path, q["exp"][0], q["sig"][0]


def _anon_get(client, path, exp=None, sig=None):
    """What Z.AI's fetcher sends: no session, no agent key, not a browser navigation."""
    url = path if exp is None else f"{path}?{urlencode({'exp': exp, 'sig': sig})}"
    return client.get(url, headers={"User-Agent": "Apache-HttpClient/4.5.14 (Java/17.0.10)"})


def _set_session(monkeypatch, user):
    import services.auth as auth
    monkeypatch.setattr(auth, "get_token_from_request", lambda: "tok" if user else None)
    monkeypatch.setattr(auth, "verify_clerk_token", lambda t: user)


# ---------------------------------------------------------------- enforce

def test_url_full_in_upload_response_verifies(client):
    from services import upload_links
    res = _upload(client)
    path, exp, sig = _signed_parts(res["url_full"])
    assert res["url_full"].startswith("https://localhost/uploads/")
    assert path == f"/uploads/{res['filename']}"
    assert upload_links.verify(path, exp, sig) == upload_links.OK


def test_signed_url_full_is_fetchable_anonymously_under_enforce(client):
    res = _upload(client)
    r = _anon_get(client, *_signed_parts(res["url_full"]))
    assert r.status_code == 200
    assert r.data == PNG


def test_bare_url_is_401_under_enforce(client):
    res = _upload(client)
    path, _, _ = _signed_parts(res["url_full"])
    assert _anon_get(client, path).status_code == 401


def test_tampered_signature_is_401(client):
    res = _upload(client)
    path, exp, sig = _signed_parts(res["url_full"])
    bad = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert _anon_get(client, path, exp, bad).status_code == 401
    assert _anon_get(client, path, str(int(exp) + 1), sig).status_code == 401   # exp is signed too


def test_signature_for_file_a_is_401_on_file_b(client):
    a = _upload(client, "a.png")
    b = _upload(client, "b.png", PNG + b"b")
    _, exp, sig = _signed_parts(a["url_full"])
    b_path, _, _ = _signed_parts(b["url_full"])
    assert _anon_get(client, b_path, exp, sig).status_code == 401
    assert _anon_get(client, *_signed_parts(b["url_full"])).status_code == 200


def test_signed_link_lives_for_the_default_ttl(client):
    import time
    from services import upload_links
    res = _upload(client)
    _, exp, _ = _signed_parts(res["url_full"])
    assert abs(int(exp) - (int(time.time()) + upload_links.DEFAULT_TTL_SECONDS)) <= 5
    assert upload_links.DEFAULT_TTL_SECONDS == 7 * 24 * 3600


def test_owner_display_url_stays_bare_and_works_with_a_session(client, monkeypatch):
    res = _upload(client)
    assert res["url"] == f"/uploads/{res['filename']}"          # display URL never signed
    _set_session(monkeypatch, "user_owner")
    assert client.get(res["url"]).status_code == 200


def test_text_file_url_full_is_signed_too(client):
    res = _upload(client, "notes.txt", b"hello")
    assert res["type"] == "file"
    assert _anon_get(client, *_signed_parts(res["url_full"])).status_code == 200


def test_index_keeps_the_bare_url(client, up):
    res = _upload(client)
    rows = [json.loads(l) for l in up.UPLOADS_INDEX.read_text().splitlines()]
    assert rows[-1]["url"] == f"https://localhost/uploads/{res['filename']}"


def test_chunked_upload_url_full_is_signed_and_marker_stays_bare(client, up, monkeypatch):
    import shutil
    from collections import namedtuple
    DU = namedtuple("DU", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(1 << 50, 0, 1 << 50))
    data = b"v" * 1000
    r = client.post("/api/upload/chunk/start", data=json.dumps({"name": "clip.mov", "size": len(data)}),
                    content_type="application/json")
    assert r.status_code == 200, r.get_json()
    uid = r.get_json()["upload_id"]
    form = {"upload_id": uid, "offset": "0", "file": (io.BytesIO(data), "clip.mov")}
    res = client.post("/api/upload/chunk", data=form, content_type="multipart/form-data").get_json()
    assert _anon_get(client, *_signed_parts(res["url_full"])).status_code == 200
    markers = list((up.UPLOADS_DIR / ".chunks").glob("*.done.json"))
    assert len(markers) == 1
    assert json.loads(markers[0].read_text())["url_full"] == f"https://localhost/uploads/{res['filename']}"


def test_enforce_without_a_signing_key_fails_open_to_bare(client, monkeypatch):
    monkeypatch.delenv("UPLOADS_SIGNING_KEY", raising=False)
    res = _upload(client)                                         # the upload itself never fails
    assert res["url_full"] == f"https://localhost/uploads/{res['filename']}"


# ---------------------------------------------------------------- shadow: nothing changes

@pytest.mark.parametrize("mode", ["shadow", "off"])
def test_shadow_and_off_url_full_is_unchanged_bare(client, up, monkeypatch, mode):
    monkeypatch.setattr(up, "UPLOADS_AUTH_MODE", mode)
    res = _upload(client)
    assert res["url_full"] == f"https://localhost/uploads/{res['filename']}"
    assert res["url"] == f"/uploads/{res['filename']}"
    assert _anon_get(client, f"/uploads/{res['filename']}").status_code == 200
