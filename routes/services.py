"""
Service Catalog API (WO-1.2 / Part C.1).

ADMIN-ONLY. Aggregates every configurable "service" — agent-framework gateway,
LLM, TTS, STT — into one descriptor shape so the admin panel becomes a renderer
over the catalog instead of hardcoding providers:

    {
      "service": "gateway|llm|tts|stt",
      "id": "hermes",
      "name": "Hermes Agent",
      "source": "builtin|plugin:<id>",
      "capabilities": ["streaming", "steer", ...],
      "config_schema": {"fields": [...]},
      "credentials": ["hermes_api_key"],
      "health": {"healthy": bool, "latency_ms": float|None, "status": "..."},
      "restart_scope": "none|ovui|container:<name>"
    }

Endpoints
  GET /api/services/catalog  — full descriptor list + credential_status map
  GET /api/services/health   — per-service health (cheap probes only)

SECURITY: both are gated by app.py require_auth() — '/api/services/' is in the
admin-only prefix set. NO secret values ever appear in the output (credential
status is has_value booleans + masked hints only). Health probes are cheap
(gateway socket, TTS singleton flags) — NEVER a vendor API call.
"""

import logging
import os

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

services_bp = Blueprint('services', __name__)


# ---------------------------------------------------------------------------
# Source + credential mapping helpers
# ---------------------------------------------------------------------------

_BUILTIN_GATEWAYS = {'openclaw'}
# gateway_id -> plugin id (for the "source" field)
_GATEWAY_PLUGIN = {
    'hermes': 'hermes-agent',
    'hermes-bridge': 'hermes-agent',
}
_GATEWAY_NAMES = {
    'openclaw': 'OpenClaw',
    'hermes': 'Hermes Agent',
    'hermes-bridge': 'Hermes Bridge (delegation)',
}

# Best-effort env-var credential per TTS provider (no secret values are read —
# only the NAME of the env var / whether a value is present).
_TTS_CRED_ENV = {
    'groq': 'GROQ_API_KEY',
    'elevenlabs': 'ELEVENLABS_API_KEY',
    'resemble': 'RESEMBLE_API_KEY',
    'hume': 'HUME_API_KEY',
    'qwen3': 'FAL_KEY',
    'qwen3-local': '',
    'supertonic': '',
}

# STT providers derived from the live /api/stt/* handlers (server.py) + the
# providers.registry STT registrations. webspeech is client-side (browser).
_STT_STATIC = {
    'webspeech': {
        'name': 'Web Speech API', 'source': 'builtin',
        'creds': [], 'note': 'Browser-native (Chrome/Edge). No server key.',
    },
    'deepgram': {
        'name': 'Deepgram', 'source': 'builtin',
        'creds': ['DEEPGRAM_API_KEY'], 'note': 'Cloud streaming STT (/api/stt/deepgram).',
    },
    'groq_whisper': {
        'name': 'Groq Whisper', 'source': 'builtin',
        'creds': ['GROQ_API_KEY'], 'note': 'Groq whisper-large (/api/stt/groq).',
    },
    'whisper': {
        'name': 'Whisper (local)', 'source': 'builtin',
        'creds': [], 'note': 'faster-whisper on-box (/api/stt/local).',
    },
    'external': {
        'name': 'External STT', 'source': 'builtin',
        'creds': ['STT_API_URL', 'STT_API_KEY'],
        'note': 'Bring-your-own Whisper-compatible API (/api/stt/external).',
    },
}


def _current_username() -> str:
    domain = os.getenv('DOMAIN', '')
    if domain and '.jam-bot.com' in domain:
        return domain.split('.')[0]
    return os.getenv('VAULT_USERNAME', 'default')


def _env_present(name: str) -> bool:
    return bool(name and os.environ.get(name, '').strip())


# ---------------------------------------------------------------------------
# Per-service descriptor builders
# ---------------------------------------------------------------------------

def _gateway_descriptors() -> list:
    """Agent-framework gateways via the WO-2.1 rich contract."""
    from services.gateway_manager import gateway_manager
    out = []
    for gw in gateway_manager.list_gateways(rich=True):
        gid = gw['id']
        plugin = _GATEWAY_PLUGIN.get(gid)
        source = 'builtin' if gid in _BUILTIN_GATEWAYS else (
            f'plugin:{plugin}' if plugin else f'plugin:{gid}')
        health = gw.get('health', {})
        out.append({
            'service': 'gateway',
            'id': gid,
            'name': _GATEWAY_NAMES.get(gid, gid),
            'source': source,
            'capabilities': gw.get('capabilities', []),
            'config_schema': gw.get('config_schema', {'fields': []}),
            'credentials': gw.get('credentials', []),
            'health': {
                'healthy': bool(gw.get('healthy')),
                'latency_ms': health.get('latency_ms'),
                'configured': bool(gw.get('configured')),
            },
            'restart_scope': gw.get('restart_scope', 'none'),
            'persistent': gw.get('persistent', False),
        })
    return out


def _tts_descriptors() -> list:
    """TTS providers from the tts_providers registry (network-free path)."""
    from tts_providers import list_providers
    out = []
    try:
        provs = list_providers(include_inactive=True, probe=False)
    except Exception as exc:
        logger.warning("TTS catalog build failed: %s", exc)
        return out
    for info in provs:
        pid = info.get('provider_id') or info.get('id') or ''
        env = _TTS_CRED_ENV.get(pid, '')
        requires_key = bool(info.get('requires_api_key'))
        fields = []
        creds = []
        if requires_key and env:
            fields.append({'id': env, 'type': 'password',
                           'label': f"{info.get('name', pid)} API key",
                           'required': True, 'credential': env})
            creds.append(env)
        out.append({
            'service': 'tts',
            'id': pid,
            'name': info.get('name', pid),
            'source': 'builtin',
            'capabilities': ['tts'] + (['clone'] if 'clone' in (info.get('features') or []) else []),
            'config_schema': {'fields': fields, 'editable': bool(fields)},
            'credentials': creds,
            'health': {
                'healthy': info.get('status', 'active') == 'active',
                'latency_ms': None,
                'status': info.get('status', 'active'),
                'has_key': (_env_present(env) if requires_key else True),
            },
            'restart_scope': 'none',
            'meta': {
                'cost_per_minute': info.get('cost_per_minute'),
                'latency': info.get('latency'),
                'requires_api_key': requires_key,
            },
        })
    return out


def _stt_registry_ids() -> set:
    """STT provider ids known to providers.registry (wired at startup, WO-1.3)."""
    try:
        from providers.registry import registry, ProviderType
        return set(registry.registered_ids(ProviderType.STT))
    except Exception as exc:
        logger.debug("STT registry unavailable: %s", exc)
        return set()


def _stt_descriptors() -> list:
    """STT catalog: live /api/stt/* handlers + providers.registry (WO-1.3)."""
    registry_ids = _stt_registry_ids()
    out = []
    for sid, meta in _STT_STATIC.items():
        creds = meta['creds']
        fields = [{'id': c, 'type': ('password' if 'KEY' in c else 'text'),
                   'label': c, 'required': (c.endswith('_URL')),
                   'credential': c} for c in creds]
        # has_key: all referenced env vars present (or none required)
        has_key = all(_env_present(c) for c in creds) if creds else True
        in_registry = sid in registry_ids or (
            sid == 'groq_whisper' and 'groq' in registry_ids)
        out.append({
            'service': 'stt',
            'id': sid,
            'name': meta['name'],
            'source': meta['source'],
            'capabilities': ['stt'],
            'config_schema': {'fields': fields, 'editable': bool(fields)},
            'credentials': creds,
            'health': {
                'healthy': has_key,
                'latency_ms': None,
                'has_key': has_key,
                'in_registry': in_registry,
            },
            'restart_scope': 'none',
            'meta': {'note': meta['note']},
        })
    return out


def _llm_descriptors() -> list:
    """LLM providers from the loadable catalog (config/ai-providers.json)."""
    from services.ai_providers import load_ai_providers
    out = []
    for pid, pinfo in load_ai_providers().items():
        env = pinfo.get('envKey', '')
        fields = []
        creds = []
        if env:
            fields.append({'id': env, 'type': 'password',
                           'label': f"{pinfo.get('name', pid)} API key",
                           'required': False, 'credential': env})
            creds.append(env)
        out.append({
            'service': 'llm',
            'id': pid,
            'name': pinfo.get('name', pid),
            'source': 'builtin',
            'capabilities': ['llm'],
            'config_schema': {'fields': fields, 'editable': bool(fields)},
            'credentials': creds,
            'health': {
                'healthy': _env_present(env) if env else True,
                'latency_ms': None,
                'has_key': _env_present(env) if env else True,
            },
            # LLM keys apply hot through the gateway config.patch RPC.
            'restart_scope': 'none',
            'meta': {
                'baseUrl': pinfo.get('baseUrl'),
                'api': pinfo.get('api'),
                'models': pinfo.get('models', []),
            },
        })
    return out


def _credential_status() -> dict:
    """Vault credential status keyed by cred_id — has_value + name only.

    NO secret values. references keys the descriptors point at so the panel can
    render a 'set / not set' chip. Best-effort — returns {} if the vault module
    or client can't be resolved.
    """
    try:
        from services.vault import get_credentials_status
        rows = get_credentials_status(_current_username())
    except Exception as exc:
        logger.debug("credential status unavailable: %s", exc)
        return {}
    status = {}
    for row in rows:
        status[row.get('id')] = {
            'name': row.get('name'),
            'group': row.get('group'),
            'has_value': bool(row.get('has_value')),
            'env_var': row.get('env_var', ''),
        }
    return status


def build_catalog() -> dict:
    """Assemble the full Service Catalog (all four service types)."""
    services = []
    services.extend(_gateway_descriptors())
    services.extend(_llm_descriptors())
    services.extend(_tts_descriptors())
    services.extend(_stt_descriptors())
    return {
        'services': services,
        'credential_status': _credential_status(),
        'counts': {
            'gateway': sum(1 for s in services if s['service'] == 'gateway'),
            'llm': sum(1 for s in services if s['service'] == 'llm'),
            'tts': sum(1 for s in services if s['service'] == 'tts'),
            'stt': sum(1 for s in services if s['service'] == 'stt'),
        },
    }


# ---------------------------------------------------------------------------
# Routes (admin-gated by app.py)
# ---------------------------------------------------------------------------

@services_bp.route('/api/services/catalog', methods=['GET'])
def services_catalog():
    """Full Service Catalog — descriptors for every gateway/llm/tts/stt."""
    try:
        return jsonify(build_catalog())
    except Exception as exc:
        logger.error("services/catalog failed: %s", exc)
        return jsonify({'error': 'Failed to build service catalog'}), 500


@services_bp.route('/api/services/health', methods=['GET'])
def services_health():
    """Per-service health with latency where cheap. No vendor API calls."""
    try:
        catalog = build_catalog()
    except Exception as exc:
        logger.error("services/health failed: %s", exc)
        return jsonify({'error': 'Failed to probe service health'}), 500

    health = []
    for s in catalog['services']:
        h = s.get('health', {})
        health.append({
            'service': s['service'],
            'id': s['id'],
            'name': s['name'],
            'healthy': bool(h.get('healthy')),
            'latency_ms': h.get('latency_ms'),
            'detail': {k: v for k, v in h.items()
                       if k not in ('healthy', 'latency_ms')},
        })
    healthy_count = sum(1 for h in health if h['healthy'])
    return jsonify({
        'services': health,
        'summary': {'total': len(health), 'healthy': healthy_count,
                    'unhealthy': len(health) - healthy_count},
    })
