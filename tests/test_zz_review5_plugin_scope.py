"""Review 5 of PR #597 (non-author): can tenant-loadable in-process code still escape the
guard-scope test (test_every_known_tenant_plugin_module_is_scanned)?

That test's population is `routes[].module` of the repo's plugins/*/plugin.json plus 4
hard-coded names. OVU loads plugin code in-process by TWO mechanisms from the same
per-tenant mount (/app/plugins = /mnt/clients/<t>/openvoiceui/plugins):
  1. services/plugins.py load_plugins: routes[].module            (modelled)
  2. services/gateway_manager.py _load_plugins: `provides: gateway` -> <dir>/gateway.py
     exec_module'd in-process                                      (NOT modelled)
Measured 2026-11-17: comms-brain/gateway.py on 13 tenants ("[Plugin:comms-brain] Loaded
successfully" in tenant logs), hermes-agent/gateway.py on 5, hermes-brain/gateway.py on 1.
None of them is in the repo or in UNSCANNED_TENANT_PLUGIN_MODULES.

Plus one module the PR lists as LOW that is reachable with the agent key and writes
outside its directory (bighead create_agent, fixture = byte copy of the module live on
bhb/kai/mike/phatty/test-dev, sha256 9183ce5a...).

Asserts the SAFE outcome: FAILED means the gap is real.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _guard():
    spec = importlib.util.spec_from_file_location('review5_guard_test', str(ROOT / 'tests' / 'test_agent_writable_io_guard.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gateway_plugin_modules_are_in_the_scope_population():
    """A repo manifest that says `provides: gateway` makes gateway_manager exec its gateway.py
    in-process. Each such module must be scanned or listed as known-unscanned."""
    g = _guard()
    scanned = g._scanned_plugin_modules(ROOT)
    missing = []
    for pj in sorted((ROOT / 'plugins').glob('*/plugin.json')):
        m = json.loads(pj.read_text())
        if m.get('provides') == 'gateway':
            rel = f'{pj.parent.name}/gateway.py'
            if rel not in scanned and rel not in g.UNSCANNED_TENANT_PLUGIN_MODULES:
                missing.append(rel)
    assert not missing, ('in-process gateway plugin module(s) neither scanned nor listed by the '
                         f'guard-scope test (gateway_manager loads <plugin>/gateway.py): {missing}')


def test_live_tenant_loadable_modules_are_scanned_or_listed():
    """Host-only (skips without /mnt/clients). Every module a live tenant's OVU can exec
    in-process (route modules AND gateway.py) is in the repo or explicitly listed."""
    clients = Path('/mnt/clients')
    if not clients.is_dir():
        pytest.skip('no /mnt/clients here')
    g = _guard()
    listed = set(g.UNSCANNED_TENANT_PLUGIN_MODULES) | g.KNOWN_TENANT_PLUGIN_MODULES_WITHOUT_REPO_MANIFEST
    retired = set()
    rf = Path('/mnt/system/base/retired-tenants.txt')
    if rf.is_file():
        retired = {l.strip() for l in rf.read_text().splitlines() if l.strip() and not l.startswith('#')}
    escaped, drift = {}, {}
    for pj in sorted(clients.glob('*/openvoiceui/plugins/*/plugin.json')):
        tenant, pdir = pj.parts[3], pj.parent
        if tenant in retired:
            continue
        m = json.loads(pj.read_text())
        mods = [r.get('module') for r in m.get('routes', []) if r.get('module')]
        if m.get('provides') == 'gateway':
            mods.append('gateway.py')
        for rel in mods:
            f = pdir / rel
            if not f.is_file():
                continue
            key = f'{pdir.name}/{rel}'
            ref = ROOT / 'plugins' / key
            if ref.is_file():
                if hashlib.sha256(ref.read_bytes()).digest() != hashlib.sha256(f.read_bytes()).digest():
                    drift.setdefault(key, set()).add(tenant)
            elif key not in listed:
                escaped.setdefault(key, set()).add(tenant)
    print('repo module differs on tenant (rollout pending):', {k: sorted(v) for k, v in drift.items()})
    assert not escaped, ('tenant-loadable in-process modules the guard-scope test does not know '
                         f'(not in the repo, not listed): {{module: tenants}} = '
                         f'{ {k: sorted(v) for k, v in escaped.items()} }')


# -- bighead (listed as "in-cell LOW"): with the agent key it writes outside its workspace -------

@pytest.fixture
def bighead(tmp_path, monkeypatch):
    src = ROOT / 'tests' / 'review5_fixtures' / 'tenant_bighead_9183ce5a.py.txt'
    spec = importlib.util.spec_from_loader('review5_tenant_bighead', loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(src.read_text(), str(src), 'exec'), mod.__dict__)
    runtime = tmp_path / 'runtime'
    (runtime / 'workspace').mkdir(parents=True)
    monkeypatch.setattr(mod, 'RUNTIME_DIR', runtime)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(mod.bighead_bp)
    return app.test_client(), runtime


def test_bighead_create_agent_absolute_id_writes_outside_the_workspace(bighead, tmp_path):
    client, runtime = bighead
    target = tmp_path / 'app' / 'routes'        # stands in for /app/routes (server-writable)
    target.mkdir(parents=True)
    client.post('/api/bighead/create-agent', json={'id': str(target), 'name': 'x',
                                                    'personality': 'AGENT TEXT'})
    written = sorted(p.name for p in target.iterdir())
    assert written == [], (f'POST /api/bighead/create-agent (agent key reaches it) wrote {written} '
                           f'into {target}, outside RUNTIME_DIR/workspace/characters (id is unvalidated; '
                           "Path('/a') / '/abs' == '/abs')")


def test_bighead_create_agent_follows_a_symlink_planted_in_uploads(bighead, tmp_path):
    client, runtime = bighead
    uploads = runtime / 'uploads'
    uploads.mkdir()
    target = tmp_path / 'app'
    target.mkdir()
    (uploads / 'x').symlink_to(target)           # agent side: uploads/ is rw
    client.post('/api/bighead/create-agent', json={'id': '../../uploads/x', 'name': 'x'})
    written = sorted(p.name for p in target.iterdir())
    assert written == [], f'create-agent wrote {written} through uploads/x -> {target}'
