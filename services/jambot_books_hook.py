"""JamBot Books — in-container provider-call recorder (file-drop leg).

The openvoiceui container calls Groq (STT/TTS/vision) and Suno directly; these
never pass through the OpenClaw JSONL (so logscrape misses them) and the books
writer binds 127.0.0.1 on the host (unreachable from the container). The
mitmproxy leg is parked behind a Mike-ack hold (2026-05-27 OVU lockout).

So we record locally: append one api_call JSON line per tracked response to a
file on a host-bind-mounted dir. The host-side books scraper tails it and POSTs
to the ledger. No network, no SDK monkeypatching, no rebuild dependency.

SAFETY (voice must never break):
  - The hook NEVER reads/consumes a streaming or audio body (that would starve
    the SDK of its own response). It only parses JSON responses, and httpx
    caches .read() so the SDK still gets the body. Audio (STT input, TTS mp3)
    is recorded as a call with no token fields — Groq audio carries no usage
    anyway.
  - Every path is wrapped; any failure is swallowed. The hook can only ever
    append a line to a file — it cannot raise into the request path.
"""
import json
import os
import threading
from datetime import datetime, timezone

_QUEUE = os.environ.get(
    "BOOKS_QUEUE_FILE",
    "/app/runtime/canvas-pages/.jambot-books/queue.jsonl",
)


def _source_container() -> str:
    """Stamp the row with a STABLE, origin-resolvable caller name.

    HOSTNAME is the docker hex id (changes on recreate, and the JamFlow
    origin-resolver can't map it to a tenant → pulse starts mid-chain). The
    OVU container always carries JAMBOT_TENANT, so emit `openvoiceui-<tenant>`
    which JamFlow's _books_origin maps to the tenant (OVU user) node."""
    tenant = os.environ.get("JAMBOT_TENANT") or os.environ.get("HOST_TENANT")
    if tenant:
        return f"openvoiceui-{tenant}"
    return os.environ.get("HOSTNAME", "")


_HOSTS = {
    "api.groq.com": "groq",
    "studio-api.suno.ai": "suno",
    "apibox.erweima.ai": "suno",      # sunoapi.org gateway, if used
    "api.openai.com": "openai",
}
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _record(response) -> None:
    try:
        host = response.url.host
        provider = _HOSTS.get(host)
        if not provider:
            return

        latency_ms = None
        try:
            el = getattr(response, "elapsed", None)
            if el is not None:
                latency_ms = int(el.total_seconds() * 1000)
        except Exception:
            pass

        model = in_tok = out_tok = cache = None
        units = None
        path = str(response.url.path)
        # ONLY touch the body when it's JSON — never consume an audio/stream body.
        ctype = ""
        try:
            ctype = response.headers.get("content-type", "").lower()
        except Exception:
            pass
        if "json" in ctype:
            try:
                # In an httpx response hook the body isn't read yet; read() it
                # (cached, so the SDK still gets it). Only safe for non-streamed
                # JSON — audio/stream bodies are skipped above via content-type.
                response.read()
                b = response.json()  # httpx caches; SDK can still read it
                if isinstance(b, dict):
                    model = b.get("model")
                    u = b.get("usage") or {}
                    in_tok = u.get("prompt_tokens") or u.get("input_tokens")
                    out_tok = u.get("completion_tokens") or u.get("output_tokens")
                    details = u.get("prompt_tokens_details") or {}
                    cache = details.get("cached_tokens") or u.get("cache_read_input_tokens")
            except Exception:
                pass

        # TTS (audio/speech) carries NO usage in the (audio) response — Groq bills
        # it per CHARACTER of input text, which lives in the REQUEST body. Parse
        # the request (cached bytes, never touches the response audio stream) so
        # the call books a real usage count instead of all-NULL. units="chars".
        if in_tok is None and "/audio/speech" in path:
            try:
                req = getattr(response, "request", None)
                rbody = getattr(req, "content", None) if req is not None else None
                if rbody:
                    rb = json.loads(rbody)
                    if isinstance(rb, dict):
                        model = model or rb.get("model")
                        txt = rb.get("input")
                        if isinstance(txt, str):
                            in_tok = len(txt)
                            units = "chars"
            except Exception:
                pass

        extras = {"leg": "ovu-file-drop"}
        if units:
            extras["units"] = units
        rec = {
            "type": "api_call",
            "capture_method": "sdk",
            "provider": provider,
            "host": host,
            "endpoint": path,
            "model": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cached_tokens": cache,
            "latency_ms": latency_ms,
            "response_status": getattr(response, "status_code", None),
            "source_container": _source_container(),
            "ts": _now_iso(),
            "provider_extras": extras,
        }
        os.makedirs(os.path.dirname(_QUEUE), exist_ok=True)
        with _lock, open(_QUEUE, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass  # never break a voice turn


_PROVIDER_HOST = {
    "suno": "api.sunoapi.org",
    "groq": "api.groq.com",
    "fal": "fal.run",
    "hf": "router.huggingface.co",
    "resemble": "f.cluster.resemble.ai",
    "deepgram": "api.deepgram.com",
    "supertonic": "supertonic-tts",  # shared local container (jambot-shared net), cost 0
}


def record_provider_call(provider: str, endpoint: str = "", op: str | None = None,
                         units: str | None = None, status: int | None = 200,
                         model: str | None = None, input_tokens=None,
                         output_tokens=None) -> None:
    """Append a ready api_call row to the books queue for providers that don't
    go through an httpx client we can hook (e.g. Suno uses `requests`). Called
    explicitly at the provider call site. Fire-and-forget, never raises."""
    try:
        rec = {
            "type": "api_call",
            "capture_method": "sdk",
            "provider": provider,
            "host": _PROVIDER_HOST.get(provider, ""),
            "endpoint": endpoint,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": None,
            "latency_ms": None,
            "response_status": status,
            "source_container": _source_container(),
            "ts": _now_iso(),
            "provider_extras": {"leg": "ovu-file-drop", "op": op, "units": units},
        }
        os.makedirs(os.path.dirname(_QUEUE), exist_ok=True)
        with _lock, open(_QUEUE, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def attach(httpx_client) -> None:
    """Attach the recorder to an httpx client's response event hooks (idempotent)."""
    try:
        hooks = httpx_client.event_hooks.get("response", [])
        # idempotent — don't double-add
        if _record in hooks:
            return
        hooks.append(_record)
        httpx_client.event_hooks["response"] = hooks
    except Exception:
        pass


def attach_groq(groq_client) -> None:
    """Attach to a `groq` SDK client (its httpx client is at ._client)."""
    try:
        inner = getattr(groq_client, "_client", None)
        if inner is not None:
            attach(inner)
    except Exception:
        pass
