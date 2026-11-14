"""
The proactive-push channel has a socket the app always opens: /ws/push.

Measured 2026-11-14: across all 14 tenants with a .proactive-push.jsonl receipt
file (84 pushes since 2026-10-19), not one push reached a browser (delivered=0
on every row), and no tenant logged a single /ws/clawdbot request. The push rode
only on /ws/clawdbot, which src/app.js opens only from ModeManager.switchMode().
The default transport is already 'supertonic', so init never called
switchMode() and the socket never opened. Background-job results showed up only
at the next voice turn.

Now:
  * server.py serves /ws/push: push-only, no gateway bridge, gated by app.py's
    require_auth (it is NOT a public path) and by a Clerk user in the handler;
  * a push reaching a VISIBLE tab returns 1; a push reaching only hidden tabs
    is still delivered to them but returns 0, so the gateway still relays it
    through the tenant's own agent (a tab left open does not swallow it);
  * each frame carries an id so a tab with two sockets plays it once;
  * the socket sends keepalives while idle and unregisters when it closes.

server.py cannot be imported outside the container, so the push functions are
lifted out of its source with ast (same approach as
test_offline_completion_relay.py) — the code under test is server.py's own.
"""

import ast
import json
import logging
import queue
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "server.py"
APP_PY = ROOT / "app.py"

_NAMES = {
    "_push_clients_lock", "_push_clients", "_push_client_session",
    "_push_client_visible", "_push_receipt_lock", "_PUSH_RECEIPT_PATH",
    "_PUSH_KEEPALIVE_S",
    "_register_push_client", "_unregister_push_client", "_push_queue_for",
    "_note_push_client_session", "_set_push_client_visible",
    "_serve_push_socket", "push_proactive_message", "_write_push_receipt",
}


def _load(receipt_path):
    tree = ast.parse(SERVER_PY.read_text())
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _NAMES:
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in _NAMES for t in targets):
                body.append(node)
    found = {n.name for n in body if isinstance(n, ast.FunctionDef)}
    missing = {"_serve_push_socket", "_set_push_client_visible"} - found
    assert not missing, f"server.py has no {sorted(missing)} — no always-open push socket"
    ns = {
        "json": json, "re": re, "time": time, "queue": queue, "uuid": uuid,
        "threading": threading, "datetime": datetime, "Path": Path,
        "logger": logging.getLogger("test-push-socket"),
        "_generate_tts_b64": lambda text, voice=None: "QVVESU8=",
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SERVER_PY), "exec"), ns)
    ns["_PUSH_RECEIPT_PATH"] = receipt_path
    return ns


class FakeBrowserSocket:
    """Stands in for flask_sock's ws: receive() blocks until the browser sends."""

    def __init__(self):
        self._in = queue.Queue()
        self.sent = []
        self.closed = threading.Event()
        self._sent_cv = threading.Condition()

    # server side
    def receive(self):
        msg = self._in.get()
        if msg is None:
            raise ConnectionError("closed")
        return msg

    def send(self, data):
        if self.closed.is_set():
            raise ConnectionError("closed")
        with self._sent_cv:
            self.sent.append(json.loads(data))
            self._sent_cv.notify_all()

    def close(self):
        self.closed.set()

    # browser side
    def browser_send(self, obj):
        self._in.put(json.dumps(obj))

    def browser_close(self):
        self._in.put(None)

    def wait_for(self, pred, timeout=3.0):
        deadline = time.time() + timeout
        with self._sent_cv:
            while True:
                hit = [f for f in self.sent if pred(f)]
                if hit:
                    return hit
                left = deadline - time.time()
                if left <= 0:
                    raise AssertionError(f"no matching frame; sent={self.sent}")
                self._sent_cv.wait(left)


def _serve(ns, ws, keepalive_s=5.0):
    t = threading.Thread(target=ns["_serve_push_socket"], args=(ws,),
                         kwargs={"keepalive_s": keepalive_s}, daemon=True)
    t.start()
    ws.wait_for(lambda f: f["type"] == "push_ready")
    return t


def _wait_until(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _receipts(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_push_route_exists_and_is_not_a_public_path():
    src = SERVER_PY.read_text()
    m = re.search(r'@sock\.route\("/ws/push"\)\s*\ndef (\w+)\(ws\):(.*?)\n\n\n', src, re.S)
    assert m, "server.py must serve the /ws/push socket"
    handler = m.group(2)
    assert "_serve_push_socket(ws)" in handler
    assert "clerk_user_id" in handler, "the push socket must require an owner session"
    # /ws/clawdbot self-authenticates and is listed as public; /ws/push relies on
    # require_auth, so it must NOT be in the public list.
    assert "'/ws/push'" not in APP_PY.read_text()


def test_visible_tab_gets_the_push_and_counts_as_delivered(tmp_path):
    receipt = tmp_path / ".proactive-push.jsonl"
    ns = _load(receipt)
    ws = FakeBrowserSocket()
    t = _serve(ns, ws)

    ws.browser_send({"type": "visibility", "visible": True})
    assert _wait_until(lambda: ns["_push_client_visible"].get(ws) is True)

    assert ns["push_proactive_message"]("Takeoff done. [CANVAS:takeoff]", "main") == 1
    frame = ws.wait_for(lambda f: f["type"] == "proactive_message")[0]
    assert frame["text"] == "Takeoff done. [CANVAS:takeoff]"
    assert frame["audio"] == "QVVESU8="
    assert re.fullmatch(r"[0-9a-f]{32}", frame["id"])
    row = _receipts(receipt)[-1]
    assert row["delivered"] == 1 and row["clients"] == 1 and row["visible"] == 1

    ws.browser_close()
    t.join(3)
    assert not t.is_alive()


def test_hidden_tab_still_gets_it_but_the_gateway_still_relays(tmp_path):
    receipt = tmp_path / ".proactive-push.jsonl"
    ns = _load(receipt)
    ws = FakeBrowserSocket()
    t = _serve(ns, ws)  # never reports visibility -> treated as hidden

    assert ns["push_proactive_message"]("Report ready.", "main") == 0, \
        "a background tab must not count as the owner having seen it"
    ws.wait_for(lambda f: f["type"] == "proactive_message")
    row = _receipts(receipt)[-1]
    assert row["delivered"] == 1 and row["visible"] == 0

    ws.browser_send({"type": "visibility", "visible": True})
    assert _wait_until(lambda: ns["_push_client_visible"].get(ws) is True)
    assert ns["push_proactive_message"]("Second.", "main") == 1

    ws.browser_send({"type": "visibility", "visible": False})
    assert _wait_until(lambda: ns["_push_client_visible"].get(ws) is False)
    assert ns["push_proactive_message"]("Third.", "main") == 0

    ws.browser_close()
    t.join(3)


def test_idle_socket_sends_keepalives(tmp_path):
    ns = _load(tmp_path / "r.jsonl")
    ws = FakeBrowserSocket()
    t = _serve(ns, ws, keepalive_s=0.05)
    assert len(ws.wait_for(lambda f: f["type"] == "keepalive")) >= 1
    ws.browser_close()
    t.join(3)
    assert not t.is_alive()


def test_closed_socket_unregisters_and_push_reports_nobody(tmp_path):
    receipt = tmp_path / ".proactive-push.jsonl"
    ns = _load(receipt)
    ws = FakeBrowserSocket()
    t = _serve(ns, ws)
    assert ws in ns["_push_clients"]

    ws.browser_close()
    t.join(3)
    assert not t.is_alive(), "writer must exit when the browser goes away"
    assert ws not in ns["_push_clients"]
    assert ws not in ns["_push_client_visible"]
    assert ws.closed.is_set()

    assert ns["push_proactive_message"]("Nobody home.", "main") == 0
    assert _receipts(receipt)[-1]["clients"] == 0


def test_each_push_has_its_own_id(tmp_path):
    ns = _load(tmp_path / "r.jsonl")
    ws = object()
    q = ns["_register_push_client"](ws)  # /ws/clawdbot-style: counts as visible
    assert ns["push_proactive_message"]("a", "main") == 1
    assert ns["push_proactive_message"]("b", "main") == 1
    ids = {json.loads(q.get_nowait())["id"], json.loads(q.get_nowait())["id"]}
    assert len(ids) == 2
