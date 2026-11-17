"""
The no-follow conversions (PR #597 review rounds) must not break the features they
touch. Each test drives the REAL route with ordinary (non-symlink) files and checks
the user-visible result: albums, music, canvas pages + data, transcripts, the
workspace browser/editor, capture media, uploads, and the private-dir moves.
"""

import io
import json
import os
import stat
from pathlib import Path

import pytest
from flask import Flask


def _client(*bps, prefix=None):
    app = Flask(__name__)
    for bp in bps:
        app.register_blueprint(bp, url_prefix=prefix) if prefix else app.register_blueprint(bp)
    return app.test_client()


def test_albums_create_upload_list_and_serve(monkeypatch, tmp_path):
    import routes.albums as albums
    import routes.static_files as sf
    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(albums, "ALBUMS_DIR", up / "albums")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False, raising=False)
    c = _client(albums.albums_bp)
    album = c.post("/api/albums", json={"name": "Job 1"}).get_json()
    aid = album["id"]
    r = c.post(f"/api/albums/{aid}/photos",
               data={"file": (io.BytesIO(b"\xff\xd8\xff fake jpeg"), "x.jpg")},
               content_type="multipart/form-data")
    assert r.status_code == 201, r.get_data(as_text=True)
    photo = r.get_json()["photo"]
    got = c.get(photo["url"])
    assert got.status_code == 200 and got.get_data() == b"\xff\xd8\xff fake jpeg"
    listed = c.get("/api/albums").get_json()["albums"]
    assert [a["id"] for a in listed] == [aid]
    assert c.get(f"/api/albums/{aid}").get_json()["photos"][0]["id"] == photo["id"]


def test_music_library_generated_and_subdir_files_serve(monkeypatch, tmp_path):
    import routes.music as m
    md, gd = tmp_path / "music", tmp_path / "gen"
    (gd / "sfx").mkdir(parents=True)
    md.mkdir()
    monkeypatch.setattr(m, "MUSIC_DIR", md)
    monkeypatch.setattr(m, "GENERATED_MUSIC_DIR", gd)
    (md / "song.mp3").write_bytes(b"ID3 song")
    (gd / "gen.mp3").write_bytes(b"ID3 gen")
    (gd / "sfx" / "boom.mp3").write_bytes(b"ID3 boom")
    c = _client(m.music_bp)
    r = c.get("/music/song.mp3")
    assert r.status_code == 200 and r.get_data() == b"ID3 song" and r.mimetype == "audio/mpeg"
    r = c.get("/music/song.mp3?download=1")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert c.get("/generated_music/gen.mp3").get_data() == b"ID3 gen"
    assert c.get("/generated_music/sfx/boom.mp3").get_data() == b"ID3 boom"
    rng = c.get("/music/song.mp3", headers={"Range": "bytes=0-2"})
    assert rng.status_code == 206 and rng.get_data() == b"ID3"
    assert c.get("/music/nope.mp3").status_code == 404
    # metadata/order round-trip through the atomic writers
    m.save_music_metadata({"song.mp3": {"title": "Song"}})
    assert m.load_music_metadata() == {"song.mp3": {"title": "Song"}}
    m.save_playlist_order("library", ["song.mp3"])
    assert m.load_playlist_order("library") == ["song.mp3"]


@pytest.fixture
def pages(monkeypatch, tmp_path):
    import routes.canvas as canvas
    root = tmp_path / "canvas-pages"
    root.mkdir()
    monkeypatch.setattr(canvas, "CANVAS_PAGES_DIR", root)
    monkeypatch.setattr(canvas, "_CANVAS_DATA_DIR", root / "_data")
    monkeypatch.setattr(canvas, "CANVAS_REQUIRE_AUTH", False)
    return _client(canvas.canvas_bp), root


def test_canvas_pages_html_and_assets_serve(pages):
    c, root = pages
    (root / "hello.html").write_text("<html><body>hi there</body></html>")
    (root / "pic.png").write_bytes(b"\x89PNG data")
    r = c.get("/pages/hello.html")
    assert r.status_code == 200 and b"hi there" in r.get_data()
    r = c.get("/pages/pic.png")
    assert r.status_code == 200 and r.get_data() == b"\x89PNG data"
    assert c.get("/pages/hello").status_code == 301
    assert c.get("/pages/missing.html").status_code == 404


def test_canvas_page_write_is_atomic_and_0644(pages):
    c, root = pages
    import routes.canvas as canvas
    canvas._write_page_replacing(root / "made.html", "<html>v1</html>")
    canvas._write_page_replacing(root / "made.html", "<html>v2</html>")
    assert (root / "made.html").read_text() == "<html>v2</html>"
    assert stat.S_IMODE((root / "made.html").stat().st_mode) == 0o644
    assert not [p for p in root.iterdir() if p.name.endswith(".tmp")]


def test_canvas_data_write_read_and_backup(pages):
    c, root = pages
    assert c.post("/api/canvas/data/state.json", json={"a": 1}).status_code == 200
    assert c.get("/api/canvas/data/state.json").get_json() == {"a": 1}
    assert c.get("/canvas-data/state.json").get_data() == b'{\n  "a": 1\n}'
    c.post("/api/canvas/data/website-setup.json", json={"stitch": {"x": 1}})
    c.post("/api/canvas/data/website-setup.json", json={"stitch": {}, "name": "Co"})
    merged = c.get("/api/canvas/data/website-setup.json").get_json()
    assert merged == {"stitch": {"x": 1}, "name": "Co"}   # merge-preserve kept stitch
    baks = list((root / "_data" / ".backups").iterdir())
    assert len(baks) == 1 and json.loads(baks[0].read_text()) == {"stitch": {"x": 1}}


def test_transcript_save_list_and_get(monkeypatch, tmp_path):
    import routes.transcripts as t
    td = tmp_path / "transcripts"
    td.mkdir()
    monkeypatch.setattr(t, "TRANSCRIPTS_DIR", str(td))
    d = td / "2026-11-17"
    d.mkdir()
    (d / "a.txt").write_text("Title: Call\nWords: 2\n\n---\n\nhello world\n")
    c = _client(t.transcripts_bp)
    r = c.get("/api/transcripts/2026-11-17/a.txt")
    assert r.status_code == 200 and "hello world" in r.get_data(as_text=True)
    assert c.get("/api/transcripts/2026-11-17/none.txt").status_code == 404


def test_workspace_browser_raw_put_delete(monkeypatch, tmp_path):
    import routes.workspace as w
    ws = tmp_path / "workspace"
    (ws / "Agent").mkdir(parents=True)
    monkeypatch.setattr(w, "WORKSPACE_DIR", ws)
    monkeypatch.setitem(w._WRITABLE_MAP, "Agent", ws / "Agent")
    c = _client(w.workspace_bp)
    assert c.put("/api/workspace/file?path=Agent/notes/a.md", json={"content": "# hi"}).status_code == 200
    assert (ws / "Agent" / "notes" / "a.md").read_text() == "# hi"
    (ws / "Agent" / "p.png").write_bytes(b"\x89PNG")
    r = c.get("/api/workspace/raw?path=Agent/p.png")
    assert r.status_code == 200 and r.get_data() == b"\x89PNG"
    names = [e["name"] for e in c.get("/api/workspace/browse?path=Agent").get_json()["entries"]]
    assert "notes" in names and "p.png" in names
    r = c.delete("/api/workspace/file?path=Agent/notes/a.md")
    assert r.status_code == 200 and (ws / "Agent" / "notes" / "a.md.deleted").exists()


def test_capture_media_and_thumb_serve(monkeypatch, tmp_path):
    import routes.capture as cap
    root = tmp_path / "captures"
    (root / "albums" / "a1" / "media").mkdir(parents=True)
    (root / "albums" / "a1" / "thumbs").mkdir()
    (root / "albums" / "a1" / "media" / "p1.jpg").write_bytes(b"\xff\xd8 full")
    (root / "albums" / "a1" / "thumbs" / "p1.jpg").write_bytes(b"\xff\xd8 thumb")
    monkeypatch.setattr(cap, "CAPTURES_DIR", root)
    monkeypatch.setattr(cap, "ALBUMS_SUBDIR", root / "albums")
    c = _client(cap.capture_bp, prefix="/api/capture")
    assert c.get("/api/capture/albums/a1/media/p1").get_data() == b"\xff\xd8 full"
    assert c.get("/api/capture/albums/a1/thumb/p1").get_data() == b"\xff\xd8 thumb"
    assert c.get("/api/capture/albums/a1/media/nope").status_code == 404


def test_uploads_serve_regular_file_with_ranges(monkeypatch, tmp_path):
    import routes.static_files as sf
    up = tmp_path / "up"
    up.mkdir()
    monkeypatch.setattr(sf, "UPLOADS_DIR", up)
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False, raising=False)
    (up / "clip.mp4").write_bytes(b"0123456789")
    c = _client(sf.static_files_bp)
    r = c.get("/uploads/clip.mp4", headers={"Range": "bytes=2-4"})
    assert r.status_code == 206 and r.get_data() == b"234"


def test_private_dir_is_0700_and_tempdir_points_there(monkeypatch, tmp_path):
    import tempfile
    import services.private_dir as pd
    monkeypatch.setattr(pd, "PRIVATE_DIR", tmp_path / "priv")
    old = tempfile.tempdir
    try:
        d = pd.set_process_tempdir()
        assert stat.S_IMODE((tmp_path / "priv").stat().st_mode) == 0o700
        fd, name = tempfile.mkstemp()
        os.close(fd)
        assert Path(name).parent == d and stat.S_IMODE(os.stat(name).st_mode) == 0o600
    finally:
        tempfile.tempdir = old
        os.environ.pop("TMPDIR", None)


def test_vault_clients_fallback_is_never_tmp():
    import services.vault as v
    assert not str(v._CLIENTS_DIR).startswith("/tmp"), v._CLIENTS_DIR


def test_read_cap_refuses_a_huge_file(tmp_path):
    from services.safe_open import UnsafePath, read_beneath
    (tmp_path / "big.mp3").write_bytes(b"x" * 2048)
    with pytest.raises(UnsafePath):
        read_beneath(tmp_path, "big.mp3", max_bytes=1024)
    assert read_beneath(tmp_path, "big.mp3", max_bytes=4096) == b"x" * 2048
