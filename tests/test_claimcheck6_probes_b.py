"""Gate-4 re-check #6 probes, part b: the agent key's shell in the OVU container (/ws/terminal) reaches the db dir.

T1 reads the shipped code (the websocket cannot be driven by the Flask test client). HOLE probes assert the BAD
outcome: PASSED means the hole is real (round-6 convention).
T2 was REWRITTEN in round 7 (host brief): it drives server.py's terminal_websocket itself and asserts the spec outcome
(round-7 convention: FAILED means the hole is real). See its docstring.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, END_FIELDS, SBX, STAFF_ORIGIN, _ends, codes, dirs, h, world,
)
from tests.test_biz_crt_tools import _watch

ROOT = Path(__file__).resolve().parents[1]


def test_T1_HOLE_agent_key_opens_a_login_shell_in_the_ovu_container():
    src = (ROOT / 'server.py').read_text(encoding='utf-8')
    m = re.search(r'@sock\.route\("/ws/terminal"\)\ndef terminal_websocket\(ws\):(.*?)\n# ----', src, re.S)
    body = m.group(1)
    assert 'has_agent_key = bool(agent_key) and request.headers.get("X-Agent-Key") == agent_key' in body
    assert 'if not user_id and not has_agent_key:' in body          # agent key alone is enough
    assert 'pty.fork()' in body and 'os.execvp(shell, [shell, "-l"])' in body
    assert not re.search(r'setuid|setgid|chroot|unshare|seccomp', body)   # same uid as the app: reads the db dir
    app = (ROOT / 'app.py').read_text(encoding='utf-8')
    assert "'/ws/terminal'," in app                                # app gate lets the handler self-authenticate
    # the R1 design rests on the agent never reaching the db dir (routes/biz.py _surface_secret docstring)
    assert "The agent's container does not mount the db dir" in (ROOT / 'routes' / 'biz.py').read_text(encoding='utf-8')
    print('T1 /ws/terminal: X-Agent-Key -> pty.fork + login shell as the app uid; no uid drop / chroot')


def _mac(key, *parts):
    return hmac.new(key, '|'.join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()


class _FakeWS:
    """flask_sock's ws for the lifted handler: receive() blocks until the 'agent' sends."""

    def __init__(self):
        import queue
        import threading
        self._in, self.out, self._lock = queue.Queue(), [], threading.Lock()

    def receive(self, timeout=None):
        return self._in.get()

    def send(self, data):
        with self._lock:
            self.out.append(data)

    def close(self):
        pass

    def type(self, text):
        self._in.put(json.dumps({'type': 'input', 'data': text}))

    def hangup(self):
        self._in.put(None)

    def text(self):
        with self._lock:
            return ''.join(self.out)


def _terminal_handler(agent_key):
    """server.py's own terminal_websocket, lifted with ast (server.py cannot be imported outside the container)."""
    import ast
    import logging
    import os
    src = (ROOT / 'server.py').read_text(encoding='utf-8')
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == 'terminal_websocket':
            node.decorator_list = []
            break
    else:
        raise AssertionError('server.py has no terminal_websocket')

    class _Req:
        headers = {'X-Agent-Key': agent_key}
    ns = {'json': json, 'os': os, 'logger': logging.getLogger('t2-terminal'), 'request': _Req()}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'server.py', 'exec'), ns)
    return ns['terminal_websocket']


def test_T2_HOLE_the_agent_keys_terminal_never_reads_the_staff_surface_key(h, dirs, monkeypatch, tmp_path):
    """REWRITTEN in round 7 (host brief, gate-4 re-check #7 "T1/T2 pair"): the round-6 T2 read staff-surface.key from
    the test's db dir in-process, so it could never observe a fix. This one drives the REAL terminal path: server.py's
    terminal_websocket, opened with the agent key on a hosted tenant (CANVAS_REQUIRE_AUTH=true), and types the `cat`
    a prompt-injected agent would. Round-7 convention: this HOLE probe asserts the SPEC outcome (the key never comes
    back), so FAILED means the hole is real. It fails on df962ce (a login shell as the app uid prints the key; the
    forge leg below then reaches plain Meets) and passes with PR #597 (no helper here: the hosted terminal refuses;
    in the image the setuid helper drops to the sandbox uid, which deploy/terminal-sandbox/verify-image.sh proves)."""
    import threading
    import time
    import services.auth as auth_mod
    import services.paths as paths_mod
    w = world(h, endorsements=_ends(scheduled_projects=['Tempe Warehouse Retrofit']), party_name='T Two Inc')
    aid = w['asg']['id']
    assert codes(h.state(aid)) == ['WRONG_PROJECT']
    key_path = Path(dirs['a']) / 'staff-surface.key'
    key = key_path.read_bytes()
    assert len(key) == 32
    agent_key = 'r7-t2-agent-key-0123456789'
    monkeypatch.setenv('AGENT_API_KEY', agent_key)
    monkeypatch.setenv('CANVAS_REQUIRE_AUTH', 'true')
    monkeypatch.delenv('TERMINAL_SANDBOX', raising=False)
    monkeypatch.setattr(auth_mod, 'get_token_from_request', lambda *a, **k: None)
    monkeypatch.setattr(auth_mod, 'verify_clerk_token', lambda *a, **k: None)
    monkeypatch.setattr(paths_mod, 'CODE_WORKSPACE_DIR', tmp_path / 'code-workspace')
    try:
        import services.terminal_sandbox as ts
        monkeypatch.setattr(ts, 'SANDBOX_HELPER', str(tmp_path / 'no-helper-in-the-test-container'))
    except ImportError:                                                  # df962ce: no sandbox module at all
        pass
    ws = _FakeWS()
    t = threading.Thread(target=_terminal_handler(agent_key), args=(ws,), daemon=True)
    t.start()
    ws.type(f"od -An -tx1 -v '{key_path}' | tr -d ' \\n'; echo; echo __T2_EN''D__\n")
    end = time.time() + 20
    while time.time() < end and '__T2_END__' not in ws.text() and t.is_alive():
        time.sleep(0.05)
    ws.type('exit\n')
    ws.hangup()
    t.join(timeout=5)
    out = ws.text()
    leaked = key.hex() in out.replace('\r', '').replace('\n', '')
    print('T2 terminal answered', repr(out[-300:]), '| key bytes came back:', leaked)
    if leaked:                       # the forge leg (round 6): those bytes are full staff powers on the staff origin
        by = 'clerk:user_forged_by_agent'
        exp = int(time.time()) + 3600
        rnd = secrets.token_hex(12)
        cookie = f's2.{exp}.{rnd}.{by.encode().hex()}.{_mac(key, "session", "s2", STAFF_ORIGIN, by, exp, rnd)}'
        rnd2 = secrets.token_hex(12)
        page = f'v2.{exp}.{rnd2}.{_mac(key, "page", "v2", STAFF_ORIGIN, by, SBX, exp, rnd2)}'
        c = h.app.test_client()                                            # the agent's own HTTP client, no Clerk
        c.set_cookie('__Host-jb_staff', cookie, domain=STAFF_ORIGIN.split('//')[1])
        hdr = {'X-Biz-Sandbox': SBX, 'X-JB-Staff-Surface': page, 'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin'}
        e = c.get(f'{B}/crt_endorsement/{w["end"]["ai_ongoing"]["id"]}', headers=hdr, base_url=STAFF_ORIGIN).get_json()
        r = c.patch(f'{B}/crt_endorsement/{e["id"]}', json={'data': {'scheduled_projects': ['Mesa Clinic Expansion']}},
                    headers={**hdr, 'If-Match': str(e['version'])}, base_url=STAFF_ORIGIN)
        e = r.get_json()
        c.post(f'{B}/crt/confirm', json={'record_collection': 'crt_endorsement', 'record_id': e['id'],
                                         'record_version': e['version'], 'fields': END_FIELDS,
                                         'source_document_id': e['data']['document_id']},
               headers=hdr, base_url=STAFF_ORIGIN)
        c.post(f'{B}/crt/assignment/{aid}/review', json={}, headers=hdr, base_url=STAFF_ORIGIN)
        print('T2 forged staff from the terminal\'s bytes ->', h.state(aid)['state'])
    # spec (1.4.0 R1 / PR #597): the agent never reads the staff secret, by any route; the terminal included
    assert not leaked, 'the agent key\'s terminal printed staff-surface.key'
