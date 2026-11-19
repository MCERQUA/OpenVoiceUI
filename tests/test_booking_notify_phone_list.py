"""notify_phone accepts a string or a list (co-owners); one queued SMS per distinct number.
The send path is the queue-file write (host drain sends); nothing here sends a real SMS."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = {'title': 'Estimate', 'attendees': [{'name': 'Zed Fakename'}]}


def _run(monkeypatch, tmp_path, notify_phone):
    spec = importlib.util.spec_from_file_location(
        'notify_list_booking', ROOT / 'plugins' / 'booking' / 'routes' / 'booking.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    up = tmp_path / 'uploads'
    up.mkdir(parents=True)
    (up / 'booking-config.json').write_text(json.dumps({'notify_phone': notify_phone}))
    monkeypatch.setattr(mod, 'CONFIG_PATH', up / 'booking-config.json')
    monkeypatch.setattr(mod, 'NOTIFY_QUEUE_DIR', up / 'booking-sms-queue')
    mod.notify_sms('BOOKING_CREATED', PAYLOAD)
    qdir = up / 'booking-sms-queue'
    return [json.loads(f.read_text())['notify_phone'] for f in sorted(qdir.glob('*.json'))] if qdir.exists() else []


def test_list_sends_one_per_number(monkeypatch, tmp_path):
    assert sorted(_run(monkeypatch, tmp_path, ['+15550100001', '+15550100002'])) == ['+15550100001', '+15550100002']


def test_string_sends_one(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, '+15550100001') == ['+15550100001']


def test_empty_sends_none(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, []) == []
    assert _run(monkeypatch, tmp_path / 'b', '') == []


def test_duplicates_deduplicated(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, ['+15550100001', ' +15550100001 ', '+15550100002', '']) == ['+15550100001', '+15550100002']
