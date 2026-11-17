"""Review 4 of PR #597: R4-B1 (writers re-moded existing files) and R4-B2 (the
tenant-mounted wan-video-studio plugin) fixed at class level, with the legitimate
behaviour kept.

The reviewer's probes (tests/test_zz_review4_*.py, byte-identical) assert the attacks
fail; these pin the semantics the fix relies on and cover the plugin's fallback path
(an OVU image without services/safe_open, which the probe does not reach).
"""
import importlib.machinery
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
WAN = ROOT / 'plugins' / 'wan-video-studio' / 'routes' / 'wan_video.py'
WAN_ORIG = ROOT / 'tests' / 'review4_fixtures' / 'tenant_wan_video_6f65d714.tenant-copy.txt'


def _mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


# ── R4-B1: safe_open writers keep an existing file's mode ───────────────────────────


def test_write_beneath_sets_mode_only_on_a_file_it_creates(tmp_path):
    from services.safe_open import Anchored
    a = Anchored(tmp_path, 'new.json')
    a.write_text('{}', mode=0o600)
    assert _mode(tmp_path / 'new.json') == 0o600, 'a created file gets exactly `mode`'
    os.chmod(tmp_path / 'new.json', 0o666)
    a.write_text('{"x": 1}')                       # default mode=0o644: must NOT apply
    assert _mode(tmp_path / 'new.json') == 0o666
    assert (tmp_path / 'new.json').read_text() == '{"x": 1}', 'existing file truncated + rewritten'
    a.write_bytes(b'1')
    assert (tmp_path / 'new.json').read_bytes() == b'1'


def test_write_beneath_still_refuses_a_symlink_at_the_name(tmp_path):
    from services.safe_open import Anchored, UnsafePath
    victim = tmp_path / 'victim'
    victim.write_text('ORIGINAL')
    os.chmod(victim, 0o600)
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'f.json').symlink_to(victim)
    with pytest.raises(UnsafePath):
        Anchored(root, 'f.json').write_text('PWNED')
    assert victim.read_text() == 'ORIGINAL' and _mode(victim) == 0o600


def test_beneath_file_keeps_an_existing_mode(tmp_path):
    from services.safe_open import BeneathFile, open_dir_beneath
    dfd = open_dir_beneath(tmp_path, None)
    try:
        BeneathFile(dfd, 'a.wav', mode=0o640).write_bytes(b'one')
        assert _mode(tmp_path / 'a.wav') == 0o640
        os.chmod(tmp_path / 'a.wav', 0o666)
        BeneathFile(dfd, 'a.wav').write_bytes(b'two')
        assert _mode(tmp_path / 'a.wav') == 0o666 and (tmp_path / 'a.wav').read_bytes() == b'two'
    finally:
        os.close(dfd)


def test_atomic_write_mode_none_keeps_the_replaced_files_bits(tmp_path):
    from services.safe_open import Anchored
    a = Anchored(tmp_path, 's.json')
    a.atomic_write_text('{}', mode=None)
    assert _mode(tmp_path / 's.json') == 0o644, 'no existing file: 0644'
    os.chmod(tmp_path / 's.json', 0o666 | stat.S_ISUID)
    a.atomic_write_text('{"a": 1}', mode=None)
    assert _mode(tmp_path / 's.json') == 0o666, 'permission bits kept, setuid/setgid/sticky dropped'
    a.atomic_write_text('{"a": 2}')                # explicit/default mode is still exact
    assert _mode(tmp_path / 's.json') == 0o644
    assert json.loads((tmp_path / 's.json').read_text()) == {'a': 2}


def test_atomic_write_mode_none_ignores_a_planted_symlinks_target(tmp_path):
    from services.safe_open import Anchored
    victim = tmp_path / 'victim'
    victim.write_text('ORIGINAL')
    os.chmod(victim, 0o600)
    root = tmp_path / 'root'
    root.mkdir()
    (root / 's.json').symlink_to(victim)
    Anchored(root, 's.json').atomic_write_text('{}', mode=None)
    assert not (root / 's.json').is_symlink() and _mode(root / 's.json') == 0o644
    assert victim.read_text() == 'ORIGINAL' and _mode(victim) == 0o600


def test_version_restore_keeps_a_0644_page_0644(tmp_path, monkeypatch):
    """The other direction of R4-B1: a restore keeps whatever mode the page has."""
    import services.canvas_versioning as cv
    monkeypatch.setattr(cv, 'CANVAS_PAGES_DIR', tmp_path)
    page = tmp_path / 'p.html'
    page.write_text('<html>v2</html>')
    os.chmod(page, 0o644)
    (tmp_path / '.versions').mkdir()
    (tmp_path / '.versions' / 'p.1700000000.html').write_bytes(b'<html>v1</html>')
    assert cv.restore_version('p', 1700000000)
    assert page.read_bytes() == b'<html>v1</html>' and _mode(page) == 0o644


# ── R4-B2: the wan-video-studio plugin (tenant-mounted, now in the repo) ────────────


def _wan(monkeypatch, uploads, without_safe_open, path=WAN):
    name = f'r4_wan_{int(without_safe_open)}_{path.name.replace(".", "_")}'
    spec = importlib.util.spec_from_file_location(
        name, str(path), loader=importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(mod, 'REQUESTS_DIR', uploads / '.wan-requests')
    if without_safe_open:   # an older OVU image: the plugin's own fd-based fallback
        monkeypatch.setattr(mod, '_Anchored', None)
    app = Flask(__name__)
    app.register_blueprint(mod.wan_video_bp)
    return mod, app.test_client()


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_wan_queue_still_works(monkeypatch, tmp_path, without_safe_open):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    _, c = _wan(monkeypatch, uploads, without_safe_open)
    r = c.post('/api/wan-video', json={'image_url': '/uploads/x.png', 'prompt': 'waves'})
    assert r.status_code == 200, r.get_json()
    rid = r.get_json()['rid']
    reqdir = uploads / '.wan-requests'
    assert _mode(reqdir) == 0o777, 'the host drain (another uid) must write results here'
    req = reqdir / f'{rid}.json'
    assert _mode(req) == 0o666
    body = json.loads(req.read_text())
    assert body['id'] == rid and body['image'] == 'x.png' and body['prompt'] == 'waves'
    assert sorted(p.name for p in reqdir.iterdir()) == [f'{rid}.json'], 'temp file left behind'
    # a second request reuses the existing dir
    assert c.post('/api/wan-video', json={'image_url': '/uploads/y.png'}).status_code == 200
    # the drain's result is served back
    assert c.get(f'/api/wan-video/result/{rid}').get_json() == {'status': 'pending'}
    (reqdir / f'{rid}.result.json').write_text(json.dumps({'id': rid, 'url': '/uploads/v.mp4'}))
    assert c.get(f'/api/wan-video/result/{rid}').get_json() == {
        'id': rid, 'url': '/uploads/v.mp4', 'status': 'ok'}


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_wan_planted_request_dir_symlink_is_refused(monkeypatch, tmp_path, without_safe_open):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    victim = tmp_path / 'app-routes'
    victim.mkdir()
    os.chmod(victim, 0o750)
    (uploads / '.wan-requests').symlink_to(victim)
    _, c = _wan(monkeypatch, uploads, without_safe_open)
    r = c.post('/api/wan-video', json={'image_url': '/uploads/x.png'})
    assert r.status_code == 500
    assert _mode(victim) == 0o750 and list(victim.iterdir()) == []


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_wan_planted_result_symlink_is_not_read(monkeypatch, tmp_path, without_safe_open):
    uploads = tmp_path / 'uploads'
    (uploads / '.wan-requests').mkdir(parents=True)
    secret = tmp_path / 'secret.json'
    secret.write_text(json.dumps({'token': 'SERVER-SECRET'}))
    (uploads / '.wan-requests' / 'abc.result.json').symlink_to(secret)
    _, c = _wan(monkeypatch, uploads, without_safe_open)
    r = c.get('/api/wan-video/result/abc')
    out = r.get_data(as_text=True)
    # Anchored: a symlink is not a file (still pending); fallback: the no-follow open refuses
    assert 'SERVER-SECRET' not in out and r.get_json()['status'] in ('pending', 'error')


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_wan_gallery_lists_files_not_symlinks(monkeypatch, tmp_path, without_safe_open):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'ai-gen-wan-1.mp4').write_bytes(b'v' * 10)
    (tmp_path / 'big').write_bytes(b'x' * 1234)
    (uploads / 'ai-gen-wan-2.mp4').symlink_to(tmp_path / 'big')
    _, c = _wan(monkeypatch, uploads, without_safe_open)
    d = c.get('/api/wan-video/gallery').get_json()
    assert [v['name'] for v in d['videos']] == ['ai-gen-wan-1.mp4'] and d['videos'][0]['size'] == 10


def test_wan_orig_tenant_copy_is_the_vulnerable_one(monkeypatch, tmp_path):
    """Positive control for the reviewer's probe: the byte copy of the module live tenants
    run today (sha256 6f65d714...) DOES chmod a dir outside uploads/ through the planted
    symlink; the repo copy (the probe's fixture now points at it) does not."""
    import hashlib
    assert hashlib.sha256(WAN_ORIG.read_bytes()).hexdigest().startswith('6f65d714')
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    victim = tmp_path / 'app-routes'
    victim.mkdir()
    os.chmod(victim, 0o750)
    (uploads / '.wan-requests').symlink_to(victim)
    _, c = _wan(monkeypatch, uploads, False, path=WAN_ORIG)
    c.post('/api/wan-video', json={'image_url': '/uploads/x.png'})
    assert _mode(victim) == 0o777


def test_probe_fixture_is_the_repo_plugin():
    fx = ROOT / 'tests' / 'review4_fixtures' / 'tenant_wan_video_6f65d714.py'
    assert fx.is_symlink() and fx.resolve() == WAN.resolve(), (
        'the review-4 probe must exercise the plugin file the repo ships and the rollout copies')
