"""
Session-start greeting brief (routes/conversation.py _build_session_start_message).

Rule (2026-10-27): a greeting is a real agent turn, never a canned clip. The agent
always generates it; a profile conversation.greeting is at most a style hint.
"""

from pathlib import Path

from routes.conversation import (
    _SESSION_START_MARK,
    _build_session_start_message,
    _session_start_speaker,
)

TEMPLATE = "Hey! What can I help you with?"


def test_known_user_gets_contextual_brief_with_name():
    msg = _build_session_start_message(current_user_name="Mike")
    assert "Mike" in msg
    assert "Greet Mike by name" in msg
    for cue in ("open thread", "promise", "never answered", "pending task", "fresh update"):
        assert cue in msg, cue
    assert "Different every time" in msg
    assert "Never invent" in msg
    assert "Do NOT address anyone by name" not in msg


def test_unknown_user_generates_without_inventing_a_name():
    msg = _build_session_start_message()
    assert "do not address anyone by name" in msg
    assert "do not guess who it is" in msg
    assert "Never invent" in msg
    assert "Say EXACTLY" not in msg


def test_unknown_or_guest_names_are_not_used_as_a_speaker():
    assert _session_start_speaker("unknown", "") == ""
    assert _session_start_speaker("", "Guest") == ""
    assert _session_start_speaker("", "  ") == ""


def test_face_name_wins_and_mismatch_is_noted():
    msg = _build_session_start_message(face_name="Josh", current_user_name="Mike")
    assert "Greet Josh by name" in msg
    assert "camera recognizes Josh" in msg and "signed-in account is Mike" in msg


def test_profile_greeting_is_only_a_style_hint_never_verbatim_order():
    for kwargs in ({}, {"current_user_name": "Mike"}, {"face_name": "Mike"},
                   {"persona_prompt": "You are Kyle."}):
        msg = _build_session_start_message(profile_greeting=TEMPLATE, **kwargs)
        assert "Say EXACTLY" not in msg
        assert "entire response" not in msg
        assert "Style hint" in msg
        assert "Do NOT say it word for word" in msg


def test_persona_prompt_kept_and_gets_user_and_context_guidance():
    persona = "PERSONA: Kyle. A new voice session just started. Output ONLY the greeting."
    msg = _build_session_start_message(current_user_name="Mike", persona_prompt=persona)
    assert persona in msg
    assert "is Mike; greet them by name" in msg
    assert "Never invent" in msg
    anon = _build_session_start_message(persona_prompt=persona)
    assert persona in anon
    assert "do not address anyone by name" in anon


def test_every_brief_carries_the_comms_brain_session_start_mark():
    # comms-brain plugin detects a session start by this sentence in the first 200 chars.
    for kwargs in ({}, {"current_user_name": "Mike"}, {"persona_prompt": "x" * 500},
                   {"profile_greeting": TEMPLATE}):
        msg = _build_session_start_message(**kwargs)
        assert _SESSION_START_MARK in msg[:200]


def test_no_verbatim_greeting_instruction_left_in_conversation_route():
    src = (Path(__file__).resolve().parent.parent / "routes" / "conversation.py").read_text()
    assert "Say EXACTLY this sentence" not in src
    assert "_rnd_g.choice(_persona_fallbacks)" not in src
    assert "_fb_greeting = (_profile_greeting" not in src
