"""Booking webhook fails CLOSED and is the ONLY booking path open without a login (2026-11-19).

Before: POST /api/booking/webhook sat behind the Clerk wall (Cal's servers got 401, so no booking
ever reached an owner), and the handler itself FAILED OPEN: with no webhook secret it accepted any
body as verified=false and still queued the owner SMS. Now:
  - no secret configured            -> refused, nothing logged, notify_sms never called
  - unsigned / wrongly signed body  -> refused, nothing logged, notify_sms never called
  - correctly signed body           -> accepted, logged, fanned out
  - the login exemption is exactly POST /api/booking/webhook; every sibling is still 401.
  - config, secret and event log are dotfiles (never served by /uploads/); a legacy
    booking-config.json is still read and the first save moves it to the dotfile.
Fixture payloads are fake.
"""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SECRET = 'whsec-test-0123456789abcdef'
BODY = json.dumps({
    'triggerEvent': 'BOOKING_CREATED', 'createdAt': '2026-11-19T00:00:00Z',
    'payload': {'title': 'Attic estimate', 'startTime': '2026-12-01T15:00:00Z',
                'attendees': [{'name': 'Zed Fakename', 'email': 'zed.fakename@example.org'}]},
}).encode()


def _sign(body, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _load(monkeypatch, tmp_path, secret=SECRET):
    spec = importlib.util.spec_from_file_location(
        f'booking_failclosed_{id(tmp_path)}', ROOT / 'plugins' / 'booking' / 'routes' / 'booking.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / '.booking-config.json').write_text(json.dumps({'notify_phone': '+15550100999'}))
    if secret:
        (uploads / '.booking-webhook-secret').write_text(secret + '\n')
    monkeypatch.setattr(mod, 'CONFIG_PATH', uploads / '.booking-config.json')
    monkeypatch.setattr(mod, 'LEGACY_CONFIG_PATH', uploads / 'booking-config.json', raising=False)
    monkeypatch.setattr(mod, 'WEBHOOK_LOG_PATH', uploads / '.booking-events.jsonl')
    monkeypatch.setattr(mod, 'NOTIFY_QUEUE_DIR', uploads / '.booking-notify-queue')
    monkeypatch.setattr(mod, 'WEBHOOK_SECRET_PATH', uploads / '.booking-webhook-secret', raising=False)
    monkeypatch.setattr(mod, 'CAL_WEBHOOK_SECRET_ENV', '')
    sms_calls = []
    monkeypatch.setattr(mod, 'notify_sms', lambda ev, p: sms_calls.append((ev, p)))
    return mod, uploads, sms_calls


@pytest.fixture()
def authed_app(monkeypatch):
    """The real app.py Clerk gate, with the plugin blueprint registered the way server.py's
    load_plugins() does it (after create_app)."""
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', 'agent-key-for-tests')
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})

    def register(mod):
        app.register_blueprint(mod.booking_bp)
        return app.test_client()
    return register


def _post(client, body=BODY, sig=None):
    headers = {'Content-Type': 'application/json'}
    if sig is not None:
        headers['x-cal-signature-256'] = sig
    return client.post('/api/booking/webhook', data=body, headers=headers)


def test_unsigned_post_is_refused_and_sends_no_sms(monkeypatch, tmp_path, authed_app):
    mod, uploads, sms = _load(monkeypatch, tmp_path)
    r = _post(authed_app(mod))
    assert r.status_code in (401, 403), r.status_code
    assert (r.get_json() or {}).get('code') == 'invalid_signature', r.get_json()
    assert sms == []
    assert not (uploads / '.booking-events.jsonl').exists()


def test_wrong_signature_is_refused(monkeypatch, tmp_path, authed_app):
    mod, uploads, sms = _load(monkeypatch, tmp_path)
    r = _post(authed_app(mod), sig=_sign(BODY, 'not-the-secret'))
    assert r.status_code == 401, r.status_code
    assert (r.get_json() or {}).get('code') == 'invalid_signature', r.get_json()
    assert sms == []
    assert not (uploads / '.booking-events.jsonl').exists()


def test_no_secret_configured_is_refused(monkeypatch, tmp_path, authed_app):
    mod, uploads, sms = _load(monkeypatch, tmp_path, secret=None)
    client = authed_app(mod)
    for sig in (None, '', _sign(BODY, ''), 'no-secret-provided'):
        r = _post(client, sig=sig)
        assert r.status_code == 403, (sig, r.status_code)
        assert (r.get_json() or {}).get('code') == 'webhook_not_configured', r.get_json()
    assert sms == []
    assert not (uploads / '.booking-events.jsonl').exists()


def test_correct_signature_is_accepted_and_logged(monkeypatch, tmp_path, authed_app):
    mod, uploads, sms = _load(monkeypatch, tmp_path)
    r = _post(authed_app(mod), sig=_sign(BODY))
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    assert r.get_json() == {'ok': True, 'event': 'BOOKING_CREATED', 'verified': True}
    lines = (uploads / '.booking-events.jsonl').read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec['event'] == 'BOOKING_CREATED' and rec['verified'] is True
    assert rec['payload']['title'] == 'Attic estimate'
    assert [ev for ev, _ in sms] == ['BOOKING_CREATED']


def test_login_exemption_is_exactly_the_webhook_path(monkeypatch, tmp_path, authed_app):
    mod, uploads, sms = _load(monkeypatch, tmp_path)
    client = authed_app(mod)
    # the exact path + method reaches the handler without a login
    assert _post(client, sig=_sign(BODY)).status_code == 200
    # every sibling still meets the Clerk wall
    for method, path in (
        ('GET', '/api/booking/webhook'),
        ('POST', '/api/booking/webhook/'),
        ('POST', '/api/booking/webhookx'),
        ('POST', '/api/booking/webhook/extra'),
        ('GET', '/api/booking/config'),
        ('POST', '/api/booking/config'),
        ('GET', '/api/booking/bookings'),
        ('POST', '/api/booking/bookings'),
        ('GET', '/api/booking/manage'),
    ):
        r = client.open(path, method=method, data=BODY,
                        headers={'Content-Type': 'application/json', 'x-cal-signature-256': _sign(BODY)})
        assert r.status_code == 401, (method, path, r.status_code)
        assert (r.get_json() or {}).get('code') == 'auth_required', (method, path, r.get_json())


def _bare_client(mod):
    from flask import Flask
    a = Flask(__name__)
    a.register_blueprint(mod.booking_bp)
    return a.test_client()


def test_secret_is_never_written_into_booking_config(monkeypatch, tmp_path):
    # the secret lives in its own dotfile, never in a config file
    mod, uploads, sms = _load(monkeypatch, tmp_path, secret=None)
    r = _bare_client(mod).post('/api/booking/config', json={'webhook_secret': SECRET})
    assert r.status_code == 200
    assert 'webhook_secret' not in json.loads((uploads / '.booking-config.json').read_text())
    assert (uploads / '.booking-webhook-secret').read_text().strip() == SECRET


def test_config_secret_and_event_log_are_dotfiles():
    # routes/static_files.py serve_upload never serves a dotfile in any UPLOADS_AUTH_MODE; the old
    # names (booking-config.json, booking-events.jsonl) were downloadable from /uploads/ (SEC-092).
    spec = importlib.util.spec_from_file_location(
        'booking_dotfiles', ROOT / 'plugins' / 'booking' / 'routes' / 'booking.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ('CONFIG_PATH', 'WEBHOOK_LOG_PATH', 'WEBHOOK_SECRET_PATH', 'NOTIFY_QUEUE_DIR'):
        assert getattr(mod, name).name.startswith('.'), (name, getattr(mod, name))
    src = (ROOT / 'routes' / 'static_files.py').read_text()
    assert "any(part.startswith('.') for part in Path(filename).parts)" in src


def test_legacy_config_is_read_and_first_save_moves_it_to_the_dotfile(monkeypatch, tmp_path):
    mod, uploads, sms = _load(monkeypatch, tmp_path, secret=None)
    (uploads / '.booking-config.json').rename(uploads / 'booking-config.json.unused')
    legacy = {'api_key': 'cal_legacy_key_0000', 'username': 'legacy-user', 'notify_phone': ''}
    (uploads / 'booking-config.json').write_text(json.dumps(legacy))
    assert mod._load_config() == legacy
    r = _bare_client(mod).post('/api/booking/config', json={'notify_identity': 'setup'})
    assert r.status_code == 200
    moved = json.loads((uploads / '.booking-config.json').read_text())
    assert moved['api_key'] == 'cal_legacy_key_0000' and moved['notify_identity'] == 'setup'
    assert json.loads((uploads / 'booking-config.json').read_text()) == legacy   # never rewritten
