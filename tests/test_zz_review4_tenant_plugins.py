"""Review 4 of PR #597 (non-author): the B3 CLASS is "in-process code the guard never sees".

47d88bf added plugins/ to the guard's SCAN, but SCAN is the REPO's plugins/. What a tenant's
server actually imports is /app/plugins = the per-tenant mount
/mnt/clients/<t>/openvoiceui/plugins, which holds plugin route modules that are NOT in the
repo at all (bighead.py, seo.py, hermes.py, wan_video.py, routes.py of desktop-native /
ovui-core, crm.py, housecall_pro.py, client_history_admin.py). The guard passes on the repo
while the fleet runs unscanned blueprints.

REAL example (fixture = byte copy of the module loaded on danielle + test-dev, sha256
6f65d714..., `[Plugin:wan-video-studio] Registered blueprint: wan_video_bp` in both logs):
POST /api/wan-video (not an admin prefix: the agent key reaches it) does
`os.chmod(UPLOADS_DIR/'.wan-requests', 0o777)` BY NAME. The agent plants
`uploads/.wan-requests -> /app/routes` (or /app, /app/runtime/db, ...) and the server makes
that directory world-writable: the terminal sandbox (the boundary this PR creates) can then
replace /app/routes/*.py = code as the server on its next start.

Asserts the SAFE outcome: a FAIL means the attack worked / the scope gap is real.
"""
import importlib.util
import os
import stat
from pathlib import Path

import pytest
from flask import Flask

FIXTURE = Path(__file__).resolve().parent / 'review4_fixtures' / 'tenant_wan_video_6f65d714.py'


@pytest.fixture
def wan(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('review4_tenant_wan_video', str(FIXTURE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setattr(mod, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(mod, 'REQUESTS_DIR', uploads / '.wan-requests')
    app = Flask(__name__)
    app.register_blueprint(mod.wan_video_bp)
    return app.test_client(), uploads


def test_tenant_wan_video_plugin_chmods_through_planted_symlink(wan, tmp_path):
    client, uploads = wan
    victim = tmp_path / 'app-routes'           # stands in for /app/routes (0750 appuser)
    victim.mkdir()
    os.chmod(victim, 0o750)
    (uploads / '.wan-requests').symlink_to(victim)          # agent side: uploads/ is rw
    client.post('/api/wan-video', json={'image_url': '/uploads/x.png'})
    mode = stat.S_IMODE(victim.stat().st_mode)
    assert mode == 0o750, (f'server dir outside uploads/ is now {oct(mode)} (world-writable) '
                           'through uploads/.wan-requests (tenant plugin wan_video.py:58)')


def test_guard_scope_covers_tenant_installed_plugin_modules():
    """Every plugin route module a live tenant loads must be in the scanned repo tree with
    the same bytes; otherwise tests/agent_io_scan.py never saw it. Host-only: skips where
    /mnt/clients is not present (CI, the container harness)."""
    import hashlib
    import json
    clients = Path('/mnt/clients')
    if not clients.is_dir():
        pytest.skip('no /mnt/clients here')
    repo = Path(__file__).resolve().parent.parent
    retired = set()
    rf = Path('/mnt/system/base/retired-tenants.txt')
    if rf.is_file():
        retired = {l.strip() for l in rf.read_text().splitlines() if l.strip() and not l.startswith('#')}
    unscanned = set()
    for pj in sorted(clients.glob('*/openvoiceui/plugins/*/plugin.json')):
        tenant, pdir = pj.parts[3], pj.parent
        if tenant in retired:
            continue
        for r in json.loads(pj.read_text()).get('routes', []):
            mod = pdir / (r.get('module') or '')
            if not mod.is_file():
                continue
            ref = repo / 'plugins' / pdir.name / r['module']
            h = hashlib.sha256(mod.read_bytes()).hexdigest()
            if not ref.is_file() or hashlib.sha256(ref.read_bytes()).hexdigest() != h:
                unscanned.add(f'{pdir.name}/{r["module"]}@{h[:8]}')
    assert not unscanned, ('in-process plugin code loaded by live tenants that the guard never '
                           f'scanned ({len(unscanned)}): {sorted(unscanned)}')
