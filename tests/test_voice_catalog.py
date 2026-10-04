"""Voice/engine mismatch guards (foambot 2026-09-23..10-02: "troy" on supertonic = silent replies).

Cases are the two real mismatches the foambot profile held, plus controls.
"""
import json

import pytest

from profiles.manager import ProfileManager
from services.voice_catalog import voice_mismatch

BASE = {
    "id": "test-agent",
    "name": "Test Agent",
    "system_prompt": "You are a test agent.",
    "llm": {"provider": "zai", "model": "glm-5-turbo"},
    "voice": {"tts_provider": "supertonic", "voice_id": "M1"},
}


class TestVoiceMismatch:
    def test_groq_voice_on_supertonic_is_refused(self):      # foambot 10-02
        msg = voice_mismatch("supertonic", "troy")
        assert msg and "groq" in msg

    def test_supertonic_voice_on_groq_is_refused(self):      # foambot 09-23
        msg = voice_mismatch("groq", "M1")
        assert msg and "supertonic" in msg

    def test_valid_pairs_pass(self):
        assert voice_mismatch("groq", "troy") is None
        assert voice_mismatch("supertonic", "F3") is None

    def test_open_engine_custom_voice_passes(self):
        # Resemble clones are UUIDs no static list knows: cannot tell, so allowed
        assert voice_mismatch("resemble", "a1b2c3d4-0000-4000-8000-123456789abc") is None

    def test_open_engine_given_another_engines_fixed_voice_is_refused(self):
        assert "groq" in (voice_mismatch("resemble", "troy") or "")

    def test_missing_fields_pass(self):
        assert voice_mismatch(None, "troy") is None
        assert voice_mismatch("supertonic", None) is None


@pytest.fixture()
def mgr(tmp_path):
    (tmp_path / "test-agent.json").write_text(json.dumps(BASE))
    ProfileManager.reset_instance()
    m = ProfileManager(str(tmp_path))
    yield m
    ProfileManager.reset_instance()


class TestProfileSaveGuard:
    def test_create_with_mismatched_voice_is_invalid(self, mgr):
        bad = {**BASE, "id": "x", "voice": {"tts_provider": "supertonic", "voice_id": "troy"}}
        assert any("voice_id" in e for e in mgr.validate_profile(bad))

    def test_update_changing_only_the_engine_is_judged_on_the_merged_pair(self, mgr):
        # profile holds supertonic/M1; switching ONLY the engine to groq strands "M1"
        with pytest.raises(ValueError):
            mgr.apply_partial_update("test-agent", {"voice": {"tts_provider": "groq"}})
        # nothing was written: the stored profile is unchanged
        assert mgr.get_profile("test-agent").voice.tts_provider == "supertonic"

    def test_update_changing_both_together_is_accepted(self, mgr):
        p = mgr.apply_partial_update("test-agent", {"voice": {"tts_provider": "groq", "voice_id": "troy"}})
        assert p.voice.tts_provider == "groq" and p.voice.voice_id == "troy"


def test_speak_time_maps_off_engine_voice_instead_of_failing(monkeypatch):
    import services.tts as tts

    calls = []

    def fake_generate(provider, text, voice):
        calls.append((provider, voice))
        if provider == "supertonic" and voice not in {f"{g}{i}" for g in "MF" for i in range(1, 6)}:
            raise ValueError(f"Invalid voice: {voice}")
        return b"RIFF-fake-audio"

    monkeypatch.setattr(tts, "_generate_with_provider", fake_generate)
    out = tts.generate_tts_b64("Hey Jordan.", voice="troy", tts_provider="supertonic")
    assert out, "must speak, not go silent"
    assert calls[0] == ("supertonic", "M1"), calls   # troy (male) -> supertonic M1
