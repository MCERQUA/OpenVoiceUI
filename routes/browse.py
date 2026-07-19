"""
routes/browse.py — OpenVoiceUI ↔ co-browsing service bridge (issue #154, Phase 2).

Thin proxy from OVU to the shared `jambot-browse` container (Phase 1). Three roles:

  1. HTTP proxy for the agent + frontend: /api/browse/start|action|stop|screenshot|dom|status
     — injects THIS tenant (the browser client never picks its own tenant) and the
     shared-secret key, and forwards to http://jambot-browse:8712.
  2. Serves the viewer canvas page at /browse-viewer (default-pages/browse-viewer.html).
  3. Keeps the per-process "latest browse state" (url/title/dom digest) so the
     conversation route can inject a compact [BROWSE_STATE: …] into the agent's next
     turn — text-sight so the agent can navigate without a full screenshot round-trip.

The live video path (CDP screencast) is a WebSocket, which flask-sock owns in
server.py — see `/ws/browse-stream` there. This module is HTTP + page serving only.

Auth: these routes ride the app's normal `before_request` gate (Clerk session or
the internal agent key for non-admin calls), same as /api/conversation. The
upstream service key (BROWSE_SERVICE_KEY) is added here, server-side only — it is
never exposed to the browser.
"""
import logging
import os

import requests
from flask import Blueprint, Response, jsonify, request

from services.paths import DEFAULT_PAGES_DIR, UPLOADS_DIR

logger = logging.getLogger(__name__)

browse_bp = Blueprint("browse", __name__)

BROWSE_SERVICE_URL = os.getenv("BROWSE_SERVICE_URL", "http://jambot-browse:8712").rstrip("/")
BROWSE_SERVICE_KEY = os.getenv("BROWSE_SERVICE_KEY", "").strip()
_TIMEOUT = float(os.getenv("BROWSE_PROXY_TIMEOUT_S", "45"))


def _tenant() -> str:
    """This OVU instance's tenant — the co-browsing session is keyed on it."""
    return (os.getenv("JAMBOT_TENANT") or os.getenv("TENANT_NAME") or "default").strip()


def _svc_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if BROWSE_SERVICE_KEY:
        h["X-Browse-Key"] = BROWSE_SERVICE_KEY
    return h


# ── Browse-active marker + state (worker-safe) ───────────────────────────────
# OVU runs multiple gunicorn workers, so in-process state is NOT shared across
# them (a browser can hit worker A on /api/browse/start and worker B on the next
# conversation turn). So: a filesystem marker (shared by all workers on the same
# volume) records "a session is active", and the browse SERVICE is the single
# shared source of truth for the live page — get_browse_state() queries it
# directly each turn. That also means USER-driven navigation (which goes to the
# service over the viewer WS, bypassing this proxy) is always reflected: we read
# the service, not a local cache.
_MARKER = UPLOADS_DIR / ".browse-active"


def _set_active(active: bool):
    try:
        if active:
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            _MARKER.write_text(_tenant(), encoding="utf-8")
        elif _MARKER.exists():
            _MARKER.unlink()
    except Exception as e:
        logger.debug("browse marker update failed: %s", e)


def get_browse_state() -> dict | None:
    """Return the live page digest if a browse session is active, else None.

    Worker-safe: gated on the filesystem marker (cheap stat, shared across
    workers), then reads the browse SERVICE directly so the value always
    reflects reality — including USER-driven navigation. Read by the
    conversation route to inject [BROWSE_STATE: …]. One DOM call per agent turn,
    only while a session is active. Self-heals: if the service reports no
    session, the marker is cleared.
    """
    if not _MARKER.exists():
        return None
    try:
        r = requests.get(
            f"{BROWSE_SERVICE_URL}/session/dom",
            params={"tenant": _tenant()}, headers=_svc_headers(), timeout=_TIMEOUT,
        )
        if r.status_code == 404:
            _set_active(False)  # service says no session — clear stale marker
            return None
        if r.status_code != 200:
            return None
        dom = r.json()
        return {
            "url": dom.get("url", ""),
            "title": dom.get("title", ""),
            "text": (dom.get("text", "") or "")[:1200],
            "links": [l.get("text", "") for l in dom.get("links", [])][:12],
            "buttons": [b.get("text", "") for b in dom.get("buttons", [])][:12],
        }
    except Exception as e:
        logger.debug("browse state read failed: %s", e)
        return None


# ── HTTP proxy endpoints ─────────────────────────────────────────────────────
@browse_bp.route("/api/browse/start", methods=["POST"])
def browse_start():
    body = request.get_json(silent=True) or {}
    payload = {
        "tenant": _tenant(),
        "url": body.get("url", "about:blank"),
    }
    for k in ("width", "height", "proxy"):
        if body.get(k) is not None:
            payload[k] = body[k]
    try:
        r = requests.post(f"{BROWSE_SERVICE_URL}/session/start",
                          json=payload, headers=_svc_headers(), timeout=_TIMEOUT)
        if r.status_code == 200:
            _set_active(True)
        else:
            logger.warning("browse start upstream %s: %s", r.status_code, r.text[:200])
        return Response(r.content, status=r.status_code, content_type="application/json")
    except requests.RequestException as e:
        logger.error("browse start failed: %s", e)
        return jsonify({"error": "browse service unavailable"}), 503


@browse_bp.route("/api/browse/action", methods=["POST"])
def browse_action():
    body = request.get_json(silent=True) or {}
    payload = dict(body)
    payload["tenant"] = _tenant()
    try:
        r = requests.post(f"{BROWSE_SERVICE_URL}/session/action",
                          json=payload, headers=_svc_headers(), timeout=_TIMEOUT)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except requests.RequestException as e:
        logger.error("browse action failed: %s", e)
        return jsonify({"error": "browse service unavailable"}), 503


@browse_bp.route("/api/browse/screenshot", methods=["GET"])
def browse_screenshot():
    try:
        r = requests.get(
            f"{BROWSE_SERVICE_URL}/session/screenshot",
            params={"tenant": _tenant(), "full": request.args.get("full", "")},
            headers=_svc_headers(), timeout=_TIMEOUT,
        )
        return Response(r.content, status=r.status_code,
                        content_type=r.headers.get("Content-Type", "image/png"))
    except requests.RequestException as e:
        logger.error("browse screenshot failed: %s", e)
        return jsonify({"error": "browse service unavailable"}), 503


@browse_bp.route("/api/browse/dom", methods=["GET"])
def browse_dom():
    try:
        r = requests.get(f"{BROWSE_SERVICE_URL}/session/dom",
                         params={"tenant": _tenant()}, headers=_svc_headers(), timeout=_TIMEOUT)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except requests.RequestException as e:
        return jsonify({"error": "browse service unavailable"}), 503


@browse_bp.route("/api/browse/status", methods=["GET"])
def browse_status():
    try:
        r = requests.get(f"{BROWSE_SERVICE_URL}/session/status",
                         params={"tenant": _tenant()}, headers=_svc_headers(), timeout=_TIMEOUT)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except requests.RequestException as e:
        return jsonify({"error": "browse service unavailable"}), 503


@browse_bp.route("/api/browse/ip", methods=["GET"])
def browse_ip():
    """Egress IP of the live session — proof of residential masking (#154 P4)."""
    try:
        r = requests.get(f"{BROWSE_SERVICE_URL}/session/ip",
                         params={"tenant": _tenant()}, headers=_svc_headers(), timeout=_TIMEOUT)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except requests.RequestException as e:
        return jsonify({"error": "browse service unavailable"}), 503


@browse_bp.route("/api/browse/stop", methods=["POST"])
def browse_stop():
    _set_active(False)
    try:
        r = requests.post(f"{BROWSE_SERVICE_URL}/session/stop",
                          json={"tenant": _tenant()}, headers=_svc_headers(), timeout=_TIMEOUT)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except requests.RequestException as e:
        return jsonify({"error": "browse service unavailable"}), 503


# ── Viewer page ──────────────────────────────────────────────────────────────
@browse_bp.route("/browse-viewer")
def browse_viewer():
    """Serve the live co-browsing viewer (loaded inside the canvas iframe)."""
    path = DEFAULT_PAGES_DIR / "browse-viewer.html"
    try:
        return Response(path.read_text(encoding="utf-8"), content_type="text/html")
    except Exception as e:
        logger.error("browse-viewer serve failed: %s", e)
        return "<h1>Browse viewer unavailable</h1>", 500
