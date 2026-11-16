"""
Qwen3-TTS over a tailnet HTTP stream — provider id ``qwen3-mlx``.

Talks to one or more self-hosted servers that run Qwen3-TTS (mlx-audio on an
Apple-silicon node, or the same contract on a GPU box) and speak
reference-cloned voices. This is the same stream the voice bot's ``ttsOpen``
uses, so a profile on this provider sounds like the bot:

    GET  <base>/health      -> {"ok": true, "model": "...", "loaded": true, "voices": [...]}
    POST <base>/tts/stream  {"text": "<one sentence>", "voice": "<id>", "chunk": 6}
                            headers X-Caller / X-Purpose (no auth on the tailnet servers)
                            -> 200, raw s16le MONO PCM, no WAV header, no framing;
                               the rate is in X-Sample-Rate (24000 when absent);
                               the stream ends when the response ends. Non-200 = failure.

What this provider guarantees:
  * A LIST of hosts, tried in order (QWEN_MLX_TTS_URLS). A host that is down,
    busy past its first-byte timeout, missing the voice, or failing is skipped
    and the next one is tried, all inside one per-sentence budget.
  * FIRST-BYTE TIMEOUT per host (``timeout_ms``): the time from sending the
    request to the first byte of AUDIO. The server only answers once it holds
    its one-at-a-time generation lock, so this is the "is it free" timeout.
  * BUSY IS NOT DOWN. A /health GET can hang while a line is mid-generation.
    A health read that times out on an OPEN port marks the host BUSY: it keeps
    the voices it last reported and is tried after the free hosts. Only a
    refused/unreachable connection marks it DOWN.
  * VOICE PRE-CHECK. A host is only asked for a voice its /health listed. A
    voice no reachable host serves raises TTSVoiceNotFoundError, which
    services/tts.py treats as "fall back now" — the servers have no
    unknown-voice contract, so asking anyway could play a wrong voice.
  * VOICE ALLOW-LIST, fail closed. Only ids named in QWEN_MLX_TTS_VOICES are
    offered or spoken, minus any id matching QWEN_MLX_TTS_DENY_PREFIXES.
    Unset allow-list = no voice is allowed. The stream port is UNAUTHENTICATED
    on the tailnet and the servers carry voices that are not this tenant's
    (other tenants' voices, real people's clones), so this list is the guard.
  * ONE request per SENTENCE (long generations drift off the reference voice);
    every sentence's PCM is stitched into ONE WAV for the rest of the pipeline.
  * ONE request in flight per process (module-level lock).
  * CLEAR FAILURES within budget. Everything raises, so services/tts.py falls
    back (profile ``voice.fallback_provider`` first, then the chain in
    config/tts-fallback.json) inside the sentence's TTS window.

Configuration (environment; the tenant's compose .env — never the profile, so a
profile edit can neither add a host nor widen the voice list):
  QWEN_MLX_TTS_URLS           host list. JSON: [{"url": "http://h:61212/tts/stream", "timeout_ms": 6000}, ...]
                              (the bot's tts_stream_urls shape; plain strings allowed), or
                              CSV: "http://h:61212|6000,http://h2:61212". A trailing /tts/stream is optional.
  QWEN_MLX_TTS_URL            single host (used when QWEN_MLX_TTS_URLS is unset). Neither set = inactive.
  QWEN_MLX_TTS_VOICES         comma-separated allowed voice ids (required)
  QWEN_MLX_TTS_DENY_PREFIXES  comma-separated id prefixes that are never allowed
  QWEN_MLX_TTS_DEFAULT_VOICE  default voice (must be allowed; else first allowed)
  QWEN_MLX_TTS_CHUNK          server "chunk" steps (default 6)
  QWEN_MLX_TTS_CALLER         X-Caller header (default "openvoiceui")
  QWEN_MLX_TTS_PURPOSE        X-Purpose header (default "voice")
  QWEN_MLX_TTS_FIRST_BYTE_MS  default first-byte timeout per host (default 6000)
  QWEN_MLX_TTS_READ_TIMEOUT   seconds of silence allowed between audio reads after the first byte (default 8)
  QWEN_MLX_TTS_SENTENCE_BUDGET_MS  total ms one sentence may spend across all hosts (default 12000)
  QWEN_MLX_TTS_HEALTH_TIMEOUT seconds for a /health read (default 2)
  QWEN_MLX_TTS_CONNECT_TIMEOUT seconds for a TCP connect (default 2)
  QWEN_MLX_TTS_LOCK_TIMEOUT   max seconds to wait for the in-flight slot (default 30, capped by the budget)
  QWEN_MLX_TTS_TOKEN_FILE     optional: file holding a bearer token sent as Authorization on every call
  QWEN_MLX_TTS_TOKEN          optional: the bearer token itself (used when no _FILE is set)
                              Neither set = no Authorization header (the tailnet stream servers take none).
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from .base_provider import (
    TTSProvider,
    TTSConfigurationError,
    TTSGenerationError,
    TTSVoiceNotFoundError,
)

logger = logging.getLogger(__name__)

PROVIDER_ID = "qwen3-mlx"
MIN_SENTENCE_CHARS = 40
DEFAULT_SAMPLE_RATE = 24000

_HEALTH_TTL = {"ok": 30.0, "busy": 5.0, "down": 10.0, "bad": 10.0}
# Content types a PCM stream may arrive with. An error page (text/html, JSON) is
# refused so it is never played as noise. Absent = the bare bot.js contract.
_PCM_TYPES = ("audio/pcm", "audio/l16", "audio/raw", "application/octet-stream")

# base url -> {"state": ok|busy|down|bad, "checked_at", "voices", "model", "error"}
_HEALTH: Dict[str, Dict[str, Any]] = {}
_HEALTH_LOCK = threading.Lock()

# One request in flight per process: parallel sentence threads queue here.
_INFLIGHT = threading.Lock()

# The last generate_speech() outcome, for the shadow check (logs + get_info()).
_LAST: Dict[str, Any] = {}


def reset_state() -> None:
    """Forget every cached health reading and the last outcome (tests, config change)."""
    with _HEALTH_LOCK:
        _HEALTH.clear()
        _LAST.clear()


# ── configuration helpers (read live so an env change is honoured) ───────────

def _csv_env(name: str) -> List[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _normalise_base(url: str) -> Optional[str]:
    """'http://h:61212/tts/stream' or 'http://h:61212/' -> 'http://h:61212'. Non-http(s) -> None."""
    u = (url or "").strip().rstrip("/")
    if u.endswith("/tts/stream"):
        u = u[: -len("/tts/stream")].rstrip("/")
    parts = urlsplit(u)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return u


def stream_hosts() -> List[Tuple[str, int]]:
    """[(base_url, first_byte_timeout_ms)] in the configured order, de-duplicated."""
    default_fb = int(_float_env("QWEN_MLX_TTS_FIRST_BYTE_MS", 6000))
    raw = os.getenv("QWEN_MLX_TTS_URLS", "").strip()
    items: List[Tuple[str, Any]] = []
    if raw:
        if raw.startswith("["):
            try:
                for e in json.loads(raw):
                    if isinstance(e, str):
                        items.append((e, None))
                    elif isinstance(e, dict) and isinstance(e.get("url"), str):
                        items.append((e["url"], e.get("timeout_ms")))
            except ValueError:
                logger.warning("[Qwen3Mlx] QWEN_MLX_TTS_URLS is not valid JSON; no hosts from it")
        else:
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                url, _, ms = part.partition("|")
                items.append((url, ms or None))
    else:
        single = os.getenv("QWEN_MLX_TTS_URL", "").strip()
        if single:
            items.append((single, None))
    out: List[Tuple[str, int]] = []
    seen = set()
    for url, ms in items:
        base = _normalise_base(url)
        if not base or base in seen:
            if not base:
                logger.warning(f"[Qwen3Mlx] ignoring stream host {url!r} (not an http(s) URL)")
            continue
        seen.add(base)
        try:
            fb = int(ms) if ms not in (None, "") else default_fb
        except (TypeError, ValueError):
            fb = default_fb
        out.append((base, max(100, fb)))
    return out


def _base_url() -> str:
    """First configured host (back-compat helper); '' when none."""
    hosts = stream_hosts()
    return hosts[0][0] if hosts else ""


def voice_allowed(voice: Optional[str]) -> bool:
    """True only if `voice` is on the allow-list and matches no deny prefix."""
    if not voice:
        return False
    if any(voice.startswith(p) for p in _csv_env("QWEN_MLX_TTS_DENY_PREFIXES")):
        return False
    return voice in _csv_env("QWEN_MLX_TTS_VOICES")


def split_sentences(text: str, min_chars: int = MIN_SENTENCE_CHARS) -> List[str]:
    """Split on sentence boundaries; merge fragments shorter than min_chars.

    A short fragment joins the FOLLOWING sentence; a short tail joins the
    previous one. Text with no boundary comes back as a single item.
    """
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text.strip()) if p.strip()]
    out: List[str] = []
    carry = ""
    for p in parts:
        cur = (carry + " " + p).strip() if carry else p
        if len(cur) < min_chars:
            carry = cur
            continue
        out.append(cur)
        carry = ""
    if carry:
        if out:
            out[-1] = out[-1] + " " + carry
        else:
            out.append(carry)
    return out


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw little-endian PCM in a canonical 44-byte RIFF/WAVE header."""
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    return (struct.pack('<4sI4s', b'RIFF', 36 + len(pcm), b'WAVE')
            + struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, channels, sample_rate,
                          byte_rate, block_align, bits)
            + struct.pack('<4sI', b'data', len(pcm))
            + pcm)


def _auth_headers() -> dict:
    """Optional bearer for servers that require one (e.g. the GPU-box trial TTS). Read on every call so a rotated
    token file is picked up without a restart; an unreadable file means no header, and the server's 401 is then
    the visible error."""
    tok = ""
    path = os.getenv("QWEN_MLX_TTS_TOKEN_FILE", "").strip()
    if path:
        try:
            tok = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            tok = ""
    if not tok:
        tok = os.getenv("QWEN_MLX_TTS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


# ── health: ok / busy / down, per host ───────────────────────────────────────

def _set_health(base: str, **fields) -> Dict[str, Any]:
    with _HEALTH_LOCK:
        cur = _HEALTH.setdefault(base, {"state": None, "checked_at": 0.0, "voices": [],
                                        "model": None, "error": None})
        cur.update(fields)
        cur["checked_at"] = time.time()
        return dict(cur)


def check_health(base: str, force: bool = False) -> Dict[str, Any]:
    """GET <base>/health with a per-state cache. Returns a copy of the host's state.

    ok   — 200 + ok:true; `voices` refreshed.
    busy — the connection was ACCEPTED but no answer came in time (the server
           holds its generation lock): keeps the voices it last reported.
    down — connect refused / unreachable / connect timeout.
    bad  — answered, but not a usable health reply (non-200, ok:false, junk).
    """
    now = time.time()
    with _HEALTH_LOCK:
        cur = _HEALTH.get(base)
        if (not force and cur and cur.get("state")
                and now - cur["checked_at"] < _HEALTH_TTL.get(cur["state"], 10.0)):
            return dict(cur)
    timeout = httpx.Timeout(connect=_float_env("QWEN_MLX_TTS_CONNECT_TIMEOUT", 2.0),
                            read=_float_env("QWEN_MLX_TTS_HEALTH_TIMEOUT", 2.0),
                            write=2.0, pool=2.0)
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(f"{base}/health", headers=_auth_headers())
        if r.status_code != 200:
            return _set_health(base, state="bad", error=f"health HTTP {r.status_code}")
        body = r.json()
        if not body.get("ok"):
            return _set_health(base, state="bad", error="server reports ok=false")
        voices = [v for v in (body.get("voices") or []) if isinstance(v, str)]
        return _set_health(base, state="ok", voices=voices, model=body.get("model"), error=None)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        return _set_health(base, state="down", error=f"unreachable: {type(e).__name__}")
    except httpx.ReadTimeout:
        return _set_health(base, state="busy", error="health read timed out on an open port (busy)")
    except Exception as e:  # bad JSON, protocol error
        return _set_health(base, state="bad", error=f"bad health reply: {type(e).__name__}")


def _any_host_ok() -> bool:
    return any(check_health(b)["state"] in ("ok", "busy") for b, _ in stream_hosts())


# ── one sentence, one host ───────────────────────────────────────────────────

class _HostFailure(Exception):
    """One host could not serve one sentence. `kind` steers the host's health state."""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind  # down | busy | voice | http | stream


def _stream_once(base: str, sentence: str, voice: str, first_byte_ms: int,
                 deadline: float) -> Tuple[bytes, int, int]:
    """POST one sentence to one host. Returns (pcm, sample_rate, first_byte_ms).

    Raw http.client so the socket timeout can move between phases: connect,
    then the first-byte deadline (headers AND the first audio byte), then a
    rolling between-reads timeout, all clipped to the sentence deadline.
    """
    parts = urlsplit(base)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    path = (parts.path.rstrip("/") or "") + "/tts/stream"
    try:
        chunk = int(os.getenv("QWEN_MLX_TTS_CHUNK", "6"))
    except ValueError:
        chunk = 6
    body = json.dumps({"text": sentence, "voice": voice, "chunk": chunk}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Caller": os.getenv("QWEN_MLX_TTS_CALLER", "openvoiceui"),
        "X-Purpose": os.getenv("QWEN_MLX_TTS_PURPOSE", "voice"),
        "Content-Length": str(len(body)),
        **_auth_headers(),
    }
    read_gap = _float_env("QWEN_MLX_TTS_READ_TIMEOUT", 8.0)
    t0 = time.monotonic()
    fb_deadline = min(t0 + first_byte_ms / 1000.0, deadline)

    def _left(until: float) -> float:
        return until - time.monotonic()

    connect_to = min(_float_env("QWEN_MLX_TTS_CONNECT_TIMEOUT", 2.0), max(0.05, _left(fb_deadline)))
    conn = conn_cls(parts.hostname, port, timeout=connect_to)
    try:
        try:
            conn.connect()
        except (OSError, socket.timeout) as e:
            raise _HostFailure("down", f"connect failed: {type(e).__name__}") from e
        sock = conn.sock  # keep a handle: http.client drops conn.sock on Connection: close
        try:
            sock.settimeout(max(0.05, _left(fb_deadline)))
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
        except socket.timeout as e:
            raise _HostFailure("busy", f"no response within first-byte timeout {first_byte_ms}ms") from e
        except (OSError, http.client.HTTPException) as e:
            raise _HostFailure("stream", f"request failed: {type(e).__name__}") from e

        if resp.status != 200:
            try:
                sock.settimeout(1.0)
                detail = resp.read(200).decode("utf-8", "replace")
            except Exception:
                detail = ""
            kind = "voice" if resp.status in (400, 404, 422) else "http"
            raise _HostFailure(kind, f"TTS error {resp.status}: {detail[:200]}")
        ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype not in _PCM_TYPES:
            raise _HostFailure("stream", f"unexpected content-type {ctype!r} (want raw PCM)")
        rate_hdr = resp.getheader("X-Sample-Rate")
        try:
            rate = int(rate_hdr) if rate_hdr not in (None, "") else DEFAULT_SAMPLE_RATE
            if rate <= 0:
                raise ValueError
        except ValueError:
            raise _HostFailure("stream", f"bad X-Sample-Rate header {rate_hdr!r}")

        buf = bytearray()
        first_ms: Optional[int] = None
        while True:
            if resp.isclosed():  # a Content-Length body was read to its end
                break
            until = fb_deadline if first_ms is None else min(time.monotonic() + read_gap, deadline)
            left = _left(until)
            if left <= 0:
                if first_ms is None:
                    raise _HostFailure("busy", f"no audio within first-byte timeout {first_byte_ms}ms")
                raise _HostFailure("stream", "sentence budget exhausted mid-stream")
            try:
                sock.settimeout(max(0.05, left))
                part = resp.read1(65536)
            except socket.timeout as e:
                if first_ms is None:
                    raise _HostFailure("busy", f"no audio within first-byte timeout {first_byte_ms}ms") from e
                raise _HostFailure("stream", "audio stream stalled") from e
            except (OSError, http.client.HTTPException) as e:
                raise _HostFailure("stream", f"stream broke: {type(e).__name__}") from e
            if not part:
                break
            if first_ms is None:
                first_ms = int((time.monotonic() - t0) * 1000)
            buf.extend(part)
        if not buf:
            raise _HostFailure("stream", "empty audio response")
        if len(buf) % 2:
            buf = buf[:-1]  # s16le: drop a dangling half-sample
        return bytes(buf), rate, first_ms or 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


class Qwen3MlxProvider(TTSProvider):
    """Qwen3-TTS on tailnet stream hosts — one sentence per request, host failover, WAV out."""

    def __init__(self):
        super().__init__()
        self._status = "active" if stream_hosts() else "inactive"

    # ── host selection ───────────────────────────────────────────────────────

    def _hosts_for(self, voice: str) -> List[Tuple[str, int]]:
        """Hosts that may serve `voice`: free ones first, then busy ones (by their
        last-known voice list). Raises when none can."""
        hosts = stream_hosts()
        if not hosts:
            raise TTSConfigurationError(PROVIDER_ID, "no stream host set (QWEN_MLX_TTS_URLS / QWEN_MLX_TTS_URL)")
        free, busy, misses, errors = [], [], [], []
        for base, fb in hosts:
            st = check_health(base)
            if st["state"] in ("ok", "busy"):
                if voice in (st.get("voices") or []):
                    (free if st["state"] == "ok" else busy).append((base, fb))
                else:
                    misses.append(base)
            else:
                errors.append(f"{base}: {st.get('error')}")
        if free or busy:
            return free + busy
        if misses:
            raise TTSVoiceNotFoundError(
                PROVIDER_ID, f"voice {voice!r} is not served by any reachable stream host")
        raise TTSGenerationError(PROVIDER_ID, "server unavailable (" + "; ".join(errors) + ")")

    def _synth_sentence(self, sentence: str, voice: str, hosts: List[Tuple[str, int]],
                        deadline: float) -> Tuple[bytes, int, int, str]:
        """One sentence, trying each host in turn until `deadline` (time.monotonic())."""
        failures = []
        for base, fb in hosts:
            if deadline - time.monotonic() <= 0.05:
                failures.append("budget exhausted")
                break
            try:
                pcm, rate, first_ms = _stream_once(base, sentence, voice, fb, deadline)
                return pcm, rate, first_ms, base
            except _HostFailure as f:
                failures.append(f"{base}: {f}")
                if f.kind == "down":
                    _set_health(base, state="down", error=str(f))
                elif f.kind == "busy":
                    with _HEALTH_LOCK:
                        known = list((_HEALTH.get(base) or {}).get("voices") or [])
                    _set_health(base, state="busy", voices=known, error=str(f))
                logger.warning(f"[Qwen3Mlx] host failed, trying next: {base}: {f}")
        msg = "; ".join(failures) or "no host tried"
        m = re.search(r"TTS error (\d{3})", msg)
        # Keep the provider-wide "TTS error NNN" shape so services/tts.py can classify it.
        prefix = f"TTS error {m.group(1)}: " if m and len(hosts) == 1 else ""
        raise TTSGenerationError(PROVIDER_ID, f"{prefix}all stream hosts failed: {msg}")

    # ── synthesis ────────────────────────────────────────────────────────────

    def generate_speech(self, text: str, voice: Optional[str] = None, **kwargs) -> bytes:
        self.validate_text(text)
        if not stream_hosts():
            raise TTSConfigurationError(PROVIDER_ID, "QWEN_MLX_TTS_URLS / QWEN_MLX_TTS_URL not set")
        call_start = time.monotonic()  # the first sentence's budget covers health + slot wait
        voice = voice or self.get_default_voice()
        if not voice_allowed(voice):
            raise TTSVoiceNotFoundError(
                PROVIDER_ID, f"voice {voice!r} is not on the allow-list (QWEN_MLX_TTS_VOICES)")
        hosts = self._hosts_for(voice)

        budget_s = _float_env("QWEN_MLX_TTS_SENTENCE_BUDGET_MS", 12000.0) / 1000.0
        sentences = split_sentences(text)
        lock_timeout = max(0.0, min(_float_env("QWEN_MLX_TTS_LOCK_TIMEOUT", 30.0),
                                    call_start + budget_s - time.monotonic()))
        if not _INFLIGHT.acquire(timeout=lock_timeout):
            raise TTSGenerationError(PROVIDER_ID, f"busy: no slot within {lock_timeout:.1f}s")
        t0 = time.time()
        try:
            pcm = bytearray()
            rate = None
            first_audio_ms = None
            used = None
            for i, s in enumerate(sentences):
                # Stick to the host that served the first sentence (same voice files).
                order = hosts if used is None else [h for h in hosts if h[0] == used] + \
                    [h for h in hosts if h[0] != used]
                deadline = (call_start if i == 0 else time.monotonic()) + budget_s
                part, r, first_ms, used = self._synth_sentence(s, voice, order, deadline)
                if rate is None:
                    rate = r
                    first_audio_ms = first_ms  # time to the first PCM byte of the reply
                elif r != rate:
                    raise TTSGenerationError(
                        PROVIDER_ID, f"sample rate changed mid-reply ({rate} -> {r})")
                pcm.extend(part)
        except TTSGenerationError as e:
            _LAST.clear()
            _LAST.update(ok=False, voice=voice, error=str(e)[:300], at=time.time())
            raise
        finally:
            _INFLIGHT.release()
        total_ms = int((time.time() - t0) * 1000)
        wav = pcm_to_wav(bytes(pcm), rate or DEFAULT_SAMPLE_RATE)
        _LAST.clear()
        _LAST.update(ok=True, voice=voice, host=used, sample_rate=rate,
                     first_byte_ms=first_audio_ms, total_ms=total_ms,
                     sentences=len(sentences), pcm_bytes=len(pcm), at=time.time())
        logger.info(f"[Qwen3Mlx] TTS voice={voice} host={used} sentences={len(sentences)} "
                    f"chars={len(text)} pcm={len(pcm)}B rate={rate} "
                    f"first_byte={first_audio_ms}ms total={total_ms}ms")
        return wav

    # ── provider interface ───────────────────────────────────────────────────

    def list_voices(self) -> List[str]:
        """Allowed voices; intersected with what the reachable hosts report, when known."""
        allowed = [v for v in _csv_env("QWEN_MLX_TTS_VOICES") if voice_allowed(v)]
        served = set()
        known = False
        with _HEALTH_LOCK:
            for base, _ in stream_hosts():
                st = _HEALTH.get(base)
                if st and st.get("state") in ("ok", "busy") and st.get("voices"):
                    known = True
                    served.update(st["voices"])
        if known:
            allowed = [v for v in allowed if v in served]
        return allowed

    def get_default_voice(self) -> Optional[str]:
        d = os.getenv("QWEN_MLX_TTS_DEFAULT_VOICE", "").strip()
        if d and voice_allowed(d):
            return d
        voices = self.list_voices()
        return voices[0] if voices else None

    def is_available(self) -> bool:
        return bool(stream_hosts()) and _any_host_ok() and bool(self.list_voices())

    def health_check(self) -> dict:
        t = time.time()
        states = {b: check_health(b, force=True) for b, _ in stream_hosts()}
        ok = any(s["state"] in ("ok", "busy") for s in states.values())
        return {"ok": ok, "latency_ms": int((time.time() - t) * 1000),
                "detail": {b: s["state"] for b, s in states.items()} or "no host set"}

    def get_info(self) -> Dict[str, Any]:
        hosts = stream_hosts()
        states = {b: check_health(b) for b, _ in hosts}
        ok = any(s["state"] in ("ok", "busy") for s in states.values())
        if not hosts:
            status = "inactive"
        else:
            status = "active" if ok else "offline"
        self._status = status
        errors = [f"{b}: {s.get('error')}" for b, s in states.items() if s.get("error")]
        return {
            "name": "Qwen3-TTS (tailnet stream)",
            "provider_id": PROVIDER_ID,
            "status": status,
            "description": ("Qwen3-TTS on tailnet stream hosts (MLX node / GPU box) over HTTP. "
                            "Host list with failover, first-byte timeout, allow-listed voices, WAV output."),
            "quality": "very-high",
            "latency": "fast",
            "cost_per_minute": 0.0,
            "voices": self.list_voices(),
            "features": ["voice-cloning", "self-hosted", "free", "wav-output"],
            "requires_api_key": False,
            "audio_format": "wav",
            "sample_rate": DEFAULT_SAMPLE_RATE,
            # Host URLs (tailnet addresses) stay in the server log; the UI sees order + state.
            "hosts": [{"n": i + 1, "first_byte_ms": fb, "state": states[b]["state"]}
                      for i, (b, fb) in enumerate(hosts)],
            "model": next((s.get("model") for s in states.values() if s.get("model")), None),
            "last": {k: (v if k != "host" else next((i + 1 for i, (b, _) in enumerate(hosts) if b == v), None))
                     for k, v in _LAST.items()},
            "available": ok,
            "error": None if ok else (f"{len(errors)} of {len(hosts)} stream hosts unusable" if hosts
                                      else "no stream host set"),
        }
