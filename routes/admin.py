"""
routes/admin.py — Admin API Blueprint (P2-T6)

Provides two groups of endpoints:

1. Gateway RPC Proxy — send one-shot RPC calls to the OpenClaw Gateway
   POST /api/admin/gateway/rpc      — proxy any RPC method
   GET  /api/admin/gateway/status   — ping gateway (connect + disconnect)

2. Refactor Monitoring — read-only views of refactor-state/ files
   GET  /api/refactor/status        — playbook-state.json (all task statuses)
   GET  /api/refactor/activity      — last 50 entries from activity-log.jsonl
   GET  /api/refactor/metrics       — metrics.json
   POST /api/refactor/control       — pause / resume / skip a task
   GET  /api/server-stats           — CPU, RAM, disk, uptime (psutil)

Ref: Canvas Section 11 (OpenClaw Integration), P2-T6 spec, ADR-005 (header versioning)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil
import websockets
from flask import Blueprint, jsonify, request

from services.gateways.compat import (
    build_connect_params, is_challenge_event,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
REFACTOR_STATE_DIR = _PROJECT_ROOT / 'refactor-state'
PLAYBOOK_STATE_PATH = REFACTOR_STATE_DIR / 'playbook-state.json'
ACTIVITY_LOG_PATH = REFACTOR_STATE_DIR / 'activity-log.jsonl'
METRICS_PATH = REFACTOR_STATE_DIR / 'metrics.json'

# ---------------------------------------------------------------------------
# Gateway RPC helper
# ---------------------------------------------------------------------------

GATEWAY_URL = os.getenv('CLAWDBOT_GATEWAY_URL', 'ws://127.0.0.1:18791')
GATEWAY_AUTH_TOKEN = None  # read at call time so env changes propagate


def _get_auth_token() -> str | None:
    return os.getenv('CLAWDBOT_AUTH_TOKEN')


async def _gateway_rpc(method: str, params: dict, timeout: float = 10.0) -> dict:
    """
    Connect to Gateway, handshake, send one RPC request, return the response.

    Returns a dict with:
      {"ok": True, "result": <response payload>}
    or
      {"ok": False, "error": <message>}
    """
    auth_token = _get_auth_token()
    if not auth_token:
        return {"ok": False, "error": "CLAWDBOT_AUTH_TOKEN not set"}

    try:
        async with websockets.connect(GATEWAY_URL, open_timeout=timeout) as ws:
            # Step 1 — receive challenge
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            challenge = json.loads(raw)
            if not is_challenge_event(challenge):
                return {"ok": False, "error": f"Unexpected greeting: {challenge}"}

            # Step 2 — send connect request. Operator scopes are only granted
            # to a SIGNED device identity (Ed25519 over the challenge nonce) —
            # without the device block the gateway connects us with no scopes
            # and every RPC fails 'missing scope: operator.read'.
            from services.gateways.openclaw import _load_device_identity, _sign_device_connect
            nonce = challenge.get('payload', {}).get('nonce', '')
            scopes = ["operator.admin", "operator.read", "operator.write"]
            identity = _load_device_identity()
            # "backend" not "cli" — matches the main gateway connection; "cli" leaked
            # into agent-visible metadata (2026-07-11 greeting bug). Signature binds
            # client_id|client_mode, so keep both call args in lockstep.
            device_block = _sign_device_connect(
                identity, "gateway-client", "backend", "operator", scopes, auth_token, nonce
            )
            req_id = str(uuid.uuid4())
            connect_params = build_connect_params(
                auth_token=auth_token,
                client_id="gateway-client",
                client_mode="backend",
                platform="linux",
                user_agent="openvoice-ui-admin/1.0.0",
                scopes=scopes,
                caps=[],
                device_block=device_block,
            )
            await ws.send(json.dumps({
                "type": "req",
                "id": f"connect-{req_id}",
                "method": "connect",
                "params": connect_params,
            }))

            # Step 3 — receive hello
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            hello = json.loads(raw)
            if hello.get('type') != 'res' or hello.get('error'):
                return {"ok": False, "error": f"Gateway auth failed: {hello.get('error')}"}

            # Step 4 — send the actual RPC
            rpc_id = str(uuid.uuid4())
            await ws.send(json.dumps({
                "type": "req",
                "id": rpc_id,
                "method": method,
                "params": params,
            }))

            # Step 5 — collect response (drain until we get our req id back)
            start = time.time()
            while time.time() - start < timeout:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                if msg.get('id') == rpc_id:
                    if msg.get('error'):
                        return {"ok": False, "error": msg['error']}
                    return {"ok": True, "result": msg.get('result', msg.get('payload', {}))}
                # Skip unrelated events (heartbeat, presence, etc.)

            return {"ok": False, "error": "RPC timed out waiting for response"}

    except OSError as exc:
        return {"ok": False, "error": f"Gateway unreachable: {exc}"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Gateway connection timed out"}
    except Exception as exc:
        logger.error(f"Gateway RPC error: {exc}")
        return {"ok": False, "error": "Internal server error"}


def _run_rpc(method: str, params: dict, timeout: float = 10.0) -> dict:
    """Synchronous wrapper around _gateway_rpc for use in Flask routes."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_gateway_rpc(method, params, timeout))
        finally:
            loop.close()
    except Exception as exc:
        logger.error("RPC error: %s", exc)
        return {"ok": False, "error": "Internal server error"}

# ---------------------------------------------------------------------------
# RPC method allowlist — only these methods may be proxied to the Gateway
# (P7-T3 security audit: prevents unrestricted Gateway access)
# ---------------------------------------------------------------------------

ALLOWED_RPC_METHODS = frozenset({
    # Session management
    'sessions.list',
    'sessions.history',
    'sessions.abort',
    # Chat operations: 'chat.send' / 'chat.abort' intentionally NOT proxied here.
    # The voice app issues these directly over its own gateway WebSocket
    # (server.py / clawdbot_provider.py), never through this admin HTTP proxy.
    # Exposing them via the proxy would allow arbitrary message injection into /
    # aborting of the live agent session — and finding (a) showed the agent key
    # also reaches this proxy. 'sessions.abort' remains for legitimate admin
    # session control. (Removed 2026-06-13, ovu-security-followups.)
    # Diagnostic
    'ping',
    'status',
    'agent.status',
})


# ---------------------------------------------------------------------------
# Auth check endpoint
# ---------------------------------------------------------------------------

@admin_bp.route('/api/auth/me', methods=['GET'])
def auth_me():
    """Return the current user's display name from the container environment."""
    username = os.getenv('JAMBOT_TENANT') or os.getenv('CLIENT_NAME') or 'User'
    return jsonify({'username': username})


@admin_bp.route('/api/auth/check', methods=['GET'])
def auth_check():
    """
    Check if the current Clerk session is on the allowed list.
    Called by the frontend after sign-in to determine whether to show the full UI
    or a waiting-list screen.

    Returns:
        200 {"allowed": true, "user_id": "..."}   — user is approved
        403 {"allowed": false, "user_id": "..."}   — signed in but not on allowlist
        401 {"allowed": false, "user_id": null}    — not signed in at all
    """
    try:
        from services.auth import get_token_from_request, verify_clerk_token
        token = get_token_from_request()
        if not token:
            return jsonify({'allowed': False, 'user_id': None, 'reason': 'not_signed_in'}), 401
        user_id = verify_clerk_token(token)
        if user_id:
            return jsonify({'allowed': True, 'user_id': user_id})
        # Token valid but user not in allowlist (verify_clerk_token returns None when blocked)
        return jsonify({'allowed': False, 'user_id': None, 'reason': 'not_on_allowlist'}), 403
    except Exception as exc:
        logger.error(f'auth_check error: {exc}')
        return jsonify({'allowed': False, 'user_id': None, 'reason': 'error'}), 500


# Gateway RPC proxy endpoints
# ---------------------------------------------------------------------------

@admin_bp.route('/api/admin/gateway/status', methods=['GET'])
def gateway_status():
    """
    Ping the Gateway — connect, handshake, disconnect.
    Returns 200 with {"connected": true} on success.
    """
    result = _run_rpc('ping', {}, timeout=8.0)
    # A 'ping' method may not exist on all gateways; what matters is whether
    # the handshake succeeded.  The helper returns ok=True if auth worked.
    if result['ok']:
        return jsonify({"connected": True, "gateway_url": GATEWAY_URL})
    # If ping method not found but handshake worked the error will say so
    err = result.get('error', '')
    err_str = str(err).lower() if err else ''
    # The handshake succeeded (auth worked) if the gateway rejected ONLY the
    # ping method itself — either it is unknown/unsupported, or ping is scope-
    # restricted. Match those specific shapes, not any error that merely
    # contains the substring 'method'/'unknown' (which also matches genuine
    # failures like a malformed request or an "unknown error"). (WS-12)
    auth_ok = (
        'missing scope' in err_str
        or 'method not found' in err_str
        or 'unknown method' in err_str
        or 'no such method' in err_str
        or 'unsupported method' in err_str
        or 'not allowed' in err_str
    )
    return jsonify({
        "connected": auth_ok,
        "message": "Handshake OK (ping restricted)" if auth_ok else "Auth failed",
        "gateway_url": GATEWAY_URL,
        "detail": err,
    }), 200


@admin_bp.route('/api/admin/gateway/rpc', methods=['POST'])
def gateway_rpc_proxy():
    """
    Proxy an arbitrary RPC call to the Gateway.

    Request body:
        {"method": "chat.abort", "params": {"sessionKey": "voice-main-6", "runId": "…"}}

    Response:
        {"ok": true, "result": <gateway response payload>}
        {"ok": false, "error": "<reason>"}

    Security note: this is an internal admin endpoint — do NOT expose it
    publicly without authentication middleware.
    """
    data = request.get_json(silent=True) or {}
    method = data.get('method', '').strip()
    params = data.get('params', {})

    if not method:
        return jsonify({"ok": False, "error": "Missing 'method' field"}), 400

    # Method allowlist guard (P7-T3 security audit)
    if method not in ALLOWED_RPC_METHODS:
        return jsonify({"ok": False, "error": f"Method '{method}' is not allowed"}), 403

    timeout = float(data.get('timeout', 10))
    result = _run_rpc(method, params, timeout=timeout)
    status_code = 200 if result['ok'] else 502
    return jsonify(result), status_code


# ---------------------------------------------------------------------------
# Refactor monitoring endpoints (spec from P0-T2)
# ---------------------------------------------------------------------------

@admin_bp.route('/api/refactor/status', methods=['GET'])
def refactor_status():
    """
    Return the full playbook-state.json — all task statuses, phase gates, etc.
    Used by the refactor-dashboard canvas page.
    """
    if not PLAYBOOK_STATE_PATH.exists():
        return jsonify({"error": "playbook-state.json not found"}), 404
    try:
        data = json.loads(PLAYBOOK_STATE_PATH.read_text())
        return jsonify(data)
    except Exception as exc:
        logger.error(f"Failed to read playbook state: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@admin_bp.route('/api/refactor/activity', methods=['GET'])
def refactor_activity():
    """
    Return the last 50 entries from activity-log.jsonl.
    Each line is a JSON object; newest entries are returned first.
    """
    if not ACTIVITY_LOG_PATH.exists():
        return jsonify([])
    try:
        lines = ACTIVITY_LOG_PATH.read_text().strip().splitlines()
        entries = []
        for line in reversed(lines[-200:]):
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return jsonify(entries[:50])
    except Exception as exc:
        logger.error(f"Failed to read activity log: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@admin_bp.route('/api/refactor/metrics', methods=['GET'])
def refactor_metrics():
    """Return metrics.json (line counts, test coverage, etc.)."""
    if not METRICS_PATH.exists():
        return jsonify({"error": "metrics.json not found"}), 404
    try:
        data = json.loads(METRICS_PATH.read_text())
        return jsonify(data)
    except Exception as exc:
        logger.error(f"Failed to read metrics: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@admin_bp.route('/api/refactor/control', methods=['POST'])
def refactor_control():
    """
    Control the refactor automation.

    Request body:
        {"action": "pause"}          — set paused=true
        {"action": "resume"}         — set paused=false
        {"action": "skip", "task_id": "P2-T6"}  — mark task as skipped

    Response:
        {"ok": true, "state": <updated playbook state>}
    """
    if not PLAYBOOK_STATE_PATH.exists():
        return jsonify({"ok": False, "error": "playbook-state.json not found"}), 404

    data = request.get_json(silent=True) or {}
    action = data.get('action', '').strip()

    if action not in ('pause', 'resume', 'skip'):
        return jsonify({"ok": False, "error": "action must be pause|resume|skip"}), 400

    try:
        state = json.loads(PLAYBOOK_STATE_PATH.read_text())

        if action == 'pause':
            state['paused'] = True

        elif action == 'resume':
            state['paused'] = False

        elif action == 'skip':
            task_id = data.get('task_id', '').strip()
            if not task_id:
                return jsonify({"ok": False, "error": "task_id required for skip"}), 400
            if task_id not in state.get('tasks', {}):
                return jsonify({"ok": False, "error": f"Unknown task: {task_id}"}), 404
            state['tasks'][task_id]['status'] = 'skipped'
            state['tasks'][task_id]['completed_at'] = datetime.now(timezone.utc).isoformat()
            state['tasks'][task_id]['notes'] = (
                (state['tasks'][task_id].get('notes') or '') + ' [skipped via admin API]'
            ).strip()

        state['last_updated'] = datetime.now(timezone.utc).isoformat()

        # Atomic write
        tmp = PLAYBOOK_STATE_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(PLAYBOOK_STATE_PATH)

        return jsonify({"ok": True, "state": state})

    except Exception as exc:
        logger.error(f"refactor control error: {exc}")
        return jsonify({"ok": False, "error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Server stats endpoint
# ---------------------------------------------------------------------------

@admin_bp.route('/api/server-stats', methods=['GET'])
def server_stats():
    """
    VPS resource snapshot — CPU, RAM, disk, uptime, top processes.
    Polled by the refactor-dashboard canvas page every few seconds.
    """
    try:
        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_dt = datetime.fromtimestamp(psutil.boot_time())
        up = datetime.now() - boot_dt
        days, rem = divmod(int(up.total_seconds()), 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        uptime_str = (f"{days}d " if days else "") + f"{hours}h {minutes}m"

        # Top processes by CPU
        procs = []
        for p in sorted(
            psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']),
            key=lambda x: x.info.get('cpu_percent') or 0,
            reverse=True,
        )[:8]:
            try:
                info = p.info
                if (info.get('cpu_percent') or 0) > 0:
                    procs.append({
                        'pid': info['pid'],
                        'name': info['name'],
                        'cpu': round(info['cpu_percent'], 1),
                        'mem': round(info.get('memory_percent') or 0, 1),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        from services.gateway_manager import gateway_manager
        gateways = gateway_manager.list_gateways()

        return jsonify({
            'cpu_percent': cpu,
            'gateways': gateways,
            'memory': {
                'used_gb': round(mem.used / 1024 ** 3, 2),
                'total_gb': round(mem.total / 1024 ** 3, 2),
                'percent': round(mem.percent, 1),
            },
            'disk': {
                'used_gb': round(disk.used / 1024 ** 3, 1),
                'free_gb': round(disk.free / 1024 ** 3, 1),
                'total_gb': round(disk.total / 1024 ** 3, 1),
                'percent': round(disk.percent, 1),
            },
            'uptime': uptime_str,
            'top_processes': procs[:5],
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

    except Exception as exc:
        logger.error(f"server-stats error: {exc}")
        return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Framework install endpoint
# ---------------------------------------------------------------------------

@admin_bp.route('/api/admin/install/start', methods=['POST'])
def install_start():
    """
    Trigger agent-driven framework installation.
    Sends install request to OpenClaw Gateway and streams response as SSE.
    Falls back to JSON response if streaming not available.
    """
    import json as _json
    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'url is required'}), 400

    message = (
        f"[ADMIN INSTALL REQUEST] Please install this agent framework: {url}\n"
        "Steps to complete:\n"
        "1. Research the framework (README, install method, dependencies)\n"
        "2. Install it (pip install or equivalent)\n"
        "3. Write a connector file in providers/ or connectors/\n"
        "4. Run a quick test\n"
        "5. Register it\n"
        "Report each step as you complete it."
    )

    def generate():
        try:
            yield f"data: {_json.dumps({'type':'log','level':'section','message':f'Starting install: {url}'})}\n\n"
            yield f"data: {_json.dumps({'type':'log','step':'research','message':'Sending to agent...'})}\n\n"

            # Try to send via gateway RPC
            import asyncio, websockets, uuid

            gateway_url = os.environ.get('CLAWDBOT_GATEWAY_URL', 'ws://127.0.0.1:18791')
            auth_token = os.environ.get('CLAWDBOT_AUTH_TOKEN', '')

            async def _send():
                async with websockets.connect(gateway_url, open_timeout=10) as ws:
                    challenge = _json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if not is_challenge_event(challenge):
                        raise RuntimeError(f"Unexpected gateway greeting: {challenge.get('type')}")
                    # Signed device block — operator scopes are not granted without it.
                    from services.gateways.openclaw import _load_device_identity, _sign_device_connect
                    nonce = challenge.get('payload', {}).get('nonce', '')
                    scopes = ["operator.admin", "operator.read", "operator.write"]
                    # Identify as the backend gateway-client, not 'cli' — the
                    # 'cli' label leaked into agent-visible metadata elsewhere
                    # (fixed in e154d32); this call site was missed. client_id
                    # is enum-validated (GATEWAY_CLIENT_IDS); signature binds
                    # client_id|client_mode, so keep both args in lockstep. (WS-12)
                    device_block = _sign_device_connect(
                        _load_device_identity(), 'gateway-client', 'backend', 'operator', scopes, auth_token, nonce
                    )
                    connect_id = str(uuid.uuid4())[:8]
                    await ws.send(_json.dumps({
                        'type': 'req',
                        'id': f'connect-{connect_id}',
                        'method': 'connect',
                        'params': build_connect_params(
                            auth_token=auth_token,
                            client_id='gateway-client',
                            client_mode='backend',
                            platform='linux',
                            user_agent='openvoice-ui-admin/1.0.0',
                            scopes=scopes,
                            caps=[],
                            device_block=device_block,
                        ),
                    }))
                    hello = _json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if hello.get('type') != 'res' or hello.get('error'):
                        raise RuntimeError(f"Gateway auth failed: {hello.get('error')}")
                    req_id = str(uuid.uuid4())
                    await ws.send(_json.dumps({'type':'req','id':req_id,'method':'chat.send','params':{'sessionKey':'admin-install','message':message,'idempotencyKey':req_id}}))
                    collected = ''
                    for _ in range(120):
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            evt = _json.loads(raw)
                            if evt.get('stream') == 'assistant' and evt.get('text'):
                                collected = evt['text']
                            if evt.get('state') == 'final' or (evt.get('stream') == 'lifecycle' and evt.get('phase') == 'end'):
                                break
                        except asyncio.TimeoutError:
                            break
                    return collected

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_send())
                for line in result.split('\n'):
                    if line.strip():
                        yield f"data: {_json.dumps({'type':'log','message':line})}\n\n"
                yield f"data: {_json.dumps({'type':'done','message':'Agent completed'})}\n\n"
            except Exception as e:
                logger.error("Agent run gateway error: %s", e)
                yield f"data: {_json.dumps({'type':'log','level':'error','message':'Gateway error'})}\n\n"
            finally:
                loop.close()
        except Exception as e:
            logger.error("Agent run error: %s", e)
            yield f"data: {_json.dumps({'type':'log','level':'error','message':'Internal server error'})}\n\n"

    from flask import Response as _Response
    return _Response(generate(), mimetype='text/event-stream', headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


# ---------------------------------------------------------------------------
# GET /api/admin/clients — list all clients with status
# ---------------------------------------------------------------------------

@admin_bp.route('/api/admin/clients', methods=['GET'])
def list_clients():
    """Scan /mnt/clients/ for all client directories and report status."""
    clients_dir = Path('/mnt/clients')
    if not clients_dir.is_dir():
        return jsonify({"clients": [], "error": "No /mnt/clients mount"})

    skip = {'.pnpm-store', 'lost+found'}
    clients = []

    for entry in sorted(clients_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith('.') or entry.name in skip:
            continue

        username = entry.name
        client = {"username": username, "domain": f"{username}.jam-bot.com"}

        # Read host port from docker-compose.yml (format: "5003:5001")
        compose_file = entry / 'compose' / 'docker-compose.yml'
        port = None
        if compose_file.exists():
            try:
                import re
                content = compose_file.read_text()
                m = re.search(r'"(\d+):5001"', content)
                if m:
                    port = m.group(1)
            except Exception:
                pass
        client['port'] = port

        # Check monitoring events for last activity
        events_file = Path(f'/app/runtime/monitoring-events/{username}.jsonl')
        last_activity = None
        ovu_status = 'unknown'
        oc_status = 'unknown'
        if events_file.exists():
            try:
                # Read last few lines
                lines = events_file.read_text().strip().split('\n')
                for line in reversed(lines[-20:]):
                    try:
                        evt = json.loads(line)
                        if not last_activity:
                            last_activity = evt.get('ts', '')
                        etype = evt.get('type', '')
                        if etype == 'startup' and evt.get('source') == 'ovu':
                            ovu_status = 'running'
                        elif etype in ('claw_listening', 'claw_health_monitor'):
                            oc_status = 'running'
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

        # If no recent events (>2h old), mark as likely suspended
        if last_activity and ovu_status == 'unknown' and oc_status == 'unknown':
            try:
                from datetime import datetime as _dt
                last_ts = _dt.fromisoformat(last_activity.replace('Z', '+00:00'))
                age_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
                if age_hours > 2:
                    ovu_status = 'suspended'
                    oc_status = 'suspended'
            except Exception:
                pass

        client['ovu_status'] = ovu_status
        client['openclaw_status'] = oc_status
        client['last_activity'] = last_activity

        clients.append(client)

    return jsonify({"clients": clients})


# ---------------------------------------------------------------------------
# AI Config — model selection + API keys (writes openclaw.json)
# ---------------------------------------------------------------------------

# Writable mount (template compose: single-file bind of the tenant's real
# openclaw.json). Older Phase-1.5 tenants only carry a read-only view at
# openclaw-client.json — reads fall back to it so the panel still shows the
# current model chain, but writes require the rw mount.
_OPENCLAW_CONFIG_PATH = Path('/app/runtime/openclaw.json')
_OPENCLAW_CONFIG_RO_FALLBACK = Path('/app/runtime/openclaw-client.json')

# Known providers and their config shape.
# Canonical source: /home/mike/MIKE-AI/docs/jambot/llm-provider-registry.md
# Active chain is Z.AI account A ('zai') + account B ('zai_fb') — both route to
# api.z.ai/api/anthropic. MiniMax ('mx'), bigmodel-GLM, and Groq-for-LLM are
# DROPPED providers and must never be offered here (Groq stays TTS-only).
# _AI_PROVIDERS moved to config/ai-providers.json (WO-1.2). A module-level
# property-like accessor keeps every existing `_AI_PROVIDERS[...]` / `.items()`
# call site working while reading the loadable catalog (mtime-cached, with the
# historical inline dict as the guaranteed fallback). Assigning at import time is
# fine because the loader is cheap and cached; the catalog endpoint + this module
# always call load_ai_providers() fresh where a live edit must be reflected.
from services.ai_providers import load_ai_providers as _load_ai_providers


class _AIProvidersProxy:
    """Read-only mapping proxy so `_AI_PROVIDERS` reflects config/ai-providers.json
    on every access without changing the many `_AI_PROVIDERS[pid]` call sites."""

    def _data(self):
        return _load_ai_providers()

    def __getitem__(self, key):
        return self._data()[key]

    def __contains__(self, key):
        return key in self._data()

    def get(self, key, default=None):
        return self._data().get(key, default)

    def items(self):
        return self._data().items()

    def keys(self):
        return self._data().keys()

    def values(self):
        return self._data().values()

    def __iter__(self):
        return iter(self._data())


_AI_PROVIDERS = _AIProvidersProxy()


def _parse_jsonc(text: str) -> dict:
    """Parse JSONC into a dict — string-aware (services.vault implementation).

    The previous local regex approach (r'//[^\\n]*') destroyed every
    'https://…' baseUrl value, so parsing failed on effectively every real
    tenant config. services.vault._parse_jsonc walks char-by-char and only
    strips comments outside string literals.
    """
    from services.vault import _parse_jsonc as _vault_parse_jsonc
    return _vault_parse_jsonc(text)


def _oc_config_read_path() -> Path | None:
    """The config path to read: rw mount first, then the ro Phase-1.5 view."""
    if _OPENCLAW_CONFIG_PATH.exists():
        return _OPENCLAW_CONFIG_PATH
    if _OPENCLAW_CONFIG_RO_FALLBACK.exists():
        return _OPENCLAW_CONFIG_RO_FALLBACK
    return None


def _oc_config_writable() -> bool:
    return _OPENCLAW_CONFIG_PATH.exists() and os.access(_OPENCLAW_CONFIG_PATH, os.W_OK)


def _read_oc_config() -> dict:
    """Read the openclaw.json config file (rw mount or ro fallback)."""
    path = _oc_config_read_path()
    if not path:
        return {}
    try:
        return _parse_jsonc(path.read_text())
    except Exception as exc:
        logger.error(f"Failed to parse {path.name}: {exc}")
        return {}


def _write_oc_config(config: dict):
    """Write openclaw.json IN PLACE (single write, same inode).

    The file reaches this container as a single-file bind mount, so an
    atomic tmp+rename would (a) fail with EBUSY on the mount point and
    (b) even if it worked, swap the inode and never reach the host file
    the openclaw container watches. A backup copy is kept beside it in
    the container layer for immediate restore.
    """
    serialized = json.dumps(config, indent=2)
    try:
        backup = _OPENCLAW_CONFIG_PATH.parent / (
            _OPENCLAW_CONFIG_PATH.name + '.bak-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        )
        backup.write_text(_OPENCLAW_CONFIG_PATH.read_text())
    except Exception as exc:
        logger.warning(f"openclaw.json backup skipped: {exc}")
    with open(_OPENCLAW_CONFIG_PATH, 'w') as fh:
        fh.write(serialized)
        fh.flush()
        os.fsync(fh.fileno())


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 6 and last 4 chars."""
    if not key or len(key) < 12:
        return '***' if key else ''
    return key[:6] + '...' + key[-4:]


# OpenClaw redacts apiKey values in config.get responses with this marker —
# its presence means "a key is configured", nothing more.
_OC_REDACTED = '__OPENCLAW_REDACTED__'


def _get_effective_config() -> tuple[dict, str, str]:
    """Current openclaw config, gateway-first.

    Returns (config, source, base_hash) where source is 'gateway' | 'file' | ''.
    The gateway is authoritative (no mount/permission dependency — openclaw
    chmods its config 600 on startup, so file access is best-effort only).
    base_hash is the config.get hash needed by config.patch ('' when file-sourced).
    """
    result = _run_rpc('config.get', {}, timeout=8.0)
    if result.get('ok'):
        payload = result.get('result') or {}
        parsed = payload.get('parsed')
        if isinstance(parsed, dict) and parsed:
            return parsed, 'gateway', payload.get('hash', '')
    config = _read_oc_config()
    return config, ('file' if config else ''), ''


@admin_bp.route('/api/admin/ai-config', methods=['GET'])
def get_ai_config():
    """
    Return the current AI model configuration + which API keys are set.
    """
    config, source, _hash = _get_effective_config()
    defaults = config.get('agents', {}).get('defaults', {})
    model_cfg = defaults.get('model', {})
    providers_cfg = config.get('models', {}).get('providers', {})

    primary = model_cfg.get('primary', '')
    fallbacks = model_cfg.get('fallbacks', [])
    fallback = fallbacks[0] if fallbacks else ''

    # Build provider status
    providers = {}
    for pid, pinfo in _AI_PROVIDERS.items():
        # Check if key is configured (in openclaw.json providers section)
        oc_prov = providers_cfg.get(pid, {})
        raw_key = oc_prov.get('apiKey', '')
        if raw_key == _OC_REDACTED:
            # Gateway redacts configured keys — try the env var for a mask.
            actual_key = os.environ.get(pinfo['envKey'], '') or raw_key
        elif raw_key.startswith('${') and raw_key.endswith('}'):
            # Env var reference like ${FOO} — check if the env var is set
            env_name = raw_key[2:-1]
            actual_key = os.environ.get(env_name, '')
        else:
            actual_key = raw_key
        # Provider not wired into openclaw.json yet, but its platform env key
        # exists — report it so the UI can offer one-click enable.
        if not actual_key:
            actual_key = os.environ.get(pinfo['envKey'], '')

        providers[pid] = {
            'name': pinfo['name'],
            'hasKey': bool(actual_key),
            'maskedKey': '•••' if actual_key == _OC_REDACTED else _mask_key(actual_key),
            'models': pinfo['models'],
            'configured': pid in providers_cfg,
        }

    return jsonify({
        'primary': primary,
        'fallback': fallback,
        'providers': providers,
        'subagentModel': defaults.get('subagents', {}).get('model', primary),
        'source': source,
    })


def _build_ai_config_partial(data: dict, config: dict) -> tuple[dict, list]:
    """Build a minimal deep-merge partial (config.patch shape) of ONLY the
    requested changes, against the current effective config. Returns
    (partial, change_log). Empty partial = nothing to change."""
    partial = {}
    changes = []

    providers_cfg = config.get('models', {}).get('providers', {})
    for pid, key_value in (data.get('keys') or {}).items():
        if pid not in _AI_PROVIDERS or not key_value:
            continue
        pinfo = _AI_PROVIDERS[pid]
        prov_partial = {'apiKey': key_value}
        if pid not in providers_cfg:
            # New provider — include the full shape so openclaw can use it.
            prov_partial.update({
                'baseUrl': pinfo['baseUrl'],
                'api': pinfo['api'],
                'models': [dict(m) for m in pinfo['models']],
            })
        partial.setdefault('models', {}).setdefault('providers', {})[pid] = prov_partial
        changes.append(f'apiKey:{pid}')

    defaults = config.get('agents', {}).get('defaults', {})
    model_cfg = defaults.get('model', {})
    agents_partial = {}

    new_primary = data.get('primary')
    if new_primary and new_primary != model_cfg.get('primary'):
        agents_partial.setdefault('model', {})['primary'] = new_primary
        agents_partial.setdefault('models', {})[new_primary] = {}
        # Subagent model follows primary
        agents_partial.setdefault('subagents', {})['model'] = new_primary
        changes.append(f'primary→{new_primary}')

    new_fallback = data.get('fallback')
    if new_fallback is not None:
        new_fallbacks = [new_fallback] if new_fallback else []
        if new_fallbacks != model_cfg.get('fallbacks', []):
            agents_partial.setdefault('model', {})['fallbacks'] = new_fallbacks
            if new_fallback:
                agents_partial.setdefault('models', {})[new_fallback] = {}
            changes.append(f'fallback→{new_fallback or "none"}')

    if agents_partial:
        partial['agents'] = {'defaults': agents_partial}
    return partial, changes


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


@admin_bp.route('/api/admin/ai-config', methods=['PUT'])
def update_ai_config():
    """
    Update AI model configuration and/or API keys.

    Request body (all fields optional):
    {
        "primary": "zai/glm-5-turbo",
        "fallback": "zai_fb/glm-5-turbo",
        "keys": {
            "anthropic": "sk-ant-...",
            "openai": "sk-..."
        }
    }

    Write path: the gateway's config.patch RPC (schema-validated, hot-applied
    by openclaw itself, no file permissions involved). Falls back to a direct
    in-place file write only in local/self-hosted mode when the gateway is
    unreachable but the config file is writable.
    """
    data = request.get_json(silent=True) or {}

    # Validate model refs early — a typo here would land in openclaw.json.
    for field in ('primary', 'fallback'):
        val = data.get(field)
        if val and ('/' not in val or val.startswith('/') or val.endswith('/')):
            return jsonify({'error': f"{field} must be 'provider/model-id', got: {val}"}), 400

    # WO-2.2 — framework-generic dispatch. If the active profile's gateway is
    # NOT openclaw, route the model/key change through that framework's
    # configure() instead of the openclaw config.patch path (a Hermes-primary
    # tenant used to 502 here because only openclaw has a config RPC).
    active_gid = _active_gateway_id()
    if active_gid and active_gid != 'openclaw':
        from services.gateway_manager import gateway_manager
        gw = gateway_manager.get(active_gid)
        if gw is not None:
            try:
                result = gw.configure(data)
            except Exception as exc:
                logger.error("framework configure(%s) failed: %s", active_gid, exc)
                return jsonify({'ok': False, 'error': f'{active_gid} configure failed: {exc}'}), 502
            status = result.get('status')
            if status in ('applied', 'needs_restart'):
                return jsonify({'ok': True, 'gateway': active_gid, 'status': status,
                                'message': result.get('detail', ''),
                                'changes': result.get('changes', [])})
            return jsonify({'ok': False, 'gateway': active_gid,
                            'error': result.get('detail', 'configure failed')}), 502

    config, source, base_hash = _get_effective_config()
    if not config:
        return jsonify({'error': 'Cannot read openclaw config — gateway unreachable and no '
                                 'readable openclaw.json mount'}), 500

    partial, changes = _build_ai_config_partial(data, config)
    if not partial:
        return jsonify({'ok': True, 'message': 'No changes'})

    # Path 1 — gateway config.patch (authoritative, schema-validated).
    if source == 'gateway':
        result = _run_rpc('config.patch', {'raw': json.dumps(partial), 'baseHash': base_hash},
                          timeout=15.0)
        if result.get('ok'):
            logger.info(f"AI Config: patched via gateway ({', '.join(changes)})")
            return jsonify({'ok': True, 'message': 'Config updated via OpenClaw gateway — '
                                                   'changes are validated and applied by openclaw itself.',
                            'changes': changes})
        logger.error(f"AI Config: gateway config.patch failed: {result.get('error')}")
        return jsonify({'ok': False, 'error': f"Gateway rejected config change: {result.get('error')}"}), 502

    # Path 2 — local mode: direct in-place file write.
    if not _oc_config_writable():
        return jsonify({'error': 'Gateway unreachable and openclaw.json is not writable from this '
                                 'container — cannot apply config changes.'}), 409
    try:
        _write_oc_config(_deep_merge(config, partial))
        logger.info(f"AI Config: wrote openclaw.json directly ({', '.join(changes)})")
        return jsonify({'ok': True, 'message': 'Config saved to openclaw.json. Restart the openclaw '
                                               'gateway if a change does not take effect.',
                        'changes': changes})
    except Exception as exc:
        logger.error(f"Failed to write openclaw.json: {exc}")
        return jsonify({'error': 'Write failed — see server logs'}), 500


# ---------------------------------------------------------------------------
# Agent Framework — gateway list + generic configure dispatch (WO-2.2)
# ---------------------------------------------------------------------------

def _active_gateway_id() -> str | None:
    """The gateway_id of the active profile (adapter_config.gateway_id).

    Returns 'openclaw' default when unset, None if profiles can't be read.
    """
    try:
        from profiles.manager import get_profile_manager
        import routes.profiles as _profiles_mod
        mgr = get_profile_manager()
        active_id = getattr(_profiles_mod, '_active_profile_id', None)
        prof = mgr.get_profile(active_id)  # get_profile(None) → default
        if prof is not None:
            return (prof.adapter_config or {}).get('gateway_id', 'openclaw')
    except Exception as exc:
        logger.debug("active gateway lookup failed: %s", exc)
    return None


def _profiles_by_gateway() -> dict:
    """Map gateway_id -> list of profile ids that select it (adapter_config)."""
    out: dict[str, list] = {}
    try:
        from profiles.manager import get_profile_manager
        mgr = get_profile_manager()
        for p in mgr.list_profiles():
            # list_profiles() returns dicts (see manager.to summary) — be tolerant.
            pid = p.get('id') if isinstance(p, dict) else getattr(p, 'id', None)
            adapter = (p.get('adapter_config') if isinstance(p, dict)
                       else getattr(p, 'adapter_config', {})) or {}
            gid = adapter.get('gateway_id', 'openclaw')
            if pid:
                out.setdefault(gid, []).append(pid)
    except Exception as exc:
        logger.debug("profiles-by-gateway lookup failed: %s", exc)
    return out


@admin_bp.route('/api/admin/gateways', methods=['GET'])
def admin_gateways():
    """List every registered gateway with the WO-2.1 rich contract + which
    profiles select it + the active gateway. Powers the Agent Framework tab."""
    from services.gateway_manager import gateway_manager
    gateways = gateway_manager.list_gateways(rich=True)
    by_gateway = _profiles_by_gateway()
    for gw in gateways:
        gw['profiles'] = by_gateway.get(gw['id'], [])
    return jsonify({
        'gateways': gateways,
        'active_gateway': _active_gateway_id(),
    })


@admin_bp.route('/api/admin/gateways/<gateway_id>/configure', methods=['POST'])
def admin_gateway_configure(gateway_id):
    """Dispatch a configuration change to a framework's configure() (WO-2.2)."""
    from services.gateway_manager import gateway_manager
    gw = gateway_manager.get(gateway_id)
    if gw is None:
        return jsonify({'error': f"Gateway '{gateway_id}' not registered"}), 404
    partial = request.get_json(silent=True) or {}
    try:
        result = gw.configure(partial)
    except Exception as exc:
        logger.error("gateway configure(%s) error: %s", gateway_id, exc)
        return jsonify({'ok': False, 'error': 'configure failed — see server logs'}), 502
    status = result.get('status')
    if status in ('applied', 'needs_restart'):
        return jsonify({'ok': True, 'status': status,
                        'message': result.get('detail', ''),
                        'changes': result.get('changes', [])})
    return jsonify({'ok': False, 'status': status or 'error',
                    'error': result.get('detail', 'configure failed')}), 400


# ---------------------------------------------------------------------------
# TTS fallback policy — chain + voice-gender map (WO-3.1)
# ---------------------------------------------------------------------------
# The fallback chain + voice-gender map live in config/tts-fallback.json and are
# read THROUGH services.tts_fallback at runtime (services/tts.py), so an edit
# here takes effect on the next utterance with no restart. These endpoints are
# admin-gated by app.py (they live under /api/admin/).

_TTS_FALLBACK_CONFIG_PATH = _PROJECT_ROOT / 'config' / 'tts-fallback.json'


def _registered_tts_ids() -> set:
    """The set of registered TTS provider ids — the only valid chain members."""
    try:
        from tts_providers import list_providers
        ids = set()
        for p in list_providers(include_inactive=True, probe=False):
            pid = p.get('provider_id') or p.get('id')
            if pid:
                ids.add(pid)
        return ids
    except Exception as exc:
        logger.warning("could not enumerate TTS providers: %s", exc)
        return set()


@admin_bp.route('/api/admin/tts-fallback', methods=['GET'])
def get_tts_fallback():
    """Return the current TTS fallback policy + the valid provider id set.

    Response:
      {"chain": {...}, "voice_gender": {...}, "provider_ids": [...]}
    """
    from services.tts_fallback import load_tts_fallback
    policy = load_tts_fallback()
    return jsonify({
        'chain': policy.get('chain', {}),
        'voice_gender': policy.get('voice_gender', {}),
        'provider_ids': sorted(_registered_tts_ids()),
    })


@admin_bp.route('/api/admin/tts-fallback', methods=['PUT'])
def update_tts_fallback():
    """Replace the TTS fallback policy (config/tts-fallback.json).

    Body: {"chain": {...}, "voice_gender": {...}}
    Validates every provider id against the live registry and rejects a chain
    with a cycle BEFORE writing. Writes in place, then invalidates the loader
    cache so the change is live on the next utterance.
    """
    from services.tts_fallback import validate_fallback_policy, invalidate

    data = request.get_json(silent=True) or {}
    policy = {
        'chain': data.get('chain', {}),
        'voice_gender': data.get('voice_gender', {}),
    }

    valid_ids = _registered_tts_ids()
    if not valid_ids:
        return jsonify({'ok': False, 'error': 'TTS provider registry unavailable — '
                                              'cannot validate fallback chain'}), 503

    err = validate_fallback_policy(policy, valid_ids)
    if err:
        return jsonify({'ok': False, 'error': err}), 400

    # Preserve the _comment header if the existing file has one.
    out = {}
    try:
        existing = json.loads(_TTS_FALLBACK_CONFIG_PATH.read_text())
        if isinstance(existing, dict) and existing.get('_comment'):
            out['_comment'] = existing['_comment']
    except Exception:
        pass
    out['chain'] = policy['chain']
    out['voice_gender'] = policy['voice_gender']

    try:
        # Atomic write — this is a plain repo/config file (NOT the openclaw.json
        # single-file bind mount), so tmp+rename is safe and preferred here.
        tmp = _TTS_FALLBACK_CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(out, indent=2))
        tmp.replace(_TTS_FALLBACK_CONFIG_PATH)
    except Exception as exc:
        logger.error("Failed to write tts-fallback.json: %s", exc)
        return jsonify({'ok': False, 'error': 'write failed — see server logs'}), 500

    invalidate()  # drop the mtime cache so the next utterance reads the new policy
    logger.info("TTS fallback policy updated (%d chain hops, %d voices)",
                len(policy['chain']), len(policy['voice_gender']))
    return jsonify({'ok': True, 'chain': policy['chain'],
                    'voice_gender': policy['voice_gender']})


# ---------------------------------------------------------------------------
# Update manager panel (WO-4.2) — admin-gated wrappers over
# services/update_manager.py. All live under /api/admin/ so they inherit the
# admin gate. The app-level /api/version* endpoints stay untouched (the main-app
# UpdateBanner still uses them); these are the operator-panel surface with an
# explicit preview → apply → verify → ROLLBACK flow.
# ---------------------------------------------------------------------------

def _update_manager():
    from services.update_manager import UpdateManager
    return UpdateManager(_PROJECT_ROOT)


@admin_bp.route('/api/admin/update/status', methods=['GET'])
def admin_update_status():
    """Current version + deployment mode. Cheap — reads version.json/package.json
    and detects deployment type; no git fetch, no network."""
    try:
        mgr = _update_manager()
        version = {}
        vf = _PROJECT_ROOT / 'version.json'
        if vf.exists():
            try:
                version = json.loads(vf.read_text())
            except Exception:
                version = {}
        pkg_version = ''
        pf = _PROJECT_ROOT / 'package.json'
        if pf.exists():
            try:
                pkg_version = json.loads(pf.read_text()).get('version', '')
            except Exception:
                pass
        return jsonify({
            'commit': version.get('commit', 'unknown'),
            'branch': version.get('branch', 'unknown'),
            'date': version.get('date', 'unknown'),
            'version': pkg_version,
            'deployment_type': mgr.detect_deployment_type(),
            'is_git_repo': (_PROJECT_ROOT / '.git').is_dir(),
        })
    except Exception as exc:
        logger.error("admin update status failed: %s", exc)
        return jsonify({'error': 'Failed to read update status'}), 500


@admin_bp.route('/api/admin/update/preview', methods=['GET'])
def admin_update_preview():
    """Preview what an update WOULD change (git fetch + diff analysis, no
    mutation). Also reports which CLI agent / smart path would run + risk."""
    try:
        return jsonify(_update_manager().get_update_preview())
    except Exception as exc:
        logger.error("admin update preview failed: %s", exc)
        return jsonify({'error': 'Update preview failed — see server logs'}), 500


@admin_bp.route('/api/admin/update/verify', methods=['POST'])
def admin_update_verify():
    """Run the post-update health checks (import app, parse routes, pip check).
    Read-only — safe to run any time to confirm the checkout is healthy."""
    try:
        return jsonify(_update_manager().verify())
    except Exception as exc:
        logger.error("admin update verify failed: %s", exc)
        return jsonify({'healthy': False, 'errors': ['verify failed — see server logs']}), 500


@admin_bp.route('/api/admin/update/apply', methods=['POST'])
def admin_update_apply():
    """Apply the intelligent update (analyse → agent/smart → verify → maybe
    rollback → schedule restart). Guarded by an explicit {"confirm": true} in the
    body so a stray GET/click can never trigger a live update."""
    data = request.get_json(silent=True) or {}
    if data.get('confirm') is not True:
        return jsonify({'ok': False,
                        'error': 'confirmation required: POST {"confirm": true}'}), 400
    try:
        result = _update_manager().apply_update()
        ok = result.get('status') in ('success', 'current')
        return jsonify({'ok': ok, **result}), (200 if ok else 502)
    except Exception as exc:
        logger.error("admin update apply failed: %s", exc)
        return jsonify({'ok': False, 'error': 'Update apply failed — see server logs'}), 500


@admin_bp.route('/api/admin/update/rollback', methods=['POST'])
def admin_update_rollback():
    """Roll the checkout back to a prior commit (git reset --hard + restore any
    .pre-update backups). Requires an explicit {"target_commit": "<sha>"} — the
    panel captures the pre-update commit from /api/admin/update/status and passes
    it here."""
    data = request.get_json(silent=True) or {}
    target = (data.get('target_commit') or '').strip()
    if not target:
        return jsonify({'ok': False, 'error': 'target_commit is required'}), 400
    # Only allow short/long hex SHAs — never an arbitrary ref/command.
    if not all(c in '0123456789abcdefABCDEF' for c in target) or not (7 <= len(target) <= 40):
        return jsonify({'ok': False, 'error': 'target_commit must be a git SHA (7-40 hex chars)'}), 400
    try:
        mgr = _update_manager()
        mgr.rollback(target)
        return jsonify({'ok': True, 'rolled_back_to': target,
                        'message': 'Rolled back. Restart the app to load the reverted code.'})
    except Exception as exc:
        logger.error("admin update rollback failed: %s", exc)
        return jsonify({'ok': False, 'error': 'Rollback failed — see server logs'}), 500


# ---------------------------------------------------------------------------
# Marketplace update-diff + one-click update (WO-5.1) — thin wrappers over
# services/plugins.py. Version-diff each installed plugin vs the catalog/GitHub
# registry; update reuses the install lifecycle (soft-archive + reinstall).
# ---------------------------------------------------------------------------

@admin_bp.route('/api/admin/plugins/updates', methods=['GET'])
def admin_plugins_updates():
    """Per-installed-plugin version diff vs the catalog/registry (update badges)."""
    try:
        from services.plugins import get_plugin_updates
        rows = get_plugin_updates()
        return jsonify({'plugins': rows,
                        'update_count': sum(1 for r in rows if r['update_available'])})
    except Exception as exc:
        logger.error("plugin updates check failed: %s", exc)
        return jsonify({'error': 'Failed to check plugin updates'}), 500


@admin_bp.route('/api/admin/plugins/<plugin_id>/update', methods=['POST'])
def admin_plugin_update(plugin_id):
    """One-click update: soft-archive the current plugin dir + reinstall the
    catalog/registry version. Rolls back the archive on failure."""
    from services.plugins import update_plugin, get_plugin
    if get_plugin(plugin_id) is None:
        return jsonify({'ok': False, 'error': f"Plugin '{plugin_id}' is not installed"}), 404
    data = request.get_json(silent=True) or {}
    config = data.get('config')
    try:
        result = update_plugin(plugin_id, config=config)
    except Exception as exc:
        logger.error("plugin update(%s) error: %s", plugin_id, exc)
        return jsonify({'ok': False, 'error': 'Update failed — see server logs'}), 500
    if result is None:
        return jsonify({'ok': False, 'error': 'Plugin not installed'}), 404
    if result.get('_error'):
        return jsonify({'ok': False, 'error': result['_error']}), 502
    return jsonify({'ok': True, 'plugin': result.get('name', plugin_id),
                    'version': result.get('version'),
                    'updated_from': result.get('_updated_from'),
                    'status': result.get('_status'),
                    'note': 'Restart the app (Plugins tab) to activate the updated plugin.'})


# ---------------------------------------------------------------------------
# Greetings editor helpers (WO-4.3) — the greetings blueprint already exposes
# GET /api/greetings + POST /api/greetings/add; these admin-gated wrappers add
# the two edit ops a compact editor needs (queue a one-shot next greeting, and
# remove a contextual entry). List-entry edits, not file deletes.
# ---------------------------------------------------------------------------

def _greetings_path():
    from routes.greetings import GREETINGS_PATH
    return GREETINGS_PATH


@admin_bp.route('/api/admin/greetings/queue', methods=['POST'])
def admin_greetings_queue():
    """Queue a one-shot 'next_greeting' used at the next session start."""
    from routes.greetings import _load, _save
    data = request.get_json(silent=True) or {}
    greeting = (data.get('greeting') or '').strip()
    if not greeting:
        return jsonify({'ok': False, 'error': 'greeting is required'}), 400
    if len(greeting) > 300:
        return jsonify({'ok': False, 'error': 'greeting too long (max 300)'}), 400
    d = _load()
    d['next_greeting'] = greeting
    _save(d)
    return jsonify({'ok': True, 'queued': greeting})


@admin_bp.route('/api/admin/greetings/remove-contextual', methods=['POST'])
def admin_greetings_remove_contextual():
    """Remove one contextual greeting by exact text (config-list edit)."""
    from routes.greetings import _load, _save
    data = request.get_json(silent=True) or {}
    text = (data.get('greeting') or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'greeting is required'}), 400
    d = _load()
    contextual = d.get('greetings', {}).get('contextual', [])
    new_list = [g for g in contextual if g != text]
    if len(new_list) == len(contextual):
        return jsonify({'ok': False, 'error': 'greeting not found in contextual list'}), 404
    d['greetings']['contextual'] = new_list
    _save(d)
    return jsonify({'ok': True, 'remaining': len(new_list)})
