"""Review 3 of PR #597 (non-author): /api/conversation image_path -> .image-intel sidecar.

The handler takes a request-supplied absolute image_path, resolve()s it, checks only that a
component is named 'uploads', then reads and WRITES `<img parent>/.image-intel/<img name>.json`
BY NAME. uploads/ is agent-writable, so the agent plants `uploads/.image-intel -> <any dir
the server can write>` (static symlink, no race) and the server overwrites `<dir>/<name>.json`.

The AST guard never sees it: image_path comes from the request, not from a ROOT_NAMES root.
Asserts the SAFE outcome: a FAIL means the attack worked.
"""
import json
from pathlib import Path

import pytest
from flask import Flask


class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {'candidates': [{'content': {'parts': [{'text': 'ATTACKER-CONTROLLED IMAGE TEXT'}]}}]}


@pytest.fixture
def conv(monkeypatch, tmp_path):
    import requests
    import routes.conversation as conv_mod
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key-for-probe')
    # keep the rest of the turn offline: no gateway, no LLM fallback key
    monkeypatch.setenv('CLAWDBOT_GATEWAY_URL', 'ws://127.0.0.1:9')
    for k in ('ZAI_API_KEY', 'ZAI_FALLBACK_API_KEY', 'GLM_API_KEY', 'GROQ_API_KEY'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResp())
    app = Flask(__name__)
    app.register_blueprint(conv_mod.conversation_bp)
    return app.test_client()


def test_image_intel_sidecar_write_follows_symlink_out_of_uploads(conv, tmp_path):
    uploads = tmp_path / 'runtime' / 'uploads'
    uploads.mkdir(parents=True)
    # a server-owned dir outside the cell, holding a server state file `profile.json`
    victim_dir = tmp_path / 'runtime' / 'profiles'
    victim_dir.mkdir()
    victim = victim_dir / 'profile.json'
    victim.write_text('{"llm": "ORIGINAL-SERVER-CONFIG"}')
    # agent side (uploads/ is mounted rw in openclaw): an image named `profile`, and the
    # sidecar dir replaced by a symlink to the victim dir
    (uploads / 'profile').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
    (uploads / '.image-intel').symlink_to(victim_dir)

    conv.post('/api/conversation', json={'message': 'what is in this picture',
                                          'image_path': str(uploads / 'profile')})

    body = victim.read_text()
    assert 'ORIGINAL-SERVER-CONFIG' in body, (
        'server state file outside uploads/ was overwritten through uploads/.image-intel: '
        + body[:160].replace('\n', ' '))


def test_guard_does_not_see_the_sidecar_write():
    """The PR's own scanner: the by-name write above is not in its findings at all."""
    import sys
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_io_scan import Scanner
    src = (root / 'routes' / 'conversation.py').read_text()
    line = next(i for i, l in enumerate(src.splitlines(), 1) if '_intel_path.write_text(' in l)
    found = {(rel, ln) for rel, _q, _s, ln in Scanner(root).findings()}
    assert ('routes/conversation.py', line) in found, (
        f'routes/conversation.py:{line} _intel_path.write_text (agent-writable uploads/, by name) '
        'is invisible to tests/agent_io_scan.py')
