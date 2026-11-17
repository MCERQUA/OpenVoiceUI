"""Review 5 of PR #597 (non-author), R4-B2: the shipped plugins/wan-video-studio module,
probed from the threat model: an agent that holds the agent key (POST /api/wan-video,
GET /api/wan-video/result/<rid>, GET /api/wan-video/gallery are not admin prefixes) and can
create, rename and symlink anything in uploads/ at any moment, including between two of
the server's syscalls. Both code paths are probed: with services.safe_open (the PR image)
and the plugin's own fd-based fallback (the plugin is rolled to the tenant mount BEFORE the
image, so the fallback is what runs first in production).

Every probe asserts the SECURE outcome: FAILED means the hole is real.
"""
import importlib.util
import os
import stat
import threading
from pathlib import Path

import pytest
from flask import Flask

PLUGIN = Path(__file__).resolve().parent.parent / 'plugins' / 'wan-video-studio' / 'routes' / 'wan_video.py'


def _mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


@pytest.fixture(params=['safe_open', 'fallback'])
def wan(request, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(f'review5_wan_{request.param}', str(PLUGIN))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    os.chmod(uploads, 0o777)
    monkeypatch.setattr(mod, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(mod, 'REQUESTS_DIR', uploads / '.wan-requests')
    if request.param == 'fallback':
        monkeypatch.setattr(mod, '_Anchored', None)
    else:
        assert mod._Anchored is not None, 'safe_open path not reached'
    app = Flask(__name__)
    app.register_blueprint(mod.wan_video_bp)
    return mod, app.test_client(), uploads


def _victim_dir(tmp_path):
    v = tmp_path / 'app-routes'                  # stands in for /app/routes (0750 appuser)
    v.mkdir()
    (v / 'server.py').write_text('ORIGINAL')
    os.chmod(v, 0o750)
    return v


def test_planted_request_dir_symlink_neither_chmods_nor_writes_outside(wan, tmp_path):
    mod, client, uploads = wan
    victim = _victim_dir(tmp_path)
    (uploads / '.wan-requests').symlink_to(victim)
    client.post('/api/wan-video', json={'image_url': '/uploads/x.png'})
    assert _mode(victim) == 0o750, f'server dir outside uploads/ re-moded to {oct(_mode(victim))}'
    assert sorted(os.listdir(victim)) == ['server.py'], f'files written outside: {os.listdir(victim)}'


def test_request_dir_swapped_for_a_symlink_after_ensure_is_refused(wan, tmp_path, monkeypatch):
    """The agent wins the window between _ensure_requests_dir() and the request write."""
    mod, client, uploads = wan
    victim = _victim_dir(tmp_path)
    real_ensure = mod._ensure_requests_dir

    def ensure_then_swap():
        real_ensure()
        os.rename(uploads / '.wan-requests', uploads / '.moved')
        (uploads / '.wan-requests').symlink_to(victim)

    monkeypatch.setattr(mod, '_ensure_requests_dir', ensure_then_swap)
    client.post('/api/wan-video', json={'image_url': '/uploads/x.png'})
    assert sorted(os.listdir(victim)) == ['server.py'], f'request written outside: {os.listdir(victim)}'
    assert _mode(victim) == 0o750


def test_planted_result_symlink_does_not_leak_a_server_file(wan, tmp_path):
    mod, client, uploads = wan
    secret = tmp_path / 'vault.json'
    secret.write_text('{"status": "ok", "api_key": "SERVER-SECRET"}')
    (uploads / '.wan-requests').mkdir()
    (uploads / '.wan-requests' / 'abc.result.json').symlink_to(secret)
    r = client.get('/api/wan-video/result/abc')
    assert b'SERVER-SECRET' not in r.data, 'result read followed a planted symlink'


def test_result_read_through_a_symlinked_request_dir_does_not_leak(wan, tmp_path):
    mod, client, uploads = wan
    d = tmp_path / 'private'
    d.mkdir()
    (d / 'abc.result.json').write_text('{"status": "ok", "api_key": "SERVER-SECRET"}')
    (uploads / '.wan-requests').symlink_to(d)
    r = client.get('/api/wan-video/result/abc')
    assert b'SERVER-SECRET' not in r.data, 'result read followed a symlinked .wan-requests dir'


def test_planted_result_fifo_does_not_hang_the_server(wan):
    mod, client, uploads = wan
    (uploads / '.wan-requests').mkdir()
    os.mkfifo(uploads / '.wan-requests' / 'abc.result.json')
    out = {}
    t = threading.Thread(target=lambda: out.setdefault('r', client.get('/api/wan-video/result/abc')),
                         daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), 'GET /api/wan-video/result hung on a planted FIFO (single-process server)'


def test_oversized_result_is_not_slurped(wan):
    mod, client, uploads = wan
    (uploads / '.wan-requests').mkdir()
    big = uploads / '.wan-requests' / 'abc.result.json'
    with open(big, 'wb') as f:
        f.truncate(512 * 1024 * 1024)            # sparse 512 MiB
    r = client.get('/api/wan-video/result/abc')
    assert len(r.data) < 2 * 1024 * 1024, 'whole planted result returned'


def test_rid_cannot_climb_out_of_the_request_dir(wan, tmp_path):
    mod, client, uploads = wan
    (uploads / '.wan-requests').mkdir()
    (uploads / 'x.result.json').write_text('{"status": "ok", "leak": "UPLOADS-ROOT"}')
    for rid in ('..%2Fx', '../x', '.%2E/x', '%2e%2e'):
        r = client.get(f'/api/wan-video/result/{rid}')
        assert b'UPLOADS-ROOT' not in r.data, f'rid {rid!r} climbed out of .wan-requests'


def test_gallery_lists_no_symlink_and_no_outside_metadata(wan, tmp_path):
    mod, client, uploads = wan
    outside = tmp_path / 'db.sqlite'
    outside.write_bytes(b'x' * 12345)
    (uploads / 'ai-gen-wan-evil.mp4').symlink_to(outside)
    (uploads / 'ai-gen-wan-ok.mp4').write_bytes(b'mp4')
    r = client.get('/api/wan-video/gallery').get_json()
    names = [v['name'] for v in r['videos']]
    assert names == ['ai-gen-wan-ok.mp4'], f'gallery listed {names}'
    assert all(v['size'] != 12345 for v in r['videos']), 'outside file size leaked'


def test_legit_queue_still_writes_a_0666_request_in_a_0777_dir(wan):
    """Positive control: the host drain (uid mike) must still be able to write the result."""
    mod, client, uploads = wan
    r = client.post('/api/wan-video', json={'image_url': '/uploads/x.png', 'prompt': 'p'})
    assert r.status_code == 200, r.data
    rid = r.get_json()['rid']
    assert _mode(uploads / '.wan-requests') == 0o777
    assert _mode(uploads / '.wan-requests' / f'{rid}.json') == 0o666
    (uploads / '.wan-requests' / f'{rid}.result.json').write_text('{"url": "/uploads/v.mp4"}')
    assert client.get(f'/api/wan-video/result/{rid}').get_json()['url'] == '/uploads/v.mp4'
