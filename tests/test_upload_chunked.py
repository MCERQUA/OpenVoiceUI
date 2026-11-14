"""
/api/upload/chunk — sliced uploads for files over Cloudflare's ~100 MB edge cap (phone videos).

Measured 2026-11-14: a 131 MB .MOV POSTed whole to /api/upload got `413 ... cloudflare` at the
edge; slices well under the cap are the only way a phone video reaches the volume.
"""

import hashlib
import io
import os

import pytest


@pytest.fixture()
def up(tmp_path, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_INDEX", tmp_path / ".uploads-index.jsonl")
    return tmp_path


@pytest.fixture()
def client(up):
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.static_files import static_files_bp
    if "static_files" not in app.blueprints:
        app.register_blueprint(static_files_bp)
    return app.test_client()


UID = "a1b2c3d4e5f6a7b8c9d0"


def _send(client, data, offset, total, name="IMG_0001.MOV", uid=UID):
    return client.post(
        "/api/upload/chunk",
        data={
            "upload_id": uid, "name": name, "size": str(total), "offset": str(offset),
            "file": (io.BytesIO(data), name),
        },
        content_type="multipart/form-data",
    )


def _blob(n):
    return os.urandom(n)


def test_three_slices_assemble_byte_exact(client, up):
    payload = _blob(250_000)
    total = len(payload)
    parts = [payload[0:100_000], payload[100_000:200_000], payload[200_000:]]
    off = 0
    for i, p in enumerate(parts):
        r = _send(client, p, off, total)
        assert r.status_code == 200, r.get_json()
        off += len(p)
        body = r.get_json()
        if i < 2:
            assert body["received"] == off and "url" not in body
    assert body["file_size"] == total
    assert body["url"].startswith("/uploads/IMG_0001__") and body["url"].endswith(".mov")
    dest = up / body["filename"]
    assert dest.read_bytes() == payload
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
    # the partial was renamed, not copied: nothing left behind for this id
    assert not (up / ".chunks" / f"{UID}.partial").exists()
    # indexed like any other upload
    assert body["filename"] in (up / ".uploads-index.jsonl").read_text()


def test_retried_slice_does_not_duplicate_bytes(client, up):
    payload = _blob(30_000)
    a, b = payload[:10_000], payload[10_000:]
    assert _send(client, a, 0, len(payload)).status_code == 200
    # response for slice 0 "lost" — client resends it
    assert _send(client, a, 0, len(payload)).get_json()["received"] == 10_000
    r = _send(client, b, 10_000, len(payload))
    assert r.status_code == 200
    assert (up / r.get_json()["filename"]).read_bytes() == payload


def test_gap_answers_409_with_bytes_held(client, up):
    payload = _blob(30_000)
    assert _send(client, payload[:10_000], 0, len(payload)).status_code == 200
    r = _send(client, payload[20_000:], 20_000, len(payload))
    assert r.status_code == 409
    assert r.get_json()["received"] == 10_000


@pytest.mark.parametrize("uid", ["short", "../../etc/passwd0000", "a" * 65, "abc def ghi jkl mno"])
def test_bad_upload_id_refused(client, up, uid):
    assert _send(client, b"x" * 10, 0, 10, uid=uid).status_code == 400
    assert not any(p.name.endswith(".partial") for p in up.rglob("*"))


def test_blocked_extension_refused(client, up):
    assert _send(client, b"x" * 10, 0, 10, name="evil.sh").status_code == 415


def test_slice_past_declared_size_refused(client, up):
    assert _send(client, b"x" * 20, 0, 10).status_code == 400


def test_over_total_cap_refused(client, up, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "_MAX_CHUNKED_TOTAL_BYTES", 100)
    assert _send(client, b"x" * 10, 0, 1000).status_code == 413


def test_filename_traversal_is_flattened(client, up):
    r = _send(client, b"hello", 0, 5, name="../../../../tmp/x.mov")
    assert r.status_code == 200
    dest = up / r.get_json()["filename"]
    assert dest.parent == up and dest.read_bytes() == b"hello"


def test_chunk_route_is_public_like_upload():
    import app as app_mod
    src = open(app_mod.__file__, encoding="utf-8").read()
    assert "'/api/upload/chunk'," in src
