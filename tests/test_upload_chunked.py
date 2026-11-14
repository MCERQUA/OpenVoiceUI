"""
/api/upload/chunk — sliced uploads for files over Cloudflare's ~100 MB edge cap (phone videos).

Measured 2026-11-14: a 131 MB .MOV POSTed whole to /api/upload got `413 ... cloudflare` at the
edge; slices well under the cap are the only way a phone video reaches the volume.

The PR #586 review found that the first version let anyone with curl fill the shared uploads
disk. The tests named test_probe_* are the reviewer's probes, kept as written where they still
apply and asserting the fixed behavior:
  P1  the unfinished-uploads cap reserves DECLARED sizes at start (open-many-then-fill bypass)
  P2  the size is frozen at start (a later slice cannot grow it)
  P3  a resent last slice returns the same file, one file lands
  P4/N1  a NUL byte in the name is handled at start, never a 500 + stuck partial
  P5  uploads/.chunks/ is never served or listed
  P6  with Clerk on, an anonymous client cannot start an upload
"""

import hashlib
import io
import json
import os
import shutil
from collections import namedtuple

import pytest

DU = namedtuple("DU", "total used free")
BIG = 1 << 50


@pytest.fixture()
def up(tmp_path, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_INDEX", tmp_path / ".uploads-index.jsonl")
    # the test machine's real free space must not decide these tests; floor tests set their own
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(BIG, 0, BIG))
    for k in ("CLERK_PUBLISHABLE_KEY", "VITE_CLERK_PUBLISHABLE_KEY", "JAMBOT_TENANT", "TENANT_NAME"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _app():
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.static_files import static_files_bp
    if "static_files" not in app.blueprints:
        app.register_blueprint(static_files_bp)
    return app


@pytest.fixture()
def client(up):
    return _app().test_client()


@pytest.fixture()
def clerk_client(up, monkeypatch):
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_probe")
    monkeypatch.setenv("AGENT_API_KEY", "agent-key-for-tests-0001")
    import services.auth as auth
    monkeypatch.setattr(auth, "verify_clerk_token", lambda t: "user_1" if t == "good-session" else None)
    return _app().test_client()


def _start(client, total, name="IMG_0001.MOV", headers=None, **extra):
    body = {"name": name, "size": total, **extra}
    return client.post("/api/upload/chunk/start", data=json.dumps(body),
                       content_type="application/json", headers=headers or {})


def _uid(client, total, name="IMG_0001.MOV", **kw):
    r = _start(client, total, name, **kw)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["upload_id"]


def _send(client, data, offset, uid, size=None, name="IMG_0001.MOV", headers=None):
    form = {"upload_id": uid, "offset": str(offset), "file": (io.BytesIO(data), name)}
    if size is not None:
        form["size"] = str(size)
    return client.post("/api/upload/chunk", data=form, content_type="multipart/form-data",
                       headers=headers or {})


def _partials(up):
    d = up / ".chunks"
    return sorted(p.name for p in d.glob("*.partial")) if d.exists() else []


def _finals(up, prefix="IMG_0001__"):
    return [p for p in up.iterdir() if p.is_file() and p.name.startswith(prefix)]


# ----------------------------------------------------------------------------- assembly

def test_three_slices_assemble_byte_exact(client, up):
    payload = os.urandom(250_000)
    total = len(payload)
    uid = _uid(client, total)
    parts = [payload[0:100_000], payload[100_000:200_000], payload[200_000:]]
    off = 0
    for i, p in enumerate(parts):
        r = _send(client, p, off, uid, size=total)
        assert r.status_code == 200, r.get_json()
        off += len(p)
        body = r.get_json()
        if i < 2:
            assert body["received"] == off and "url" not in body
    assert body["file_size"] == total
    assert body["url"].startswith("/uploads/IMG_0001__") and body["url"].endswith(".mov")
    dest = up / body["filename"]
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert _partials(up) == []          # renamed, not copied
    assert body["filename"] in (up / ".uploads-index.jsonl").read_text()


def test_retried_slice_does_not_duplicate_bytes(client, up):
    payload = os.urandom(30_000)
    uid = _uid(client, len(payload))
    a, b = payload[:10_000], payload[10_000:]
    assert _send(client, a, 0, uid).status_code == 200
    assert _send(client, a, 0, uid).get_json()["received"] == 10_000   # response "lost", resent
    r = _send(client, b, 10_000, uid)
    assert r.status_code == 200
    assert (up / r.get_json()["filename"]).read_bytes() == payload


def test_gap_answers_409_with_bytes_held(client, up):
    payload = os.urandom(30_000)
    uid = _uid(client, len(payload))
    assert _send(client, payload[:10_000], 0, uid).status_code == 200
    r = _send(client, payload[20_000:], 20_000, uid)
    assert r.status_code == 409 and r.get_json()["received"] == 10_000


def test_filename_traversal_is_flattened(client, up):
    uid = _uid(client, 5, name="../../../../tmp/x.mov")
    r = _send(client, b"hello", 0, uid)
    assert r.status_code == 200
    dest = up / r.get_json()["filename"]
    assert dest.parent == up and dest.read_bytes() == b"hello"


# ----------------------------------------------------------------------------- the signed id

@pytest.mark.parametrize("uid", [
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",          # a client-made id (the first version's format)
    "../../etc/passwd0000", "a" * 65, "short",
    "c1.0123456789abcdef01234567.10.9999999999." + "0" * 63,   # truncated signature
])
def test_unissued_upload_id_refused_and_creates_nothing(client, up, uid):
    r = _send(client, b"x" * 10, 0, uid)
    assert r.status_code == 400 and r.get_json()["code"] == "start_required"
    assert _partials(up) == []


def test_forged_or_altered_id_refused(client, up):
    uid = _uid(client, 20)
    head, sig = uid.rsplit(".", 1)
    forged = head + "." + ("0" if sig[0] != "0" else "1") + sig[1:]
    assert _send(client, b"x" * 10, 0, forged).status_code == 403
    _c, rid, size, exp, sig = uid.split(".")
    bigger = ".".join(["c1", rid, "2000", exp, sig])            # claim a larger size
    assert _send(client, b"x" * 10, 0, bigger).status_code == 403
    assert (up / ".chunks" / f"{rid}.partial").stat().st_size == 0


def test_id_from_another_tenant_refused(client, up, monkeypatch):
    monkeypatch.setenv("JAMBOT_TENANT", "tenant-a")
    uid = _uid(client, 20)
    monkeypatch.setenv("JAMBOT_TENANT", "tenant-b")
    assert _send(client, b"x" * 10, 0, uid).status_code == 403


def test_expired_id_refused(client, up, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_CHUNK_UPLOAD_TTL_S", -5)
    uid = _uid(client, 20)
    assert _send(client, b"x" * 10, 0, uid).status_code == 410


# ----------------------------------------------------------------------------- start validation

def test_blocked_extension_refused_at_start(client, up):
    assert _start(client, 10, name="evil.sh").status_code == 415
    assert _start(client, 10, name="evil.s\x00h").status_code == 415   # NUL cannot smuggle .sh
    assert _partials(up) == []


def test_over_total_cap_refused_at_start(client, up, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_MAX_CHUNKED_TOTAL_BYTES", 100)
    assert _start(client, 1000).status_code == 413
    assert _partials(up) == []


def test_slice_past_declared_size_refused(client, up):
    uid = _uid(client, 10)
    assert _send(client, b"x" * 20, 0, uid).status_code == 400
    assert _partials(up) and (up / ".chunks" / _partials(up)[0]).stat().st_size == 0


def test_probe_P4_N1_nul_in_extension_handled_at_start(client, up):
    """Was: ValueError at os.replace after every byte, 500, full-size partial stuck forever."""
    r = _start(client, 4, name="v.mo\x00v")
    assert r.status_code == 200
    r = _send(client, b"abcd", 0, r.get_json()["upload_id"], name="v")
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["filename"].endswith(".mov") and "\x00" not in j["filename"] + j["original_name"]
    assert (up / j["filename"]).read_bytes() == b"abcd"
    assert _partials(up) == []


def test_probe_P4_traversal_and_odd_names(client, up):
    for bad in ["../../../../etc/passwdxx", "a" * 15, "a/b" + "c" * 20, "a.b" + "c" * 20, "x" * 65]:
        assert _send(client, b"z", 0, bad).status_code == 400, bad
    uid = _uid(client, 4, name="../../../../tmp/evil.mov")
    assert os.path.dirname(_send(client, b"z" * 4, 0, uid).get_json()["path"]) == str(up)
    uid = _uid(client, 4, name="..\\..\\evil.mov")
    assert "\\" not in _send(client, b"z" * 4, 0, uid).get_json()["filename"]
    assert _start(client, 4, name="x.SH").status_code == 415


# ----------------------------------------------------------------------------- caps

def test_probe_P1_inflight_cap_reserves_declared_sizes(client, up, monkeypatch):
    """Was: six uploads opened at 10 bytes each and then filled held 4794 bytes past a 1000 cap."""
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_MAX_CHUNKED_TOTAL_BYTES", 800)
    monkeypatch.setattr(sf, "_MAX_CHUNKED_INFLIGHT_BYTES", 1000)
    statuses, uids = [], []
    for _ in range(6):
        r = _start(client, 800)
        statuses.append(r.status_code)
        if r.status_code == 200:
            uids.append(r.get_json()["upload_id"])
    assert statuses == [200, 507, 507, 507, 507, 507]
    for u in uids:
        assert _send(client, b"x" * 10, 0, u).status_code == 200
    for u in uids:
        _send(client, b"y" * 789, 10, u)
    held = sum((up / ".chunks" / p).stat().st_size for p in _partials(up))
    assert held <= 1000


def test_cap_frees_when_an_upload_finishes(client, up, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_MAX_CHUNKED_INFLIGHT_BYTES", 25)
    u = _uid(client, 20)
    assert _start(client, 20).status_code == 507               # 20 reserved + 20 > 25
    assert _send(client, b"a" * 20, 0, u).get_json()["file_size"] == 20
    assert _start(client, 20).status_code == 200               # finished: reservation released


def test_expired_upload_counts_bytes_held_not_declared(client, up, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_MAX_CHUNKED_INFLIGHT_BYTES", 100)
    monkeypatch.setattr(sf, "_CHUNK_UPLOAD_TTL_S", 3600)
    u = _uid(client, 90)
    assert _send(client, b"a" * 30, 0, u).status_code == 200
    assert _start(client, 50).status_code == 507               # 90 reserved + 50 > 100
    rid = u.split(".")[1]
    meta_p = up / ".chunks" / f"{rid}.meta.json"
    meta = json.loads(meta_p.read_text()); meta["exp"] = 1; meta_p.write_text(json.dumps(meta))
    assert _start(client, 50).status_code == 200               # 30 held + 50 <= 100
    assert _start(client, 30).status_code == 507               # 30 + 50 + 30 > 100


def test_probe_P2_declared_size_is_frozen_at_start(client, up, monkeypatch):
    """Was: open with size=50 to pass the cap, then declare size=800 on the next slice -> 200."""
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_MAX_CHUNKED_TOTAL_BYTES", 800)
    monkeypatch.setattr(sf, "_MAX_CHUNKED_INFLIGHT_BYTES", 100)
    u = _uid(client, 50)
    assert _send(client, b"a" * 5, 0, u, size=50).status_code == 200
    r = _send(client, b"b" * 700, 5, u, size=800)
    assert r.status_code == 400 and r.get_json()["size"] == 50
    assert _send(client, b"b" * 700, 5, u).status_code == 400   # without the field: runs past 50
    rid = u.split(".")[1]
    assert (up / ".chunks" / f"{rid}.partial").stat().st_size == 5


def test_free_space_floor_at_start(client, up, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_CHUNK_FREE_FLOOR_BYTES", 1000)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(BIG, 0, 1500))
    assert _start(client, 400).status_code == 200              # 1500 - 400 >= 1000
    assert _start(client, 400).status_code == 507              # 1500 - 400 pending - 400 < 1000
    assert len(_partials(up)) == 1


def test_free_space_floor_checked_on_every_slice(client, up, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_CHUNK_FREE_FLOOR_BYTES", 1000)
    free = {"v": 10_000}
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(BIG, 0, free["v"]))
    u = _uid(client, 300)
    assert _send(client, b"a" * 100, 0, u).status_code == 200
    free["v"] = 1150                    # another tenant filled the shared disk meanwhile
    r = _send(client, b"b" * 100, 100, u)
    assert r.status_code == 507 and r.get_json()["received"] == 100
    rid = u.split(".")[1]
    assert (up / ".chunks" / f"{rid}.partial").stat().st_size == 100   # nothing written
    free["v"] = 10_000
    assert _send(client, b"b" * 100, 100, u).status_code == 200      # resumes when space returns


# ----------------------------------------------------------------------------- completion

def test_probe_P3_resent_last_slice_returns_same_file(client, up):
    payload = os.urandom(20_000)
    u = _uid(client, len(payload))
    assert _send(client, payload[:10_000], 0, u).status_code == 200
    first = _send(client, payload[10_000:], 10_000, u).get_json()
    again = _send(client, payload[10_000:], 10_000, u)
    assert again.status_code == 200 and again.get_json()["filename"] == first["filename"]
    finals = _finals(up)
    assert len(finals) == 1 and finals[0].read_bytes() == payload
    # any slice for a finished id answers with the result and never creates a new partial
    assert _send(client, payload[:10_000], 0, u).get_json()["filename"] == first["filename"]
    assert _partials(up) == []


def test_result_is_written_before_the_partial_is_renamed(client, up, monkeypatch):
    """The race: a slice that opened a+b after the rename but before the done-marker made an
    empty partial, got 409 received:0 and re-sent the whole file. The result now lands first."""
    import routes.static_files as sf
    seen = []
    real = os.replace

    def spy(src, dst):
        if str(src).endswith(".partial"):
            rid = os.path.basename(str(src))[:-len(".partial")]
            seen.append((up / ".chunks" / f"{rid}.done.json").exists())
        return real(src, dst)
    monkeypatch.setattr(sf.os, "replace", spy)
    u = _uid(client, 10)
    assert _send(client, b"a" * 10, 0, u).status_code == 200
    assert seen == [True]


def test_slices_never_create_a_partial(client, up):
    u = _uid(client, 20)
    rid = u.split(".")[1]
    os.replace(up / ".chunks" / f"{rid}.partial", up / "moved-away.partial")
    r = _send(client, b"a" * 10, 0, u)
    assert r.status_code == 410 and r.get_json()["code"] == "start_required"
    assert _partials(up) == []


def test_finish_interrupted_after_result_is_completed_by_next_slice(client, up, monkeypatch):
    import routes.static_files as sf
    real = os.replace
    calls = {"n": 0}

    def crash_on_rename(src, dst):
        if str(src).endswith(".partial") and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("simulated crash between result and rename")
        return real(src, dst)
    monkeypatch.setattr(sf.os, "replace", crash_on_rename)
    payload = os.urandom(20)
    u = _uid(client, 20)
    with pytest.raises(OSError):
        _send(client, payload, 0, u)
    rid = u.split(".")[1]
    stored = json.loads((up / ".chunks" / f"{rid}.done.json").read_text())
    r = _send(client, payload[:10], 0, u)          # any slice: completes, bytes untouched
    assert r.status_code == 200 and r.get_json()["filename"] == stored["filename"]
    finals = _finals(up)
    assert len(finals) == 1 and finals[0].read_bytes() == payload
    lines = (up / ".uploads-index.jsonl").read_text().splitlines()
    assert [json.loads(x)["filename"] for x in lines] == [stored["filename"]]   # indexed once


# ----------------------------------------------------------------------------- auth (Clerk on)

def test_route_gate_entries():
    import app as app_mod
    src = open(app_mod.__file__, encoding="utf-8").read()
    assert "'/api/upload/chunk'," in src and "'/api/upload/chunk/start'," in src


def test_probe_P6_clerk_on_anonymous_cannot_start(clerk_client, up):
    r = _start(clerk_client, 10)
    assert r.status_code == 401 and r.get_json()["code"] == "auth_required"
    # and the first version's anonymous one-shot slice no longer writes anything
    assert _send(clerk_client, b"a" * 10, 0, "probeanon0000000001", size=10).status_code == 400
    r2 = _start(clerk_client, 10, headers={"Origin": "https://evil.example"})
    assert r2.status_code == 403 and r2.get_json()["code"] == "csrf_blocked"
    assert _partials(up) == []


@pytest.mark.parametrize("how", ["bearer", "cookie", "expired-bearer-good-cookie", "agent-key", "grant"])
def test_clerk_on_credentials_that_start(clerk_client, up, how):
    import routes.static_files as sf
    headers, extra = {}, {}
    if how == "bearer":
        headers["Authorization"] = "Bearer good-session"
    elif how == "cookie":
        clerk_client.set_cookie("__session", "good-session")
    elif how == "expired-bearer-good-cookie":
        headers["Authorization"] = "Bearer expired-60s-token"
        clerk_client.set_cookie("__session", "good-session")
    elif how == "agent-key":
        headers["X-Agent-Key"] = "agent-key-for-tests-0001"
    else:
        from flask import Flask
        with Flask("t").test_request_context("/", base_url="http://localhost"):
            extra["grant"] = sf.mint_upload_grant()
    r = _start(clerk_client, 10, headers=headers, **extra)
    assert r.status_code == 200, r.get_json()
    # slices need no session at all: the signed id is the credential
    clerk_client.delete_cookie("__session")
    r = _send(clerk_client, b"a" * 10, 0, r.get_json()["upload_id"])
    assert r.status_code == 200 and r.get_json()["file_size"] == 10


@pytest.mark.parametrize("grant", [
    "g1.9999999999.0123456789abcdef." + "ab" * 32,      # forged signature
    "g1.100.0123456789abcdef." + "ab" * 32,             # long expired
    "", "not-a-grant",
])
def test_clerk_on_bad_grant_refused(clerk_client, up, grant):
    assert _start(clerk_client, 10, grant=grant).status_code == 401
    assert _partials(up) == []


def test_clerk_on_expired_real_grant_refused(clerk_client, up):
    import routes.static_files as sf
    from flask import Flask
    with Flask("t").test_request_context("/", base_url="http://localhost"):
        g = sf.mint_upload_grant(ttl=-10)
    assert _start(clerk_client, 10, grant=g).status_code == 401


# ----------------------------------------------------------------------------- page grant

@pytest.fixture()
def pages_client(up, tmp_path_factory, monkeypatch):
    import routes.canvas as canvas
    pdir = tmp_path_factory.mktemp("pages")
    (pdir / "bulk-image-uploader.html").write_text(
        "<html><head></head><body><script>fetch('/api/upload/chunk/start')</script></body></html>")
    (pdir / "plain.html").write_text("<html><head></head><body>no uploads</body></html>")
    (pdir / "pub-uploader.html").write_text(
        "<html><head></head><body><script>'/api/upload/chunk'</script></body></html>")
    monkeypatch.setattr(canvas, "CANVAS_PAGES_DIR", pdir)
    monkeypatch.setattr(canvas, "CANVAS_REQUIRE_AUTH", True)
    monkeypatch.setattr(canvas, "load_canvas_manifest",
                        lambda: {"pages": {"pub-uploader": {"is_public": True}}})
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_probe")
    import services.auth as auth
    monkeypatch.setattr(auth, "verify_clerk_token", lambda t: "user_1" if t == "good-session" else None)
    app = _app()
    if "canvas" not in app.blueprints:
        app.register_blueprint(canvas.canvas_bp)
    return app.test_client()


def _grant_in(html):
    import re
    m = re.search(r'window\._canvasUploadGrant="([^"]+)"', html)
    return m and m.group(1)


def test_signed_in_page_serve_embeds_a_working_grant(pages_client, up):
    """The texted-link case: no parent frame, no pushed token, only the session the page serve
    itself verified. The grant in the page starts the upload; the slices need nothing more."""
    pages_client.set_cookie("__session", "good-session")
    html = pages_client.get("/pages/bulk-image-uploader.html").get_data(as_text=True)
    grant = _grant_in(html)
    assert grant and grant.startswith("g1.")
    pages_client.delete_cookie("__session")
    r = _start(pages_client, 10, grant=grant)
    assert r.status_code == 200, r.get_json()
    assert _send(pages_client, b"a" * 10, 0, r.get_json()["upload_id"]).get_json()["file_size"] == 10


def test_no_grant_for_anonymous_or_non_uploading_pages(pages_client, up):
    r = pages_client.get("/pages/bulk-image-uploader.html", headers={"Accept": "application/json"})
    assert r.status_code == 401                                      # private page, anonymous
    assert _grant_in(pages_client.get("/pages/pub-uploader.html").get_data(as_text=True)) is None
    pages_client.set_cookie("__session", "good-session")
    assert _grant_in(pages_client.get("/pages/plain.html").get_data(as_text=True)) is None
    assert _grant_in(pages_client.get("/pages/pub-uploader.html").get_data(as_text=True))


# ----------------------------------------------------------------------------- hidden dir

def test_probe_P5_hidden_dir_not_served_or_listed(client, up):
    u = _uid(client, 20)
    _send(client, b"a" * 10, 0, u)
    rid = u.split(".")[1]
    for p in [f"/uploads/.chunks/{rid}.partial", f"/uploads/.chunks/{rid}.meta.json",
              "/uploads/.chunks/.grant.key", "/uploads/.chunks/",
              f"/uploads/%2Echunks/{rid}.partial", f"/uploads/./.chunks/{rid}.partial",
              f"/uploads/x/../.chunks/{rid}.partial"]:
        assert client.get(p).status_code in (400, 404), p
    body = client.get("/api/uploads").get_data(as_text=True)
    assert ".chunks" not in body and rid not in body
