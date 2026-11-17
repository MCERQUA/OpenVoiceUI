"""
Regression tests for the security review of PR #597
(docs: sec-ovu-pr597-review-20261117; attack harnesses run.sh, run2.sh, run3.sh, run4.sh).

The reviewer's attacks are races: another party that can write a directory the
server reads/writes (the agent: uploads/, canvas-pages/, music dirs; the
terminal sandbox: the code workspace) swaps a checked file or directory for a
symlink between the server's check and its open. Each race test runs the swap
(renameat2 RENAME_EXCHANGE, exactly as the harnesses do) against the REAL route
handler for a bounded time and counts leaks; the unfixed code leaks within the
window, the fixed code must leak 0 times. Deterministic symlink cases that the
review's class also covers (album photos, sibling covers, audio-clip mail
attachments, the events file, story writes) are tested directly.

  run.sh   M1 read   -> test_workspace_read_never_follows_a_swapped_symlink
  run2.sh  M1 write  -> test_workspace_write_never_lands_outside_the_workspace
                        test_workspace_tree_lists_no_symlinks
  run4.sh  H1 cover  -> test_push_song_cover_race_never_ships_another_file
                        test_set_image_race_never_ships_another_file
                        test_serve_upload_race_never_serves_another_file
  run3.sh  L3 events -> test_events_* ; L1/L2 (helper fds, survivors) need the
                        setuid helper: proven by deploy/terminal-sandbox/verify-image.sh
"""

import base64
import ctypes
import importlib
import json
import os
import stat
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

SECRET = b"TOP-SECRET-MARKER-0123456789"
RACE_SECONDS = 2.5

_libc = ctypes.CDLL(None, use_errno=True)
_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


def _exchange(a, b):
    _libc.renameat2(_AT_FDCWD, os.fsencode(a), _AT_FDCWD, os.fsencode(b), _RENAME_EXCHANGE)


class Swapper:
    """Keeps exchanging path a (real) and path b (symlink) until stopped."""

    def __init__(self, a, b):
        self.a, self.b = str(a), str(b)
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            _exchange(self.a, self.b)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join(timeout=5)
        # leave a (real) at its own name for any later assertion
        if os.path.islink(self.a):
            _exchange(self.a, self.b)


def _race(fn, seconds=RACE_SECONDS):
    """Call fn() repeatedly; return (leaks, attempts). fn returns True on a leak."""
    leaks = attempts = 0
    end = time.time() + seconds
    while time.time() < end:
        attempts += 1
        if fn():
            leaks += 1
            if leaks >= 3:  # enough to prove it; keep the unfixed run short
                break
    return leaks, attempts


@pytest.fixture
def secret_dir(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    for name in ("environ", "f.png", "cover.png", "zz.py"):
        (d / name).write_bytes(SECRET)
    return d


# ── M1: workspace file API over a sandbox-writable tree ──────────────────────


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    import routes.canvas as canvas
    root = tmp_path / "code-workspace"
    root.mkdir()
    monkeypatch.setattr(canvas, "CODE_WORKSPACE_DIR", root)
    app = Flask(__name__)
    app.register_blueprint(canvas.canvas_bp)
    return app.test_client(), root


def test_workspace_read_never_follows_a_swapped_symlink(workspace, secret_dir):
    client, root = workspace
    (root / "a").mkdir()
    (root / "a" / "environ").write_text("decoy\n")
    (root / "b").symlink_to(secret_dir)

    def attempt():
        r = client.get("/api/workspace/file?path=a/environ")
        return r.status_code == 200 and SECRET.decode() in r.get_json().get("content", "")

    with Swapper(root / "a", root / "b"):
        leaks, n = _race(attempt)
    assert leaks == 0, f"workspace read followed a swapped symlink {leaks}x in {n} reads"
    # and a plain symlink path is refused outright
    assert client.get("/api/workspace/file?path=b/environ").status_code == 404


def test_workspace_write_never_lands_outside_the_workspace(workspace, tmp_path):
    client, root = workspace
    victim = tmp_path / "app-routes"
    victim.mkdir()
    (root / "c").mkdir()
    (root / "d").symlink_to(victim)

    def attempt():
        client.post("/api/workspace/file", json={"path": "c/zz_planted.py", "content": "# probe\n"})
        return (victim / "zz_planted.py").exists()

    with Swapper(root / "c", root / "d"):
        leaks, n = _race(attempt)
    assert not (victim / "zz_planted.py").exists(), "an editor save landed outside the workspace"
    r = client.post("/api/workspace/file", json={"path": "d/zz2.py", "content": "x"})
    assert r.status_code == 400 and not (victim / "zz2.py").exists()
    r = client.post("/api/workspace/mkdir", json={"path": "d/newdir"})
    assert r.status_code == 400 and not (victim / "newdir").exists()


def test_workspace_tree_lists_no_symlinks(workspace, secret_dir):
    client, root = workspace
    (root / "real.txt").write_text("hello")
    (root / "link-to-secret").symlink_to(secret_dir / "environ")
    (root / "dirlink").symlink_to(secret_dir)
    entries = client.get("/api/workspace/tree").get_json()["entries"]
    paths = {e["path"] for e in entries}
    assert "real.txt" in paths
    assert not any(p.startswith(("link-to-secret", "dirlink")) for p in paths), paths


def test_workspace_editor_files_are_shared_with_the_sandbox_group(workspace):
    client, root = workspace
    r = client.post("/api/workspace/file", json={"path": "x/y/z.txt", "content": "hi"})
    assert r.status_code == 200
    assert stat.S_IMODE((root / "x" / "y" / "z.txt").stat().st_mode) == 0o664
    assert stat.S_IMODE((root / "x").stat().st_mode) == 0o2770
    assert client.get("/api/workspace/file?path=x/y/z.txt").get_json()["content"] == "hi"


# ── H1: AI-Radio cover / set-image, and the /uploads/ file server ────────────


@pytest.fixture
def airadio(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRADIO_URL", "http://ai-radio:3000")
    monkeypatch.setenv("AIRADIO_AGENT_KEY", "fake-shared-secret")
    monkeypatch.setenv("AIRADIO_JAMBOT_USER", "test-dev")
    import routes.airadio_bridge as bridge
    importlib.reload(bridge)
    uploads = tmp_path / "uploads"
    music = tmp_path / "music"
    uploads.mkdir()
    music.mkdir()
    monkeypatch.setattr(bridge, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(bridge, "MUSIC_DIR", music)
    monkeypatch.setattr(bridge, "_URL_PREFIX_MAP", {"/uploads/": uploads})
    monkeypatch.setattr(bridge, "_IMAGE_WHITELIST_DIRS", [uploads.resolve()])
    monkeypatch.setattr(bridge, "load_music_metadata", lambda: {})
    # a big song widens the check->read window exactly as the review's harness did
    (music / "race-song.mp3").write_bytes(b"\0" * (4 * 1024 * 1024))
    app = Flask(__name__)
    app.register_blueprint(bridge.airadio_bp)
    return app.test_client(), bridge, uploads, music


class _Capture:
    """Stands in for requests.request: records every byte the bridge sends (at call time,
    so a file object handed to `files=` is read while it is still open)."""

    def __init__(self):
        self.sent = b""

    def __call__(self, *args, **kw):
        meta = (kw.get("data") or {}).get("meta")
        if meta:
            cov = json.loads(meta).get("cover", "")
            if cov.startswith("data:"):
                self.sent += base64.b64decode(cov.split(",", 1)[1])
        for v in (kw.get("files") or {}).values():
            body = v[1]
            self.sent += body if isinstance(body, bytes) else body.read()
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"ok": True, "data": {"songId": "s1", "url": "u"}}
        r.text = "{}"
        return r


def test_push_song_cover_race_never_ships_another_file(airadio, secret_dir):
    client, bridge, uploads, music = airadio
    (uploads / "cover.png").write_bytes(b"\x89PNG decoy")
    (uploads / "evil").symlink_to(secret_dir / "environ")

    def attempt():
        cap = _Capture()
        with patch.object(bridge.http_requests, "request", side_effect=cap):
            client.post("/api/airadio/push-song", json={
                "filename": "race-song.mp3", "playlist": "library", "cover_path": "/uploads/cover.png"})
        return SECRET in cap.sent

    with Swapper(uploads / "cover.png", uploads / "evil"):
        leaks, n = _race(attempt)
    assert leaks == 0, f"push-song shipped a file outside uploads {leaks}x in {n} calls"


def test_set_image_race_never_ships_another_file(airadio, secret_dir, monkeypatch):
    client, bridge, uploads, music = airadio
    (uploads / "avatar.png").write_bytes(b"\x89PNG decoy")
    (uploads / "evil").symlink_to(secret_dir / "environ")
    # set-image logs a line between resolving the path and sending the file; a
    # slow log write widens that window the way a loaded box does
    monkeypatch.setattr(bridge, "print", lambda *a, **k: time.sleep(0.002), raising=False)

    def attempt():
        cap = _Capture()
        with patch.object(bridge.http_requests, "request", side_effect=cap):
            client.post("/api/airadio/set-image", json={"target": "avatar", "path": "/uploads/avatar.png"})
        return SECRET in cap.sent

    with Swapper(uploads / "avatar.png", uploads / "evil"):
        leaks, n = _race(attempt)
    assert leaks == 0, f"set-image shipped a file outside uploads {leaks}x in {n} calls"


def test_sibling_cover_symlink_is_not_attached(airadio, secret_dir):
    client, bridge, uploads, music = airadio
    (music / "race-song.png").symlink_to(secret_dir / "environ")
    cap = _Capture()
    with patch.object(bridge.http_requests, "request", side_effect=cap):
        r = client.post("/api/airadio/push-song", json={"filename": "race-song.mp3", "playlist": "library"})
    assert r.status_code == 200
    assert SECRET not in cap.sent, "a symlinked sibling cover was attached"


@pytest.fixture
def uploads_server(monkeypatch, tmp_path):
    import routes.static_files as sf
    uploads = tmp_path / "srv-uploads"
    uploads.mkdir()
    monkeypatch.setattr(sf, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False, raising=False)
    app = Flask(__name__)
    app.register_blueprint(sf.static_files_bp)
    return app.test_client(), uploads


def test_serve_upload_race_never_serves_another_file(uploads_server, secret_dir):
    client, uploads = uploads_server
    (uploads / "sub").mkdir()
    (uploads / "sub" / "f.png").write_bytes(b"\x89PNG decoy")
    (uploads / "evil").symlink_to(secret_dir)

    def attempt():
        r = client.get("/uploads/sub/f.png")
        return SECRET in r.get_data()

    with Swapper(uploads / "sub", uploads / "evil"):
        leaks, n = _race(attempt)
    assert leaks == 0, f"/uploads/ served a file outside uploads {leaks}x in {n} GETs"
    ok = client.get("/uploads/sub/f.png")
    assert ok.status_code == 200 and ok.get_data() == b"\x89PNG decoy"


def test_album_photo_symlink_is_not_served(monkeypatch, tmp_path, secret_dir):
    import routes.albums as albums
    import routes.static_files as sf
    adir = tmp_path / "albums"
    (adir / "x1").mkdir(parents=True)
    (adir / "x1" / "a.png").symlink_to(secret_dir / "environ")
    (adir / "x1" / "ok.png").write_bytes(b"\x89PNG real")
    (adir / "x2").symlink_to(secret_dir)
    monkeypatch.setattr(albums, "ALBUMS_DIR", adir)
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False, raising=False)
    app = Flask(__name__)
    app.register_blueprint(albums.albums_bp)
    c = app.test_client()
    for url in ("/uploads/albums/x1/a.png", "/uploads/albums/x2/f.png"):
        r = c.get(url)
        assert SECRET not in r.get_data(), f"{url} served a symlink target"
    r = c.get("/uploads/albums/x1/ok.png")
    assert r.status_code == 200 and r.get_data() == b"\x89PNG real"


@pytest.mark.parametrize("planted", ["absolute", "symlink"])
def test_audio_clip_email_never_attaches_another_file(monkeypatch, tmp_path, secret_dir, planted):
    import routes.audio_clips as ac
    cdir = tmp_path / "audio-clips"
    cdir.mkdir()
    if planted == "absolute":
        fname = str(secret_dir / "environ")
    else:
        fname = "c1.mp3"
        (cdir / fname).symlink_to(secret_dir / "environ")
    (cdir / "manifest.json").write_text(json.dumps({"clips": [{"id": "c1", "filename": fname, "label": "x"}]}))
    monkeypatch.setattr(ac, "AUDIO_CLIPS_DIR", cdir)
    monkeypatch.setattr(ac, "AUDIO_CLIPS_MANIFEST", cdir / "manifest.json")
    monkeypatch.setenv("AGENTMAIL_API_KEY", "k")
    monkeypatch.setenv("AGENTMAIL_INBOX", "inbox")
    app = Flask(__name__)
    app.register_blueprint(ac.audio_clips_bp)
    sent = MagicMock()
    sent.return_value.json.return_value = {"message_id": "m"}
    with patch.object(ac.requests, "post", sent):
        app.test_client().post("/api/audio-clips/email",
                               json={"clip_id": "c1", "to": ["a@example.com"], "subject": "s"})
    payload = json.dumps([c.kwargs.get("json") for c in sent.call_args_list])
    assert base64.b64encode(SECRET).decode() not in payload, "a file outside audio-clips was emailed"


# ── L3: the events file in /tmp ──────────────────────────────────────────────


def test_events_file_created_private_and_planted_files_refused(tmp_path):
    from services import events_file as ef
    p = tmp_path / "events.jsonl"
    assert ef.ensure_private(p) is True
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert ef.append_event({"type": "conversation", "message": "hi"}, p)
    with ef.open_for_read(p) as f:
        assert "hi" in f.read()
    # a planted symlink is refused for create, append and read
    victim = tmp_path / "victim"
    victim.write_text("")
    link = tmp_path / "linked.jsonl"
    link.symlink_to(victim)
    assert ef.ensure_private(link) is False
    assert ef.append_event({"type": "conversation", "message": "x"}, link) is False
    assert ef.open_for_read(link) is None
    assert victim.read_text() == ""


def test_notify_brain_never_appends_through_a_planted_symlink(monkeypatch, tmp_path):
    import routes.canvas as canvas
    victim = tmp_path / "victim.txt"
    victim.write_text("")
    link = tmp_path / "events.jsonl"
    link.symlink_to(victim)
    monkeypatch.setattr(canvas, "BRAIN_EVENTS_PATH", link)
    canvas._notify_brain("conversation", message="owner said something private")
    assert victim.read_text() == "", "event appended through a planted symlink"
    fresh = tmp_path / "fresh.jsonl"
    monkeypatch.setattr(canvas, "BRAIN_EVENTS_PATH", fresh)
    canvas._notify_brain("conversation", message="m")
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600, "events file readable by other users"


# ── L4: story writes after a slow generation step ────────────────────────────


def test_story_dir_swapped_mid_generation_cannot_redirect_writes(monkeypatch, tmp_path):
    import routes.story as story
    pages = tmp_path / "canvas-pages"
    pages.mkdir()
    victim = tmp_path / "db"
    victim.mkdir()
    os.chmod(victim, 0o700)
    monkeypatch.setattr(story, "CANVAS_PAGES_DIR", pages)
    monkeypatch.setattr(story, "generate_scene_json", lambda *a, **k: {"title": "t"})

    def slow_image(prompt, out):
        # the agent swaps the checked story dir for a symlink while generation runs
        sdir = pages / "stories" / "s1"
        os.rename(sdir, pages / "stories" / "s1-moved")
        sdir.symlink_to(victim)
        out.write_bytes(b"jpeg")
        return True

    monkeypatch.setattr(story, "gen_image", slow_image)
    monkeypatch.setattr(story, "gen_suno_sound", lambda *a, **k: True)
    monkeypatch.setattr(story, "gen_tts_line", lambda *a, **k: True)
    app = Flask(__name__)
    app.register_blueprint(story.story_bp)
    app.test_client().post("/api/story/generate-scene", json={"story_id": "s1", "choice_text": "go"})
    assert not any(victim.iterdir()), "a story asset was written through the swapped symlink"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o700
    moved = pages / "stories" / "s1-moved"
    assert stat.S_IMODE(moved.stat().st_mode) & 0o022 == 0, "story dir is group/world-writable"
    assert stat.S_IMODE((pages / "stories").stat().st_mode) & 0o022 == 0
