"""The credits check must call sunoapi.org's real endpoint and read a numeric `data`."""
from unittest import mock

import pytest


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p


@pytest.fixture
def suno(app_context=None):
    import routes.suno as s
    return s


def _call(s, payload, status=200):
    seen = {}

    def fake_get(url, **kw):
        seen['url'] = url
        return _Resp(status, payload)

    with mock.patch.object(s.http_requests, 'get', side_effect=fake_get):
        from flask import Flask
        app = Flask(__name__)
        with app.app_context():
            out = s._action_credits().get_json()
    return seen.get('url', ''), out


def test_uses_generate_credit_path(suno):
    url, out = _call(suno, {'code': 200, 'data': 12.5})
    assert url.endswith('/api/v1/generate/credit')
    assert out['action'] == 'credits' and out['credits'] == 12.5


def test_zero_credits_is_not_an_error(suno):
    _, out = _call(suno, {'code': 200, 'data': 0.0})
    assert out['action'] == 'credits' and out['credits'] == 0.0


def test_provider_error_code_is_reported(suno):
    _, out = _call(suno, {'code': 401, 'msg': 'bad key'})
    assert out['action'] == 'error' and 'bad key' in out['response']


def test_http_error_is_reported(suno):
    _, out = _call(suno, {}, status=404)
    assert out['action'] == 'error' and '404' in out['response']
