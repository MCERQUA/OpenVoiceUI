"""Review 2 of PR #597: the reviewer's 16 attack tests, kept verbatim as regression tests.

Written by the non-author reviewer against the real route handlers. Every test asserts the SAFE
outcome; at 59a9121 15 fail (the attacks work) and the control passes."""

import json, os, threading
from pathlib import Path
import pytest
from flask import Flask
from tests.test_pr597_review_regressions import SECRET, Swapper, _race, secret_dir  # noqa: F401


def _app(*bps):
    app = Flask(__name__)
    for bp in bps:
        app.register_blueprint(bp)
    return app.test_client()


# ---- canvas-pages (agent-writable) /pages/<x> -------------------------------
@pytest.fixture
def pages(monkeypatch, tmp_path):
    import routes.canvas as canvas
    root = tmp_path / "canvas-pages"; root.mkdir()
    monkeypatch.setattr(canvas, "CANVAS_PAGES_DIR", root)
    monkeypatch.setattr(canvas, "_CANVAS_DATA_DIR", root / "_data")
    monkeypatch.setattr(canvas, "CANVAS_REQUIRE_AUTH", False)
    return _app(canvas.canvas_bp), root


@pytest.mark.parametrize("name", ["p.png", "p.html"])
def test_pages_swap_serves_outside_file(pages, secret_dir, name):
    client, root = pages
    (root / name).write_bytes(b"decoy")
    (root / ("evil" + name)).symlink_to(secret_dir / "environ")
    def attempt():
        return SECRET in client.get("/pages/" + name).get_data()
    with Swapper(root / name, root / ("evil" + name)):
        leaks, n = _race(attempt)
    assert leaks == 0, f"/pages/{name} served outside file {leaks}x in {n}"


def test_canvas_data_static_swap(pages, secret_dir):
    client, root = pages
    d = root / "_data"; d.mkdir()
    (d / "s.mp3").write_bytes(b"decoy")
    (d / "evil.mp3").symlink_to(secret_dir / "environ")
    with Swapper(d / "s.mp3", d / "evil.mp3"):
        leaks, n = _race(lambda: SECRET in client.get("/canvas-data/s.mp3").get_data())
    assert leaks == 0, f"/canvas-data served outside file {leaks}x in {n}"


def test_canvas_data_json_swap(pages, secret_dir):
    client, root = pages
    d = root / "_data"; d.mkdir()
    (d / "s.json").write_bytes(b"{}")
    (d / "evil.json").symlink_to(secret_dir / "environ")
    with Swapper(d / "s.json", d / "evil.json"):
        leaks, n = _race(lambda: SECRET in client.get("/api/canvas/data/s.json").get_data())
    assert leaks == 0, f"/api/canvas/data served outside file {leaks}x in {n}"


def test_canvas_data_write_backups_symlink_writes_outside(pages, tmp_path):
    client, root = pages
    victim = tmp_path / "app-routes"; victim.mkdir()
    d = root / "_data"; d.mkdir()
    (d / "website-setup.json").write_text('{"planted": "x"}')
    (d / ".backups").symlink_to(victim)           # deterministic, no race
    client.post("/api/canvas/data/website-setup.json", json={"a": 1})
    landed = list(victim.iterdir())
    assert not landed, f"canvas data write landed outside canvas-pages: {[p.name for p in landed]}"


def test_page_write_tmp_symlink_overwrites_outside(pages, tmp_path):
    """_write_page_replacing writes a predictable .<name>.<pid>.<tid>.tmp in the
    agent-writable dir (an agent watching the dir learns pid/tid from any write)."""
    client, root = pages
    victim = tmp_path / "server.py"; victim.write_text("ORIGINAL\n"); victim.chmod(0o644)
    tmp = root / f".x.html.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.symlink_to(victim)
    client.post("/api/canvas/pages", json={"filename": "x.html", "html": "PWNED_BY_AGENT"})
    body = victim.read_text()
    mode = oct(victim.stat().st_mode & 0o777)
    assert "PWNED_BY_AGENT" not in body and mode == "0o644", f"outside file rewritten={'PWNED_BY_AGENT' in body} mode={mode}"


# ---- music / generated_music (agent-writable, PUBLIC prefixes) -------------
@pytest.fixture
def music(monkeypatch, tmp_path):
    import routes.music as m
    md = tmp_path / "music"; md.mkdir(); gd = tmp_path / "gen"; (gd / "sfx").mkdir(parents=True)
    monkeypatch.setattr(m, "MUSIC_DIR", md)
    monkeypatch.setattr(m, "GENERATED_MUSIC_DIR", gd)
    return _app(m.music_bp), md, gd


@pytest.mark.parametrize("which", ["music", "generated_music", "generated_music/sfx"])
def test_music_swap_serves_outside_file(music, secret_dir, which):
    client, md, gd = music
    d = {"music": md, "generated_music": gd, "generated_music/sfx": gd / "sfx"}[which]
    # a real file as target so stat() size is non-zero (e.g. platform-credentials.json)
    (d / "t.mp3").write_bytes(b"decoy-decoy-decoy-decoy-decoy-decoy")
    (d / "evil.mp3").symlink_to(secret_dir / "f.png")
    with Swapper(d / "t.mp3", d / "evil.mp3"):
        leaks, n = _race(lambda: SECRET in client.get(f"/{which}/t.mp3").get_data())
    assert leaks == 0, f"/{which}/ served outside file {leaks}x in {n}"


# ---- transcripts (agent-writable via openclaw workspace/transcripts) --------
def test_transcript_swap_serves_outside_file(monkeypatch, tmp_path, secret_dir):
    import routes.transcripts as t
    td = tmp_path / "transcripts"; (td / "2026-11-17").mkdir(parents=True)
    monkeypatch.setattr(t, "TRANSCRIPTS_DIR", str(td))
    client = _app(t.transcripts_bp)
    d = td / "2026-11-17"
    (d / "a.json").write_text("{}")
    (d / "evil.json").symlink_to(secret_dir / "environ")
    with Swapper(d / "a.json", d / "evil.json"):
        leaks, n = _race(lambda: SECRET in client.get("/api/transcripts/2026-11-17/a.json").get_data())
    assert leaks == 0, f"transcripts served outside file {leaks}x in {n}"


# ---- capture albums (captures mounted into openclaw on some tenants) --------
def test_capture_media_static_symlink(monkeypatch, tmp_path, secret_dir):
    import routes.capture as c
    cap = tmp_path / "captures"; media = cap / "albums" / "a1" / "media"; media.mkdir(parents=True)
    (cap / "albums" / "a1" / "thumbs").mkdir()
    (media / "p1.jpg").symlink_to(secret_dir / "f.png")
    (cap / "albums" / "a1" / "thumbs" / "p1.jpg").symlink_to(secret_dir / "f.png")
    monkeypatch.setattr(c, "CAPTURES_DIR", cap)
    monkeypatch.setattr(c, "ALBUMS_SUBDIR", cap / "albums")
    app = Flask(__name__); app.register_blueprint(c.capture_bp, url_prefix="/api/capture")
    client = app.test_client()
    hits = [u for u in ("/api/capture/albums/a1/media/p1", "/api/capture/albums/a1/thumb/p1")
            if SECRET in client.get(u).get_data()]
    assert not hits, f"capture served symlink targets: {hits}"


# ---- CONTROL: same harness against the route the PR converted -> must be 0 --
def test_control_uploads_converted_route_holds(monkeypatch, tmp_path, secret_dir):
    import routes.static_files as sf
    up = tmp_path / "up"; up.mkdir()
    monkeypatch.setattr(sf, "UPLOADS_DIR", up)
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False, raising=False)
    client = _app(sf.static_files_bp)
    (up / "t.png").write_bytes(b"decoy")
    (up / "evil.png").symlink_to(secret_dir / "f.png")
    with Swapper(up / "t.png", up / "evil.png"):
        leaks, n = _race(lambda: SECRET in client.get("/uploads/t.png").get_data())
    assert leaks == 0 and n > 50, f"control leaked {leaks}x in {n}"


# ---- sandbox -> server via /tmp/vault-clients (no /mnt/clients in tenant containers) --
def test_vault_env_sync_into_sandbox_owned_tmp(monkeypatch, tmp_path):
    """services/vault.py: _CLIENTS_DIR falls back to /tmp/vault-clients when /mnt/clients
    is absent (true in live tenant containers). /tmp is shared with the sandbox, which can
    pre-create <user>/compose/ and plant the .vault-tmp name as a symlink. sync_credential()
    then calls _update_env_file(<that>/.openclaw.env, {KEY: secret}) when an admin saves a key."""
    import services.vault as v
    fake_tmp_clients = tmp_path / "vault-clients"            # stands in for /tmp/vault-clients
    comp = fake_tmp_clients / "tenant" / "compose"; comp.mkdir(parents=True)
    victim = tmp_path / "server.py"; victim.write_text("ORIGINAL\n")
    (comp / ".openclaw.env").write_text("import os  # sandbox-controlled line\n")
    (comp / ".openclaw.env.vault-tmp").symlink_to(victim)
    monkeypatch.setattr(v, "_CLIENTS_DIR", fake_tmp_clients)
    v._update_env_file(v._CLIENTS_DIR / "tenant" / "compose" / ".openclaw.env",
                       {"OPENAI_API_KEY": "sk-REVIEW2-FAKE"})
    body = victim.read_text()
    assert "sandbox-controlled" not in body and "sk-REVIEW2-FAKE" not in body, \
        "server wrote sandbox-chosen content + the credential through a sandbox symlink"


# ---- safe_open: the ROOT itself is opened following symlinks; ALBUMS_DIR's root
# component 'albums' lives inside agent-writable uploads/ ---------------------
def test_album_root_symlink_is_not_followed(monkeypatch, tmp_path, secret_dir):
    import routes.albums as albums
    import routes.static_files as sf
    up = tmp_path / "uploads"; up.mkdir()
    runtime = tmp_path / "runtime"; (runtime / "db").mkdir(parents=True)
    (runtime / "db" / "usage.db").write_bytes(SECRET)      # stands in for /app/runtime/db/usage.db
    (up / "albums").symlink_to(runtime)                     # agent: rm -r uploads/albums; ln -s /app/runtime albums
    monkeypatch.setattr(albums, "ALBUMS_DIR", up / "albums")
    monkeypatch.setattr(sf, "UPLOADS_DIR", up)
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", False, raising=False)
    client = _app(albums.albums_bp)
    r = client.get("/uploads/albums/db/usage.db")
    assert SECRET not in r.get_data(), f"album route served a file through a symlinked albums/ root (status {r.status_code})"


# ---- routes/workspace.py over WORKSPACE_DIR (Agent/ = the agent's own workspace,
# Uploads/Canvas/Music = agent-writable). Admin-only prefix; live routes are raw,
# browse, PUT/DELETE file (GET file/tree/mkdir are shadowed by canvas_bp). ------
@pytest.fixture
def agent_ws(monkeypatch, tmp_path):
    import routes.workspace as w
    ws = tmp_path / "workspace"; (ws / "Agent").mkdir(parents=True)
    monkeypatch.setattr(w, "WORKSPACE_DIR", ws)
    monkeypatch.setitem(w._WRITABLE_MAP, "Agent", ws / "Agent")
    return _app(w.workspace_bp), ws / "Agent"


def test_workspace_raw_swap_serves_outside_file(agent_ws, secret_dir):
    client, ag = agent_ws
    (ag / "p.png").write_bytes(b"decoy-decoy-decoy")
    (ag / "evil.png").symlink_to(secret_dir / "f.png")
    with Swapper(ag / "p.png", ag / "evil.png"):
        leaks, n = _race(lambda: SECRET in client.get("/api/workspace/raw?path=Agent/p.png").get_data())
    assert leaks == 0, f"/api/workspace/raw served outside file {leaks}x in {n}"


def test_workspace_put_swap_writes_outside(agent_ws, tmp_path):
    client, ag = agent_ws
    victim = tmp_path / "app-routes"; victim.mkdir()
    (ag / "c").mkdir(); (ag / "d").symlink_to(victim)
    def attempt():
        client.put("/api/workspace/file?path=Agent/c/zz.py", json={"content": "# planted\n"})
        return (victim / "zz.py").exists()
    with Swapper(ag / "c", ag / "d"):
        leaks, n = _race(attempt)
    assert not (victim / "zz.py").exists(), "PUT /api/workspace/file landed outside the workspace"
