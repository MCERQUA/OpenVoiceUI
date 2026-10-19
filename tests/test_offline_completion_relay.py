"""
Offline subagent completions are recorded and relayed through the tenant's
own agent.

A voice-started background subagent that finishes after the owner left is
announced back to the parent session; the orphan continuation collects the
parent's reply and calls server.push_proactive_message(). With no browser
connected that function used to return early with NO receipt, so the result
vanished without a trace (0 .proactive-push.jsonl files fleet-wide; one tenant lost
4 completed jobs incl. a client deliverable, another 12).

Now:
  * push_proactive_message writes a delivered=0 receipt (with session + run
    id) and returns 0 when no browser is connected;
  * the gateway then hands the result to the tenant's own agent in a
    dedicated session, at most once per subagent runId, and never for a
    relay-originated turn.

server.py is a monolith that cannot be imported outside the container (it
creates /app/plugins at import), so the push functions are lifted out of its
source with ast and exercised directly — the code under test is server.py's
own, unmodified.
"""

import ast
import asyncio
import json
import logging
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

import services.gateways.openclaw as gw
from services.gateways.openclaw import EventDispatcher, GatewayConnection

SERVER_PY = Path(__file__).resolve().parent.parent / "server.py"

_PUSH_NAMES = {
    "_push_clients_lock", "_push_clients", "_push_client_session",
    "_push_receipt_lock", "_PUSH_RECEIPT_PATH",
    "_register_push_client", "_unregister_push_client",
    "_note_push_client_session", "push_proactive_message", "_write_push_receipt",
}


def _load_push_module(receipt_path, tts_calls):
    """Exec server.py's proactive-push definitions in an isolated namespace."""
    tree = ast.parse(SERVER_PY.read_text())
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _PUSH_NAMES:
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in _PUSH_NAMES for t in targets):
                body.append(node)

    def fake_tts(text, voice=None):
        tts_calls.append(text)
        return "QVVESU8="

    ns = {
        "json": json, "re": re, "time": time, "queue": queue,
        "threading": threading, "datetime": datetime, "Path": Path,
        "logger": logging.getLogger("test-proactive-push"),
        "_generate_tts_b64": fake_tts,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SERVER_PY), "exec"), ns)
    ns["_PUSH_RECEIPT_PATH"] = receipt_path
    return ns


def _receipts(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# (a) / (b) server.py push_proactive_message
# ---------------------------------------------------------------------------

class TestProactivePushReceipt:
    def test_no_browser_writes_delivered_zero_receipt(self, tmp_path):
        receipt = tmp_path / ".proactive-push.jsonl"
        tts = []
        ns = _load_push_module(receipt, tts)

        result = ns["push_proactive_message"](
            "Your site takeoff is done. [CANVAS:site-takeoff]",
            "main", run_id="run-abc")

        rows = _receipts(receipt)
        assert len(rows) == 1, "an undelivered push must leave a receipt"
        row = rows[0]
        assert row["delivered"] == 0
        assert row["clients"] == 0
        assert row["session_key"] == "main"
        assert row["run_id"] == "run-abc"
        assert row["text"].startswith("Your site takeoff is done.")
        assert result == 0
        assert tts == [], "no audio is generated for a push nobody can hear"

    def test_connected_browser_unchanged(self, tmp_path):
        receipt = tmp_path / ".proactive-push.jsonl"
        tts = []
        ns = _load_push_module(receipt, tts)
        ws = object()
        q = ns["_register_push_client"](ws)

        result = ns["push_proactive_message"]("Done. [CANVAS:report]", "main")

        frame = json.loads(q.get_nowait())
        assert frame["type"] == "proactive_message"
        assert frame["text"] == "Done. [CANVAS:report]"
        assert frame["audio"] == "QVVESU8="
        assert frame["sessionKey"] == "main"
        assert tts == ["Done."]
        rows = _receipts(receipt)
        assert len(rows) == 1
        assert rows[0]["delivered"] == 1 and rows[0]["clients"] == 1
        assert result == 1

    def test_connected_browser_on_other_session_counts_zero(self, tmp_path):
        receipt = tmp_path / ".proactive-push.jsonl"
        ns = _load_push_module(receipt, [])
        ws = object()
        q = ns["_register_push_client"](ws)
        ns["_note_push_client_session"](ws, "other-tab")

        assert ns["push_proactive_message"]("x", "main") == 0
        assert q.empty()
        assert _receipts(receipt)[0]["delivered"] == 0


# ---------------------------------------------------------------------------
# (c) gateway relay through the tenant's own agent
# ---------------------------------------------------------------------------

AGENT = "openvoiceui"
CANON_MAIN = "agent:openvoiceui:main"
RELAY_WIRE_KEY = "agent:openvoiceui:offline-relay"


class FakeWS:
    def __init__(self, on_send=None):
        self.sent = []
        self.on_send = on_send
        self.close_code = None

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if self.on_send is not None:
            asyncio.ensure_future(self.on_send(frame))


def _conn():
    conn = GatewayConnection()
    conn._dispatcher = EventDispatcher()

    async def reply(frame):
        # Answer only the continuation nudge (chat-*), like the gateway would.
        if frame.get("method") != "chat.send" or not frame["id"].startswith("chat-"):
            return
        cid = frame["id"][len("chat-"):]
        d = conn._dispatcher
        await d._route({"type": "res", "id": frame["id"], "ok": True,
                        "payload": {"runId": "cont-run", "status": "started"}})
        await d._route({"type": "event", "event": "agent", "payload": {
            "runId": "cont-run", "sessionKey": CANON_MAIN, "stream": "assistant",
            "data": {"text": "Your takeoff is ready. [CANVAS:site-takeoff]"}}})
        await d._route({"type": "event", "event": "chat", "payload": {
            "runId": "cont-run", "sessionKey": CANON_MAIN, "state": "final"}})

    conn._ws = FakeWS(reply)
    conn._connected = True
    conn._default_agent_id = AGENT
    return conn


def _relays(conn):
    return [f for f in conn._ws.sent
            if f.get("method") == "chat.send" and f["id"].startswith("relay-")]


_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def fast_sleep(monkeypatch):
    async def _instant(_s, *a, **k):
        await _REAL_SLEEP(0)
    monkeypatch.setattr(gw.asyncio, "sleep", _instant)


@pytest.fixture
def handler(monkeypatch):
    calls = []
    state = {"delivered": 0}

    def _h(text, session_key, run_id=None):
        calls.append((text, session_key, run_id))
        return state["delivered"]

    monkeypatch.setattr(gw, "_PROACTIVE_HANDLER", _h)
    return calls, state


async def _settle(conn, n_calls, calls, timeout=3.0):
    """Wait for the push thread (and any relay it schedules) to finish."""
    deadline = time.time() + timeout
    while time.time() < deadline and len(calls) < n_calls:
        await _REAL_SLEEP(0.01)
    for _ in range(20):
        await _REAL_SLEEP(0.01)


class TestOfflineRelay:
    def test_offline_completion_relayed_once_per_run_id(self, fast_sleep, handler):
        calls, state = handler
        conn = _conn()

        async def go():
            await conn._run_orphan_continuation("main", ("sub-run-1",))
            await _settle(conn, 1, calls)
            await conn._run_orphan_continuation("main", ("sub-run-1",))
            await _settle(conn, 2, calls)

        asyncio.run(asyncio.wait_for(go(), timeout=15))

        assert [c[2] for c in calls] == ["sub-run-1", "sub-run-1"]
        relays = _relays(conn)
        assert len(relays) == 1, "a run id is relayed at most once"
        params = relays[0]["params"]
        assert params["sessionKey"] == RELAY_WIRE_KEY
        assert gw._OFFLINE_RELAY_MARKER in params["message"]
        assert "The owner is offline." in params["message"]
        assert "Your takeoff is ready. [CANVAS:site-takeoff]" in params["message"]

    def test_new_run_id_relays_again(self, fast_sleep, handler):
        calls, _ = handler
        conn = _conn()

        async def go():
            await conn._run_orphan_continuation("main", ("sub-run-1",))
            await _settle(conn, 1, calls)
            await conn._run_orphan_continuation("main", ("sub-run-2",))
            await _settle(conn, 2, calls)

        asyncio.run(asyncio.wait_for(go(), timeout=15))
        assert len(_relays(conn)) == 2

    def test_browser_received_it_no_relay(self, fast_sleep, handler):
        calls, state = handler
        state["delivered"] = 1
        conn = _conn()

        async def go():
            await conn._run_orphan_continuation("main", ("sub-run-1",))
            await _settle(conn, 1, calls)

        asyncio.run(asyncio.wait_for(go(), timeout=15))
        assert len(calls) == 1
        assert _relays(conn) == []

    def test_relay_session_turn_never_continues_or_relays(self, fast_sleep, handler):
        calls, _ = handler
        conn = _conn()

        async def go():
            await conn._run_orphan_continuation(gw._OFFLINE_RELAY_SESSION, ("sub-run-9",))
            await _settle(conn, 1, calls, timeout=0.3)

        asyncio.run(asyncio.wait_for(go(), timeout=15))
        assert conn._ws.sent == [], "a relay turn must not be nudged or relayed"
        assert calls == []

    def test_marked_text_is_never_relayed(self):
        conn = _conn()
        text = gw._OFFLINE_RELAY_MSG.format(summary="anything")
        sent = asyncio.run(conn._relay_offline_completion(text, "main", ("r1",)))
        assert sent is False
        assert _relays(conn) == []

    def test_dispatcher_passes_subagent_run_ids_to_hook(self, fast_sleep):
        d = EventDispatcher()
        seen = []
        d.set_orphan_hook(lambda parent, run_ids=(): seen.append((parent, run_ids)))

        async def go():
            for rid in ("sub-b", "sub-a"):
                d._note_orphan_subagent_end({
                    "runId": rid, "stream": "lifecycle", "data": {"phase": "end"},
                    "sessionKey": "agent:openvoiceui:subagent:x",
                    "spawnedBy": CANON_MAIN})
            for _ in range(20):
                await _REAL_SLEEP(0.01)

        asyncio.run(go())
        assert seen == [("main", ("sub-a", "sub-b"))]


class _FakeClock:
    """gw.time stand-in: real module, but time() can be pushed forward."""
    def __init__(self):
        self.offset = 0.0

    def time(self):
        return time.time() + self.offset

    def __getattr__(self, name):
        return getattr(time, name)


class TestSlowParentTurn:
    def test_parent_reply_after_300s_is_still_relayed(self, fast_sleep, handler, monkeypatch):
        # A parent turn after a timed-out child has been measured at 303 s.
        # With a 180 s collect deadline the continuation gave up with no text
        # and returned before any receipt or relay: the completion was lost.
        clock = _FakeClock()
        monkeypatch.setattr(gw, "time", clock)
        calls, state = handler
        conn = _conn()

        async def slow_reply(frame):
            if frame.get("method") != "chat.send" or not frame["id"].startswith("chat-"):
                return
            d = conn._dispatcher
            clock.offset += 303          # the parent's turn takes 303 s
            await d._route({"type": "res", "id": frame["id"], "ok": True,
                            "payload": {"runId": "cont-run", "status": "started"}})
            await d._route({"type": "event", "event": "agent", "payload": {
                "runId": "cont-run", "sessionKey": CANON_MAIN, "stream": "assistant",
                "data": {"text": "The takeoff is done."}}})
            await d._route({"type": "event", "event": "chat", "payload": {
                "runId": "cont-run", "sessionKey": CANON_MAIN, "state": "final"}})

        conn._ws.on_send = slow_reply

        async def go():
            await conn._run_orphan_continuation("main", ("sub-run-slow",))
            await _settle(conn, 1, calls)

        asyncio.run(asyncio.wait_for(go(), timeout=15))
        assert gw._ORPHAN_COLLECT_S >= 600
        assert [c[2] for c in calls] == ["sub-run-slow"], "the slow reply was pushed"
        assert len(_relays(conn)) == 1, "and relayed when nobody received it"
