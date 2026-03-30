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

            # Step 2 — send connect request
            req_id = str(uuid.uuid4())
            params = build_connect_params(
                auth_token=auth_token,
                client_id="cli",
                client_mode="cli",
                platform="linux",
                user_agent="openvoice-ui-admin/1.0.0",
                caps=[],
            )
            await ws.send(json.dumps({
                "type": "req",
                "id": f"connect-{req_id}",
                "method": "connect",
                "params": params,
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
    # Chat operations
    'chat.abort',
    'chat.send',
    # Diagnostic
    'ping',
    'status',
    'agent.status',
})


# ---------------------------------------------------------------------------
# Auth check endpoint
# ---------------------------------------------------------------------------

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
    # "missing scope" or "unknown method" = handshake succeeded, just no permission for ping
    auth_ok = 'missing scope' in err_str or 'method' in err_str or 'unknown' in err_str
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
                    challenge = await asyncio.wait_for(ws.recv(), timeout=5)
                    await ws.send(_json.dumps({'type':'connect','token':auth_token,'protocol':3,'role':'operator'}))
                    hello = await asyncio.wait_for(ws.recv(), timeout=5)
                    req_id = str(uuid.uuid4())[:8]
                    await ws.send(_json.dumps({'type':'req','id':req_id,'method':'chat.send','params':{'sessionKey':'admin-install','message':message,'deliver':False}}))
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

_OPENCLAW_CONFIG_PATH = Path('/app/runtime/openclaw.json')

# Known providers and their config shape
_AI_PROVIDERS = {
    'mx': {
        'name': 'MiniMax',
        'envKey': 'MINIMAX_API_KEY',
        'baseUrl': 'https://api.minimax.io/anthropic',
        'api': 'anthropic-messages',
        'models': [
            {'id': 'MiniMax-M2.7-highspeed', 'name': 'M2.7 Highspeed', 'contextWindow': 204800},
            {'id': 'MiniMax-M2.7', 'name': 'M2.7', 'contextWindow': 204800},
        ],
    },
    'glm': {
        'name': 'GLM / ZhipuAI',
        'envKey': 'GLM_API_KEY',
        'baseUrl': 'https://open.bigmodel.cn/api/paas/v4',
        'api': 'openai-completions',
        'models': [
            {'id': 'glm-5-turbo', 'name': 'GLM-5 Turbo', 'contextWindow': 128000},
            {'id': 'glm-4-plus', 'name': 'GLM-4 Plus', 'contextWindow': 128000},
        ],
    },
    'zai': {
        'name': 'Z.AI',
        'envKey': 'ZAI_API_KEY',
        'baseUrl': 'https://api.z.ai/api/anthropic/v1/messages',
        'api': 'anthropic-messages',
        'models': [
            {'id': 'glm-5-turbo', 'name': 'GLM-5 Turbo (Z.AI)', 'contextWindow': 204000},
            {'id': 'glm-4.7', 'name': 'GLM-4.7 (Z.AI)', 'contextWindow': 204000},
        ],
    },
    'openai': {
        'name': 'OpenAI',
        'envKey': 'OPENAI_API_KEY',
        'baseUrl': 'https://api.openai.com/v1',
        'api': 'openai-responses',
        'models': [
            {'id': 'gpt-4.1', 'name': 'GPT-4.1', 'contextWindow': 1047576},
            {'id': 'gpt-4o', 'name': 'GPT-4o', 'contextWindow': 128000},
            {'id': 'gpt-4o-mini', 'name': 'GPT-4o Mini', 'contextWindow': 128000},
        ],
    },
    'anthropic': {
        'name': 'Anthropic',
        'envKey': 'ANTHROPIC_API_KEY',
        'baseUrl': 'https://api.anthropic.com',
        'api': 'anthropic-messages',
        'models': [
            {'id': 'claude-sonnet-4-5-20250514', 'name': 'Claude Sonnet 4.5', 'contextWindow': 200000},
            {'id': 'claude-haiku-4-5-20251001', 'name': 'Claude Haiku 4.5', 'contextWindow': 200000},
        ],
    },
    'groqcloud': {
        'name': 'Groq',
        'envKey': 'GROQ_API_KEY',
        'baseUrl': 'https://api.groq.com/openai/v1',
        'api': 'openai-completions',
        'models': [
            {'id': 'llama-4-scout-17b-16e-instruct', 'name': 'Llama 4 Scout', 'contextWindow': 131072},
            {'id': 'llama-4-maverick-17b-128e-instruct', 'name': 'Llama 4 Maverick', 'contextWindow': 131072},
        ],
    },
    'google': {
        'name': 'Google Gemini',
        'envKey': 'GEMINI_API_KEY',
        'baseUrl': 'https://generativelanguage.googleapis.com/v1beta',
        'api': 'google-generative-ai',
        'models': [
            {'id': 'gemini-2.5-flash', 'name': 'Gemini 2.5 Flash', 'contextWindow': 1048576},
            {'id': 'gemini-2.5-pro', 'name': 'Gemini 2.5 Pro', 'contextWindow': 1048576},
        ],
    },
}


def _parse_jsonc(text: str) -> dict:
    """Parse JSONC (JSON with comments and trailing commas) into a dict."""
    import re
    # Strip single-line comments (// ...)
    text = re.sub(r'//[^\n]*', '', text)
    # Strip multi-line comments (/* ... */)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return json.loads(text)


def _read_oc_config() -> dict:
    """Read the openclaw.json config file."""
    if not _OPENCLAW_CONFIG_PATH.exists():
        return {}
    try:
        return _parse_jsonc(_OPENCLAW_CONFIG_PATH.read_text())
    except Exception as exc:
        logger.error(f"Failed to parse openclaw.json: {exc}")
        return {}


def _write_oc_config(config: dict):
    """Write openclaw.json (clean JSON). OpenClaw hot-reloads on change."""
    tmp = _OPENCLAW_CONFIG_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(config, indent=2))
    tmp.replace(_OPENCLAW_CONFIG_PATH)


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 6 and last 4 chars."""
    if not key or len(key) < 12:
        return '***' if key else ''
    return key[:6] + '...' + key[-4:]


@admin_bp.route('/api/admin/ai-config', methods=['GET'])
def get_ai_config():
    """
    Return the current AI model configuration + which API keys are set.
    """
    config = _read_oc_config()
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
        # If it's an env var reference like ${FOO}, check if env var is set
        if raw_key.startswith('${') and raw_key.endswith('}'):
            env_name = raw_key[2:-1]
            actual_key = os.environ.get(env_name, '')
        else:
            actual_key = raw_key

        providers[pid] = {
            'name': pinfo['name'],
            'hasKey': bool(actual_key),
            'maskedKey': _mask_key(actual_key),
            'models': pinfo['models'],
            'configured': pid in providers_cfg,
        }

    return jsonify({
        'primary': primary,
        'fallback': fallback,
        'providers': providers,
        'subagentModel': defaults.get('subagents', {}).get('model', primary),
    })


@admin_bp.route('/api/admin/ai-config', methods=['PUT'])
def update_ai_config():
    """
    Update AI model configuration and/or API keys.

    Request body (all fields optional):
    {
        "primary": "mx/MiniMax-M2.7-highspeed",
        "fallback": "glm/glm-5-turbo",
        "keys": {
            "mx": "sk-...",
            "openai": "sk-..."
        }
    }
    """
    data = request.get_json(silent=True) or {}
    config = _read_oc_config()

    if not config:
        return jsonify({'error': 'Cannot read openclaw.json — config mount missing'}), 500

    defaults = config.setdefault('agents', {}).setdefault('defaults', {})
    model_cfg = defaults.setdefault('model', {})
    models_section = config.setdefault('models', {'mode': 'merge', 'providers': {}})
    providers_cfg = models_section.setdefault('providers', {})

    changed = False

    # Update API keys
    keys = data.get('keys', {})
    for pid, key_value in keys.items():
        if pid not in _AI_PROVIDERS or not key_value:
            continue
        pinfo = _AI_PROVIDERS[pid]
        # Ensure provider section exists in config
        if pid not in providers_cfg:
            providers_cfg[pid] = {
                'baseUrl': pinfo['baseUrl'],
                'api': pinfo['api'],
                'apiKey': key_value,
                'models': [
                    {k: v for k, v in m.items()}
                    for m in pinfo['models']
                ],
            }
        else:
            providers_cfg[pid]['apiKey'] = key_value
        changed = True
        logger.info(f"AI Config: updated API key for {pid}")

    # Update model selection
    new_primary = data.get('primary')
    if new_primary and new_primary != model_cfg.get('primary'):
        model_cfg['primary'] = new_primary
        # Also update the models dict
        defaults.setdefault('models', {})[new_primary] = {}
        # Subagent model follows primary
        defaults.setdefault('subagents', {})['model'] = new_primary
        changed = True
        logger.info(f"AI Config: primary model → {new_primary}")

    new_fallback = data.get('fallback')
    if new_fallback is not None:
        current_fallbacks = model_cfg.get('fallbacks', [])
        new_fallbacks = [new_fallback] if new_fallback else []
        if new_fallbacks != current_fallbacks:
            model_cfg['fallbacks'] = new_fallbacks
            if new_fallback:
                defaults.setdefault('models', {})[new_fallback] = {}
            changed = True
            logger.info(f"AI Config: fallback model → {new_fallback or 'none'}")

    if changed:
        try:
            _write_oc_config(config)
            return jsonify({'ok': True, 'message': 'Config saved — OpenClaw will hot-reload'})
        except Exception as exc:
            logger.error(f"Failed to write openclaw.json: {exc}")
            return jsonify({'error': f'Write failed: {exc}'}), 500
    else:
        return jsonify({'ok': True, 'message': 'No changes'})
