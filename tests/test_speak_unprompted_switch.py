"""
Per-tenant switch: profile conversation.speak_unprompted = false (2026-11-24).

Measured 2026-11-24 00:00:02Z (josh): opening the app on a Mac mini fired __session_start__
and the browser spoke "Hi Josh." plus a status line. The owner: "Don't talk to me on the
Mac mini". There was no per-tenant switch: conversation.greeting is only a style hint and the
in-app text-mode toggle resets every time the app opens.

Rule: with speak_unprompted = false the agent never speaks UNPROMPTED. The session-start
greeting, background pushes and untagged canvas auto-speaks are shown as text with no TTS
(and the stream still ends in no_audio so the mic re-opens). A reply to what the user says
still speaks. Unset / true = unchanged platform behaviour (the controls below).
"""

import json
import sys
import types

import pytest

import routes.conversation as conv
import routes.profiles as profiles_mod
from profiles.manager import ProfileManager


# ── the helper reads the ACTIVE profile ──────────────────────────────────────

def _write_profile(d, pid, conversation):
    (d / f"{pid}.json").write_text(json.dumps({
        "id": pid, "name": pid, "conversation": conversation,
        "voice": {"tts_provider": "groq", "voice_id": "autumn"},
    }))


@pytest.mark.parametrize("conversation,expected", [
    ({"speak_unprompted": False}, False),
    ({"speak_unprompted": True}, True),
    ({"speak_unprompted": None}, True),
    ({}, True),                      # field absent: every existing tenant profile
])
def test_helper_follows_the_active_profile(tmp_path, monkeypatch, conversation, expected):
    _write_profile(tmp_path, "default", {})
    _write_profile(tmp_path, "comms-brain", conversation)
    mgr = ProfileManager(str(tmp_path))
    monkeypatch.setattr(profiles_mod, "get_profile_manager", lambda: mgr)
    monkeypatch.setattr(profiles_mod, "_active_profile_id", "comms-brain")
    assert profiles_mod.speaks_unprompted() is expected


def test_helper_speaks_when_the_profile_is_unreadable(monkeypatch):
    def boom():
        raise RuntimeError("no profiles")
    monkeypatch.setattr(profiles_mod, "get_profile_manager", boom)
    assert profiles_mod.speaks_unprompted() is True


def test_field_round_trips_through_to_dict(tmp_path):
    _write_profile(tmp_path, "default", {"speak_unprompted": False})
    prof = ProfileManager(str(tmp_path)).get_profile("default")
    assert prof.to_dict()["conversation"]["speak_unprompted"] is False


# ── the conversation route ───────────────────────────────────────────────────

class _Gateway:
    def __init__(self, reply):
        self.reply = reply

    def is_configured(self):
        return True

    def get(self, gateway_id=None):
        return None

    def stream_to_queue(self, event_queue, message, session_key,
                        captured_actions=None, **kwargs):
        event_queue.put({'type': 'delta', 'text': self.reply})
        event_queue.put({'type': 'text_done', 'response': self.reply, 'actions': []})


@pytest.fixture(scope='module')
def app_client():
    with pytest.MonkeyPatch.context() as mp:
        for var in ('CLERK_PUBLISHABLE_KEY', 'VITE_CLERK_PUBLISHABLE_KEY',
                    'CLERK_SECRET_KEY', 'CANVAS_REQUIRE_AUTH'):
            mp.delenv(var, raising=False)
        from app import create_app
        app, _ = create_app(config_override={'TESTING': True})
        from routes.conversation import conversation_bp
        if 'conversation' not in app.blueprints:
            app.register_blueprint(conversation_bp)
        yield app.test_client()


@pytest.fixture
def tts_calls(monkeypatch):
    calls = []

    def fake_tts(text, **kw):
        calls.append(text)
        return 'QUJD'

    monkeypatch.setattr(conv, '_tts_generate_b64', fake_tts)
    monkeypatch.setattr(conv, 'save_conversation_turn', lambda **kw: None)
    monkeypatch.setattr(conv, 'log_conversation', lambda role, msg, **kw: None)
    monkeypatch.setattr(conv, 'log_metrics', lambda m: None)
    fake_server = types.ModuleType('server')
    fake_server.get_zai_direct_response = lambda *a, **kw: 'FLASH'
    monkeypatch.setitem(sys.modules, 'server', fake_server)
    return calls


def _stream(client, monkeypatch, *, speaks, message, reply, **extra):
    monkeypatch.setattr(profiles_mod, 'speaks_unprompted', lambda: speaks, raising=False)
    monkeypatch.setattr(conv, 'gateway_manager', _Gateway(reply))
    resp = client.post('/api/conversation?stream=1',
                       json={'message': message, 'tts_provider': 'groq', **extra})
    assert resp.status_code == 200
    return [json.loads(l) for l in resp.get_data(as_text=True).splitlines() if l.strip()]


def _types(events):
    return [e.get('type') for e in events]


GREETING = 'Hi Josh. Two leads came in overnight and the Allstate page is live.'


def test_quiet_profile_greeting_is_text_only_and_reopens_the_mic(app_client, monkeypatch, tts_calls):
    ev = _stream(app_client, monkeypatch, speaks=False,
                 message='__session_start__', reply=GREETING)
    done = [e for e in ev if e.get('type') == 'text_done']
    assert done and GREETING in done[0].get('response', ''), _types(ev)
    assert tts_calls == [], tts_calls
    assert 'audio' not in _types(ev), _types(ev)
    assert 'no_audio' in _types(ev), _types(ev)


def test_control_default_profile_greeting_still_speaks(app_client, monkeypatch, tts_calls):
    ev = _stream(app_client, monkeypatch, speaks=True,
                 message='__session_start__', reply=GREETING)
    assert tts_calls, 'default profile greeting produced no TTS'
    assert 'audio' in _types(ev), _types(ev)


def test_quiet_profile_still_speaks_replies_to_the_user(app_client, monkeypatch, tts_calls):
    ev = _stream(app_client, monkeypatch, speaks=False,
                 message='what is on my calendar today',
                 reply='You have two estimates this afternoon.')
    assert tts_calls, 'a reply to the user must still speak'
    assert 'audio' in _types(ev), _types(ev)


def test_quiet_profile_unprompted_canvas_speak_is_text_only(app_client, monkeypatch, tts_calls):
    ev = _stream(app_client, monkeypatch, speaks=False,
                 message='The quote page is ready for review',
                 reply='Your quote page is open on screen.', unprompted=True)
    assert tts_calls == [], tts_calls
    assert 'no_audio' in _types(ev), _types(ev)


def test_control_unprompted_flag_is_ignored_when_the_switch_is_on(app_client, monkeypatch, tts_calls):
    ev = _stream(app_client, monkeypatch, speaks=True,
                 message='The quote page is ready for review',
                 reply='Your quote page is open on screen.', unprompted=True)
    assert tts_calls and 'audio' in _types(ev), _types(ev)


# ── the browser marks untagged canvas speaks as unprompted ───────────────────

def test_app_js_marks_untagged_canvas_speak_unprompted():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'src' / 'app.js').read_text()
    assert "event.data.userInitiated === true ? {} : { unprompted: true }" in src
    assert "...(opts.unprompted ? { unprompted: true } : {})" in src


# ── background pushes (server.push_proactive_message) ────────────────────────

def _push(monkeypatch, tmp_path, speaks):
    from tests.test_push_tts_profile_voice import _connect, _install_profile, _load
    _install_profile(monkeypatch, "groq", "autumn")
    if speaks is not None:
        sys.modules["routes.profiles"].speaks_unprompted = lambda: speaks
    calls = []
    ns = _load(calls, tmp_path / "r.jsonl")
    ws = _connect(ns)
    ns["push_proactive_message"]("Done - your blog idea bank is on your screen [CANVAS:x]")
    frame = json.loads(ns["_push_clients"][ws].get_nowait())
    return calls, frame


def test_quiet_profile_push_is_text_only(monkeypatch, tmp_path):
    calls, frame = _push(monkeypatch, tmp_path, speaks=False)
    assert calls == [], calls
    assert frame["audio"] is None
    assert "blog idea bank" in frame["text"]


def test_control_default_profile_push_still_speaks(monkeypatch, tmp_path):
    calls, frame = _push(monkeypatch, tmp_path, speaks=True)
    assert calls and frame["audio"]


def test_control_push_speaks_when_the_helper_is_missing(monkeypatch, tmp_path):
    calls, frame = _push(monkeypatch, tmp_path, speaks=None)   # older routes.profiles
    assert calls and frame["audio"]
