"""
Tailnet stream parity for qwen3-mlx: host LIST failover, first-byte timeout,
busy-vs-down health, voice pre-check, X-Sample-Rate default, chunked PCM, and
the profile-chosen fallback in services/tts.py.

Real HTTP servers on 127.0.0.1 stand in for the stream hosts, so every timeout
and failover is exercised over an actual socket (no mocked transport).
"""

import base64
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SAMPLE = struct.pack("<h", 1234)


def _pause(seconds):
    """The fake hosts wait with Event.wait, not time.sleep: _spy() patches time.sleep
    (no-retry guard) and a fake host must not die of that guard."""
    threading.Event().wait(seconds)


def _make_host(**cfg):
    """Start one fake stream host. cfg keys:
    voices, rate (None = no X-Sample-Rate), ctype (None = no Content-Type),
    header_delay, body_delay, stall_after_first, health_delay, chunks, odd_tail, status
    """
    state = {"posts": [], "health_gets": 0}
    c = state["cfg"] = {"voices": ["char-a", "char-b", "realperson-x"], "rate": 24000, "ctype": "audio/pcm",
         "header_delay": 0.0, "body_delay": 0.0, "stall_after_first": 0.0, "health_delay": 0.0,
         "chunks": 3, "odd_tail": False, "status": 200}
    c.update(cfg)

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            state["health_gets"] += 1
            if self.path != "/health":
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            if c["health_delay"]:
                _pause(c["health_delay"])
            body = json.dumps({"ok": True, "model": "fake", "loaded": True, "voices": c["voices"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or b"{}")
            state["posts"].append((self.path, req, {k.lower(): v for k, v in self.headers.items()}))
            if c["header_delay"]:
                _pause(c["header_delay"])
            if c["status"] != 200:
                msg = b"unknown voice"
                self.send_response(c["status"])
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            self.send_response(200)
            if c["ctype"]:
                self.send_header("Content-Type", c["ctype"])
            if c["rate"] is not None:
                self.send_header("X-Sample-Rate", str(c["rate"]))
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            if c["body_delay"]:
                _pause(c["body_delay"])
            try:
                for i in range(c["chunks"]):
                    payload = SAMPLE * 100
                    if i == 0 and c["odd_tail"]:
                        payload = payload + b"\x07"   # a half sample split across chunks
                    self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                    self.wfile.flush()
                    if i == 0 and c["stall_after_first"]:
                        _pause(c["stall_after_first"])
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", state


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def env(monkeypatch):
    from tts_providers import qwen3_mlx_provider as m
    for k in ("QWEN_MLX_TTS_URL", "QWEN_MLX_TTS_URLS", "QWEN_MLX_TTS_TOKEN", "QWEN_MLX_TTS_TOKEN_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QWEN_MLX_TTS_VOICES", "char-a,char-b,char-c,realperson-x")
    monkeypatch.setenv("QWEN_MLX_TTS_DENY_PREFIXES", "realperson")
    monkeypatch.setenv("QWEN_MLX_TTS_HEALTH_TIMEOUT", "0.3")
    monkeypatch.setenv("QWEN_MLX_TTS_CONNECT_TIMEOUT", "0.5")
    m.reset_state()
    servers = []

    def host(**cfg):
        srv, url, st = _make_host(**cfg)
        servers.append(srv)
        return url, st

    yield monkeypatch, host
    for s in servers:
        s.shutdown()
    m.reset_state()


def _urls(monkeypatch, *entries):
    monkeypatch.setenv("QWEN_MLX_TTS_URLS", json.dumps(
        [{"url": u + "/tts/stream", "timeout_ms": ms} for u, ms in entries]))


def _provider():
    from tts_providers.qwen3_mlx_provider import Qwen3MlxProvider
    return Qwen3MlxProvider()


def _wav_rate_and_data(wav):
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    return struct.unpack("<I", wav[24:28])[0], wav[44:]


LINE = "This sentence is comfortably over forty characters long for one call."


# ── host list parsing ────────────────────────────────────────────────────────

def test_hosts_json_list_strips_stream_path_and_dedupes(monkeypatch):
    from tts_providers.qwen3_mlx_provider import stream_hosts
    monkeypatch.setenv("QWEN_MLX_TTS_URLS", json.dumps([
        {"url": "http://10.0.0.1:61212/tts/stream", "timeout_ms": 6000},
        "http://10.0.0.2:61212/",
        {"url": "http://10.0.0.1:61212", "timeout_ms": 1},   # duplicate host: first wins
        {"url": "file:///etc/passwd"},                          # not http(s): ignored
    ]))
    monkeypatch.setenv("QWEN_MLX_TTS_FIRST_BYTE_MS", "4000")
    assert stream_hosts() == [("http://10.0.0.1:61212", 6000), ("http://10.0.0.2:61212", 4000)]


def test_hosts_csv_and_single_url_fallback(monkeypatch):
    from tts_providers.qwen3_mlx_provider import stream_hosts
    monkeypatch.setenv("QWEN_MLX_TTS_URLS", "http://a:1|2500, http://b:2")
    monkeypatch.delenv("QWEN_MLX_TTS_FIRST_BYTE_MS", raising=False)
    assert stream_hosts() == [("http://a:1", 2500), ("http://b:2", 6000)]
    monkeypatch.delenv("QWEN_MLX_TTS_URLS")
    monkeypatch.setenv("QWEN_MLX_TTS_URL", "http://c:3")
    assert stream_hosts() == [("http://c:3", 6000)]


# ── PCM stream handling ──────────────────────────────────────────────────────

def test_chunked_pcm_is_stitched_and_half_sample_dropped(env):
    mp, host = env
    url, st = host(chunks=3, odd_tail=True)
    _urls(mp, (url, 2000))
    wav = _provider().generate_speech(LINE, voice="char-a")
    rate, data = _wav_rate_and_data(wav)
    assert rate == 24000
    # 3 chunks x 100 samples + 1 stray byte -> the stray byte is dropped at the end
    assert len(data) == 3 * 100 * 2
    assert len(data) % 2 == 0
    path, req, hdrs = st["posts"][0]
    assert path == "/tts/stream" and req == {"text": LINE, "voice": "char-a", "chunk": 6}
    assert "authorization" not in hdrs


def test_missing_sample_rate_header_defaults_to_24000(env):
    mp, host = env
    url, _ = host(rate=None, ctype=None)          # the bare bot.js contract: no rate, no type
    _urls(mp, (url, 2000))
    rate, _ = _wav_rate_and_data(_provider().generate_speech(LINE, voice="char-a"))
    assert rate == 24000


def test_sample_rate_header_is_honoured(env):
    mp, host = env
    url, _ = host(rate=16000)
    _urls(mp, (url, 2000))
    rate, _ = _wav_rate_and_data(_provider().generate_speech(LINE, voice="char-a"))
    assert rate == 16000


def test_error_page_content_type_is_refused(env):
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    url, _ = host(ctype="text/html")
    _urls(mp, (url, 2000))
    with pytest.raises(TTSGenerationError, match="content-type"):
        _provider().generate_speech(LINE, voice="char-a")


# ── first-byte timeout ───────────────────────────────────────────────────────

def test_first_byte_timeout_on_headers_fails_fast_and_marks_busy(env):
    from tts_providers import qwen3_mlx_provider as m
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    url, _ = host(header_delay=1.5)
    _urls(mp, (url, 300))
    t = time.monotonic()
    with pytest.raises(TTSGenerationError, match="first-byte"):
        _provider().generate_speech(LINE, voice="char-a")
    assert time.monotonic() - t < 1.2
    assert m._HEALTH[url]["state"] == "busy"
    assert "char-a" in m._HEALTH[url]["voices"]      # busy keeps the last-known voices


def test_headers_without_audio_do_not_count_as_first_byte(env):
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    url, _ = host(body_delay=1.5)                    # 200 + headers at once, audio late
    _urls(mp, (url, 300))
    t = time.monotonic()
    with pytest.raises(TTSGenerationError, match="no audio within first-byte"):
        _provider().generate_speech(LINE, voice="char-a")
    assert time.monotonic() - t < 1.2


def test_slow_first_byte_inside_timeout_succeeds(env):
    mp, host = env
    url, _ = host(header_delay=0.2)
    _urls(mp, (url, 2000))
    _provider().generate_speech(LINE, voice="char-a")
    from tts_providers import qwen3_mlx_provider as m
    assert m._LAST["ok"] is True and m._LAST["first_byte_ms"] >= 150


# ── list-of-urls failover ────────────────────────────────────────────────────

def test_dead_first_host_fails_over_to_second(env):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    dead = _dead_url()
    url, st = host()
    _urls(mp, (dead, 2000), (url, 2000))
    _provider().generate_speech(LINE, voice="char-a")
    assert len(st["posts"]) == 1
    assert m._HEALTH[dead]["state"] == "down"
    assert m._LAST["host"] == url


def test_busy_first_host_fails_over_inside_budget(env):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    slow, slow_st = host(header_delay=2.0)
    good, good_st = host()
    _urls(mp, (slow, 300), (good, 2000))
    m.check_health(slow); m.check_health(good)        # both answer health: both 'ok'
    t = time.monotonic()
    _provider().generate_speech(LINE, voice="char-a")
    assert time.monotonic() - t < 1.5
    assert len(slow_st["posts"]) == 1 and len(good_st["posts"]) == 1
    assert m._LAST["host"] == good


def test_stall_mid_stream_fails_over(env, monkeypatch):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    monkeypatch.setenv("QWEN_MLX_TTS_READ_TIMEOUT", "0.3")
    stall, _ = host(stall_after_first=1.5)
    good, _ = host()
    _urls(mp, (stall, 2000), (good, 2000))
    _provider().generate_speech(LINE, voice="char-a")
    assert m._LAST["host"] == good


def test_sentence_budget_caps_total_time_across_hosts(env, monkeypatch):
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    monkeypatch.setenv("QWEN_MLX_TTS_SENTENCE_BUDGET_MS", "600")
    a, _ = host(header_delay=2.0)
    b, _ = host(header_delay=2.0)
    c, c_st = host(header_delay=2.0)
    _urls(mp, (a, 5000), (b, 5000), (c, 5000))
    t = time.monotonic()
    with pytest.raises(TTSGenerationError):
        _provider().generate_speech(LINE, voice="char-a")
    assert time.monotonic() - t < 1.3


def test_waiting_for_the_in_flight_slot_counts_against_the_budget(env, monkeypatch):
    from tts_providers import qwen3_mlx_provider as m
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    monkeypatch.setenv("QWEN_MLX_TTS_SENTENCE_BUDGET_MS", "400")
    url, st = host()
    _urls(mp, (url, 2000))
    assert m._INFLIGHT.acquire(timeout=1)          # another sentence holds the slot
    try:
        t = time.monotonic()
        with pytest.raises(TTSGenerationError, match="no slot"):
            _provider().generate_speech(LINE, voice="char-a")
        assert time.monotonic() - t < 0.9
    finally:
        m._INFLIGHT.release()
    assert st["posts"] == []


def test_health_timeout_on_open_port_is_busy_not_down(env):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    url, _ = host(health_delay=1.0)
    assert m.check_health(url)["state"] == "busy"
    assert m.check_health(_dead_url())["state"] == "down"


def test_busy_host_is_still_tried_with_its_last_known_voices(env):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    url, st = host()
    _urls(mp, (url, 2000))
    assert m.check_health(url)["state"] == "ok"
    m._set_health(url, state="busy", error="simulated")  # mid-generation: health hangs
    _provider().generate_speech(LINE, voice="char-a")
    assert len(st["posts"]) == 1


# ── sticky demotion (voice-assets DECISIONS 2026-11-09) ─────────────────────

def test_wedged_host_costs_one_real_line_then_replies_go_straight_to_fallback(env):
    """Accepts the connection, never sends audio: the first real line pays the
    first-byte timeout once; the next line pays ~0 ms and is spoken by the fallback,
    with no POST and no /health read on the wedged host."""
    import services.tts as tts
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    mp.setenv("QWEN_MLX_TTS_RECHECK_S", "60")          # no recheck inside this test
    url, st = host(header_delay=3.0)                   # wedged: never answers the stream
    _urls(mp, (url, 300))
    seen = _spy(mp, tts)
    mp.setattr(tts, "_profile_fallback", lambda p, v: ("groq", "austin"))

    t = time.monotonic()
    out1 = tts.generate_tts_b64(LINE, voice="char-a", tts_provider="qwen3-mlx", fallback_state={})
    first = time.monotonic() - t
    assert base64.b64decode(out1) == b"RIFF-fallback-groq"
    assert 0.25 <= first < 1.5                         # paid the first-byte timeout, once
    assert m.is_demoted(url)
    posts, gets = len(st["posts"]), st["health_gets"]

    t = time.monotonic()
    out2 = tts.generate_tts_b64(LINE, voice="char-a", tts_provider="qwen3-mlx", fallback_state={})
    second = time.monotonic() - t
    assert base64.b64decode(out2) == b"RIFF-fallback-groq"
    assert second < 0.1                                # no silence added on the next reply
    assert len(st["posts"]) == posts and st["health_gets"] == gets
    assert seen[-1] == ("groq", "austin")
    assert _provider().get_info()["hosts"][0]["state"] == "demoted"


def test_empty_200_counts_as_no_audio_and_demotes(env):
    from tts_providers import qwen3_mlx_provider as m
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    mp.setenv("QWEN_MLX_TTS_RECHECK_S", "60")
    url, _ = host(chunks=0)                            # connect OK, 200, zero audio
    _urls(mp, (url, 2000))
    with pytest.raises(TTSGenerationError, match="empty audio"):
        _provider().generate_speech(LINE, voice="char-a")
    assert m.is_demoted(url)


def test_demote_after_knob_counts_consecutive_no_audio_lines(env):
    from tts_providers import qwen3_mlx_provider as m
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    mp.setenv("QWEN_MLX_TTS_RECHECK_S", "60")
    mp.setenv("QWEN_MLX_TTS_DEMOTE_AFTER", "2")
    url, st = host(header_delay=3.0)
    _urls(mp, (url, 200))
    with pytest.raises(TTSGenerationError):
        _provider().generate_speech(LINE, voice="char-a")
    assert not m.is_demoted(url)                       # one failure < 2
    with pytest.raises(TTSGenerationError):
        _provider().generate_speech(LINE, voice="char-a")
    assert m.is_demoted(url)
    assert len(st["posts"]) == 2


def test_offpath_recheck_restores_the_host_only_after_it_speaks(env):
    from tts_providers import qwen3_mlx_provider as m
    from tts_providers.base_provider import TTSGenerationError
    mp, host = env
    mp.setenv("QWEN_MLX_TTS_RECHECK_S", "0.15")
    url, st = host(header_delay=3.0)
    _urls(mp, (url, 300))
    with pytest.raises(TTSGenerationError):
        _provider().generate_speech(LINE, voice="char-a")
    assert m.is_demoted(url)

    # still wedged: background rechecks fail and the host stays demoted
    end = time.monotonic() + 3.0
    while time.monotonic() < end and not any(h.get("x-purpose") == "recheck" for _, _, h in st["posts"]):
        time.sleep(0.05)
    rechecks = [p for p in st["posts"] if p[2].get("x-purpose") == "recheck"]
    assert rechecks and rechecks[0][1]["voice"] == "char-a"
    time.sleep(0.4)
    assert m.is_demoted(url)

    st["cfg"]["header_delay"] = 0.0                    # the host can speak again
    end = time.monotonic() + 3.0
    while time.monotonic() < end and m.is_demoted(url):
        time.sleep(0.05)
    assert not m.is_demoted(url)                       # restored by the off-path recheck

    real_before = len([p for p in st["posts"] if p[2].get("x-purpose") != "recheck"])
    _provider().generate_speech(LINE, voice="char-a")  # a real line is served by the host again
    real_after = len([p for p in st["posts"] if p[2].get("x-purpose") != "recheck"])
    assert real_after == real_before + 1 and m._LAST["host"] == url


def test_hanging_health_busy_host_stays_tryable_and_is_not_demoted(env):
    """The normal shared-generator case: /health hangs while the bot generates.
    That BUSY reading must not demote; the host is still asked and serves the line."""
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    mp.setenv("QWEN_MLX_TTS_RECHECK_S", "60")
    url, st = host()
    _urls(mp, (url, 2000))
    assert m.check_health(url)["state"] == "ok"        # voices learned
    st["cfg"]["health_delay"] = 1.0                    # now /health hangs past the 0.3 s read
    m._HEALTH[url]["checked_at"] = 0.0                 # cache expired: the reply re-reads health
    for _ in range(2):
        _provider().generate_speech(LINE, voice="char-a")
        assert m._HEALTH[url]["state"] == "busy"
        assert not m.is_demoted(url)
        m._HEALTH[url]["checked_at"] = 0.0
    assert len(st["posts"]) == 2 and m._LAST["host"] == url


def test_demoted_host_is_skipped_and_second_host_serves_without_delay(env):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    mp.setenv("QWEN_MLX_TTS_RECHECK_S", "60")
    wedged, w_st = host(header_delay=3.0)
    good, g_st = host()
    _urls(mp, (wedged, 300), (good, 2000))
    _provider().generate_speech(LINE, voice="char-a")  # pays 300 ms once, served by `good`
    assert m.is_demoted(wedged) and m._LAST["host"] == good
    t = time.monotonic()
    _provider().generate_speech(LINE, voice="char-a")
    assert time.monotonic() - t < 0.5
    assert len(w_st["posts"]) == 1 and len(g_st["posts"]) == 2


# ── voice pre-check ──────────────────────────────────────────────────────────

def test_voice_missing_on_host_is_refused_without_a_post(env):
    from tts_providers.base_provider import TTSVoiceNotFoundError
    mp, host = env
    url, st = host(voices=["char-b"])
    _urls(mp, (url, 2000))
    with pytest.raises(TTSVoiceNotFoundError):
        _provider().generate_speech(LINE, voice="char-a")
    assert st["posts"] == []


def test_voice_routes_to_the_host_that_has_it(env):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    a, a_st = host(voices=["char-b"])
    b, b_st = host(voices=["char-a"])
    _urls(mp, (a, 2000), (b, 2000))
    _provider().generate_speech(LINE, voice="char-a")
    assert a_st["posts"] == [] and len(b_st["posts"]) == 1


def test_host_404_for_voice_fails_over(env):
    from tts_providers import qwen3_mlx_provider as m
    mp, host = env
    a, _ = host(status=404)
    b, _ = host()
    _urls(mp, (a, 2000), (b, 2000))
    _provider().generate_speech(LINE, voice="char-a")
    assert m._LAST["host"] == b


def test_allow_list_beats_what_a_host_serves(env):
    from tts_providers.base_provider import TTSVoiceNotFoundError
    mp, host = env
    url, st = host(voices=["realperson-x", "not-listed"])
    _urls(mp, (url, 2000))
    for v in ("realperson-x", "not-listed"):
        with pytest.raises(TTSVoiceNotFoundError):
            _provider().generate_speech(LINE, voice=v)
    assert st["posts"] == []


def test_info_hides_host_urls(env):
    mp, host = env
    url, _ = host()
    _urls(mp, (url, 2000))
    info = _provider().get_info()
    assert info["status"] == "active"
    assert url not in json.dumps(info)
    assert info["hosts"] == [{"n": 1, "first_byte_ms": 2000, "state": "ok"}]


# ── services/tts.py: fallback inside the window ─────────────────────────────

def _spy(monkeypatch, tts):
    real = tts._generate_with_provider
    seen = []

    def fake(provider_id, text, voice):
        seen.append((provider_id, voice))
        if provider_id == "qwen3-mlx":
            return real(provider_id, text, voice)    # real provider, real sockets
        return b"RIFF-fallback-" + provider_id.encode()

    monkeypatch.setattr(tts, "_generate_with_provider", fake)
    monkeypatch.setattr(tts.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("no retry sleep")))
    return seen


def test_voice_precheck_miss_falls_back_to_profile_engine_without_retry(env):
    import services.tts as tts
    mp, host = env
    url, st = host(voices=["char-b"])
    _urls(mp, (url, 2000))
    seen = _spy(mp, tts)
    mp.setattr(tts, "_profile_fallback", lambda p, v: ("groq", "austin") if (p, v) == ("qwen3-mlx", "char-a") else None)
    state = {}
    out = tts.generate_tts_b64(LINE, voice="char-a", tts_provider="qwen3-mlx", fallback_state=state)
    assert base64.b64decode(out) == b"RIFF-fallback-groq"
    assert seen == [("qwen3-mlx", "char-a"), ("groq", "austin")]
    assert st["posts"] == []
    assert state == {"provider": "groq", "voice": "austin"}


def test_dead_hosts_fall_back_inside_the_sentence_window(env):
    import services.tts as tts
    mp, host = env
    _urls(mp, (_dead_url(), 6000), (_dead_url(), 6000))
    seen = _spy(mp, tts)
    mp.setattr(tts, "_profile_fallback", lambda p, v: ("groq", "austin"))
    t = time.monotonic()
    out = tts.generate_tts_b64(LINE, voice="char-a", tts_provider="qwen3-mlx", fallback_state={})
    assert base64.b64decode(out) == b"RIFF-fallback-groq"
    assert time.monotonic() - t < 3.0
    assert [s[0] for s in seen] == ["qwen3-mlx", "groq"]     # one attempt, then the profile's engine


def test_no_profile_fallback_uses_the_chain(env):
    import services.tts as tts
    mp, host = env
    _urls(mp, (_dead_url(), 1000))
    seen = _spy(mp, tts)
    mp.setattr(tts, "_profile_fallback", lambda p, v: None)
    out = tts.generate_tts_b64(LINE, voice="char-a", tts_provider="qwen3-mlx", fallback_state={})
    assert base64.b64decode(out) == b"RIFF-fallback-groq"
    assert seen[1] == ("groq", "troy")


def test_profile_fallback_only_for_the_profiles_own_engine_and_voice(monkeypatch):
    import types
    import services.tts as tts
    import routes.profiles as rp
    import profiles.manager as pm
    from profiles.manager import VoiceConfig
    voice = VoiceConfig(tts_provider="qwen3-mlx", voice_id="char-a",
                        fallback_provider="groq", fallback_voice="austin")
    prof = types.SimpleNamespace(voice=voice)
    monkeypatch.setattr(rp, "_active_profile_id", "p1", raising=False)
    monkeypatch.setattr(pm, "get_profile_manager",
                        lambda: types.SimpleNamespace(get_profile=lambda pid: prof))
    assert tts._profile_fallback("qwen3-mlx", "char-a") == ("groq", "austin")
    assert tts._profile_fallback("qwen3-mlx", "char-b") is None      # a preview of another voice
    assert tts._profile_fallback("groq", "char-a") is None
    voice.fallback_voice = None
    assert tts._profile_fallback("qwen3-mlx", "char-a") == ("groq", "troy")   # same-gender map
    voice.fallback_provider = None
    assert tts._profile_fallback("qwen3-mlx", "char-a") is None


def test_profile_save_rejects_a_fallback_voice_not_on_its_engine():
    from profiles.manager import get_profile_manager
    errs = get_profile_manager().validate_profile({
        "id": "x", "name": "x", "system_prompt": "x", "llm": {"provider": "gateway"},
        "voice": {"tts_provider": "qwen3-mlx", "voice_id": "char-a",
                  "fallback_provider": "groq", "fallback_voice": "M1"}})
    assert any("fallback_voice" in e for e in errs)
