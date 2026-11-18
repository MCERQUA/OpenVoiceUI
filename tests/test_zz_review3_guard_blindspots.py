"""Review 3 of PR #597 (non-author): blind spots of tests/agent_io_scan.py.

Each case is a tiny synthetic route that opens/writes/deletes a path under an agent-writable
root BY NAME (symlink-followable) -- exactly what the guard promises to "classify or fail".
Every test asserts the guard FLAGS it; a FAIL is a blind spot. Cases marked REAL mirror a
pattern that exists in this tree today (file:line in the id).
"""
import textwrap
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_io_scan import Scanner, spawn_sites  # noqa: E402

HDR = ('from pathlib import Path\n'
       'from flask import request, send_file\n'
       'from services.paths import UPLOADS_DIR, RUNTIME_DIR\n'
       'from services.safe_open import Anchored\n')

IO_CASES = {
    'alias_import_os_as': 'import os as _os\ndef h(n):\n    return _os.open(str(UPLOADS_DIR / n), _os.O_RDONLY)\n',
    'from_shutil_import_copy': 'from shutil import copy\ndef h(n):\n    copy(UPLOADS_DIR / n, "/srv/out")\n',
    'gzip_open_module_receiver': 'import gzip\ndef h(n):\n    return gzip.open(UPLOADS_DIR / n).read()\n',
    'REAL_zipfile_ZipFile__chatgpt_parser_100':
        'import zipfile\ndef h(n):\n    return zipfile.ZipFile(UPLOADS_DIR / n).read("x")\n',
    'PIL_Image_open_excluded_receiver':
        'from PIL import Image\ndef h(n):\n    return Image.open(UPLOADS_DIR / n).size\n',
    'tarfile_extractall_into_root':
        'import tarfile\ndef h(blob):\n    tarfile.open(fileobj=blob).extractall(UPLOADS_DIR)\n',
    'sqlite_connect': 'import sqlite3\ndef h(n):\n    return sqlite3.connect(str(UPLOADS_DIR / n))\n',
    'REAL_os_path_join_runtime_uploads__gateways_openclaw_80':
        'import os\ndef h():\n    d = os.path.join(os.path.dirname(__file__), "..", "runtime", "uploads")\n'
        '    with open(os.path.join(d, ".device-identity.json")) as f:\n        return f.read()\n',
    'fstring_runtime_uploads':
        'def h(n):\n    return open(f"{RUNTIME_DIR}/uploads/{n}").read()\n',
    'REAL_request_path_checked_by_parts__conversation_1787':
        'def h():\n    p = Path(request.json["image_path"]).resolve()\n'
        '    if "uploads" in p.parts:\n        (p.parent / ".image-intel" / (p.name + ".json")).write_text("{}")\n',
    'anchored_laundered_via_fspath':
        'def h(n):\n    a = Anchored(UPLOADS_DIR, n)\n    return open(a).read()\n',
    'anchored_laundered_via_path_attr':
        'def h(n):\n    return send_file(Anchored(UPLOADS_DIR, n).path)\n',
    'method_on_other_object':
        'class Store:\n    def load(self, p):\n        return open(p).read()\n'
        'def h(n):\n    s = Store()\n    return s.load(UPLOADS_DIR / n)\n',
    'generator_yield_hands_out_paths':
        'def pages():\n    for p in UPLOADS_DIR.iterdir():\n        yield p\n'
        'def h():\n    return [open(p).read() for p in pages()]\n',
    'lambda_helper': 'rd = lambda p: open(p).read()\ndef h(n):\n    return rd(UPLOADS_DIR / n)\n',
    'unlink_through_symlinked_subdir':
        'def h(d, n):\n    (UPLOADS_DIR / d / n).unlink()\n',
    'os_remove_through_symlinked_subdir':
        'import os\ndef h(d, n):\n    os.remove(UPLOADS_DIR / d / n)\n',
    'os_link_symlink':
        'import os\ndef h(n):\n    os.link(UPLOADS_DIR / n, "/srv/x")\n',
}


def _scan(tmp_path, body):
    (tmp_path / 'routes').mkdir(exist_ok=True)
    (tmp_path / 'routes' / 'probe.py').write_text(HDR + textwrap.dedent(body))
    return [f for f in Scanner(tmp_path).findings() if f[0] == 'routes/probe.py']


def test_control_plain_open_is_flagged(tmp_path):
    """Positive control: the guard does see the textbook case (so a miss below is real)."""
    assert _scan(tmp_path, 'def h(n):\n    return open(UPLOADS_DIR / n).read()\n')


@pytest.mark.parametrize('case', sorted(IO_CASES))
def test_guard_flags(tmp_path, case):
    assert _scan(tmp_path, IO_CASES[case]), f'blind spot: {case} is not flagged by agent_io_scan'


SPAWN_CASES = {
    'import_subprocess_as': 'import subprocess as sp\ndef h(c):\n    sp.run(c, shell=True)\n',
    'from_subprocess_import_Popen': 'from subprocess import Popen\ndef h(c):\n    Popen(c, shell=True)\n',
    'os_system_via_getattr': 'import os\ndef h(c):\n    getattr(os, "system")(c)\n',
}


@pytest.mark.parametrize('case', sorted(SPAWN_CASES))
def test_spawn_guard_classifies(tmp_path, case):
    (tmp_path / 'routes').mkdir()
    (tmp_path / 'routes' / 'x.py').write_text(SPAWN_CASES[case])
    assert any(k.startswith('routes/x.py::h::') for k in spawn_sites(tmp_path)), \
        f'blind spot: spawn form {case} is invisible to spawn_sites'


def test_spawn_scope_covers_imported_provider_packages():
    """REAL: tts_providers/ (imported by the server) spawns ffmpeg/ffprobe with the server's
    full environment (no env=); SCAN = routes, services, server.py, app.py never looks there."""
    root = Path(__file__).resolve().parent.parent
    hits = [p for p in (root / 'tts_providers').rglob('*.py') if 'subprocess.run(' in p.read_text()]
    sites = spawn_sites(root)
    missed = [p.name for p in hits if not any(k.startswith(f'tts_providers/{p.name}') for k in sites)]
    assert not missed, f'spawns outside the guard scope: {missed}'
