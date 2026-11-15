"""
Server-initiated audio (proactive push, /ws/clawdbot proxy) must speak in the ACTIVE PROFILE's voice.

Measured 2026-11-15 (danielle): profile groq/autumn (female), but every push logged
"TTS VOICE MISMATCH: voice 'M1' is not available on groq ... speaking with groq/troy" and the owner
heard a male voice on a live call. push_proactive_message and _tts_bytes hard-coded voice="M1"
with no provider, so the default engine (groq) mapped the supertonic male voice to troy.

server.py cannot be imported outside the container, so the functions are lifted out of its
source with ast (same approach as test_push_socket.py) — the code under test is server.py's own.
"""

import ast
import base64
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import types
import uuid
from datetime import datetime
from pathlib import Path

SERVER_PY = Path(__file__).resolve().parent.parent / "server.py"
_NAMES = {
    "_push_clients_lock", "_push_clients", "_push_client_session",
    "_push_client_visible", "_push_client_user", "_push_receipt_lock",
    "_PUSH_RECEIPT_PATH", "_PUSH_VISIBLE_TTL_S", "_push_user_is_operator",
    "_push_client_seen", "_write_push_receipt", "push_proactive_message",
    "_push_tts_args", "_tts_bytes",
}


def _load(calls, receipt_path):
    tree = ast.parse(SERVER_PY.read_text())
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _NAMES:
            body.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in _NAMES for t in targets):
                body.append(node)

    def fake_tts(text, **kw):
        calls.append(kw)
        return "QVVESU8="

    ns = {
        "json": json, "re": re, "time": time, "queue": queue, "uuid": uuid, "os": os,
        "threading": threading, "datetime": datetime, "Path": Path, "base64": base64,
        "logger": logging.getLogger("test-push-tts-voice"),
        "_generate_tts_b64": fake_tts,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SERVER_PY), "exec"), ns)
    ns["_PUSH_RECEIPT_PATH"] = receipt_path
    return ns


def _install_profile(monkeypatch, provider, voice_id, active="comms-brain"):
    voice = types.SimpleNamespace(tts_provider=provider, voice_id=voice_id)
    prof = types.SimpleNamespace(voice=voice)

    class _Mgr:
        def get_profile(self, pid):
            assert pid == active
            return prof

    routes_pkg = types.ModuleType("routes")
    routes_profiles = types.ModuleType("routes.profiles")
    routes_profiles._active_profile_id = active
    routes_pkg.profiles = routes_profiles
    prof_pkg = types.ModuleType("profiles")
    prof_mgr = types.ModuleType("profiles.manager")
    prof_mgr.get_profile_manager = lambda: _Mgr()
    prof_pkg.manager = prof_mgr
    for name, mod in (("routes", routes_pkg), ("routes.profiles", routes_profiles),
                      ("profiles", prof_pkg), ("profiles.manager", prof_mgr)):
        monkeypatch.setitem(sys.modules, name, mod)


class _Sock:
    def send(self, data):
        pass


def _connect(ns):
    ws = _Sock()
    with ns["_push_clients_lock"]:
        ns["_push_clients"][ws] = queue.Queue()
    return ws


def test_push_speaks_in_the_active_profile_voice(monkeypatch, tmp_path):
    _install_profile(monkeypatch, "groq", "autumn")
    calls = []
    ns = _load(calls, tmp_path / "r.jsonl")
    _connect(ns)
    ns["push_proactive_message"]("Done girl - your blog idea bank is on your screen [CANVAS:x]")
    assert calls, "push generated no audio"
    assert calls[0].get("voice") == "autumn", calls[0]
    assert calls[0].get("tts_provider") == "groq", calls[0]


def test_push_uses_the_profile_engine_too(monkeypatch, tmp_path):
    _install_profile(monkeypatch, "resemble", "kyle")
    calls = []
    ns = _load(calls, tmp_path / "r.jsonl")
    _connect(ns)
    ns["push_proactive_message"]("hello there")
    assert calls[0] == {"voice": "kyle", "tts_provider": "resemble"}


def test_ws_proxy_tts_uses_the_profile_voice(monkeypatch, tmp_path):
    _install_profile(monkeypatch, "groq", "hannah")
    calls = []
    ns = _load(calls, tmp_path / "r.jsonl")
    assert ns["_tts_bytes"]("hi") == b"AUDIO"
    assert calls[0] == {"voice": "hannah", "tts_provider": "groq"}


def test_no_profile_keeps_the_old_default(monkeypatch, tmp_path):
    for name in ("routes", "routes.profiles", "profiles", "profiles.manager"):
        monkeypatch.setitem(sys.modules, name, None)   # import fails -> old behaviour
    calls = []
    ns = _load(calls, tmp_path / "r.jsonl")
    _connect(ns)
    ns["push_proactive_message"]("hello")
    assert calls[0] == {"voice": "M1"}
