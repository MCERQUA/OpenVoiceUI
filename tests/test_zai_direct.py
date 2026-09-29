"""
tests/test_zai_direct.py — Z.AI direct-call A/B fallback (services/zai_direct.py).

Covers the 2026-09-09 defect: routes/chat.py and routes/conversation.py called
Z.AI directly with ONLY ZAI_API_KEY and no fallback to ZAI_FALLBACK_API_KEY
when account A hit its weekly cap (HTTP 429 / error 1310).
"""

from unittest.mock import MagicMock, patch

import pytest

from services.zai_direct import zai_messages_post


def _mk_response(status_code, text=''):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


@patch.dict('os.environ', {'ZAI_API_KEY': 'key-a', 'ZAI_FALLBACK_API_KEY': 'key-b'})
@patch('services.zai_direct.requests.post')
def test_429_then_fallback_succeeds(mock_post):
    mock_post.side_effect = [
        _mk_response(429, '{"error":{"code":1310,"message":"Weekly Limit Exhausted"}}'),
        _mk_response(200, '{"content":[{"text":"ok"}]}'),
    ]

    resp = zai_messages_post({'model': 'glm-5-turbo', 'messages': []}, timeout=30)

    assert mock_post.call_count == 2
    first_headers = mock_post.call_args_list[0].kwargs['headers']
    second_headers = mock_post.call_args_list[1].kwargs['headers']
    assert first_headers['x-api-key'] == 'key-a'
    assert second_headers['x-api-key'] == 'key-b'
    assert resp.status_code == 200


@patch.dict('os.environ', {'ZAI_API_KEY': 'key-a', 'ZAI_FALLBACK_API_KEY': 'key-b'})
@patch('services.zai_direct.requests.post')
def test_200_first_try_no_fallback_call(mock_post):
    mock_post.return_value = _mk_response(200, '{"content":[{"text":"ok"}]}')

    resp = zai_messages_post({'model': 'glm-5-turbo', 'messages': []}, timeout=30)

    assert mock_post.call_count == 1
    assert mock_post.call_args.kwargs['headers']['x-api-key'] == 'key-a'
    assert resp.status_code == 200


@patch.dict('os.environ', {'ZAI_API_KEY': 'key-a', 'ZAI_FALLBACK_API_KEY': ''}, clear=False)
@patch('services.zai_direct.requests.post')
def test_429_no_fallback_env_propagates_429(mock_post):
    mock_post.return_value = _mk_response(429, '{"error":{"code":1310}}')

    resp = zai_messages_post({'model': 'glm-5-turbo', 'messages': []}, timeout=30)

    assert mock_post.call_count == 1
    assert resp.status_code == 429


@patch.dict('os.environ', {'ZAI_API_KEY': '', 'ZAI_FALLBACK_API_KEY': ''}, clear=False)
def test_no_keys_configured_raises():
    with pytest.raises(RuntimeError):
        zai_messages_post({'model': 'glm-5-turbo', 'messages': []}, timeout=30)
