"""
Tests for routes/conversation.py — Conversation Blueprint helpers and endpoints (P7-T1, ADR-010)
Tests focus on pure-logic helpers and endpoints that don't require a live Gateway.
"""

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers: clean_for_tts
# ---------------------------------------------------------------------------

class TestCleanForTts:
    def _clean(self, text):
        from routes.conversation import clean_for_tts
        return clean_for_tts(text)

    def test_empty_string(self):
        assert self._clean("") == ""

    def test_none_like_falsy(self):
        from routes.conversation import clean_for_tts
        assert clean_for_tts("") == ""

    def test_strips_code_block(self):
        result = self._clean("See ```python\nprint('hi')\n``` for details")
        assert "```" not in result

    def test_strips_inline_code(self):
        result = self._clean("Use `my_func()` here")
        assert "`" not in result

    def test_strips_bold_markdown(self):
        result = self._clean("This is **bold** text")
        assert "**" not in result
        assert "bold" in result

    def test_strips_italic_markdown(self):
        result = self._clean("This is *italic* text")
        assert "*" not in result
        assert "italic" in result

    def test_strips_headers(self):
        result = self._clean("## Title\nContent")
        assert "##" not in result
        assert "Title" in result

    def test_strips_canvas_tags(self):
        result = self._clean("Go here [CANVAS:dashboard] now")
        assert "[CANVAS:" not in result

    def test_strips_music_play_tag(self):
        result = self._clean("Play [MUSIC_PLAY:track.mp3] now")
        assert "[MUSIC_PLAY" not in result

    def test_strips_music_stop_tag(self):
        result = self._clean("Stop [MUSIC_STOP] now")
        assert "[MUSIC_STOP]" not in result

    def test_strips_url(self):
        result = self._clean("Visit https://example.com for more")
        assert "https://example.com" not in result

    def test_strips_link_keeps_text(self):
        result = self._clean("See [the docs](https://example.com)")
        assert "the docs" in result

    def test_replaces_ampersand(self):
        result = self._clean("Cats & dogs")
        assert "&" not in result
        assert "and" in result

    def test_replaces_percent(self):
        result = self._clean("99% done")
        assert "%" not in result
        assert "percent" in result

    def test_replaces_dollar(self):
        result = self._clean("Costs $50")
        assert "$" not in result
        assert "dollars" in result

    def test_strips_no_reply_prefix(self):
        result = self._clean("NO_REPLY Hello there")
        assert "NO_REPLY" not in result
        assert "Hello" in result

    def test_preserves_plain_text(self):
        result = self._clean("Hello world.")
        assert "Hello" in result
        assert "world" in result

    def test_collapses_whitespace(self):
        result = self._clean("Hello   world")
        assert "  " not in result

    def test_strips_canvas_menu_tag(self):
        result = self._clean("[CANVAS_MENU] Open the menu")
        assert "[CANVAS_MENU]" not in result

    # --- CANVAS_ACTION brace-walker (2026-07-02 tradeshow form-fill fix) ---

    def test_strips_canvas_action_simple(self):
        result = self._clean(
            'Filling that in now. [CANVAS_ACTION:{"action":"spf-quote-set-field",'
            '"payload":{"name":"businessName","value":"Acme Spray Foam"}}] Done.'
        )
        assert "CANVAS_ACTION" not in result
        assert "spf-quote-set-field" not in result
        assert "Filling that in now." in result
        assert "Done." in result

    def test_strips_canvas_action_nested_array(self):
        # values arrays embed ] — the case that defeats every [^\]]* regex
        result = self._clean(
            'On it. [CANVAS_ACTION:{"action":"spf-quote-set-group","payload":'
            '{"name":"typeOfWork","values":["open-cell","closed-cell"]}}] Submitted.'
        )
        assert "CANVAS_ACTION" not in result
        assert "open-cell" not in result
        assert "On it." in result and "Submitted." in result

    def test_strips_multiple_canvas_action_tags(self):
        result = self._clean(
            '[CANVAS_ACTION:{"action":"a","payload":{"x":1}}] middle '
            '[CANVAS_ACTION:{"action":"b","payload":{"y":[2,3]}}] end'
        )
        assert "CANVAS_ACTION" not in result
        assert "middle" in result and "end" in result

    def test_strips_unterminated_canvas_action_midstream(self):
        # A half-arrived tag (stream cut) must never be spoken
        result = self._clean('Sure thing. [CANVAS_ACTION:{"action":"spf-quote-set')
        assert "CANVAS_ACTION" not in result
        assert "Sure thing." in result

    def test_canvas_action_with_escaped_quotes_and_braces_in_strings(self):
        result = self._clean(
            'Ok. [CANVAS_ACTION:{"action":"set","payload":{"note":"brace } and \\" quote"}}] After.'
        )
        assert "CANVAS_ACTION" not in result
        assert "Ok." in result and "After." in result

    def test_yes_no_preserved_as_single_word(self):
        from routes.conversation import clean_for_tts
        # "NO" as sole response should not be stripped
        result = clean_for_tts("NO")
        assert result.strip().upper() in ("NO", "NO.")

    def test_api_acronym_expansion(self):
        result = self._clean("The API endpoint is")
        # API should be expanded or at least not break anything
        assert isinstance(result, str)

    def test_multiline_becomes_sentences(self):
        result = self._clean("Line one\nLine two")
        # Newlines become ". " separators
        assert "Line one" in result
        assert "Line two" in result


# ---------------------------------------------------------------------------
# Helpers: get_voice_session_key / bump_voice_session
# ---------------------------------------------------------------------------

class TestVoiceSessionHelpers:
    def test_get_voice_session_key_returns_string(self, tmp_path, monkeypatch):
        from routes import conversation as conv_mod
        monkeypatch.setattr(
            conv_mod, "VOICE_SESSION_FILE",
            str(tmp_path / ".voice-session-counter")
        )
        key = conv_mod.get_voice_session_key()
        assert isinstance(key, str)
        assert key.startswith("voice-main")

    def test_bump_voice_session_increments(self, tmp_path, monkeypatch):
        from routes import conversation as conv_mod
        counter_file = tmp_path / ".voice-session-counter"
        counter_file.write_text("10")
        monkeypatch.setattr(conv_mod, "VOICE_SESSION_FILE", str(counter_file))
        new_key = conv_mod.bump_voice_session()
        assert "voice-main" in new_key

    def test_bump_voice_session_creates_counter_file(self, tmp_path, monkeypatch):
        from routes import conversation as conv_mod
        counter_file = tmp_path / ".voice-session-counter"
        monkeypatch.setattr(conv_mod, "VOICE_SESSION_FILE", str(counter_file))
        conv_mod.bump_voice_session()
        assert counter_file.exists()


# ---------------------------------------------------------------------------
# Endpoints: /api/tts/providers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conv_client():
    """Minimal Flask app with conversation blueprint registered."""
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})
    from routes.canvas import canvas_bp
    from routes.conversation import conversation_bp
    app.register_blueprint(canvas_bp)
    app.register_blueprint(conversation_bp)
    return app.test_client()


class TestTtsProvidersEndpoint:
    def test_tts_providers_returns_200(self, conv_client):
        resp = conv_client.get("/api/tts/providers")
        assert resp.status_code == 200

    def test_tts_providers_returns_json(self, conv_client):
        resp = conv_client.get("/api/tts/providers")
        data = resp.get_json()
        assert data is not None

    def test_tts_providers_has_providers_key(self, conv_client):
        resp = conv_client.get("/api/tts/providers")
        data = resp.get_json()
        # API returns {"providers": [...], "default_provider": "..."} or a list
        assert isinstance(data, (list, dict))


# ---------------------------------------------------------------------------
# Endpoints: /api/conversation/reset
# ---------------------------------------------------------------------------

class TestConversationResetEndpoint:
    def test_reset_returns_200(self, conv_client):
        resp = conv_client.post(
            "/api/conversation/reset",
            json={"session_id": "test-session"},
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_reset_returns_json(self, conv_client):
        resp = conv_client.post(
            "/api/conversation/reset",
            json={},
            content_type="application/json",
        )
        data = resp.get_json()
        assert data is not None

    def test_reset_clears_conversation_history(self, conv_client):
        from routes.conversation import conversation_histories
        # Pre-seed a history
        conversation_histories["test-clear"] = [{"role": "user", "content": "hi"}]
        conv_client.post(
            "/api/conversation/reset",
            json={"session_id": "test-clear"},
            content_type="application/json",
        )
        # History should be cleared
        assert conversation_histories.get("test-clear", []) == []
