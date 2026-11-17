"""
Systemic guard (review 2 of PR #597): every file I/O whose path can derive from an
agent-writable root goes through services/safe_open.py, or is listed below with a reason.

Two reviews in a row found routes that check a path and then open it BY NAME inside a
directory the tenant agent (or the terminal sandbox) can write: a symlink swapped in
between the check and the open turns the route into "read/write any file the server
can". Fixing the list each time missed the class. This test enumerates the class from
the code itself (tests/agent_io_scan.py: an AST taint walk of everything the server runs
in-process: routes/, services/, server.py, app.py, plugins/, tts_providers/, providers/)
and fails on ANY sink — send_file, send_from_directory, open,
os.open, Path.open/read_*/write_*/chmod/rename/replace, os.replace/rename/chmod,
shutil copy/move/rmtree, FileStorage/Image .save(path), unlink/remove/rmdir/link/symlink,
zipfile/tarfile/gzip/bz2/lzma/codecs/wave/PIL/sqlite3/shelve openers, <module>.open(path),
TarFile.extractall(path), resolved through import aliases — that is reached by a path
derived from uploads, canvas-pages (+ _data, stories, styles, singalong), music,
generated_music, transcripts, captures, the workspace mounts, the code workspace or
/tmp — or whose head is request-supplied (the request body, query, a URL parameter) —
unless it is listed in ALLOWLIST with a one-line reason. A call the scanner cannot resolve
(a dynamic callee, a sink passed as a value) fails closed the same way.

A new route that opens an agent-writable path by name fails this test until it uses
services.safe_open (Anchored / open_beneath / send_file_beneath / atomic_write_beneath,
all no-follow and beneath a root anchored at the mount point) or is justified here.
"""

import ast
import sys

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_io_scan import SCAN, Scanner, spawn_sites, unclassified  # noqa: E402

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
    # ── review 3 of PR #597 (wider engine: plugins/, providers, aliases, request paths,
    #    unlink/remove/link, archive/db openers, Anchored laundering, dynamic calls) ──
    'plugins/booking/routes/booking.py::_root_fd::os.open':
        'opens the uploads MOUNT POINT itself as a directory fd (only on an image without '
        'services/safe_open); every file below it is then opened no-follow relative to that fd. '
        'The mount point\'s own path is not agent-replaceable (same as safe_open.open_root)',
    # ── review 5 of PR #597 (follow-up): the bighead plugin route module, now in the repo ──
    'plugins/bhb-animated-characters/routes/bighead.py::_root_fd::os.open':
        'opens the anchoring root itself (the workspace dir, or RUNTIME_DIR for bighead/) as a '
        'directory fd with O_DIRECTORY|O_NOFOLLOW, only on an image without services/safe_open; '
        'every directory and file below it is opened no-follow relative to that fd, and the '
        'request id is a [A-Za-z0-9_-]{1,64} slug. Same as wan_video::_uploads_fd',
    # ── review 4 of PR #597: the tenant-mounted wan-video-studio plugin, now in the repo ──
    'plugins/wan-video-studio/routes/wan_video.py::_uploads_fd::os.open':
        'opens the uploads MOUNT POINT itself as a directory fd (O_DIRECTORY|O_NOFOLLOW); '
        '.wan-requests and every file in it are then opened no-follow relative to that fd, '
        'modes set by fchmod on the fds. Same as booking::_root_fd / safe_open.open_root',
    'routes/canvas.py::openclaw_ui_proxy::<dynamic-call>':
        'getattr(requests, request.method.lower())(url, params=request.args, ...): an HTTP verb '
        'on the requests module (GET/POST/... to the internal openclaw UI), no file I/O; the '
        'request values are query params/body, never a path',
    'tts_providers/supertonic_tts.py::<module>::open':
        'inside `if __name__ == "__main__":` — a manual CLI self-test writing /tmp/'
        'supertonic_test_output.wav; never runs in the server process (module is imported)',
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
    for item in SCAN:
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


def _scan_tree(tmp_path, files):
    for rel, src in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src)
    return {(rel, q, s) for rel, q, s, _ in Scanner(tmp_path).findings()}


def test_scanner_flags_the_review3_b2_pattern(tmp_path):
    """Negative control (review 3, B2): the pre-fix /api/conversation sidecar write, a
    request-supplied path checked by `'uploads' in parts` and then written by name."""
    found = _scan_tree(tmp_path, {'routes/conv.py': (
        'from pathlib import Path\n'
        'from flask import request\n'
        'def conversation():\n'
        '    data = request.get_json() or {}\n'
        '    image_path = data.get("image_path") or None\n'
        '    if image_path:\n'
        '        _img_file = Path(image_path).resolve()\n'
        '        if "uploads" not in _img_file.parts:\n'
        '            raise ValueError("x")\n'
        '        _intel_path = _img_file.parent / ".image-intel" / (_img_file.name + ".json")\n'
        '        _intel_path.parent.mkdir(exist_ok=True)\n'
        '        _intel_path.write_text("{}")\n'
        '        return _img_file.read_bytes()\n')})
    assert ('routes/conv.py', 'conversation', '.write_text') in found
    assert ('routes/conv.py', 'conversation', '.read_bytes') in found


def test_scanner_flags_the_review3_b3_pattern_in_plugins(tmp_path):
    """Negative control (review 3, B3): the pre-fix booking plugin's predictable temp, in
    plugins/ (which the guard did not scan before)."""
    found = _scan_tree(tmp_path, {'plugins/booking/routes/booking.py': (
        'from services.paths import UPLOADS_DIR\n'
        'CONFIG_PATH = UPLOADS_DIR / "booking-config.json"\n'
        'def _save_config(data):\n'
        '    tmp = CONFIG_PATH.with_suffix(".json.tmp")\n'
        '    tmp.write_text(data)\n'
        '    tmp.replace(CONFIG_PATH)\n')})
    assert ('plugins/booking/routes/booking.py', '_save_config', '.write_text') in found


def test_scanner_flags_the_review4_wan_video_plugin(tmp_path):
    """Negative control (review 4, R4-B2): the tenant-mounted wan-video-studio module as
    live tenants run it today (byte copy, sha256 6f65d714...) is flagged in plugins/."""
    orig = (Path(__file__).resolve().parent / 'review4_fixtures'
            / 'tenant_wan_video_6f65d714.tenant-copy.txt').read_text()
    found = _scan_tree(tmp_path, {'plugins/wan-video-studio/routes/wan_video.py': orig})
    rel = 'plugins/wan-video-studio/routes/wan_video.py'
    assert (rel, '_ensure_requests_dir', 'os.chmod') in found
    assert (rel, 'queue_generation', '.write_text') in found
    assert (rel, 'get_result', '.read_text') in found


# ── tenant-loadable plugin code (review 4 of PR #597, R4-B2) ─────────────────────────
# OVU imports /app/plugins/<id>/<routes[].module> in-process; /app/plugins is a PER-TENANT
# mount (/mnt/clients/<t>/openvoiceui/plugins), not the image. The guard only sees what is
# in this repo, so every plugin route module a tenant can load must be HERE (and is then
# scanned via SCAN's plugins/), or be named below as a known, not-yet-scanned module.
# Sources: the route modules declared by this repo's plugins/*/plugin.json, plus the
# modules review 4 measured on live tenants that no repo manifest declares.
KNOWN_TENANT_PLUGIN_MODULES_WITHOUT_REPO_MANIFEST = {
    'desktop-native/routes.py', 'ovui-core/routes.py', 'housecall-pro/routes/housecall_pro.py',
    'client-history-admin/routes/client_history_admin.py',
    # gateway_manager loads <plugin>/gateway.py in-process (provides: gateway)
    'comms-brain/gateway.py', 'hermes-brain/gateway.py',
}
# Known tenant-loaded modules whose code is NOT in this repo yet, so the guard has never
# scanned them. Follow-up (review 4, R4-B2 part 3): bring each into plugins/ (then remove
# it here; test_unscanned_plugin_list_is_not_stale enforces that) or gate rollouts on a
# host-side scan of /mnt/clients/*/openvoiceui/plugins.
UNSCANNED_TENANT_PLUGIN_MODULES = {
    'hermes-agent/routes/hermes.py': 'catalog stub only; review 4 scan: no finding',
    'seo-platform/routes/seo.py': 'catalog stub only; review 4 scan: no finding',
    'twenty-crm/routes/crm.py': 'catalog stub only; review 4 scan: no finding',
    'desktop-native/routes.py': 'not in the repo; review 4 scan: no finding',
    'ovui-core/routes.py': 'not in the repo (3 tenant variants); review 4 scan: no finding',
    'housecall-pro/routes/housecall_pro.py': 'not in the repo; review 4 scan: no finding',
    'client-history-admin/routes/client_history_admin.py': 'not in the repo; review 4 scan: no finding',
    'comms-brain/gateway.py': 'gateway plugin (13 tenants); review 5 scan: no finding, HTTP only',
    'hermes-agent/gateway.py': 'gateway plugin (4 tenants); review 5 scan: no finding (docker exec, no socket mounted)',
    'hermes-brain/gateway.py': 'gateway plugin (1 tenant); review 5 scan: no finding',
}


def _repo_manifest_route_modules(root):
    import json
    out = set()
    for pj in sorted((root / 'plugins').glob('*/plugin.json')):
        m = json.loads(pj.read_text())
        for r in m.get('routes', []):
            if r.get('module'):
                out.add(f'{pj.parent.name}/{r["module"]}')
        if m.get('provides') == 'gateway':   # services/gateway_manager.py execs <plugin>/gateway.py
            out.add(f'{pj.parent.name}/gateway.py')
    return out


def _scanned_plugin_modules(root):
    return {rel[len('plugins/'):] for rel in Scanner(root).modules if rel.startswith('plugins/')}


def _unscanned_plugin_modules(root):
    known = _repo_manifest_route_modules(root) | KNOWN_TENANT_PLUGIN_MODULES_WITHOUT_REPO_MANIFEST
    return sorted(known - _scanned_plugin_modules(root) - set(UNSCANNED_TENANT_PLUGIN_MODULES))


def test_every_known_tenant_plugin_module_is_scanned():
    scanned = _scanned_plugin_modules(ROOT)
    # the plugins rolled to tenants by sha-gated scripts (booking, wan-video-studio, bighead)
    assert {'booking/routes/booking.py', 'wan-video-studio/routes/wan_video.py',
            'bhb-animated-characters/routes/bighead.py'} <= scanned
    unscanned = _unscanned_plugin_modules(ROOT)
    assert not unscanned, (
        'tenant-loadable plugin route module(s) the I/O guard never scans (their code is not '
        'in this repo). Bring the module into plugins/<id>/ or list it in '
        f'UNSCANNED_TENANT_PLUGIN_MODULES with a reason: {unscanned}')


def test_unscanned_plugin_list_is_not_stale():
    stale = sorted(set(UNSCANNED_TENANT_PLUGIN_MODULES) & _scanned_plugin_modules(ROOT))
    assert not stale, f'now in the repo and scanned (remove from UNSCANNED_TENANT_PLUGIN_MODULES): {stale}'


def test_plugin_scope_check_fires_on_an_unscanned_module(tmp_path):
    """Negative control: a manifest whose route module is not in the tree (what a catalog
    stub for a tenant plugin looks like) is reported; once the code is in, it is not."""
    import json
    (tmp_path / 'plugins' / 'newplug').mkdir(parents=True)
    (tmp_path / 'plugins' / 'newplug' / 'plugin.json').write_text(
        json.dumps({'routes': [{'module': 'routes/new.py', 'blueprint': 'bp'}]}))
    assert 'newplug/routes/new.py' in _unscanned_plugin_modules(tmp_path)
    (tmp_path / 'plugins' / 'newplug' / 'routes').mkdir()
    (tmp_path / 'plugins' / 'newplug' / 'routes' / 'new.py').write_text('x = 1\n')
    assert 'newplug/routes/new.py' not in _unscanned_plugin_modules(tmp_path)
    # the out-of-repo tenant modules are reported unless listed as known-unscanned
    assert set(_unscanned_plugin_modules(tmp_path)) == (
        KNOWN_TENANT_PLUGIN_MODULES_WITHOUT_REPO_MANIFEST - set(UNSCANNED_TENANT_PLUGIN_MODULES))


def test_request_value_joined_under_a_private_root_is_not_flagged(tmp_path):
    """Precision of the request label: only a request-supplied path HEAD is the caller's
    choice; a request value joined under a fixed server-private root is judged by the root."""
    found = _scan_tree(tmp_path, {'routes/p.py': (
        'from pathlib import Path\n'
        'from flask import request\n'
        'PROFILES = Path("/app/runtime/profiles")\n'
        'def ok():\n'
        '    return (PROFILES / request.args["n"]).read_text()\n'
        'def bad():\n'
        '    return Path(request.args["p"]).read_text()\n'
        '@bp.route("/x/<path:name>")\n'
        'def url_param(name):\n'
        '    return open(name).read()\n')})
    assert ('routes/p.py', 'bad', '.read_text') in found
    assert ('routes/p.py', 'url_param', 'open') in found
    assert not any(q == 'ok' for _, q, _ in found)


def test_unresolvable_calls_fail_closed(tmp_path):
    found = _scan_tree(tmp_path, {'routes/d.py': (
        'from services.paths import UPLOADS_DIR\n'
        'HANDLERS = {}\n'
        'def table(k, n):\n'
        '    return HANDLERS[k](UPLOADS_DIR / n)\n'
        'def dyn(o, name, n):\n'
        '    return getattr(o, name)(UPLOADS_DIR / n)\n'
        'def as_value(names):\n'
        '    return list(map(open, [UPLOADS_DIR / n for n in names]))\n'
        'def fine(n):\n'
        '    return len(str(n))\n')})
    assert ('routes/d.py', 'table', '<dynamic-call>') in found
    assert ('routes/d.py', 'dyn', '<dynamic-call>') in found
    assert ('routes/d.py', 'as_value', '<sink-as-value:open>') in found
    assert not any(q == 'fine' for _, q, _ in found)


def test_anchored_refuses_to_be_a_path(tmp_path):
    """Anchored.__fspath__ raises: open(a)/Path(a)/os.fspath(a) cannot silently open by name."""
    import os
    import pytest
    from services.safe_open import Anchored
    (tmp_path / 'f').write_text('x')
    a = Anchored(tmp_path, 'f')
    for consumer in (open, os.fspath, Path):
        with pytest.raises(TypeError):
            consumer(a)
    assert a.read_text() == 'x' and str(a).endswith('/f')


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
    # tts_providers/ joined the scope in review 3 of PR #597
    'tts_providers/qwen3_gpu_provider.py::_measure_duration::subprocess.run':
        '(a) agent (any TTS request with the qwen3-gpu provider): fixed ffprobe argv on a '
        'voice-clone file path (server-side voice-clones dir, not agent-writable); no shell; '
        'env=minimal_env()',
    'tts_providers/qwen3_gpu_provider.py::_transcode_for_wire::subprocess.run':
        '(a) same: fixed ffmpeg resample argv, src/dst in the voice-clones dir; env=minimal_env()',
    'tts_providers/qwen3_gpu_provider.py::_measure_loudness::subprocess.run':
        '(a) same: fixed ffmpeg loudnorm argv over stdin bytes (pipe:0), clamped float params '
        'from constants; env=minimal_env()',
    'tts_providers/qwen3_provider.py::Qwen3Provider._ffmpeg_speed::subprocess.run':
        '(a) agent (TTS with qwen3): fixed ffmpeg atempo argv over stdin bytes; speed is a float '
        'from the active profile (not a string the caller splices); env=minimal_env()',
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
    for item in SCAN:
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
        'tts_providers/qwen3_gpu_provider.py': ('_measure_duration', '_transcode_for_wire',
                                                '_measure_loudness'),
        'tts_providers/qwen3_provider.py': ('_ffmpeg_speed',),
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
