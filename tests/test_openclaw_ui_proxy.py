"""
/openclaw-ui/ HTTP reverse proxy: confined to the prefix, allowlisted headers,
no foreign service workers.

Review finding R2 (2026-11-17, contractors-choice claim-check 4): the proxy
built its upstream URL with an f-string from Flask's <path:path>, which has
already percent-decoded the request. `/openclaw-ui/..%2F..%2Fx` became
`http://openclaw:18789/openclaw-ui/../../x`, and requests/urllib3 normalised
that to `http://openclaw:18789/x`, so any gateway path was reachable (and the
route is in app.py's _PUBLIC_PREFIXES, so without a session). The proxy also
relayed every upstream response header except XFO/CSP/hop headers, so an
agent-controlled gateway plugin could send Set-Cookie, Service-Worker-Allowed,
Clear-Site-Data or Cross-Origin-Opener-Policy on the tenant's own origin.

What the legitimate Control UI (openclaw 2026.5.7 dist/control-ui) fetches
through this proxy, all under /openclaw-ui/: index.html and SPA routes,
assets/*.js|css, favicon*, apple-touch-icon.png, manifest.webmanifest, sw.js
(registered as a service worker with the default ./ scope),
__openclaw/control-ui-config.json, avatar/<encodeURIComponent(id)>[?meta=1],
__openclaw__/assistant-media?..., and the gateway's own redirect to
`/openclaw-ui/?<search>`. Its RPC is the /openclaw-ui WebSocket (server.py),
which this file does not cover.
"""

import posixpath
from urllib.parse import unquote, urlsplit

import pytest
import requests

UPSTREAM = "http://openclaw:18789"
PREFIX = "/openclaw-ui/"


# ---------------------------------------------------------------------------
# Fake gateway
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status=200, headers=None, body=b"ok"):
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict(headers or {"Content-Type": "text/plain"})
        self._body = body

    def iter_content(self, chunk_size=8192):
        yield self._body


class _FakeGateway:
    """Stands in for routes.canvas.http_requests; records what would be sent."""

    def __init__(self):
        self.calls = []
        self.next = _FakeResp()

    def _call(self, method, url, **kwargs):
        # What requests would actually put on the wire (urllib3 removes dot
        # segments here; that is the normalisation the reviewer measured).
        sent = requests.Request(method.upper(), url, params=kwargs.get("params")).prepare().url
        self.calls.append({"method": method, "url": url, "sent": sent, "kwargs": kwargs})
        return self.next

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete", "patch"):
            return lambda url, **kw: self._call(name, url, **kw)
        raise AttributeError(name)


def _gateway_view(url):
    """The path a lenient upstream (WHATWG URL + decodeURIComponent) would serve."""
    path = urlsplit(url).path
    for ch in "\t\r\n":
        path = path.replace(ch, "")
    path = path.replace("\\", "/")
    for _ in range(4):
        path = unquote(path).replace("\\", "/")
    return posixpath.normpath(path)


def _inside_prefix(url):
    p = _gateway_view(url)
    return p == "/openclaw-ui" or p.startswith(PREFIX)


@pytest.fixture(scope="module")
def app():
    from app import create_app
    from routes.canvas import canvas_bp
    a, _ = create_app(config_override={"TESTING": True})
    a.register_blueprint(canvas_bp)
    return a


@pytest.fixture
def gw(monkeypatch):
    import routes.canvas as canvas
    fake = _FakeGateway()
    monkeypatch.setattr(canvas, "http_requests", fake)
    return fake


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# 1. Path confinement
# ---------------------------------------------------------------------------

# Each form escapes /openclaw-ui/ on origin/main (first four are the reviewer's
# measured cases in proxy-path.out).
TRAVERSALS = [
    "..%2Fplugins%2Fx",
    "..%2F..%2Fx",
    "%2e%2e/x",
    "a/..%2F..%2Fx",
    "%2E%2E%2Fx",
    "assets/%2e%2e/%2e%2e/x",
    "%252e%252e%252fx",          # double-encoded: Flask decodes once, gateway once more
    "..%252Fx",
    "%2e%2e%5cx",                # backslash: WHATWG URL reads it as '/'
    "..%5C..%5Cx",
    ".%09./x",                   # tab inside a dot segment: WHATWG strips it -> '..'
    "..",
    ".",
]


@pytest.mark.parametrize("raw", TRAVERSALS)
def test_traversal_is_refused_and_never_reaches_gateway(client, gw, raw):
    r = client.get(PREFIX + raw)
    assert r.status_code == 400, f"{raw!r} must be refused, got {r.status_code}"
    assert gw.calls == [], f"{raw!r} reached the gateway as {gw.calls[0]['sent'] if gw.calls else None}"


@pytest.mark.parametrize("decoded", [".\n./x", ".\r./x", "..\x00/x", ".\x7f./x", "a\nb"])
def test_control_characters_are_refused(decoded):
    # Werkzeug's <path> converter already 404s a raw newline, so check the
    # builder directly: WHATWG URL parsing strips tab/CR/LF, turning ".\n." into "..".
    from routes.canvas import _openclaw_ui_target
    assert _openclaw_ui_target(decoded) is None


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_traversal_is_refused_for_every_method(client, gw, method):
    r = getattr(client, method)(PREFIX + "..%2F..%2Fhooks%2Fx", data=b"{}", content_type="application/json")
    assert r.status_code == 400
    assert gw.calls == []


def test_encoded_query_or_fragment_cannot_be_smuggled_into_the_path(client, gw):
    # %3F decodes to '?' in Flask's path; an f-string target would turn the rest
    # into an upstream query string.
    r = client.get(PREFIX + "assets/x.js%3Fa=1%23b")
    assert r.status_code == 200
    sent = gw.calls[0]["sent"]
    assert urlsplit(sent).query == ""
    assert urlsplit(sent).path == "/openclaw-ui/assets/x.js%3Fa=1%23b"


LEGIT = [
    ("", f"{UPSTREAM}/openclaw-ui/"),
    ("chat", f"{UPSTREAM}/openclaw-ui/chat"),
    ("assets/index-NYVkUQrq.js", f"{UPSTREAM}/openclaw-ui/assets/index-NYVkUQrq.js"),
    ("assets/index-DyaZOQpm.css", f"{UPSTREAM}/openclaw-ui/assets/index-DyaZOQpm.css"),
    ("favicon.svg", f"{UPSTREAM}/openclaw-ui/favicon.svg"),
    ("manifest.webmanifest", f"{UPSTREAM}/openclaw-ui/manifest.webmanifest"),
    ("__openclaw/control-ui-config.json", f"{UPSTREAM}/openclaw-ui/__openclaw/control-ui-config.json"),
    ("avatar/main?meta=1", f"{UPSTREAM}/openclaw-ui/avatar/main?meta=1"),
    ("avatar/agent%3Aops", f"{UPSTREAM}/openclaw-ui/avatar/agent:ops"),
    ("__openclaw__/assistant-media?source=a%2Fb.png",
     f"{UPSTREAM}/openclaw-ui/__openclaw__/assistant-media?source=a%2Fb.png"),
    ("sessions/..hidden-ish", f"{UPSTREAM}/openclaw-ui/sessions/..hidden-ish"),
]


@pytest.mark.parametrize("raw,expected", LEGIT)
def test_control_ui_paths_still_proxy(client, gw, raw, expected):
    gw.next = _FakeResp(200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>ui</html>")
    r = client.get(PREFIX + raw)
    assert r.status_code == 200, raw
    assert r.data == b"<html>ui</html>"
    assert len(gw.calls) == 1
    assert gw.calls[0]["sent"] == expected
    assert _inside_prefix(gw.calls[0]["sent"])
    assert r.headers["Content-Type"].startswith("text/html")


def test_post_body_and_content_type_still_relayed(client, gw):
    r = client.post(PREFIX + "api/thing", data=b'{"a":1}', content_type="application/json")
    assert r.status_code == 200
    call = gw.calls[0]
    assert call["method"] == "post"
    assert call["kwargs"]["data"] == b'{"a":1}'
    assert call["kwargs"]["headers"] == {"Content-Type": "application/json"}


def test_upstream_redirects_are_not_followed(client, gw):
    # Following a gateway 3xx server-side would let an agent-served route under
    # /openclaw-ui/ bounce the proxy to any gateway path or internal host.
    client.get(PREFIX + "chat")
    assert gw.calls[0]["kwargs"].get("allow_redirects") is False


# ---------------------------------------------------------------------------
# 2. Response header allowlist
# ---------------------------------------------------------------------------

EVIL_HEADERS = {
    "Set-Cookie": "__session=attacker; Path=/",
    "Service-Worker-Allowed": "/",
    "Clear-Site-Data": '"cookies", "storage"',
    "Content-Security-Policy": "script-src * 'unsafe-inline'",
    "Content-Security-Policy-Report-Only": "default-src 'none'; report-uri https://evil.example/r",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Origin-Agent-Cluster": "?0",
    "Access-Control-Allow-Origin": "https://evil.example",
    "Access-Control-Allow-Credentials": "true",
    "Refresh": "0; url=https://evil.example/",
    "Link": "</openclaw-ui/x.js>; rel=serviceworker",
    "X-Frame-Options": "ALLOWALL",
    "Permissions-Policy": "camera=*",
    "Strict-Transport-Security": "max-age=0",
}
KEPT = {
    "Content-Type": "application/javascript",
    "Cache-Control": "no-cache",
    "ETag": '"abc"',
    "Last-Modified": "Tue, 17 Nov 2026 09:00:00 GMT",
}


def test_dangerous_upstream_headers_never_reach_the_browser(client, gw):
    gw.next = _FakeResp(200, {**EVIL_HEADERS, **KEPT}, b"x")
    r = client.get(PREFIX + "assets/index-NYVkUQrq.js")
    assert r.status_code == 200
    for name, evil in EVIL_HEADERS.items():
        got = r.headers.getlist(name)
        assert evil not in got, f"upstream {name} leaked to the browser: {got}"
    for name in ("Set-Cookie", "Service-Worker-Allowed", "Clear-Site-Data",
                 "Content-Security-Policy-Report-Only", "Cross-Origin-Opener-Policy",
                 "Cross-Origin-Embedder-Policy", "Cross-Origin-Resource-Policy",
                 "Origin-Agent-Cluster", "Refresh", "Link"):
        assert name not in r.headers, f"{name} must be absent"
    for name, value in KEPT.items():
        assert r.headers.get(name) == value, f"{name} should still be relayed"


def test_ovu_own_security_headers_apply_to_proxied_responses(client, gw):
    gw.next = _FakeResp(200, {"Content-Type": "text/html", "Content-Security-Policy": "script-src *",
                              "X-Frame-Options": "ALLOWALL"}, b"<html></html>")
    r = client.get(PREFIX)
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp and csp != "script-src *" and "default-src 'self'" in csp
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


@pytest.mark.parametrize("location,kept", [
    ("/openclaw-ui/?session=main", True),     # the gateway's own basePath redirect
    ("/openclaw-ui/chat", True),
    ("./", True),
    ("/api/biz/v1/crt/staff/", False),
    ("/openclaw-ui/../api/x", False),
    ("/openclaw-ui/%2e%2e/api/x", False),
    ("../api/x", False),
    ("https://evil.example/", False),
    ("//evil.example/", False),
    ("/\\evil.example/", False),
])
def test_redirect_location_is_kept_only_inside_prefix(client, gw, location, kept):
    gw.next = _FakeResp(302, {"Location": location, "Content-Type": "text/plain"}, b"")
    r = client.get(PREFIX + "chat")
    assert r.status_code == 302
    if kept:
        assert r.headers.get("Location", "").endswith(location.lstrip("."))
    else:
        assert "Location" not in r.headers, f"off-prefix Location {location!r} was relayed"


# ---------------------------------------------------------------------------
# 3. Service workers
# ---------------------------------------------------------------------------

def test_control_ui_sw_still_registers_but_without_scope_widening(client, gw):
    gw.next = _FakeResp(200, {"Content-Type": "application/javascript", "Service-Worker-Allowed": "/"},
                        b"self.addEventListener('fetch',()=>{})")
    r = client.get(PREFIX + "sw.js", headers={"Service-Worker": "script"})
    assert r.status_code == 200
    assert "Service-Worker-Allowed" not in r.headers
    assert gw.calls[0]["sent"] == f"{UPSTREAM}/openclaw-ui/sw.js"


@pytest.mark.parametrize("raw", ["plugins/evil.js", "assets/index-NYVkUQrq.js", "x/sw.js", "SW.JS"])
def test_other_service_worker_script_fetches_are_refused(client, gw, raw):
    r = client.get(PREFIX + raw, headers={"Service-Worker": "script"})
    assert r.status_code == 403
    assert gw.calls == []


def test_plain_fetch_of_a_script_is_unaffected(client, gw):
    r = client.get(PREFIX + "plugins/evil.js")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 4. Mutant controls: the code this replaced fails these same cases
# ---------------------------------------------------------------------------

def test_mutant_old_target_builder_escapes_prefix():
    """origin/main's f-string target, sent through requests, leaves /openclaw-ui/."""
    old = lambda path: f"{UPSTREAM}/openclaw-ui/{path}"  # noqa: E731
    # Flask hands the route a once-decoded path.
    escaped = [raw for raw in TRAVERSALS
               if not _inside_prefix(requests.Request("GET", old(unquote(raw))).prepare().url)]
    assert len(escaped) >= 8, f"old builder should escape >=8 forms, escaped {escaped}"

    from routes.canvas import _openclaw_ui_target
    assert all(_openclaw_ui_target(unquote(raw)) is None for raw in TRAVERSALS)


def test_mutant_old_exclude_list_leaks_headers():
    """origin/main's exclude-list filter relays the dangerous headers."""
    excluded = ['content-encoding', 'content-length', 'transfer-encoding',
                'connection', 'x-frame-options', 'content-security-policy']
    leaked = [k for k in EVIL_HEADERS if k.lower() not in excluded]
    assert {"Set-Cookie", "Service-Worker-Allowed", "Clear-Site-Data",
            "Cross-Origin-Opener-Policy"} <= set(leaked)

    from routes.canvas import _openclaw_ui_response_headers
    kept = {k for k, _ in _openclaw_ui_response_headers(requests.structures.CaseInsensitiveDict(EVIL_HEADERS))}
    assert kept == set(), f"allowlist relayed {kept}"
