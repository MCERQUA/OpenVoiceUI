"""Every repository path the server reads at runtime must be in the npm package.

The npm CLI builds its Docker image from the installed package, so a directory missing from
package.json "files" is missing from the image. default-faces/ was missing: on npm installs
GET /api/custom-faces/template answered 404 (routes/custom_faces.py reads
default-faces/template.html). default-styles/ was missing too, so the built-in Canvas Style
presets (services/canvas_styles.py reads default-styles/*.json) did not exist on npm installs.

Check 1 scans the server code for app-root-relative paths and needs nothing but Python.
Check 2 asks npm itself (`npm pack --dry-run`) and skips only when npm is absent (CI has it).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# APP_ROOT / "x", Path(__file__).parent.parent / 'x' (routes/*, services/*).
_REF_RE = re.compile(r"""(?:APP_ROOT|parent\.parent)\s*/\s*['"]([^'"]+)['"]""")
# Written at runtime, never shipped.
_GENERATED = {'runtime'}


def _files_entries() -> list[str]:
    return json.loads((ROOT / 'package.json').read_text())['files']


def _shipped(rel: str) -> bool:
    for entry in _files_entries():
        if entry.startswith('!'):
            continue
        if entry.endswith('/') and rel.startswith(entry):
            return True
        if any(ch in entry for ch in '*?[') and fnmatch.fnmatch(rel, entry):
            return True
        if rel == entry or rel.startswith(entry.rstrip('/') + '/'):
            return True
    return False


def _runtime_refs() -> set[str]:
    refs = set()
    sources = [ROOT / 'app.py', ROOT / 'server.py']
    sources += sorted((ROOT / 'routes').glob('*.py')) + sorted((ROOT / 'services').glob('*.py'))
    for src in sources:
        for m in _REF_RE.finditer(src.read_text(encoding='utf-8')):
            top = m.group(1).split('/')[0]
            if top not in _GENERATED:
                refs.add(top)
    return refs


def _repo_files_under(top: str) -> list[str]:
    p = ROOT / top
    if p.is_file():
        return [top]
    out = []
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        out += [str(Path(dirpath, f).relative_to(ROOT)) for f in filenames if not f.endswith('.pyc')]
    return out


def test_the_scan_finds_the_known_runtime_paths():
    # Guards the scanner itself: if the pattern stops matching, check 1 would pass vacuously.
    refs = _runtime_refs()
    assert {'default-pages', 'default-faces', 'default-styles', 'prompts', 'static'} <= refs, refs


def test_every_runtime_path_is_in_the_npm_package():
    missing = []
    for top in sorted(_runtime_refs()):
        files = _repo_files_under(top)
        if not files:
            continue
        if not any(_shipped(f) for f in files):
            missing.append(top)
    assert not missing, f'read at runtime but not in package.json "files": {missing}'


def test_face_template_and_style_presets_are_shipped():
    assert _shipped('default-faces/template.html')
    presets = sorted(p.name for p in (ROOT / 'default-styles').glob('*.json'))
    assert presets, 'no style presets found in default-styles/'
    assert all(_shipped(f'default-styles/{n}') for n in presets)


def test_npm_pack_includes_runtime_assets():
    npm = shutil.which('npm')
    if not npm:
        pytest.skip('npm not installed')
    proc = subprocess.run([npm, 'pack', '--dry-run', '--json', '--ignore-scripts'], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    packed = {f['path'] for f in json.loads(proc.stdout)[0]['files']}
    assert 'default-faces/template.html' in packed
    assert any(p.startswith('default-styles/') and p.endswith('.json') for p in packed)
    assert 'skills/suno-music/SKILL.md' in packed
