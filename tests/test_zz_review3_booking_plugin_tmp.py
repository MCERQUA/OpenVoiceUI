"""Review 3 of PR #597 (non-author): in-process PLUGIN routes are outside the guard's scope.

plugins/booking/routes/booking.py (byte-identical copy installed in /app/plugins on 18 tenants,
sha256 f8542cf08524...) is loaded into the server process by services/plugins.py and
registers /api/booking/* (not an admin prefix: the agent key reaches it). _save_config()
writes a PREDICTABLE temp `uploads/booking-config.json.tmp` by name, then renames it -- the
exact class review 2 found in _write_page_replacing. uploads/ is agent-writable: the agent
pre-plants that name as a symlink and POST /api/booking/config writes agent-chosen JSON into
any file the server can write (/app/*.py, vault, the 0700 private dir). No race needed.
Asserts the SAFE outcome; a FAIL means the attack worked.
"""
import importlib.util
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent.parent


def _load_booking(monkeypatch, uploads):
    spec = importlib.util.spec_from_file_location(
        'review3_booking', ROOT / 'plugins' / 'booking' / 'routes' / 'booking.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'CONFIG_PATH', uploads / 'booking-config.json')
    monkeypatch.setattr(mod, 'WEBHOOK_LOG_PATH', uploads / 'booking-events.jsonl')
    app = Flask(__name__)
    bp = next(v for v in vars(mod).values() if type(v).__name__ == 'Blueprint')
    app.register_blueprint(bp)
    return app.test_client()


def test_booking_config_tmp_symlink_writes_outside_uploads(monkeypatch, tmp_path):
    uploads = tmp_path / 'uploads'; uploads.mkdir()
    victim = tmp_path / 'app' / 'server.py'; victim.parent.mkdir(); victim.write_text('ORIGINAL SERVER CODE\n')
    (uploads / 'booking-config.json.tmp').symlink_to(victim)          # planted by the agent
    client = _load_booking(monkeypatch, uploads)
    client.post('/api/booking/config', json={'api_url': 'https://agent-chosen.example'})
    body = victim.read_text()
    assert body == 'ORIGINAL SERVER CODE\n', f'/app/server.py rewritten through uploads/: {body[:120]!r}'


def test_guard_scope_includes_in_process_plugin_routes():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_io_scan import Scanner
    found = {rel for rel, *_ in Scanner(ROOT).findings()}
    assert any(r.startswith('plugins/') for r in found), \
        'tests/agent_io_scan.py never scans plugins/ (SCAN = routes, services, server.py, app.py)'
