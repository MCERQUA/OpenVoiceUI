"""
Path-confinement fixes from the db-dir side audit (CCA gate-4 re-check #6):

  * POST /api/story/generate-scene: `story_id` named a directory that was then
    chmod 0777'ed. `../../db`, an absolute path, or a planted `stories` symlink
    aimed that chmod at any directory the server can reach (e.g. the db dir).
  * POST /api/airadio/push-song: `cover_path` was read from ANY absolute path
    and shipped, base64'd, to AI-Radio — a read-any-file primitive for the
    agent key.
  * DELETE /api/plugins/<id>: `..` made uninstall rmtree the parent of the
    plugin dir (/app).

Each test is written to fail on the unfixed code and pass on the fix.
"""

import base64
import importlib
import json
import os
import shutil
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


# ── story_id ──────────────────────────────────────────────────────────────


@pytest.fixture
def story_app(monkeypatch, tmp_path):
    import routes.story as story
    pages = tmp_path / "canvas-pages"
    pages.mkdir()
    victim = tmp_path / "db"
    victim.mkdir()
    os.chmod(victim, 0o700)
    monkeypatch.setattr(story, "CANVAS_PAGES_DIR", pages)
    monkeypatch.setattr(story, "generate_scene_json",
                        lambda *a, **k: {"title": "t", "sfx": [{"id": "../../db/x", "prompt": "p"}]})
    monkeypatch.setattr(story, "gen_image", lambda *a, **k: True)
    monkeypatch.setattr(story, "gen_suno_sound", lambda *a, **k: True)
    monkeypatch.setattr(story, "gen_tts_line", lambda *a, **k: True)
    app = Flask(__name__)
    app.register_blueprint(story.story_bp)
    return app.test_client(), pages, victim


@pytest.mark.parametrize("bad_id", ["../../db", "../db", "__ABS__", "a/b", "x\n"])
def test_story_id_cannot_escape_or_chmod_outside_stories(story_app, bad_id):
    client, pages, victim = story_app
    sid = str(victim) if bad_id == "__ABS__" else bad_id
    resp = client.post("/api/story/generate-scene", json={"story_id": sid, "choice_text": "go"})
    assert resp.status_code == 400
    assert oct(victim.stat().st_mode & 0o777) == oct(0o700), "victim dir was chmod'ed"


def test_story_symlinked_stories_dir_is_refused(story_app):
    client, pages, victim = story_app
    (pages / "stories").symlink_to(victim)
    resp = client.post("/api/story/generate-scene", json={"story_id": "fine-id", "choice_text": "go"})
    assert resp.status_code == 400
    assert oct(victim.stat().st_mode & 0o777) == oct(0o700)
    assert not (victim / "fine-id").exists()


def test_story_valid_id_still_works_and_llm_sfx_id_cannot_be_a_path(story_app):
    client, pages, victim = story_app
    resp = client.post("/api/story/generate-scene",
                       json={"story_id": "rex-parts-king", "choice_text": "go"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert (pages / "stories" / "rex-parts-king").is_dir()
    # the LLM-supplied sfx id '../../db/x' was replaced, never used as a path
    scene = resp.get_json()["scene"]
    assert all(".." not in s["file"] for s in scene["sounds"])
    assert not any(victim.iterdir())


# ── airadio cover_path ────────────────────────────────────────────────────


@pytest.fixture
def airadio(monkeypatch):
    monkeypatch.setenv("AIRADIO_URL", "http://ai-radio:3000")
    monkeypatch.setenv("AIRADIO_AGENT_KEY", "fake-shared-secret")
    monkeypatch.setenv("AIRADIO_JAMBOT_USER", "test-dev")
    import routes.airadio_bridge as bridge
    importlib.reload(bridge)
    app = Flask(__name__)
    app.register_blueprint(bridge.airadio_bp)
    song = bridge.MUSIC_DIR / "cover-audit-song.mp3"
    bridge.MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"\x00\x00fake-mp3")
    yield app.test_client(), bridge
    song.unlink(missing_ok=True)


def _ok_resp():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"ok": True, "data": {"songId": "s1", "url": "u"}}
    r.text = "{}"
    return r


def _push(client, bridge, cover_path):
    with patch.object(bridge.http_requests, "request", return_value=_ok_resp()) as req:
        resp = client.post("/api/airadio/push-song",
                           json={"filename": "cover-audit-song.mp3", "playlist": "library",
                                 "cover_path": cover_path})
    meta = None
    if req.call_count:
        meta = json.loads(req.call_args.kwargs["data"]["meta"])
    return resp, meta


def test_push_song_cover_path_cannot_read_files_outside_uploads(airadio, tmp_path):
    client, bridge = airadio
    secret = tmp_path / "staff-surface.key"
    secret.write_bytes(b"TOP-SECRET-KEY-BYTES")
    resp, meta = _push(client, bridge, str(secret))
    leaked = base64.b64encode(b"TOP-SECRET-KEY-BYTES").decode()
    assert not (meta and leaked in meta.get("cover", "")), "cover_path shipped a file outside uploads/"
    assert resp.status_code == 400


@pytest.mark.parametrize("bad", ["/etc/passwd", "/uploads/../../etc/passwd", "../server.py"])
def test_push_song_cover_path_rejects_traversal(airadio, bad):
    client, bridge = airadio
    resp, meta = _push(client, bridge, bad)
    assert resp.status_code == 400
    assert meta is None


def test_push_song_cover_in_uploads_still_attached(airadio):
    client, bridge = airadio
    bridge.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    img = bridge.UPLOADS_DIR / "cover-audit.png"
    img.write_bytes(b"\x89PNG-fake")
    try:
        for given in ("/uploads/cover-audit.png", "cover-audit.png", str(img.resolve())):
            resp, meta = _push(client, bridge, given)
            assert resp.status_code == 200, (given, resp.get_data(as_text=True))
            assert meta["cover"].startswith("data:image/png;base64,"), given
    finally:
        img.unlink(missing_ok=True)


# ── plugin uninstall ──────────────────────────────────────────────────────


@pytest.fixture
def plugin_tree(monkeypatch, tmp_path):
    import services.plugins as plugins
    app_dir = tmp_path / "app"
    pdir = app_dir / "plugins"
    pdir.mkdir(parents=True)
    (app_dir / "server.py").write_text("keep me")
    (pdir / "real-plugin").mkdir()
    (pdir / "real-plugin" / "plugin.json").write_text("{}")
    monkeypatch.setattr(plugins, "PLUGIN_DIR", pdir)
    return plugins, app_dir, pdir


@pytest.mark.parametrize("bad", ["..", "."])
def test_uninstall_dotdot_does_not_remove_the_parent(plugin_tree, bad):
    plugins, app_dir, pdir = plugin_tree
    try:
        result = plugins.uninstall_plugin(bad)
    except OSError:
        result = "raised"
    assert (app_dir / "server.py").exists(), "uninstall('..') removed the app dir"
    assert pdir.is_dir()
    assert result is False


def test_uninstall_route_dotdot_is_refused(plugin_tree):
    plugins, app_dir, pdir = plugin_tree
    import routes.plugins as rp
    importlib.reload(rp)
    app = Flask(__name__)
    app.register_blueprint(rp.plugins_bp)
    resp = app.test_client().delete("/api/plugins/..")
    assert (app_dir / "server.py").exists()
    assert resp.status_code in (404, 405)


def test_uninstall_real_plugin_still_works(plugin_tree):
    plugins, app_dir, pdir = plugin_tree
    with patch.object(plugins, "_run_lifecycle_hook", return_value={"ok": True}):
        assert plugins.uninstall_plugin("real-plugin") is True
    assert not (pdir / "real-plugin").exists()
    assert (app_dir / "server.py").exists()
