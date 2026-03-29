"""
Instructions Blueprint — live agent instruction file editor.

GET  /api/instructions        → list available instruction files
GET  /api/instructions/<name> → read a file's content
PUT  /api/instructions/<name> → write new content to a file

Files live in prompts/ (app-level) and the OpenClaw workspace dir (config: paths.openclaw_workspace / env: OPENCLAW_WORKSPACE).
Changes take effect on the next conversation request — no restart needed.
"""

import os
import logging
from pathlib import Path
from flask import Blueprint, jsonify, request
from config.loader import config

logger = logging.getLogger(__name__)

instructions_bp = Blueprint('instructions', __name__)

# ── File registries ───────────────────────────────────────────────────────────

# App-level prompt files (relative to project root)
_APP_ROOT = Path(__file__).parent.parent

APP_FILES = {
    'voice-system-prompt': {
        'path': _APP_ROOT / 'prompts' / 'voice-system-prompt.md',
        'label': 'Voice System Prompt',
        'description': 'Injected before every user message sent to the OpenClaw Gateway. '
                       'Controls tone, formatting, and behaviour rules.',
        'scope': 'app',
        'hot_reload': True,
    },
}

# OpenClaw workspace files (read-only from the voice agent's perspective — agent writes these)
_OPENCLAW_DIR = Path(
    os.path.expanduser(config.get('paths.openclaw_workspace', '~/.openclaw/workspace'))
)
OPENCLAW_FILES = {
    'soul': {
        'path': _OPENCLAW_DIR / 'SOUL.md',
        'label': 'Soul (Core Identity)',
        'description': "Defines the agent's core identity, personality, and values.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'identity': {
        'path': _OPENCLAW_DIR / 'IDENTITY.md',
        'label': 'Identity (Name & Role)',
        'description': "Agent name, role, business context, and client-specific identity.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'agent': {
        'path': _OPENCLAW_DIR / 'AGENT.md',
        'label': 'Agent (Behavior)',
        'description': "Agent behavioral directives and operational rules.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'client': {
        'path': _OPENCLAW_DIR / 'CLIENT.md',
        'label': 'Client (Business Info)',
        'description': "Client business details, services, hours, contact info.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'agents': {
        'path': _OPENCLAW_DIR / 'AGENTS.md',
        'label': 'Agents (Sub-agents)',
        'description': "Sub-agent definitions and delegation rules.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'user': {
        'path': _OPENCLAW_DIR / 'USER.md',
        'label': 'User (Context)',
        'description': "Dynamic user context — updated by the agent from conversations.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'tools': {
        'path': _OPENCLAW_DIR / 'TOOLS.md',
        'label': 'Tools (Available Tools)',
        'description': "Tool definitions and usage rules.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'memory': {
        'path': _OPENCLAW_DIR / 'MEMORY.md',
        'label': 'Memory (Persistent)',
        'description': "Agent long-term memory — key facts accumulated across sessions.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'claude': {
        'path': _OPENCLAW_DIR / 'CLAUDE.md',
        'label': 'Claude (Capabilities)',
        'description': "Agent capability definitions and tool access rules.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
    'heartbeat': {
        'path': _OPENCLAW_DIR / 'HEARTBEAT.md',
        'label': 'Heartbeat (Status)',
        'description': "Agent self-monitoring and status tracking.",
        'scope': 'openclaw',
        'hot_reload': False,
    },
}

ALL_FILES = {**APP_FILES, **OPENCLAW_FILES}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_file(path: Path) -> tuple[str | None, str | None]:
    """Returns (content, error). error is None on success."""
    try:
        if not path.exists():
            return None, 'file_not_found'
        return path.read_text(encoding='utf-8'), None
    except Exception as e:
        logger.warning(f'[instructions] read {path}: {e}')
        return None, 'Read failed'


def _write_file(path: Path, content: str) -> str | None:
    """Returns error string or None on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return None
    except Exception as e:
        logger.warning(f'[instructions] write {path}: {e}')
        return 'Write failed'


# ── Routes ────────────────────────────────────────────────────────────────────

@instructions_bp.route('/api/instructions', methods=['GET'])
def list_instructions():
    """List all registered instruction files with metadata."""
    result = []
    for key, meta in ALL_FILES.items():
        content, err = _read_file(meta['path'])
        result.append({
            'id': key,
            'label': meta['label'],
            'description': meta['description'],
            'scope': meta['scope'],
            'hot_reload': meta['hot_reload'],
            'exists': err != 'file_not_found',
            'error': err if err and err != 'file_not_found' else None,
            'size': len(content) if content else 0,
        })
    return jsonify({'files': result})


@instructions_bp.route('/api/instructions/<name>', methods=['GET'])
def get_instruction(name: str):
    """Read a single instruction file."""
    if name not in ALL_FILES:
        return jsonify({'error': 'Unknown file', 'name': name}), 404

    meta = ALL_FILES[name]
    content, err = _read_file(meta['path'])

    if err == 'file_not_found':
        return jsonify({
            'id': name,
            'label': meta['label'],
            'scope': meta['scope'],
            'content': '',
            'exists': False,
        })

    if err:
        return jsonify({'error': err}), 500

    return jsonify({
        'id': name,
        'label': meta['label'],
        'scope': meta['scope'],
        'hot_reload': meta['hot_reload'],
        'content': content,
        'exists': True,
        'size': len(content),
    })


@instructions_bp.route('/api/instructions/<name>', methods=['PUT'])
def update_instruction(name: str):
    """Write new content to an instruction file."""
    if name not in ALL_FILES:
        return jsonify({'error': 'Unknown file', 'name': name}), 404

    body = request.get_json(silent=True) or {}
    content = body.get('content')
    if content is None:
        return jsonify({'error': 'Missing content field'}), 400

    meta = ALL_FILES[name]
    err = _write_file(meta['path'], content)
    if err:
        return jsonify({'error': err}), 500

    logger.info(f'[instructions] updated {name} ({len(content)} chars)')
    return jsonify({
        'ok': True,
        'id': name,
        'size': len(content),
        'hot_reload': meta['hot_reload'],
        'message': 'Saved. Changes take effect on the next conversation.' if meta['hot_reload']
                   else 'Saved. Agent reads this file fresh each session.',
    })
