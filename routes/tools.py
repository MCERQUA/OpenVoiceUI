"""
Voice Agent Tool Bus — HTTP surface.

Spec: docs/jambot/voice-agent-tool-bus.md

    GET  /api/tools/catalog          capability-filtered catalog for a profile
    POST /api/tools/invoke           run a server tool (sync result or job_id)
    GET  /api/tools/jobs/<job_id>    poll one job
    GET  /api/tools/events           SSE — job completions pushed to the browser

Auth: every /api/* path is already covered by the app-wide `require_auth`
before_request gate in app.py. We resolve user_id here only for provenance.

── WHY SSE AND NOT A WEBSOCKET ────────────────────────────────────────────────
The spec originally named this /ws/agent-events. SSE won because the channel is
strictly one-way (server → browser), EventSource reconnects natively with
Last-Event-ID semantics we get for free via ?since=, and it stays inside a
blueprint instead of requiring surgery on server.py's @sock routes. server.py
runs app.run(threaded=True), so a held SSE connection costs one thread — fine
for a single-user tenant. nginx needs X-Accel-Buffering: no, which is set below
and matches the existing SSE routes in routes/admin.py.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from flask import Blueprint, Response, jsonify, request

from services import tool_jobs
from services.tool_catalog import catalog

logger = logging.getLogger(__name__)

tools_bp = Blueprint("tools", __name__)


def _profiles_dir() -> Path:
    """Resolve the LIVE profiles dir the same way ProfileManager does.

    Do not hardcode <repo>/profiles: when /app/runtime/profiles is mounted it
    SHADOWS the bundled dir, and a tenant's real profile set lives only there.
    Reading the bundled copy would grant tools based on a profile the running
    app never loads.
    """
    try:
        from profiles.manager import ProfileManager
        return Path(ProfileManager.get_instance().profiles_dir)
    except Exception:
        runtime = Path("/app/runtime/profiles")
        if runtime.is_dir():
            return runtime
        return Path(__file__).parent.parent / "profiles"



# SSE tuning. The heartbeat is not decoration: without periodic bytes, nginx and
# intermediate proxies close an idle stream and the browser silently stops
# receiving job completions — a promise the agent made and can no longer keep.
SSE_POLL_SECS = 1.0
SSE_HEARTBEAT_SECS = 20.0
SSE_MAX_LIFETIME_SECS = 3600.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tenant() -> Optional[str]:
    return (
        os.getenv("JAMBOT_TENANT")
        or os.getenv("TENANT_NAME")
        or os.getenv("CLIENT_NAME")
        or None
    )


def _user_id() -> Optional[str]:
    try:
        from services.auth import get_token_from_request, verify_clerk_token
        token = get_token_from_request()
        return verify_clerk_token(token) if token else None
    except Exception:
        return None


def _profile_tool_grants(profile_id: Optional[str]) -> Tuple[Set[str], Set[str]]:
    """(capabilities, allow_dangerous) for a profile.

    Read from the profile JSON directly rather than through ProfileManager: the
    Profile dataclass has no `tools` field, so round-tripping through
    from_dict/to_dict would silently DROP these grants on the next profile save.
    Losing a grant fails closed (annoying), but a save that quietly rewrites a
    security-relevant block is the worse failure.

    Profile contract:
        "tools": {
          "capabilities":    ["canvas", "music"],
          "allow_dangerous": ["code_task"]
        }

    Absent block → fall back to features.tools. `true` grants the safe default
    set; anything dangerous still needs an explicit opt-in and is never implied.
    """
    if not profile_id:
        return set(), set()

    path = _profiles_dir() / f"{profile_id}.json"
    if not path.exists():
        logger.warning("tools: unknown profile %r — no tools granted", profile_id)
        return set(), set()

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        # Unreadable config is an ALERT, and it must fail CLOSED.
        logger.error("tools: profile %s unreadable (%s) — no tools granted", profile_id, exc)
        return set(), set()

    block = data.get("tools")
    if isinstance(block, dict):
        return (
            set(block.get("capabilities") or ()),
            set(block.get("allow_dangerous") or ()),
        )

    if (data.get("features") or {}).get("tools"):
        return {"canvas", "music", "audio_fx", "session", "face"}, set()

    return set(), set()


def _log_invocation(tool: str, params: Dict[str, Any], *, profile: Optional[str],
                    user_id: Optional[str], outcome: str) -> None:
    """Audit line. The surface must be reviewable after the fact, not only gated
    before it (voice-agent-tool-bus.md §4)."""
    logger.info(
        "tool-invoke tool=%s outcome=%s profile=%s user=%s params=%s",
        tool, outcome, profile, user_id, json.dumps(params, ensure_ascii=False)[:500],
    )


# ---------------------------------------------------------------------------
# GET /api/tools/catalog
# ---------------------------------------------------------------------------

@tools_bp.route("/api/tools/catalog", methods=["GET"])
def get_catalog():
    """Catalog filtered to what this profile may use.

    The realtime_tools[] in the response is what the adapter hands the model in
    session.update. A tool absent from it is one the model never learns exists.
    """
    profile_id = request.args.get("profile")
    caps, dangerous = _profile_tool_grants(profile_id)
    payload = catalog.to_client_json(caps=caps, allow_dangerous=dangerous)
    payload["profile"] = profile_id
    payload["granted_capabilities"] = sorted(caps)
    payload["granted_dangerous"] = sorted(dangerous)
    return jsonify(payload)


# ---------------------------------------------------------------------------
# POST /api/tools/invoke
# ---------------------------------------------------------------------------

@tools_bp.route("/api/tools/invoke", methods=["POST"])
def invoke():
    """Execute a server-side tool.

    server_sync  → {"status": "ok", "result": {...}}
    server_async → {"status": "dispatched", "job_id": "...", "message": "..."}

    The dispatched response is what gets handed back to the model as the function
    result, so `message` is written to be spoken.
    """
    body = request.get_json(silent=True) or {}
    tool_name = body.get("tool")
    params = body.get("params") or {}
    profile_id = body.get("profile")
    session_id = body.get("session_id")

    tool = catalog.get(tool_name) if tool_name else None
    if not tool:
        _log_invocation(str(tool_name), params, profile=profile_id,
                        user_id=None, outcome="unknown_tool")
        return jsonify({"status": "error", "error": f"Unknown tool: {tool_name}"}), 404

    user_id = _user_id()
    caps, dangerous = _profile_tool_grants(profile_id)

    # Gate. Re-checked server-side even though the browser was only ever GIVEN
    # allowed tools — a client-side filter is a UX affordance, not a control.
    if tool.capability not in caps:
        _log_invocation(tool.name, params, profile=profile_id,
                        user_id=user_id, outcome="denied_capability")
        return jsonify({
            "status": "error",
            "error": f"Tool '{tool.name}' requires capability '{tool.capability}', "
                     f"which this profile does not have.",
        }), 403

    if tool.dangerous and tool.name not in dangerous:
        _log_invocation(tool.name, params, profile=profile_id,
                        user_id=user_id, outcome="denied_dangerous")
        return jsonify({
            "status": "error",
            "error": f"Tool '{tool.name}' is restricted and this profile has not opted in.",
        }), 403

    if tool.is_client:
        # Client tools never come here — the browser routes them through the
        # EventBridge. Reaching this branch means tool-bridge.js mis-routed.
        _log_invocation(tool.name, params, profile=profile_id,
                        user_id=user_id, outcome="wrong_route")
        return jsonify({
            "status": "error",
            "error": f"'{tool.name}' is a client-side tool and cannot be invoked over HTTP.",
        }), 400

    # ── async: spool a job, return immediately ────────────────────────────
    if tool.is_async:
        job = tool_jobs.create(
            tool.name,
            params,
            tenant=_tenant(),
            user_id=user_id,
            profile=profile_id,
            session_id=session_id,
        )
        _log_invocation(tool.name, params, profile=profile_id,
                        user_id=user_id, outcome=f"dispatched:{job['id']}")
        return jsonify({
            "status": "dispatched",
            "job_id": job["id"],
            # Written to be SPOKEN — this is the function result the model sees.
            "message": "Started. This runs in the background; you'll be told when "
                       "it finishes. Acknowledge briefly and continue the conversation.",
        })

    # ── sync ──────────────────────────────────────────────────────────────
    handler = SYNC_HANDLERS.get(tool.handler or "")
    if not handler:
        _log_invocation(tool.name, params, profile=profile_id,
                        user_id=user_id, outcome="no_handler")
        return jsonify({
            "status": "error",
            "error": f"No handler registered for '{tool.name}'.",
        }), 501

    try:
        result = handler(params)
    except Exception as exc:
        logger.exception("tool %s handler failed", tool.name)
        _log_invocation(tool.name, params, profile=profile_id,
                        user_id=user_id, outcome="handler_error")
        return jsonify({"status": "error", "error": str(exc)}), 500

    _log_invocation(tool.name, params, profile=profile_id, user_id=user_id, outcome="ok")
    return jsonify({"status": "ok", "result": result})


# Sync server tools register here: handler id (from tools.yaml) -> callable.
SYNC_HANDLERS: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# GET /api/tools/jobs/<job_id>
# ---------------------------------------------------------------------------

@tools_bp.route("/api/tools/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    job = tool_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@tools_bp.route("/api/tools/jobs", methods=["GET"])
def list_jobs():
    return jsonify({
        "jobs": tool_jobs.list_jobs(
            status=request.args.get("status"),
            session_id=request.args.get("session_id"),
        )
    })


# ---------------------------------------------------------------------------
# GET /api/tools/events  — SSE push
# ---------------------------------------------------------------------------

@tools_bp.route("/api/tools/events", methods=["GET"])
def events():
    """Stream job completions to the browser.

    The browser turns a `job.done` event into
    bridge.emit(AgentActions.FORCE_MESSAGE, ...) — which every adapter already
    implements — so the live voice agent speaks the conclusion.

    ?session_id= scopes the stream to one conversation.
    ?since=<epoch> replays anything that completed while the tab was reconnecting,
    which is what stops a result from being lost in the gap.
    """
    session_id = request.args.get("session_id")
    try:
        since = float(request.args.get("since") or 0.0)
    except ValueError:
        since = 0.0

    def generate():
        seen: Set[str] = set()
        watermark = since or (time.time() - 300.0)  # 5-min replay window on a cold open
        started = time.time()
        last_beat = 0.0

        yield f"data: {json.dumps({'type': 'connected', 'at': time.time()})}\n\n"

        while True:
            now = time.time()
            if now - started > SSE_MAX_LIFETIME_SECS:
                # Bounded lifetime; EventSource reconnects on its own and the
                # ?since= watermark makes the handover lossless.
                yield f"data: {json.dumps({'type': 'reconnect'})}\n\n"
                return

            try:
                jobs = tool_jobs.list_jobs(session_id=session_id, since=watermark)
            except Exception as exc:
                logger.error("tools/events: job scan failed: %s", exc)
                jobs = []

            for job in sorted(jobs, key=lambda j: j.get("updated_at", 0)):
                if job.get("status") not in tool_jobs.TERMINAL:
                    continue
                if job["id"] in seen:
                    continue
                seen.add(job["id"])
                watermark = max(watermark, job.get("updated_at", now))

                ok = job.get("status") == tool_jobs.DONE
                summary = (
                    (job.get("result") or {}).get("summary")
                    if ok else job.get("error")
                ) or "Finished with no summary."

                payload = {
                    "type": "job.done" if ok else "job.failed",
                    "job_id": job["id"],
                    "tool": job.get("tool"),
                    "ok": ok,
                    "summary": summary,
                    "artifacts": (job.get("result") or {}).get("artifacts", []),
                    "at": job.get("updated_at"),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                last_beat = now

            if now - last_beat > SSE_HEARTBEAT_SECS:
                # Comment frame — keeps proxies from reaping an idle stream.
                yield ": keepalive\n\n"
                last_beat = now

            time.sleep(SSE_POLL_SECS)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
