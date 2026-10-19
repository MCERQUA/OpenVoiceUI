"""
Tests for tts_providers/qwen3_mlx_provider.py (provider id 'qwen3-mlx').

A real HTTP server on 127.0.0.1 stands in for the MLX node, so the request
shape, headers, PCM->WAV wrapping, per-sentence calls and the failure modes are
exercised over an actual socket. Negative controls: server down, non-PCM
response, HTTP error, refused voice, unconfigured URL — and that a dead server
hands the sentence to the fallback chain in services/tts.py.
"""

import base64
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

RATE = 24000


class _FakeNode(BaseHTTPRequestHandler):
    mode = "ok"          # ok | html | http500 | empty
    calls = []           # (path, body, headers) per request

    def log_message(self, *a):  # keep pytest output quiet
        pass

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True, "model": "fake-qwen",
                               "voices": ["char-a", "char-b", "realperson-x"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).calls.append((self.path, body, dict(self.headers)))
        if self.mode == "http500":
            self.send_response(500); self.send_header("Content-Length", "4"); self.end_headers()
            self.wfile.write(b"boom"); return
        if self.mode == "html":
            payload = b"<html>not audio</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        else:
            # 0.1 s of a non-zero sample per request (2 bytes per sample)
            payload = b"" if self.mode == "empty" else struct.pack("<h", 1000) * (RATE // 10)
            self.send_response(200)
            self.send_header("Content-Type", "audio/pcm")
            self.send_header("X-Sample-Rate", str(RATE))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def node(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeNode)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _FakeNode.mode = "ok"
    _FakeNode.calls = []
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("QWEN_MLX_TTS_URL", url)
    monkeypatch.setenv("QWEN_MLX_TTS_VOICES", "char-a,char-b,realperson-x")
    monkeypatch.setenv("QWEN_MLX_TTS_DENY_PREFIXES", "realperson")
    monkeypatch.setenv("QWEN_MLX_TTS_CALLER", "ovu-test")
    monkeypatch.setenv("QWEN_MLX_TTS_PURPOSE", "unit-test")
    _reset_health()
    yield url
    srv.shutdown()
    _reset_health()


def _reset_health():
    from tts_providers import qwen3_mlx_provider as m
    m._HEALTH.update(ok=None, checked_at=0.0, voices=[], model=None, error=None, url=None)


def _provider():
    from tts_providers.qwen3_mlx_provider import Qwen3MlxProvider
    return Qwen3MlxProvider()


def _dead_url():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    return f"http://127.0.0.1:{port}"


# ── registry ─────────────────────────────────────────────────────────────────

def test_registered_in_provider_registry():
    from tts_providers import _PROVIDERS, get_provider
    assert "qwen3-mlx" in _PROVIDERS
    assert get_provider("qwen3-mlx").__class__.__name__ == "Qwen3MlxProvider"


def test_fallback_chain_has_qwen3_mlx():
    from services.tts_fallback import _DEFAULT_FALLBACK, load_tts_fallback
    assert _DEFAULT_FALLBACK["chain"]["qwen3-mlx"] == "groq"
    assert load_tts_fallback()["chain"]["qwen3-mlx"] == "groq"


# ── sentence splitting ───────────────────────────────────────────────────────

def test_split_sentences_merges_short_fragments():
    from tts_providers.qwen3_mlx_provider import split_sentences
    text = ("Hi. This is a much longer sentence that easily passes the minimum. "
            "Ok. Another long sentence that also passes the forty char minimum.")
    out = split_sentences(text)
    assert len(out) == 2
    assert out[0].startswith("Hi. This is")
    assert out[1].startswith("Ok. Another")
    assert all(len(s) >= 40 for s in out)


def test_split_sentences_short_tail_joins_previous():
    from tts_providers.qwen3_mlx_provider import split_sentences
    out = split_sentences("This first sentence is comfortably over forty characters. Bye.")
    assert out == ["This first sentence is comfortably over forty characters. Bye."]


# ── happy path ───────────────────────────────────────────────────────────────

def test_generate_returns_wav_one_call_per_sentence(node):
    p = _provider()
    text = ("This first sentence is comfortably over forty characters long. "
            "And here is a second one that is also long enough to stand alone.")
    wav = p.generate_speech(text, voice="char-a")
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    rate = struct.unpack("<I", wav[24:28])[0]
    assert rate == RATE
    data_len = struct.unpack("<I", wav[40:44])[0]
    assert data_len == 2 * (RATE // 10) * 2  # two sentences x 0.1 s x 2 bytes
    posts = [c for c in _FakeNode.calls if c[0] == "/tts/stream"]
    assert len(posts) == 2
    path, body, headers = posts[0]
    assert body["voice"] == "char-a" and body["chunk"] == 6
    assert headers.get("X-Caller") == "ovu-test"
    assert headers.get("X-Purpose") == "unit-test"


def test_list_voices_filters_allow_and_deny(node):
    p = _provider()
    p.get_info()  # populates the server voice list via /health
    assert p.list_voices() == ["char-a", "char-b"]
    assert p.get_info()["status"] == "active"


# ── negative controls ────────────────────────────────────────────────────────

def test_denied_voice_is_refused_even_if_allow_listed(node):
    from tts_providers.base_provider import TTSVoiceNotFoundError
    with pytest.raises(TTSVoiceNotFoundError):
        _provider().generate_speech("A sentence that is long enough to be sent on.", voice="realperson-x")
    assert not [c for c in _FakeNode.calls if c[0] == "/tts/stream"]


def test_voice_not_on_allow_list_is_refused(node):
    from tts_providers.base_provider import TTSVoiceNotFoundError
    with pytest.raises(TTSVoiceNotFoundError):
        _provider().generate_speech("A sentence that is long enough to be sent on.", voice="char-z")


def test_unset_allow_list_fails_closed(node, monkeypatch):
    from tts_providers.base_provider import TTSVoiceNotFoundError
    monkeypatch.delenv("QWEN_MLX_TTS_VOICES")
    p = _provider()
    assert p.list_voices() == []
    with pytest.raises(TTSVoiceNotFoundError):
        p.generate_speech("A sentence that is long enough to be sent on.", voice="char-a")


def test_non_pcm_response_is_an_error(node):
    from tts_providers.base_provider import TTSGenerationError
    _FakeNode.mode = "html"
    with pytest.raises(TTSGenerationError, match="content-type"):
        _provider().generate_speech("A sentence that is long enough to be sent on.", voice="char-a")


def test_http_error_is_classified_for_no_retry(node):
    from tts_providers.base_provider import TTSGenerationError
    from services.tts import _classify_http_error
    _FakeNode.mode = "http500"
    with pytest.raises(TTSGenerationError) as ei:
        _provider().generate_speech("A sentence that is long enough to be sent on.", voice="char-a")
    assert _classify_http_error(str(ei.value)) == "5xx"


def test_empty_audio_is_an_error(node):
    from tts_providers.base_provider import TTSGenerationError
    _FakeNode.mode = "empty"
    with pytest.raises(TTSGenerationError, match="empty"):
        _provider().generate_speech("A sentence that is long enough to be sent on.", voice="char-a")


def test_unconfigured_url_is_inactive_and_raises(monkeypatch):
    from tts_providers.base_provider import TTSConfigurationError
    monkeypatch.delenv("QWEN_MLX_TTS_URL", raising=False)
    monkeypatch.setenv("QWEN_MLX_TTS_VOICES", "char-a")
    _reset_health()
    p = _provider()
    assert p.get_info()["status"] == "inactive"
    with pytest.raises(TTSConfigurationError):
        p.generate_speech("A sentence that is long enough to be sent on.", voice="char-a")


def test_server_down_raises(monkeypatch):
    from tts_providers.base_provider import TTSGenerationError
    monkeypatch.setenv("QWEN_MLX_TTS_URL", _dead_url())
    monkeypatch.setenv("QWEN_MLX_TTS_VOICES", "char-a")
    _reset_health()
    p = _provider()
    with pytest.raises(TTSGenerationError, match="unavailable"):
        p.generate_speech("A sentence that is long enough to be sent on.", voice="char-a")
    assert p.get_info()["status"] == "offline"


def test_server_down_falls_back_through_chain(monkeypatch):
    """Dead node -> services.tts walks qwen3-mlx -> groq and locks the sticky fallback."""
    import services.tts as tts
    monkeypatch.setenv("QWEN_MLX_TTS_URL", _dead_url())
    monkeypatch.setenv("QWEN_MLX_TTS_VOICES", "char-a")
    _reset_health()
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    real = tts._generate_with_provider
    seen = []

    def fake(provider_id, text, voice):
        seen.append((provider_id, voice))
        if provider_id == "qwen3-mlx":
            return real(provider_id, text, voice)   # the real provider, real dead socket
        return b"RIFF-fallback-audio"

    monkeypatch.setattr(tts, "_generate_with_provider", fake)
    state = {}
    out = tts.generate_tts_b64("A sentence that is long enough to be sent on.",
                               voice="char-a", tts_provider="qwen3-mlx", fallback_state=state)
    assert base64.b64decode(out) == b"RIFF-fallback-audio"
    assert seen[0][0] == "qwen3-mlx"
    assert ("groq", "troy") in seen          # unknown gender -> male fallback voice
    assert state == {"provider": "groq", "voice": "troy"}
