"""
Agent-scoped session keys on the wire + rejected chat.send handling.

openclaw 2026.8.35 rejects a bare sessionKey ("main") in chat.send once more
than one agent is configured:
  "Multiple agents are configured, but session key "main" has no explicit
   owner. Use an agent-prefixed session key or select an agent explicitly."
Every JamBot tenant has two agents (openvoiceui, openvoiceui-fast). 2026.5.7
canonicalizes "main" to agent:<defaultAgentId>:main itself, so the prefixed
form is the SAME session on both versions. Both announce the default agent in
the connect hello at payload.snapshot.sessionDefaults.defaultAgentId.

Internal keys (subscriptions, steer maps, caches) keep the short alias; only
the outbound RPC param changes. The fixtures below use the frame shapes the
gateway actually sends (`res` + `payload`, `error: {code, message}`).
"""

import asyncio
import json
import queue
from unittest.mock import patch

from services.gateways.openclaw import (
    EventDispatcher,
    GatewayConnection,
    Subscription,
    extract_default_agent_id,
    res_error_message,
    wire_session_key,
)

AGENT = "openvoiceui"
CANON_MAIN = "agent:openvoiceui:main"

# The rejection 2026.8.35 returns for a bare key (agent-scope-config.ts).
REJECT_FRAME_ERROR = {
    "code": "INVALID_REQUEST",
    "message": ('Multiple agents are configured, but session key "main" has no '
                'explicit owner. Use an agent-prefixed session key or select an '
                'agent explicitly.'),
}


def _hello(default_agent_id=AGENT):
    """A hello-ok `res` frame in the shape both 2026.5.7 and 2026.8.35 send."""
    defaults = {"mainKey": "main", "mainSessionKey": CANON_MAIN, "scope": "per-sender"}
    if default_agent_id is not None:
        defaults["defaultAgentId"] = default_agent_id
    return {
        "type": "res",
        "id": "connect-x",
        "ok": True,
        "payload": {
            "type": "hello-ok",
            "protocol": 3,
            "server": {"version": "2026.8.35"},
            "snapshot": {"sessionDefaults": defaults},
        },
    }


class FakeWS:
    """Records every outbound frame; optionally reacts to it via on_send."""

    def __init__(self, on_send=None):
        self.sent = []
        self.on_send = on_send
        self.close_code = None

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if self.on_send is not None:
            asyncio.ensure_future(self.on_send(frame))


def _conn(default_agent_id=AGENT, on_send=None):
    conn = GatewayConnection()
    conn._dispatcher = EventDispatcher()
    conn._ws = FakeWS(on_send)
    conn._connected = True
    conn._default_agent_id = default_agent_id
    return conn


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


# ---------------------------------------------------------------------------
# wire_session_key / _wire_session_key
# ---------------------------------------------------------------------------

class TestWireSessionKey:
    def test_bare_key_with_announced_default_is_prefixed(self):
        assert wire_session_key("main", AGENT) == CANON_MAIN
        assert wire_session_key("recovery-1759500000", AGENT) == "agent:openvoiceui:recovery-1759500000"

    def test_already_prefixed_key_is_untouched(self):
        assert wire_session_key(CANON_MAIN, AGENT) == CANON_MAIN
        assert wire_session_key("agent:openvoiceui-fast:main", AGENT) == "agent:openvoiceui-fast:main"

    def test_no_announced_default_keeps_key_unchanged(self):
        assert wire_session_key("main", None) == "main"
        assert wire_session_key("main", "") == "main"

    def test_falsy_key_unchanged(self):
        assert wire_session_key("", AGENT) == ""
        assert wire_session_key(None, AGENT) is None

    def test_unscoped_global_and_unknown_stay_bare(self):
        # Both gateway versions keep these unscoped; agent:<id>:global would
        # address a DIFFERENT session.
        assert wire_session_key("global", AGENT) == "global"
        assert wire_session_key("unknown", AGENT) == "unknown"

    def test_connection_method_uses_its_own_announced_default(self):
        assert _conn(AGENT)._wire_session_key("main") == CANON_MAIN
        assert _conn(None)._wire_session_key("main") == "main"


# ---------------------------------------------------------------------------
# Capturing defaultAgentId from the connect hello
# ---------------------------------------------------------------------------

class TestDefaultAgentCapture:
    def test_extracts_from_payload_snapshot(self):
        assert extract_default_agent_id(_hello(AGENT)) == AGENT

    def test_absent_returns_none(self):
        assert extract_default_agent_id(_hello(None)) is None
        assert extract_default_agent_id({"type": "res", "ok": True}) is None
        assert extract_default_agent_id({}) is None

    def test_handshake_captures_and_refreshes_on_every_connect(self):
        challenge = {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n"}}

        class HandshakeWS:
            def __init__(self, hello):
                self._frames = [json.dumps(challenge), json.dumps(hello)]

            async def recv(self):
                return self._frames.pop(0)

            async def send(self, raw):
                pass

        conn = GatewayConnection()
        with patch("services.gateways.openclaw._load_device_identity", return_value={}), \
                patch("services.gateways.openclaw._sign_device_connect", return_value={}):
            asyncio.run(conn._handshake(HandshakeWS(_hello("openvoiceui"))))
            assert conn._default_agent_id == "openvoiceui"
            # Reconnect to a gateway announcing a different default -> refreshed.
            asyncio.run(conn._handshake(HandshakeWS(_hello("other-agent"))))
            assert conn._default_agent_id == "other-agent"
            # Reconnect to a gateway that announces none -> back to unchanged keys.
            asyncio.run(conn._handshake(HandshakeWS(_hello(None))))
            assert conn._default_agent_id is None


# ---------------------------------------------------------------------------
# Outbound RPCs carry the agent-scoped key; internal state keeps the alias
# ---------------------------------------------------------------------------

class TestOutboundSites:
    def test_chat_send_wire_key_prefixed_subscription_keeps_alias(self):
        async def reply(frame):
            if frame.get("method") != "chat.send":
                return
            d = conn._dispatcher
            # The subscription must still be keyed by the short alias.
            sub = d._subscriptions[frame["id"][len("chat-"):]]
            seen_sub_keys.append(sub.session_key)
            await d._route({"type": "res", "id": frame["id"], "ok": True,
                            "payload": {"runId": "run-1", "status": "started"}})
            await d._route({"type": "event", "event": "agent", "payload": {
                "runId": "run-1", "sessionKey": CANON_MAIN, "stream": "assistant",
                "data": {"text": "hello there", "delta": "hello there"}}})
            await d._route({"type": "event", "event": "agent", "payload": {
                "runId": "run-1", "sessionKey": CANON_MAIN, "stream": "lifecycle",
                "data": {"phase": "end"}}})

        seen_sub_keys = []
        conn = _conn(AGENT, on_send=reply)
        q = queue.Queue()
        asyncio.run(asyncio.wait_for(
            conn._send_and_stream(q, "hi", "main", []), timeout=10))

        sends = [f for f in conn._ws.sent if f.get("method") == "chat.send"]
        assert len(sends) == 1
        assert sends[0]["params"]["sessionKey"] == CANON_MAIN
        assert seen_sub_keys == ["main"]
        done = [e for e in _drain(q) if e.get("type") == "text_done"]
        assert len(done) == 1 and done[0]["response"] == "hello there"
        # The dispatcher learned alias -> canonical from the runId-routed event.
        assert conn._dispatcher._canonical_keys.get("main") == CANON_MAIN

    def test_chat_send_unchanged_when_gateway_announced_no_default(self):
        async def reply(frame):
            await conn._dispatcher._route({"type": "res", "id": frame["id"], "ok": False,
                                           "error": {"code": "X", "message": "stop"}})

        conn = _conn(None, on_send=reply)
        asyncio.run(asyncio.wait_for(
            conn._send_and_stream(queue.Queue(), "hi", "main", []), timeout=10))
        assert conn._ws.sent[0]["params"]["sessionKey"] == "main"

    def test_chat_abort_wire_key_prefixed(self):
        conn = _conn(AGENT)
        asyncio.run(conn._send_abort("run-1", "main", "test"))
        frame = conn._ws.sent[-1]
        assert frame["method"] == "chat.abort"
        assert frame["params"] == {"sessionKey": CANON_MAIN, "runId": "run-1"}

    def test_steer_wire_key_prefixed_and_active_sub_found_by_alias(self):
        conn = _conn(AGENT)
        sub = conn._dispatcher.subscribe("c1", "main")
        sub.state = Subscription.ACTIVE
        assert asyncio.run(conn._send_steer("actually, stop", "main")) is True
        frame = conn._ws.sent[-1]
        assert frame["method"] == "chat.send"
        assert frame["params"]["sessionKey"] == CANON_MAIN


# ---------------------------------------------------------------------------
# Inbound matching still works for canonical event keys
# ---------------------------------------------------------------------------

class TestInboundMatching:
    def test_canonical_event_key_matches_alias_subscription(self):
        d = EventDispatcher()
        assert d._sk_match(CANON_MAIN, "main")
        assert not d._sk_match("agent:openvoiceui:other", "main")

    def test_followup_run_with_canonical_key_attaches_to_queued_alias_sub(self):
        async def run():
            d = EventDispatcher()
            sub = d.subscribe("c1", "main")
            sub.state = Subscription.QUEUED
            await d._route({"type": "event", "event": "agent", "payload": {
                "runId": "run-new", "sessionKey": CANON_MAIN, "stream": "assistant",
                "data": {"text": "x", "delta": "x"}}})
            return sub

        sub = asyncio.run(run())
        assert sub.state == Subscription.ACTIVE and sub.run_id == "run-new"


# ---------------------------------------------------------------------------
# A rejected chat.send ends the turn with an error instead of heartbeating
# ---------------------------------------------------------------------------

class TestRejectedChatSend:
    def test_res_error_message(self):
        assert res_error_message({"type": "res", "ok": False, "error": REJECT_FRAME_ERROR}) \
            == REJECT_FRAME_ERROR["message"]
        assert res_error_message({"type": "res", "error": "boom"}) == "boom"
        assert res_error_message({"type": "res", "ok": False}) == "request rejected by gateway"
        assert res_error_message({"type": "res", "ok": True, "payload": {"runId": "r"}}) is None
        assert res_error_message({"type": "event", "error": "x"}) is None

    def test_rejected_res_is_not_marked_queued(self):
        async def run():
            d = EventDispatcher()
            sub = d.subscribe("c1", "main")
            await d._route({"type": "res", "id": "chat-c1", "ok": False,
                            "error": REJECT_FRAME_ERROR})
            return sub

        sub = asyncio.run(run())
        assert sub.state == Subscription.PENDING
        assert sub.event_queue.qsize() == 1  # still delivered to the stream loop

    def test_error_res_emits_error_text_done_promptly(self):
        async def reject(frame):
            if frame.get("method") == "chat.send":
                await conn._dispatcher._route({"type": "res", "id": frame["id"],
                                               "ok": False, "error": REJECT_FRAME_ERROR})

        conn = _conn(AGENT, on_send=reject)
        q = queue.Queue()
        # Before the fix this heartbeated for the 300s hard timeout; the 5s
        # cap here (one inner poll interval) proves it now ends on the frame.
        asyncio.run(asyncio.wait_for(
            conn._send_and_stream(q, "hi", "main", []), timeout=5))
        events = _drain(q)
        assert [e["type"] for e in events] == ["text_done"]
        assert events[0]["response"] is None
        assert events[0]["error"] == REJECT_FRAME_ERROR["message"]
        # No abort is sent for a run that never existed; subscription cleaned up.
        assert [f["method"] for f in conn._ws.sent] == ["chat.send"]
        assert conn._dispatcher._subscriptions == {}
