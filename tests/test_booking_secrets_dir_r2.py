"""SEC-092 R2: the booking plugin keeps its Cal config + webhook secret in UPLOADS_DIR/.booking-secrets/.

The admin tenant binds the whole clients tree read-only and every OVU container runs as the same
uid/gid, so the host masks this one fixed DIRECTORY there (a per-file mask is detached by the rename
every writer does). Transition rule: read whichever of the new file and the old dotfile was written
LAST (a tenant still on the previous plugin, or a host script, may have saved only the old dotfile);
a tie goes to the new file; writes go to the new file only; the old dotfile is never rewritten.
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SECRET_NEW = 'n' * 64
SECRET_OLD = 'o' * 64


def _load(monkeypatch, tmp_path, without_safe_open):
    spec = importlib.util.spec_from_file_location(
        f'booking_r2_{int(without_safe_open)}_{id(tmp_path)}', ROOT / 'plugins' / 'booking' / 'routes' / 'booking.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    up = tmp_path / 'uploads'
    up.mkdir()
    monkeypatch.setattr(mod, 'CONFIG_PATH', up / '.booking-config.json')
    monkeypatch.setattr(mod, 'LEGACY_CONFIG_PATH', up / 'booking-config.json')
    monkeypatch.setattr(mod, 'WEBHOOK_SECRET_PATH', up / '.booking-webhook-secret')
    monkeypatch.setattr(mod, 'CAL_WEBHOOK_SECRET_ENV', '')
    if without_safe_open:
        monkeypatch.setattr(mod, '_Anchored', None)
    return mod, up


def _write(p, text, mtime):
    p.parent.mkdir(exist_ok=True)
    p.write_text(text)
    os.utime(p, (mtime, mtime))


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_newest_of_new_and_old_wins_tie_goes_to_new(monkeypatch, tmp_path, without_safe_open):
    mod, up = _load(monkeypatch, tmp_path, without_safe_open)
    new, old, legacy = up / '.booking-secrets' / 'booking-config.json', up / '.booking-config.json', up / 'booking-config.json'
    _write(legacy, json.dumps({'username': 'legacy'}), 1_000)
    assert mod._load_config() == {'username': 'legacy'} and mod._config_source() == 'booking-config.json'
    _write(old, json.dumps({'username': 'old'}), 2_000)
    assert mod._load_config() == {'username': 'old'}
    _write(new, json.dumps({'username': 'new'}), 2_000)          # tie -> new
    assert mod._load_config() == {'username': 'new'}
    assert mod._config_source() == '.booking-secrets/booking-config.json'
    os.utime(old, (3_000, 3_000))                                  # old saved later (previous plugin)
    assert mod._load_config() == {'username': 'old'} and mod._config_source() == '.booking-config.json'
    os.utime(new, (4_000, 4_000))
    assert mod._load_config() == {'username': 'new'}


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_webhook_secret_newest_wins_and_save_goes_to_new_only(monkeypatch, tmp_path, without_safe_open):
    mod, up = _load(monkeypatch, tmp_path, without_safe_open)
    new, old = up / '.booking-secrets' / 'booking-webhook-secret', up / '.booking-webhook-secret'
    assert mod._webhook_secret() == ''
    _write(old, SECRET_OLD + '\n', 2_000)
    assert mod._webhook_secret() == SECRET_OLD                     # pre-migration tenant keeps working
    _write(new, SECRET_NEW + '\n', 2_000)
    assert mod._webhook_secret() == SECRET_NEW                     # tie -> new
    os.utime(old, (3_000, 3_000))
    assert mod._webhook_secret() == SECRET_OLD
    mod._save_webhook_secret(SECRET_NEW)
    assert mod._webhook_secret() == SECRET_NEW
    assert old.read_text() == SECRET_OLD + '\n'                    # the old dotfile is never rewritten
    mod._save_webhook_secret('')                                   # a clear in the NEWEST file wins
    assert mod._webhook_secret() == ''


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_save_config_writes_only_the_new_file(monkeypatch, tmp_path, without_safe_open):
    mod, up = _load(monkeypatch, tmp_path, without_safe_open)
    old = up / '.booking-config.json'
    _write(old, json.dumps({'username': 'old', 'api_key': 'cal_old'}), 2_000)
    cfg = mod._load_config()
    cfg['notify_identity'] = 'setup'
    mod._save_config(cfg)
    assert json.loads((up / '.booking-secrets' / 'booking-config.json').read_text()) == {
        'username': 'old', 'api_key': 'cal_old', 'notify_identity': 'setup'}
    assert json.loads(old.read_text()) == {'username': 'old', 'api_key': 'cal_old'}
    assert mod._load_config()['notify_identity'] == 'setup'
    assert sorted(p.name for p in (up / '.booking-secrets').iterdir()) == ['booking-config.json']


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_symlinked_new_file_is_ignored_in_favour_of_the_old_dotfile(monkeypatch, tmp_path, without_safe_open):
    mod, up = _load(monkeypatch, tmp_path, without_safe_open)
    victim = tmp_path / 'victim.json'
    victim.write_text(json.dumps({'username': 'planted'}))
    _write(up / '.booking-config.json', json.dumps({'username': 'old'}), 2_000)
    (up / '.booking-secrets').mkdir()
    (up / '.booking-secrets' / 'booking-config.json').symlink_to(victim)
    os.utime(up / '.booking-secrets' / 'booking-config.json', (9_000, 9_000), follow_symlinks=False)
    assert mod._load_config() == {'username': 'old'}
