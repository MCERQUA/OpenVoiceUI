"""Booking owner SMS carries the customer's contact details (2026-11-19).

The owner notice used to say only "<name> booked '<title>' — <time>." and dropped the phone,
email and address that the Cal.com/Cal.diy webhook payload carries, so the owner could not call
the customer back. Contact data toward the OWNER is never masked. Fixture = a Cal.com
BOOKING_CREATED payload shape (attendees[], responses.attendeePhoneNumber, responses.location),
fake values only.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        'owner_sms_booking', ROOT / 'plugins' / 'booking' / 'routes' / 'booking.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'booking-config.json').write_text(json.dumps({'notify_phone': '+15550100999'}))
    monkeypatch.setattr(mod, 'CONFIG_PATH', uploads / 'booking-config.json')
    monkeypatch.setattr(mod, 'NOTIFY_QUEUE_DIR', uploads / 'booking-sms-queue')
    return mod, uploads / 'booking-sms-queue'


PAYLOAD = {
    'title': 'Attic estimate', 'startTime': '2026-12-01T15:00:00Z',
    'attendees': [{'name': 'Zed Fakename', 'email': 'zed.fakename@example.org', 'timeZone': 'America/Toronto'}],
    'responses': {
        'name': {'value': 'Zed Fakename'},
        'attendeePhoneNumber': {'value': '+15550100123'},
        'location': {'value': 'attendeeInPerson', 'optionValue': '12 Fake Street, Nowhere ON'},
    },
}


def _queued(qdir):
    files = sorted(qdir.glob('*.json'))
    assert len(files) == 1, files
    return json.loads(files[0].read_text())['body']


def test_owner_sms_has_name_phone_email_address(monkeypatch, tmp_path):
    mod, qdir = _load(monkeypatch, tmp_path)
    mod.notify_sms('BOOKING_CREATED', PAYLOAD)
    body = _queued(qdir)
    for want in ('Zed Fakename', '+15550100123', 'zed.fakename@example.org', '12 Fake Street, Nowhere ON'):
        assert want in body, f'{want!r} missing from owner SMS: {body!r}'


def test_missing_fields_are_left_out(monkeypatch, tmp_path):
    mod, qdir = _load(monkeypatch, tmp_path)
    mod.notify_sms('BOOKING_CANCELLED', {'title': 'Attic estimate', 'attendees': [{'name': 'Zed Fakename'}]})
    body = _queued(qdir)
    assert body == "Zed Fakename cancelled 'Attic estimate'.", body
