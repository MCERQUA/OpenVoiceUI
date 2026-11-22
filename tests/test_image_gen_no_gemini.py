"""Gemini = vision/text only. Generating images with Gemini is impossible.

Operator rule 2026-11-22: "make sure that we can use gemini for VISION and never use it for
generation ... restrict the tools so that it wouldnt be even possible to generate and only to
use it to look at images." There is no env flag that re-enables generation.

The Google transport is stubbed BELOW the guard (requests.Session.send's original), so these
tests exercise the real guard — both the explicit check in each caller and the process-wide
Session.send hook — and record what would have left the process. No network is used.
"""
from __future__ import annotations

import base64
import io
import json
import logging

import pytest
import requests
from flask import Flask

from services import gemini_guard as gg
import routes.image_gen as ig
import routes.icons as icons
import routes.vision as vision


def _png_b64(color=(0, 255, 0)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def _response(url, body, status=200):
    r = requests.models.Response()
    r.status_code = status
    r.url = url
    r._content = json.dumps(body).encode()
    r.headers['Content-Type'] = 'application/json'
    return r


@pytest.fixture
def wire(monkeypatch):
    """Replace the network layer UNDER the guard hook. Everything that reaches here
    passed the guard and would have been sent."""
    assert gg.transport_guard_installed(), 'importing services.gemini_guard must install the hook'
    sent = []

    def fake_network(self, request, **kw):
        sent.append(request.url.split('?')[0])
        if 'googleapis.com' in request.url:
            return _response(request.url, {'candidates': [{'content': {'parts': [
                {'text': 'a green square'}]}}]})
        return _response(request.url, {'data': [{'b64_json': _png_b64()}]})

    # Same logic as the production hook, delegating to the fake network.
    def guarded(self, request, **kw):
        url = request.url or ''
        if gg.is_google_genai_host(requests.utils.urlparse(url).hostname or ''):
            gg.check_gemini_request(url, request.body)
        return fake_network(self, request, **kw)
    guarded._gemini_guard = True
    monkeypatch.setattr(requests.Session, 'send', guarded)

    monkeypatch.setattr(ig, 'HF_TOKEN', 'hf-test-token')
    monkeypatch.setattr(ig, 'GEMINI_KEY', 'gemini-test-key')
    monkeypatch.setattr(icons, 'GEMINI_API_KEY', 'gemini-test-key')
    monkeypatch.setenv('GEMINI_API_KEY', 'gemini-test-key')
    monkeypatch.setattr(ig, 'log_spend', lambda *a, **k: None)
    monkeypatch.setattr(ig, '_save_generated_image', lambda mime, data: '/uploads/test.png')
    import services.metered_spend as ms
    monkeypatch.setattr(ms, 'log_spend', lambda *a, **k: None)
    return sent


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(ig.image_gen_bp)
    return app.test_client()


def _google(urls):
    return [u for u in urls if 'googleapis.com' in u]


G = 'https://generativelanguage.googleapis.com/v1beta/models'
VISION_BODY = {'contents': [{'parts': [{'text': 'what is this'},
                                       {'inline_data': {'mime_type': 'image/png', 'data': 'AAAA'}}]}]}


# ── the guard itself ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url,payload,why', [
    (f'{G}/gemini-3.1-flash-image-preview:generateContent', VISION_BODY, 'image model'),
    (f'{G}/gemini-2.5-flash-image:generateContent', VISION_BODY, 'image model'),
    (f'{G}/gemini-2.0-flash-exp-image-generation:generateContent', VISION_BODY, 'image model'),
    (f'{G}/nano-banana-pro-preview:generateContent',
     {'contents': [], 'generationConfig': {'responseModalities': ['IMAGE', 'TEXT']}}, 'IMAGE modality'),
    (f'{G}/gemini-2.5-flash:generateContent',
     {'contents': [], 'generationConfig': {'responseModalities': ['TEXT', 'image']}}, 'IMAGE modality'),
    (f'{G}/gemini-2.5-flash:generateContent',
     {'contents': [], 'generation_config': {'response_modalities': 'IMAGE'}}, 'IMAGE modality (snake)'),
    (f'{G}/gemini-2.5-flash:generateContent',
     {'contents': [], 'generationConfig': {'imageConfig': {'aspectRatio': '1:1'}}}, 'imageConfig'),
    (f'{G}/imagen-4.0-generate-001:predict', {'instances': [{'prompt': 'x'}]}, ':predict / imagen'),
    (f'{G}/gemini-2.5-flash:predict', {'instances': [{'prompt': 'x'}]}, ':predict'),
    (f'{G}/veo-3.0-generate-001:predictLongRunning', {}, 'veo'),
    (f'{G}/gemini-2.5-flash:batchGenerateContent', {}, 'method not allowlisted'),
    ('https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/l/publishers/google/models/'
     'imagen-3.0-generate-002:predict', {}, 'vertex imagen'),
    (f'{G}/gemini-2.5-flash:generateContent', b'\xff\xfe', 'non-UTF-8 body'),
])
def test_guard_refuses_generation(url, payload, why, caplog):
    with caplog.at_level(logging.ERROR, logger='services.gemini_guard'):
        with pytest.raises(gg.GeminiGenerationBlocked):
            gg.check_gemini_request(url + '?key=SECRET-NOT-LOGGED', payload)
    assert any('REFUSED' in r.getMessage() for r in caplog.records), why
    assert 'SECRET-NOT-LOGGED' not in caplog.text


@pytest.mark.parametrize('url,payload', [
    (f'{G}/gemini-flash-latest:generateContent', VISION_BODY),
    (f'{G}/gemini-pro-latest:generateContent', VISION_BODY),
    (f'{G}/gemini-2.5-flash:generateContent', json.dumps(VISION_BODY).encode()),
    (f'{G}/gemini-2.5-flash:streamGenerateContent', VISION_BODY),
    (f'{G}/gemini-2.0-flash:generateContent',
     {'contents': [], 'generationConfig': {'maxOutputTokens': 512, 'responseModalities': ['TEXT']}}),
    ('https://router.huggingface.co/nscale/v1/images/generations', {'model': 'x'}),   # not Google
])
def test_guard_allows_vision_and_text(url, payload):
    gg.check_gemini_request(url, payload)   # no raise


def test_guard_hook_covers_a_raw_requests_call(wire):
    """A caller that never calls check_gemini_request is still refused by the hook."""
    with pytest.raises(gg.GeminiGenerationBlocked):
        requests.post(f'{G}/gemini-2.5-flash-image:generateContent', json=VISION_BODY)
    with pytest.raises(gg.GeminiGenerationBlocked):
        requests.post(f'{G}/imagen-4.0-generate-001:predict', json={'instances': []})
    assert wire == []
    r = requests.post(f'{G}/gemini-flash-latest:generateContent', json=VISION_BODY)
    assert r.status_code == 200 and len(_google(wire)) == 1


def test_real_hook_is_what_production_installs():
    """The production wrapper (not the test fixture) refuses before delegating, so this
    real Session.send never reaches the network."""
    assert getattr(requests.Session.send, '_gemini_guard', False)
    with pytest.raises(gg.GeminiGenerationBlocked):
        requests.Session().send(requests.Request(
            'POST', f'{G}/gemini-3.1-flash-image-preview:generateContent', json=VISION_BODY).prepare())


def test_no_reenable_flag_exists(monkeypatch):
    for name in ('ALLOW_GEMINI_IMAGE_GEN', 'GEMINI_IMAGE_GEN', 'ICON_GEN_PROVIDER'):
        monkeypatch.setenv(name, 'on' if name != 'ICON_GEN_PROVIDER' else 'gemini')
    with pytest.raises(gg.GeminiGenerationBlocked):
        gg.check_gemini_request(f'{G}/gemini-3.1-flash-image-preview:generateContent', VISION_BODY)
    assert icons._icon_provider() == 'hf'


# ── /api/image-gen ──────────────────────────────────────────────────────────────

def test_default_model_is_not_google():
    assert ig.DEFAULT_MODEL.startswith('hf:')


def test_bare_request_uses_hf_and_never_google(wire, client):
    r = client.post('/api/image-gen', json={'prompt': 'a red barn'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['model'] == ig.DEFAULT_MODEL
    assert wire and not _google(wire)


@pytest.mark.parametrize('model', ['gemini-3.1-flash-image-preview', 'nano-banana-pro-preview',
                                   'imagen-4.0-generate-001', 'gemini-2.5-flash'])
def test_google_model_on_route_is_never_sent_to_google(monkeypatch, wire, client, model):
    monkeypatch.setenv('ALLOW_GEMINI_IMAGE_GEN', 'on')   # the old flag does nothing now
    r = client.post('/api/image-gen', json={'prompt': 'a red barn', 'model': model})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['model'] == ig.DEFAULT_MODEL and body['fallback_from'] == model
    assert not _google(wire)


def test_google_edit_with_reference_images_is_refused(wire, client):
    r = client.post('/api/image-gen', json={
        'prompt': 'make it blue', 'model': 'gemini-3.1-flash-image-preview',
        'images': [{'mime_type': 'image/png', 'data': _png_b64()}]})
    assert r.status_code == 403
    assert r.get_json()['code'] == 'gemini_image_gen_disabled'
    assert wire == []


def test_gemini_generation_functions_refuse_before_sending(wire):
    with pytest.raises(gg.GeminiGenerationBlocked):
        ig._generate_gemini('gemini-3.1-flash-image-preview', 'x', [])
    with pytest.raises(gg.GeminiGenerationBlocked):
        ig._generate_gemini('gemini-2.5-flash', 'x', [])        # IMAGE modality in its payload
    with pytest.raises(gg.GeminiGenerationBlocked):
        ig._generate_imagen('imagen-4.0-generate-001', 'x')
    assert wire == []


def test_config_reports_gemini_off(wire, client):
    assert client.get('/api/image-gen/config').get_json() == {
        'default_model': ig.DEFAULT_MODEL, 'gemini_enabled': False, 'hf_enabled': True}


def test_prompt_enhance_text_call_still_allowed(wire, client):
    r = client.post('/api/image-gen/enhance', json={'idea': 'a foam rig'})
    assert r.status_code == 200, r.get_json()
    assert len(_google(wire)) == 1


def test_rejected_hf_key_is_named_not_a_bare_502(monkeypatch, wire, client):
    """Measured 2026-11-22: the fleet HF_TOKEN answers 401 at the nscale router."""
    def deny(self, request, **kw):
        wire.append(request.url)
        return _response(request.url, {'error': 'Invalid username or password.'}, status=401)
    deny._gemini_guard = True
    monkeypatch.setattr(requests.Session, 'send', deny)
    r = client.post('/api/image-gen', json={'prompt': 'a red barn'})
    assert r.status_code == 502
    body = r.get_json()
    assert body['code'] == 'provider_key_rejected' and body['upstream_status'] == 401
    assert 'HF_TOKEN' in body['error'] and 'Invalid username' in body['error']


# ── icons ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def icon_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(icons, '_ensure_generated_dir', lambda: tmp_path)
    return tmp_path


def test_icon_generates_on_hf_never_google(monkeypatch, wire, icon_dir):
    monkeypatch.setenv('ICON_GEN_PROVIDER', 'gemini')
    monkeypatch.setenv('ALLOW_GEMINI_IMAGE_GEN', 'on')
    out = icons.generate_icon_image('a wrench', name='wrench', allow_reuse=False)
    assert (icon_dir / out['filename']).read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
    assert wire and not _google(wire)


def test_icon_gemini_helper_refuses(wire):
    with pytest.raises(icons.IconGenerationError) as ei:
        icons._generate_icon_gemini('x')
    assert ei.value.status == 403
    assert wire == []


# ── vision keeps working ────────────────────────────────────────────────────────

def test_vision_call_goes_through(wire):
    out = vision._gemini_vision('AAAA', 'image/png', 'what is this', 'gemini-flash-latest')
    assert out == 'a green square'
    assert len(_google(wire)) == 1 and wire[0].endswith('gemini-flash-latest:generateContent')


def test_vision_refuses_an_image_generation_model(wire):
    """A VISION_MODEL env pointed at a *-image model must not become a generation path."""
    with pytest.raises(gg.GeminiGenerationBlocked):
        vision._gemini_vision('AAAA', 'image/png', 'draw a cat', 'gemini-2.5-flash-image')
    assert wire == []
