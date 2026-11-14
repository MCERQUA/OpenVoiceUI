"""
WebSocket upgrades are refused when the Origin's host is not the request Host.

Review of PR #585: require_auth's CSRF guard (app.py) checks Origin only on
POST/PUT/DELETE/PATCH. A WebSocket upgrade is a GET, the browser still attaches
the __session cookie (SameSite=Lax), and every *.jam-bot.com tenant counts as
the same SITE, so a page on another tenant could open a socket as the
signed-in user. app.py's ws_origin_guard now refuses those upgrades with 403
before flask_sock accepts them.

A missing Origin still passes: browsers always send one on a WebSocket
handshake, so its absence means a non-browser client (X-Agent-Key callers,
internal tools) that carries its own credential, not a victim's cookie.
"""

import pytest

from services.auth import websocket_origin_allowed

# Each socket the guard must cover. A path missing from app.py's
# _WS_ORIGIN_CHECKED_PATHS fails here with the attacker's upgrade let through.
GUARDED = ["/ws/push"]


@pytest.mark.parametrize("path", GUARDED)
def test_cross_origin_upgrade_is_refused(client, path):
    r = client.get(path, headers={
        "Origin": "https://othertenant.jam-bot.com",
        "Upgrade": "websocket", "Connection": "Upgrade",
    })
    assert r.status_code == 403, f"{path}: a cross-origin upgrade must be refused"
    assert r.get_json()["code"] == "ws_origin_blocked"


@pytest.mark.parametrize("path", GUARDED)
def test_null_origin_is_refused(client, path):
    r = client.get(path, headers={"Origin": "null", "Upgrade": "websocket"})
    assert r.status_code == 403


@pytest.mark.parametrize("path", GUARDED)
def test_same_origin_upgrade_passes_the_guard(client, path):
    # The test client's Host is "localhost". The route itself may 404/400 here
    # (server.py's sockets are not on the factory app); the guard must not block.
    r = client.get(path, headers={"Origin": "http://localhost", "Upgrade": "websocket"})
    assert not (r.status_code == 403 and (r.get_json(silent=True) or {}).get("code") == "ws_origin_blocked")


@pytest.mark.parametrize("path", GUARDED)
def test_no_origin_passes_the_guard(client, path):
    r = client.get(path, headers={"Upgrade": "websocket", "X-Agent-Key": "not-checked-here"})
    assert not (r.status_code == 403 and (r.get_json(silent=True) or {}).get("code") == "ws_origin_blocked")


def test_other_paths_are_not_touched(client):
    r = client.get("/health/live", headers={"Origin": "https://evil.example"})
    assert (r.get_json(silent=True) or {}).get("code") != "ws_origin_blocked"


@pytest.mark.parametrize("origin,host,ok", [
    (None, "a.jam-bot.com", True),
    ("", "a.jam-bot.com", True),
    ("https://a.jam-bot.com", "a.jam-bot.com", True),
    ("https://A.Jam-Bot.com", "a.jam-bot.com", True),
    ("http://localhost:5001", "localhost:5001", True),
    ("https://b.jam-bot.com", "a.jam-bot.com", False),   # sibling tenant, same site
    ("https://a.jam-bot.com.evil.com", "a.jam-bot.com", False),
    ("https://a.jam-bot.com:8443", "a.jam-bot.com", False),
    ("http://localhost:5173", "localhost:5001", False),
    ("null", "a.jam-bot.com", False),
    ("not a url", "a.jam-bot.com", False),
    ("https://a.jam-bot.com", "", False),
])
def test_websocket_origin_allowed(origin, host, ok):
    assert websocket_origin_allowed(origin, host) is ok
