"""Review 3 of PR #597: the fixes for B2 (conversation image sidecar) and B3 (booking
plugin temp files) keep the legitimate behaviour and refuse the planted symlinks.

The reviewer's probes (tests/test_zz_review3_*.py) assert the attack fails; these assert
the feature still works with the SAME uploads dir the server uses (the probes point the
request outside the real uploads mount, which the fix refuses outright).
"""
import importlib.util
import json
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent


# ── B2: /api/conversation image_path -> uploads/.image-intel/<name>.json ─────────────


class _FakeGemini:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {'candidates': [{'content': {'parts': [{'text': 'A RED BARN'}]}}]}


@pytest.fixture
def conv(monkeypatch, tmp_path):
    import requests
    import routes.conversation as conv_mod
    uploads = tmp_path / 'runtime' / 'uploads'
    uploads.mkdir(parents=True)
    monkeypatch.setattr(conv_mod, 'UPLOADS_DIR', uploads)
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key-for-test')
    monkeypatch.setenv('CLAWDBOT_GATEWAY_URL', 'ws://127.0.0.1:9')
    for k in ('ZAI_API_KEY', 'ZAI_FALLBACK_API_KEY', 'GLM_API_KEY', 'GROQ_API_KEY'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeGemini())
    app = Flask(__name__)
    app.register_blueprint(conv_mod.conversation_bp)
    return app.test_client(), uploads


def _image(uploads, name='barn.png'):
    p = uploads / name
    p.write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
    return p


def test_uploaded_image_still_gets_its_sidecar(conv):
    client, uploads = conv
    img = _image(uploads)
    client.post('/api/conversation', json={'message': 'what is this', 'image_path': str(img)})
    side = uploads / '.image-intel' / 'barn.png.json'
    assert side.is_file(), 'legit upload lost its .image-intel sidecar'
    rec = json.loads(side.read_text())
    assert rec['analysis']['description'] == 'A RED BARN'
    assert rec['size'] == img.stat().st_size and rec['file'] == 'barn.png'


def test_sidecar_dir_symlink_inside_uploads_is_refused(conv, tmp_path):
    client, uploads = conv
    victim_dir = tmp_path / 'runtime' / 'profiles'
    victim_dir.mkdir()
    victim = victim_dir / 'profile.json'
    victim.write_text('{"llm": "ORIGINAL"}')
    _image(uploads, 'profile')
    (uploads / '.image-intel').symlink_to(victim_dir)
    client.post('/api/conversation', json={'message': 'x', 'image_path': str(uploads / 'profile')})
    assert victim.read_text() == '{"llm": "ORIGINAL"}'


def test_image_symlink_inside_uploads_is_not_read(conv, tmp_path, monkeypatch):
    client, uploads = conv
    secret = tmp_path / 'secret.png'
    secret.write_bytes(b'SERVER-SECRET')
    (uploads / 'leak.png').symlink_to(secret)
    sent = []
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **k: sent.append(k) or _FakeGemini())
    client.post('/api/conversation', json={'message': 'x', 'image_path': str(uploads / 'leak.png')})
    assert not any('U0VSVkVSLVNFQ1JFVA' in json.dumps(k.get('json', {})) for k in sent), \
        'a symlinked upload was read and sent to the vision API'


def test_image_path_outside_uploads_is_refused(conv, tmp_path):
    client, uploads = conv
    other = tmp_path / 'elsewhere' / 'uploads'
    other.mkdir(parents=True)
    _image(other)
    client.post('/api/conversation', json={'message': 'x', 'image_path': str(other / 'barn.png')})
    assert not (other / '.image-intel').exists()


# ── B3: booking plugin config / event log / notify queue ─────────────────────────────


def _booking(monkeypatch, uploads, without_safe_open):
    spec = importlib.util.spec_from_file_location(
        f'r3_booking_{int(without_safe_open)}', ROOT / 'plugins' / 'booking' / 'routes' / 'booking.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'CONFIG_PATH', uploads / 'booking-config.json')
    monkeypatch.setattr(mod, 'WEBHOOK_LOG_PATH', uploads / 'booking-events.jsonl')
    monkeypatch.setattr(mod, 'NOTIFY_QUEUE_DIR', uploads / '.booking-notify-queue')
    if without_safe_open:   # an older OVU image: the plugin's own fd-based fallback
        monkeypatch.setattr(mod, '_Anchored', None)
    return mod


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_booking_config_roundtrip_and_symlinks_refused(monkeypatch, tmp_path, without_safe_open):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    mod = _booking(monkeypatch, uploads, without_safe_open)
    victim = tmp_path / 'server.py'
    victim.write_text('ORIGINAL\n')

    mod._save_config({'api_url': 'https://cal.example'})
    assert mod._load_config() == {'api_url': 'https://cal.example'}
    # SEC-092 R2: the config is saved inside the fixed .booking-secrets/ dir
    sdir = uploads / '.booking-secrets'
    assert [p.name for p in uploads.iterdir()] == ['.booking-secrets'], 'temp file left behind'
    assert [p.name for p in sdir.iterdir()] == ['booking-config.json'], 'temp file left behind'

    # the final name planted as a symlink: replaced (never written through), never read
    (sdir / 'booking-config.json').unlink()
    (sdir / 'booking-config.json').symlink_to(victim)
    assert mod._load_config() == {}
    mod._save_config({'api_url': 'https://agent.example'})
    assert victim.read_text() == 'ORIGINAL\n'
    assert not (sdir / 'booking-config.json').is_symlink()

    # the DIRECTORY planted as a symlink: never read through, never written through
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'booking-config.json').write_text('{"api_url": "https://planted.example"}')
    sdir.rename(tmp_path / 'secrets-moved-aside')
    sdir.symlink_to(elsewhere)
    assert mod._load_config() == {}
    with pytest.raises(OSError):
        mod._save_config({'api_url': 'https://agent.example'})
    assert (elsewhere / 'booking-config.json').read_text() == '{"api_url": "https://planted.example"}'
    assert sorted(p.name for p in elsewhere.iterdir()) == ['booking-config.json']
    sdir.unlink()

    # event log + notify queue through planted symlinks
    log = uploads / 'booking-events.jsonl'
    log.symlink_to(victim)
    mod._log_event({'e': 1})
    (uploads / '.booking-notify-queue').symlink_to(tmp_path)
    mod._save_config({'notify_phone': '+15550000000'})
    mod.notify_sms('BOOKING_CREATED', {'title': 't'})
    assert victim.read_text() == 'ORIGINAL\n'
    assert not any(p.suffix == '.json' for p in tmp_path.iterdir()), 'queue file escaped uploads/'


@pytest.mark.parametrize('without_safe_open', [False, True])
def test_booking_event_log_and_queue_still_work(monkeypatch, tmp_path, without_safe_open):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    mod = _booking(monkeypatch, uploads, without_safe_open)
    mod._log_event({'e': 1})
    mod._log_event({'e': 2})
    assert [json.loads(x) for x in (uploads / 'booking-events.jsonl').read_text().splitlines()] == \
        [{'e': 1}, {'e': 2}]
    mod._save_config({'notify_phone': '+15550000000'})
    mod.notify_sms('BOOKING_CREATED', {'title': 'Estimate', 'attendees': [{'name': 'Pat'}]})
    q = list((uploads / '.booking-notify-queue').iterdir())
    assert len(q) == 1 and q[0].suffix == '.json' and not q[0].name.startswith('.')
    assert 'Pat booked' in json.loads(q[0].read_text())['body']
