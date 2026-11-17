"""
Systemic guard (review 2 of PR #597): every file I/O whose path can derive from an
agent-writable root goes through services/safe_open.py, or is listed below with a reason.

Two reviews in a row found routes that check a path and then open it BY NAME inside a
directory the tenant agent (or the terminal sandbox) can write: a symlink swapped in
between the check and the open turns the route into "read/write any file the server
can". Fixing the list each time missed the class. This test enumerates the class from
the code itself (tests/agent_io_scan.py: an AST taint walk of routes/, services/,
server.py and app.py) and fails on ANY sink — send_file, send_from_directory, open,
os.open, Path.open/read_*/write_*/chmod/rename/replace, os.replace/rename/chmod,
shutil copy/move/rmtree, FileStorage/Image .save(path) — that is reached by a path
derived from uploads, canvas-pages (+ _data, stories, styles, singalong), music,
generated_music, transcripts, captures, the workspace mounts, the code workspace or
/tmp, unless it is listed in ALLOWLIST with a one-line reason.

A new route that opens an agent-writable path by name fails this test until it uses
services.safe_open (Anchored / open_beneath / send_file_beneath / atomic_write_beneath,
all no-follow and beneath a root anchored at the mount point) or is justified here.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_io_scan import Scanner, spawn_sites, unclassified  # noqa: E402

# 'file::function::sink' -> why it is safe without services/safe_open.
ALLOWLIST = {
    'routes/chatgpt_import.py::get_imported_conversation::.read_text':
        'IMPORT_DIR = WORKSPACE_DIR/memory/chatgpt-import: the server\'s own container dir '
        '(no mount, not reachable by the agent or the sandbox: /app is 0750)',
    'routes/chatgpt_import.py::import_status::.read_text': 'same IMPORT_DIR (server-only)',
    'routes/chatgpt_import.py::list_imported_conversations::.read_text': 'same IMPORT_DIR (server-only)',
    'services/chatgpt_parser.py::_process_conversation::.write_text':
        'writes into IMPORT_DIR (server-only); only the zip it parses came from uploads, '
        'saved there under a random name by a no-follow open',
    'services/chatgpt_parser.py::parse_chatgpt_export::.write_text': 'same IMPORT_DIR (server-only)',
    'routes/custom_faces.py::_extract_face_meta::open':
        'reads FACES_DIR/*.html (server-only dir, not mounted into openclaw); promoted pages '
        'are copied in from canvas-pages by a no-follow read first',
    'routes/custom_faces.py::_write_face_file::.write_bytes':
        'writes FACES_DIR/<name> (server-only); the bytes come from a no-follow read of the '
        'canvas page, only the file NAME derives from it',
    'server.py::_open_trusted_catalog::open':
        'only ever given _GAME_CATALOG_PATH (/app/runtime/game-catalog.json, a host bind '
        'mount); the uploads fallback in the caller goes through Anchored',
    'services/plugins.py::load_plugins::shutil.copy2':
        'copies a plugin-shipped profile into /app/runtime/profiles (the server\'s own mount, '
        'not agent-writable); src is in the plugin dir',
}

# Roots that are NOT mount points: a safe_open call anchored at one of these would follow
# a symlink planted at its last component (review 2, H-B). Anchor at the mount (e.g.
# ALBUMS_DIR.parent) and walk the rest.
DERIVED_ROOTS = {
    'ALBUMS_DIR', 'AUDIO_CLIPS_DIR', '_CANVAS_DATA_DIR', 'STYLES_DIR', 'CACHE_DIR',
    'GENERATED_SOUNDS_DIR', 'ALBUMS_SUBDIR', '_BUILD_LOG_DIR', '_ZCODE_SESSIONS_DIR',
    'ORDERS_DIR', 'CUSTOM_DIR', 'IMPORT_DIR',
}
SAFE_FUNCS = {'Anchored', 'open_beneath', 'read_beneath', 'write_beneath', 'atomic_write_beneath',
              'append_beneath', 'send_file_beneath', 'open_dir_beneath', 'lstat_beneath',
              'scandir_beneath', 'chmod_beneath', 'rename_beneath'}


def test_every_agent_writable_io_is_no_follow_or_justified():
    missing, groups = unclassified(ROOT, ALLOWLIST)
    assert groups, 'scanner found no I/O at all — it is broken, not the code clean'
    assert not missing, (
        f'{len(missing)} file-I/O call group(s) reach a path derived from an agent-writable root '
        'by NAME (symlink-followable). Use services/safe_open.py (Anchored / open_beneath / '
        'send_file_beneath / atomic_write_beneath) or add a reasoned ALLOWLIST entry:\n  '
        + '\n  '.join(f'{k} lines {v}' for k, v in sorted(missing.items())))


def test_allowlist_has_no_stale_entries():
    _, groups = unclassified(ROOT, {})
    stale = sorted(set(ALLOWLIST) - set(groups))
    assert not stale, f'allowlisted but no longer found (remove them): {stale}'


def test_safe_open_is_anchored_at_mount_points_not_derived_dirs():
    bad = []
    for item in ('routes', 'services', 'server.py', 'app.py'):
        p = ROOT / item
        for f in ([p] if p.is_file() else sorted(p.rglob('*.py'))):
            tree = ast.parse(f.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ''
                if name.lstrip('_') not in SAFE_FUNCS:
                    continue
                root = node.args[0]
                if isinstance(root, ast.Name) and root.id in DERIVED_ROOTS:
                    bad.append(f'{f.relative_to(ROOT)}:{node.lineno} {name}({root.id}, ...)')
    assert not bad, 'safe_open anchored at a derived (agent-replaceable) dir:\n  ' + '\n  '.join(bad)


def test_scanner_flags_a_known_bad_route():
    """Negative control: the scanner must flag the exact pre-fix /music/ pattern."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / 'routes').mkdir()
        (Path(d) / 'routes' / 'bad.py').write_text(
            'from flask import send_file\n'
            'from services.paths import MUSIC_DIR\n'
            'def _safe_path(base, name):\n'
            '    p = (base / name).resolve()\n'
            '    return p\n'
            'def serve(filename):\n'
            '    p = _safe_path(MUSIC_DIR, filename)\n'
            '    return send_file(p)\n'
            'def fine(filename):\n'
            '    from services.safe_open import Anchored\n'
            '    return Anchored(MUSIC_DIR, filename).send()\n')
        found = {(rel, q, s) for rel, q, s, _ in Scanner(d).findings()}
        assert ('routes/bad.py', 'serve', 'send_file') in found
        assert not any(q == 'fine' for _, q, _ in found)


# ── process spawns (review 2 addendum: the T class) ───────────────────────────
# Every process the server can start is classified here. Rule (host ruling): a process
# an agent-reachable caller can trigger either (a) runs a FIXED argv with no
# caller-controlled shell and no secrets in its environment, or (b) runs through the
# sandbox helper as the sandbox uid. Anything only an admin can trigger says so.
# Reachability: "agent" = X-Agent-Key or a Clerk session (a page the agent wrote is
# handed the viewer's JWT); "public" = no credential; "admin" = app.py
# _ADMIN_ONLY_PREFIXES (the agent key never passes).
SPAWN_ALLOWLIST = {
    'server.py::terminal_websocket::pty.fork':
        '(b) agent: /ws/terminal. The child only execve()s the plan from services/terminal_sandbox '
        '(the setuid helper -> sandbox uid, no env, no args); hosted tenants refuse without the helper',
    'services/terminal_sandbox.py::exec_terminal_child::os.execve':
        '(b) the child of the pty.fork above: execs the sandbox helper (or, self-hosted without a '
        'helper only, a same-user shell with a scrubbed env)',
    'services/terminal_sandbox.py::sandbox_uid::subprocess.run':
        '(b) the helper\'s --uid mode: fixed argv, empty env, prints a number',
    'services/terminal_sandbox.py::session_closed::subprocess.run':
        '(b) the helper\'s --reap mode: fixed argv, empty env, kills only sandbox-uid processes',
    'server.py::run_command::subprocess.run':
        '(a) agent: /api/command. argv comes ONLY from the fixed ALLOWED_COMMANDS table (the '
        'caller picks a key), no shell, fixed cwd, env=minimal_env() (no secrets)',
    'server.py::get_version::subprocess.run':
        '(a) public: /api/version. fixed `git merge-base --is-ancestor <sha> HEAD`; the sha comes '
        'from GitHub, validated [0-9a-f]{7,40} (never an option); env=minimal_env()',
    'server.py::local_stt::subprocess.run':
        '(a) agent: /api/stt/local. fixed ffmpeg argv over server-made temp files in the private '
        'tempdir; the caller supplies only audio BYTES; env=minimal_env()',
    'routes/audio_clips.py::_encode_mp3_with_silence::subprocess.run':
        '(a) agent: /api/audio-clips/generate. fixed ffmpeg argv over files in a private mkdtemp '
        'dir + a clamped int (0-5000); no shell; env=minimal_env(); result written no-follow',
    'routes/audio_clips.py::_mp3_duration_ms::subprocess.run':
        '(a) fixed ffprobe argv on the private temp mp3; env=minimal_env()',
    'routes/registry.py::_get_git_sha::subprocess.check_output':
        '(a) public: /checkpoints/snapshot, /registry/debug. fixed `git rev-parse HEAD`, fixed '
        'cwd, env=minimal_env(); the caller\'s repo/registry values never reach argv',
    'services/host_broker.py::queue_vision_task_legacy::subprocess.run':
        '(a) agent (image upload) but only on a legacy non-broker tenant with a docker socket '
        '(none on the fleet: RESTART_VIA_BROKER=1, no socket): fixed docker-exec argv, the '
        'subject passed as "$1" (never spliced into the shell) and pre-validated '
        '[A-Za-z0-9._-]; env=minimal_env()',
    'services/update_manager.py::UpdateManager._git::subprocess.run':
        '(a) agent via /api/version/preview: fixed `git` subcommands chosen by the code, refs '
        'from git/GitHub, no shell, env=minimal_env()',
    'services/update_manager.py::UpdateManager.run_agent_update::subprocess.run':
        'admin only: apply_update via /api/version/update (now in _ADMIN_ONLY_PREFIXES) and '
        '/api/admin/*; self-hosted update tooling',
    'services/update_manager.py::UpdateManager.run_smart_update::subprocess.run':
        'admin only (same): pip install -r requirements.txt after an update',
    'services/update_manager.py::UpdateManager.verify::subprocess.run':
        'admin only: /api/admin/update/verify + post-update check; checks the server\'s own '
        '/app files',
    'services/update_manager.py::UpdateManager._schedule_restart._do_restart::os.execv':
        'admin only: re-execs the server itself after an admin-approved update',
    'services/vault.py::_restart_via_docker_subprocess::subprocess.run':
        'admin only: /api/vault/* credential save, legacy docker path (VAULT_RESTART_VIA_DOCKER=1, '
        'no fleet tenant has it); fixed docker compose argv for this tenant',
    'routes/vision.py::_call_vision_via_claude::subprocess.run':
        'no caller (dead code kept for reference, see its docstring; grep shows no call site)',
}


def test_every_process_spawn_is_classified():
    sites = spawn_sites(ROOT)
    assert sites, 'found no spawn sites at all — the scanner is broken'
    missing = sorted(set(sites) - set(SPAWN_ALLOWLIST))
    assert not missing, (
        'process spawn(s) with no classification. A process an agent-reachable caller can '
        'trigger must run a fixed argv with no caller-controlled shell and no secrets '
        '(services.safe_proc.minimal_env), or go through the sandbox helper; then add it to '
        'SPAWN_ALLOWLIST with the reason:\n  ' + '\n  '.join(f'{k} {sites[k]}' for k in missing))
    stale = sorted(set(SPAWN_ALLOWLIST) - set(sites))
    assert not stale, f'SPAWN_ALLOWLIST entries with no spawn any more (remove them): {stale}'


def test_vision_claude_helper_really_has_no_caller():
    hits = []
    for item in ('routes', 'services', 'server.py', 'app.py'):
        p = ROOT / item
        for f in ([p] if p.is_file() else sorted(p.rglob('*.py'))):
            for n in ast.walk(ast.parse(f.read_text())):
                if isinstance(n, ast.Call):
                    name = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, 'attr', '')
                    if name == '_call_vision_via_claude':
                        hits.append(f'{f.name}:{n.lineno}')
    assert not hits, f'_call_vision_via_claude is called again ({hits}); classify it properly'


def test_agent_reachable_spawns_scrub_env():
    """(a)-class spawns pass env=minimal_env(); a missing env= would hand every key to the child."""
    need = {
        'server.py': ('run_command', 'get_version', 'local_stt'),
        'routes/audio_clips.py': ('_encode_mp3_with_silence', '_mp3_duration_ms'),
        'routes/registry.py': ('_get_git_sha',),
        'services/host_broker.py': ('queue_vision_task_legacy',),
        'services/update_manager.py': ('_git',),
    }
    bad = []
    for rel, funcs in need.items():
        tree = ast.parse((ROOT / rel).read_text())
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in funcs:
                for c in ast.walk(fn):
                    if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                            and isinstance(c.func.value, ast.Name) and c.func.value.id == 'subprocess'):
                        if not any(k.arg == 'env' for k in c.keywords):
                            bad.append(f'{rel}::{fn.name}:{c.lineno}')
    assert not bad, f'agent-reachable spawn without env=minimal_env(): {bad}'


def test_self_update_is_admin_only():
    src = (ROOT / 'app.py').read_text()
    block = src[src.index('_ADMIN_ONLY_PREFIXES = ('):]
    block = block[:block.index('\n    )')]
    assert "'/api/version/update'" in block


def test_spawn_guard_flags_a_new_unclassified_spawn(tmp_path):
    (tmp_path / 'routes').mkdir()
    (tmp_path / 'routes' / 'x.py').write_text('import subprocess\ndef h(c):\n    subprocess.run(c, shell=True)\n')
    assert 'routes/x.py::h::subprocess.run' in spawn_sites(tmp_path)


def test_agent_key_cannot_trigger_self_update(monkeypatch):
    """Runtime check of the admin gate: the agent key gets 401 on /api/version/update
    (it reached the git-pull + coding-agent spawn before), and still reaches a normal
    agent path."""
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_placeholder')
    monkeypatch.setenv('AGENT_API_KEY', 'agent-key-for-tests')
    from flask import jsonify
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True})
    hit = []

    @app.route('/api/version/update', methods=['POST'])
    def _update():
        hit.append(1)
        return jsonify({'ran': True})

    @app.route('/api/command', methods=['POST'])
    def _command():
        return jsonify({'ok': True})

    c = app.test_client()
    r = c.post('/api/version/update', headers={'X-Agent-Key': 'agent-key-for-tests'})
    assert r.status_code == 401 and not hit
    assert c.post('/api/command', headers={'X-Agent-Key': 'agent-key-for-tests'}).status_code == 200
