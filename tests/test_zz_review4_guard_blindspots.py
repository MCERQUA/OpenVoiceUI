"""Review 4 of PR #597 (non-author): blind spots of tests/agent_io_scan.py at 47d88bf,
beyond review 3's 23.

Each case is a tiny synthetic module that opens/writes/deletes/spawns BY NAME on a path
under an agent-writable root -- what the guard promises to "classify or fail closed".
Every test asserts the guard FLAGS it, so a FAIL is a blind spot. COVERED_* cases are
forms the brief asked about that the guard already catches (they must PASS; they prove
the harness discriminates). None of the blind-spot forms exists in the tree at 47d88bf
(grep in the review doc): these are regression gaps, not live leaks.
"""
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_io_scan import Scanner, spawn_sites  # noqa: E402

HDR = ('import os\n'
       'from pathlib import Path\n'
       'from flask import request, send_file, send_from_directory\n'
       'from services.paths import UPLOADS_DIR, RUNTIME_DIR\n'
       'from services.safe_open import Anchored\n')

BLIND_IO = {
    # a sink bound to a local name, then called through it
    'local_alias_of_open': 'def h(n):\n    o = open\n    return o(UPLOADS_DIR / n).read()\n',
    # functools.partial with the PATH supplied at call time (review 3 only covered partial(open, p))
    'partial_path_supplied_later':
        'import functools\ndef h(n):\n    w = functools.partial(open, mode="w")\n'
        '    w(UPLOADS_DIR / n).write("x")\n',
    'importlib_import_module_os_remove':
        'import importlib\ndef h(n):\n    importlib.import_module("os").remove(UPLOADS_DIR / n)\n',
    'dunder_import_shutil_copy':
        'def h(n):\n    __import__("shutil").copy("/srv/x", UPLOADS_DIR / n)\n',
    # the dir_fd= exemption does not check that the path is a bare name (absolute ignores dir_fd)
    'dir_fd_none_exemption':
        'def h(n):\n    return os.open(str(UPLOADS_DIR / n), os.O_RDONLY, dir_fd=None)\n',
    'dir_fd_with_absolute_path':
        'def h(n, dfd):\n    return os.open(str(UPLOADS_DIR / n), os.O_WRONLY, dir_fd=dfd)\n',
    # paths stored on a class / an instance / a function-assigned global
    'class_attribute_path':
        'class Store:\n    P = UPLOADS_DIR / "state.json"\n'
        '    def save(self, d):\n        self.P.write_text(d)\n',
    'instance_attribute_across_methods':
        'class Store:\n    def __init__(self, n):\n        self.p = UPLOADS_DIR / n\n'
        '    def save(self, d):\n        with open(self.p, "w") as f:\n            f.write(d)\n',
    'global_assigned_in_function':
        'P = None\ndef init(n):\n    global P\n    P = UPLOADS_DIR / n\n'
        'def save(d):\n    open(P, "w").write(d)\n',
    # root named only inside a format string the engine does not parse
    'percent_format_runtime_uploads':
        'def h(n):\n    return open("%s/uploads/%s" % (RUNTIME_DIR, n)).read()\n',
    'str_format_runtime_uploads':
        'def h(n):\n    return open("{}/uploads/{}".format(RUNTIME_DIR, n)).read()\n',
    # unbound Path methods: the path is args[0], the "receiver" is the class
    'unbound_Path_write_text':
        'def h(n):\n    Path.write_text(UPLOADS_DIR / n, "x")\n',
    'unbound_Path_unlink_via_map':
        'def h(names):\n    list(map(Path.unlink, [UPLOADS_DIR / n for n in names]))\n',
    # the tainted value is the getattr RECEIVER, not an argument of the dynamic call
    'getattr_method_on_tainted_receiver':
        'def h(n):\n    getattr(UPLOADS_DIR / n, "write_text")("x")\n',
    'io_FileIO':
        'import io\ndef h(n):\n    return io.FileIO(str(UPLOADS_DIR / n), "w")\n',
    # a module that defines ANY function/method named `open` disables the bare open() sink
    'local_def_named_open_shadows_builtin':
        'class Conn:\n    def open(self):\n        return self\n'
        'def h(n):\n    return open(UPLOADS_DIR / n).read()\n',
    # Anchored laundered through its plain-string parts
    'anchored_laundered_via_root_rel':
        'def h(n):\n    a = Anchored(UPLOADS_DIR, n)\n    return open(os.path.join(a.root, a.rel)).read()\n',
    # temp file created by name inside a (possibly symlinked) subdir of a root
    'tempfile_dir_in_root_subdir':
        'import tempfile\ndef h(sub):\n'
        '    return tempfile.NamedTemporaryFile(dir=UPLOADS_DIR / sub, delete=False)\n',
}

COVERED_IO = {
    'COVERED_positive_control_open': 'def h(n):\n    return open(UPLOADS_DIR / n).read()\n',
    'COVERED_partial_open_with_path': 'import functools\ndef h(n):\n    functools.partial(open, UPLOADS_DIR / n)()\n',
    'COVERED_os_fdopen_on_follow_open':
        'def h(n):\n    return os.fdopen(os.open(str(UPLOADS_DIR / n), os.O_RDONLY)).read()\n',
    'COVERED_with_name_parent_chain':
        'def h(n):\n    (UPLOADS_DIR / n).parent.with_name("x.json").write_text("{}")\n',
    'COVERED_shutil_copytree': 'import shutil\ndef h(n):\n    shutil.copytree(UPLOADS_DIR / n, "/srv/o")\n',
    'COVERED_shutil_copyfile': 'import shutil\ndef h(n):\n    shutil.copyfile("/srv/i", UPLOADS_DIR / n)\n',
    'COVERED_shutil_move': 'import shutil\ndef h(n):\n    shutil.move(UPLOADS_DIR / n, "/srv/o")\n',
    'COVERED_os_rename_across': 'def h(n):\n    os.rename(UPLOADS_DIR / n, "/app/runtime/x")\n',
    'COVERED_send_from_directory_kw':
        'def h(n):\n    return send_from_directory(directory=UPLOADS_DIR, path=n)\n',
    'COVERED_send_file_str': 'def h(n):\n    return send_file(str(UPLOADS_DIR / n))\n',
    'COVERED_codecs_open': 'import codecs\ndef h(n):\n    return codecs.open(UPLOADS_DIR / n).read()\n',
    'COVERED_io_open': 'import io\ndef h(n):\n    return io.open(UPLOADS_DIR / n).read()\n',
    'COVERED_json_dump_to_open_handle':
        'import json\ndef h(n, d):\n    json.dump(d, open(UPLOADS_DIR / n, "w"))\n',
    'COVERED_module_global_path':
        'P = UPLOADS_DIR / "x.json"\ndef h(d):\n    P.write_text(d)\n',
}

BLIND_SPAWN = {
    'local_alias_of_Popen': 'import subprocess\ndef h(c):\n    sp = subprocess.Popen\n    sp(c)\n',
    'partial_of_run': 'import functools, subprocess\ndef h(c):\n    functools.partial(subprocess.run, c)()\n',
    'importlib_subprocess': 'import importlib\ndef h(c):\n    importlib.import_module("subprocess").run(c)\n',
    'dunder_import_os_system': 'def h(c):\n    __import__("os").system(c)\n',
    'exec_string': 'def h(c):\n    exec("import os; os.system(c)")\n',
}
COVERED_SPAWN = {
    'COVERED_positive_control_run': 'import subprocess\ndef h(c):\n    subprocess.run(c)\n',
    'COVERED_pty_spawn': 'import pty\ndef h(c):\n    pty.spawn(c)\n',
    'COVERED_os_execvp_alias': 'from os import execvp as ev\ndef h(c):\n    ev(c[0], c)\n',
}


def _io_flagged(tmp_path, src):
    (tmp_path / 'routes').mkdir()
    (tmp_path / 'routes' / 'x.py').write_text(HDR + src)
    return [f for f in Scanner(tmp_path).findings() if f[0] == 'routes/x.py']


@pytest.mark.parametrize('case', sorted(BLIND_IO) + sorted(COVERED_IO))
def test_guard_flags_by_name_io(tmp_path, case):
    src = BLIND_IO.get(case) or COVERED_IO[case]
    assert _io_flagged(tmp_path, src), f'blind spot: {case} is invisible to tests/agent_io_scan.py'


def _spawned(tmp_path, src):
    (tmp_path / 'routes').mkdir()
    (tmp_path / 'routes' / 'x.py').write_text(src)
    return [k for k in spawn_sites(tmp_path) if k.startswith('routes/x.py::')]


@pytest.mark.parametrize('case', sorted(BLIND_SPAWN) + sorted(COVERED_SPAWN))
def test_spawn_guard_sees_spawn(tmp_path, case):
    src = BLIND_SPAWN.get(case) or COVERED_SPAWN[case]
    assert _spawned(tmp_path, src), f'blind spot: spawn form {case} is invisible to spawn_sites'
