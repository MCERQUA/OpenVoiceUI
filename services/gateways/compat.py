"""
OpenClaw protocol compatibility layer.

Centralizes all version-sensitive constants, event names, and protocol
details so that upgrading OpenClaw requires changing ONE file instead of
hunting through 4+ modules.

When OpenClaw bumps protocol versions or renames events, update the
mappings here and all consumers benefit automatically.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol version negotiation
# ---------------------------------------------------------------------------

# Supported protocol versions — accept a range so minor bumps don't break us.
# OpenClaw negotiates the highest mutually supported version.
PROTOCOL_MIN = 3
PROTOCOL_MAX = 5  # forward-compatible — OpenClaw ignores unsupported maxes

# Version this code was tested against (for warning logs).
OPENCLAW_TESTED_VERSION = "2026.5.2"
OPENCLAW_MIN_VERSION = "2026.3.1"


# ---------------------------------------------------------------------------
# Event name aliases — maps canonical names to sets of accepted alternatives.
# When OpenClaw renames an event, add the new name here.
# ---------------------------------------------------------------------------

# Top-level event types (data['event'] field)
_EVENT_ALIASES = {
    'agent': frozenset({'agent', 'run', 'agent.run'}),
    'chat': frozenset({'chat', 'conversation', 'chat.state'}),
    'connect.challenge': frozenset({'connect.challenge', 'auth.challenge'}),
}

# Agent stream types (payload['stream'] field)
_STREAM_ALIASES = {
    'assistant': frozenset({'assistant', 'text', 'message'}),
    'tool': frozenset({'tool', 'tool_use', 'tool-use'}),
    'lifecycle': frozenset({'lifecycle', 'status', 'run.status'}),
}

# Chat state values (payload['state'] field)
_STATE_ALIASES = {
    'final': frozenset({'final', 'complete', 'done', 'finished'}),
    'aborted': frozenset({'aborted', 'cancelled', 'canceled'}),
    'error': frozenset({'error', 'failed'}),
    'delta': frozenset({'delta', 'streaming', 'partial'}),
}

# Subagent tool names — detected by pattern match, not exact strings
_SUBAGENT_SPAWN_PATTERNS = re.compile(
    r'(sessions?[_-]spawn|spawn[_-]?sub[_-]?agent|sub[_-]?agent\.spawn|agent\.delegate)',
    re.IGNORECASE
)

_SUBAGENT_SEND_PATTERNS = re.compile(
    r'(agent[_-]send|agent\.send|delegate[_-]message)',
    re.IGNORECASE
)

# Stale response model markers — gateway-injected responses to discard
_STALE_MODEL_MARKERS = frozenset({
    'gateway-injected',
    'system-generated',
    'injected',
    'replay',
})

# System response patterns to suppress (never surface to user)
_SYSTEM_RESPONSE_RE = re.compile(
    r'^\s*(HEARTBEAT[_ ]?OK|heartbeat[_ ]?ok|ACK|PONG|system[_-]ok)\s*$',
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def match_event(event_name: str) -> str | None:
    """Map an event name to its canonical form, or None if unknown.

    >>> match_event('agent')
    'agent'
    >>> match_event('run')
    'agent'
    >>> match_event('unknown')
    None
    """
    for canonical, aliases in _EVENT_ALIASES.items():
        if event_name in aliases:
            return canonical
    return None


def match_stream(stream_name: str) -> str | None:
    """Map a stream name to its canonical form."""
    for canonical, aliases in _STREAM_ALIASES.items():
        if stream_name in aliases:
            return canonical
    return None


def match_state(state_name: str) -> str | None:
    """Map a chat state to its canonical form."""
    for canonical, aliases in _STATE_ALIASES.items():
        if state_name in aliases:
            return canonical
    return None


def is_noise_event(event_name: str) -> bool:
    """Return True for events that should be silently dropped."""
    return event_name in ('health', 'tick', 'presence', 'ping', 'keepalive')


def is_subagent_spawn_tool(tool_name: str) -> bool:
    """Return True if this tool name indicates a subagent spawn."""
    return bool(_SUBAGENT_SPAWN_PATTERNS.search(tool_name))


def is_subagent_send_tool(tool_name: str) -> bool:
    """Return True if this tool name indicates an agent-send."""
    return bool(_SUBAGENT_SEND_PATTERNS.search(tool_name))


def is_subagent_tool(tool_name: str) -> bool:
    """Return True if this tool name is any subagent-related tool."""
    return is_subagent_spawn_tool(tool_name) or is_subagent_send_tool(tool_name)


def is_stale_response(model: str, total_tokens: int) -> bool:
    """Return True if this looks like a gateway-injected stale replay."""
    if model in _STALE_MODEL_MARKERS and total_tokens == 0:
        return True
    # Also check for explicit isStale flag (future OpenClaw versions)
    return False


def is_stale_response_ex(model: str, total_tokens: int, payload: dict) -> bool:
    """Extended stale check — includes payload-level flags."""
    if payload.get('isStale') or payload.get('injected'):
        return True
    return is_stale_response(model, total_tokens)


def is_system_response(text: str) -> bool:
    """Return True if this text is a system response that should be suppressed."""
    return bool(_SYSTEM_RESPONSE_RE.match(text))


def is_subagent_session_key(session_key: str) -> bool:
    """Return True if this session key belongs to a subagent."""
    # Accept multiple possible prefixes
    return any(prefix in session_key for prefix in ('subagent:', 'sub:', 'child:'))


def extract_server_version(hello_result: dict) -> str | None:
    """Extract server version from hello response, trying multiple paths."""
    if not hello_result:
        return None
    return (
        hello_result.get('serverVersion')
        or hello_result.get('version')
        or (hello_result.get('server', {}) or {}).get('version')
        or (hello_result.get('gateway', {}) or {}).get('version')
    )


def extract_run_id(data: dict) -> str | None:
    """Extract runId from an ACK response, trying multiple paths."""
    result = data.get('result') or data.get('payload') or {}
    return (
        result.get('runId')
        or data.get('runId')
        or result.get('run_id')
        or data.get('run_id')
    )


def extract_text_content(content) -> str:
    """Extract text from message content (string or Anthropic-style list).

    Handles:
    - Plain string
    - List of {type: "text", text: "..."} blocks
    - List of {type: "markdown", text: "..."} blocks
    - Mixed content blocks (extracts all text-like entries)
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                t = item.get('type', '')
                text = item.get('text', '')
                if t in ('text', 'markdown', 'plain') and text.strip():
                    text_parts.append(text)
                elif not t and text.strip():
                    # No type field — assume text
                    text_parts.append(text)
        return ' '.join(text_parts)
    return ''


def build_connect_params(
    auth_token: str,
    client_id: str = "cli",
    client_mode: str = "cli",
    platform: str = "linux",
    user_agent: str = "openvoice-ui/1.0.0",
    scopes: list | None = None,
    caps: list | None = None,
    device_block: dict | None = None,
) -> dict:
    """Build the params dict for a connect request.

    Centralizes the handshake params shape so all callers stay in sync.
    """
    if scopes is None:
        scopes = ["operator.admin", "operator.read", "operator.write"]
    if caps is None:
        caps = ["tool-events"]

    params = {
        "minProtocol": PROTOCOL_MIN,
        "maxProtocol": PROTOCOL_MAX,
        "client": {
            "id": client_id,
            "version": "1.0.0",
            "platform": platform,
            "mode": client_mode,
        },
        "role": "operator",
        "scopes": scopes,
        "caps": caps,
        "commands": [],
        "permissions": {},
        "auth": {"token": auth_token},
        "locale": "en-US",
        "userAgent": user_agent,
    }
    if device_block:
        params["device"] = device_block
    return params


def is_challenge_event(data: dict) -> bool:
    """Return True if this message is a connect challenge (current or future format)."""
    if data.get('type') != 'event':
        return False
    evt = data.get('event', '')
    return evt in _EVENT_ALIASES.get('connect.challenge', {evt})
