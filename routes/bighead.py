"""
routes/bighead.py — BigHead Avatar Character API

Endpoints:
  GET    /api/bighead/characters       — list saved characters
  POST   /api/bighead/characters       — save a new character
  PUT    /api/bighead/characters/<id>  — update a character
  GET    /api/bighead/active           — get the active character config
  PUT    /api/bighead/active           — set the active character

Storage: JSON files in RUNTIME_DIR/bighead/
"""

import json
import logging
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from services.paths import RUNTIME_DIR

logger = logging.getLogger(__name__)

bighead_bp = Blueprint("bighead", __name__)

BIGHEAD_DIR = RUNTIME_DIR / "bighead"


def _ensure_dir():
    BIGHEAD_DIR.mkdir(parents=True, exist_ok=True)


def _characters_file():
    return BIGHEAD_DIR / "characters.json"


def _active_file():
    return BIGHEAD_DIR / "active.json"


def _load_characters():
    _ensure_dir()
    f = _characters_file()
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return []
    return []


def _save_characters(chars):
    _ensure_dir()
    _characters_file().write_text(json.dumps(chars, indent=2))


# ── Endpoints ────────────────────────────────────────────────────────────────

@bighead_bp.route("/api/bighead/characters", methods=["GET"])
def list_characters():
    return jsonify(_load_characters())


@bighead_bp.route("/api/bighead/characters", methods=["POST"])
def create_character():
    data = request.get_json(silent=True)
    if not data or "BODY" not in data:
        return jsonify({"error": "Invalid character config"}), 400

    chars = _load_characters()

    # Generate an ID
    char_id = data.get("id") or f"bighead-{int(time.time())}"
    data["id"] = char_id
    data.setdefault("name", f"Character {len(chars) + 1}")
    data["created"] = time.time()

    chars.append(data)
    _save_characters(chars)

    return jsonify(data), 201


@bighead_bp.route("/api/bighead/characters/<char_id>", methods=["PUT"])
def update_character(char_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400

    chars = _load_characters()
    for i, c in enumerate(chars):
        if c.get("id") == char_id:
            chars[i] = {**c, **data, "id": char_id}
            _save_characters(chars)
            return jsonify(chars[i])

    return jsonify({"error": "Character not found"}), 404


@bighead_bp.route("/api/bighead/create-agent", methods=["POST"])
def create_agent():
    """Create openclaw agent files for a BigHead character.

    Writes SOUL.md (personality), memory files, and character identity
    into the openclaw workspace so the agent has its own persistent context.
    """
    data = request.get_json(silent=True)
    if not data or not data.get("id") or not data.get("name"):
        return jsonify({"error": "id and name required"}), 400

    agent_id = data["id"]
    name = data["name"]
    personality = data.get("personality", "")
    backstory = data.get("backstory", "")
    memories_text = data.get("memories", "")
    archetype = data.get("archetype", "custom")
    traits = data.get("traits", {})

    workspace = RUNTIME_DIR / "workspace"
    if not workspace.exists():
        # Try the openclaw workspace path
        workspace = Path("/home/node/.openclaw/workspace")
    if not workspace.exists():
        workspace = RUNTIME_DIR / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

    # Create agent directory structure
    agent_dir = workspace / "characters" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    memory_dir = agent_dir / "memory"
    memory_dir.mkdir(exist_ok=True)

    # Write SOUL.md — the character's identity file
    soul = f"""# {name}

## Identity
**Name:** {name}
**Archetype:** {archetype}
**Type:** BigHead Character

## Personality
{personality}

"""
    if backstory:
        soul += f"""## Backstory
{backstory}

"""
    if traits:
        trait_lines = ", ".join(f"{k}: {v}" for k, v in traits.items() if v and v != "None")
        soul += f"""## Appearance
{trait_lines}

"""
    soul += f"""## Rules
- Stay in character as {name} at all times
- Never break character or act like a generic assistant
- Reference your memories and quirks naturally in conversation
- Swear if it fits your personality
"""

    (agent_dir / "SOUL.md").write_text(soul)

    # Write memories as individual files
    if memories_text:
        (memory_dir / "core-memories.md").write_text(
            f"# {name}'s Core Memories\n\n{memories_text}\n"
        )

    # Write character config
    char_config = {
        "id": agent_id,
        "name": name,
        "archetype": archetype,
        "traits": traits,
        "created": time.time()
    }
    (agent_dir / "character.json").write_text(json.dumps(char_config, indent=2))

    logger.info(f"Created BigHead agent: {agent_id} at {agent_dir}")
    return jsonify({"ok": True, "agent_dir": str(agent_dir)}), 201


@bighead_bp.route("/api/bighead/active", methods=["GET"])
def get_active():
    _ensure_dir()
    f = _active_file()
    if f.exists():
        try:
            return jsonify(json.loads(f.read_text()))
        except Exception:
            pass
    return jsonify(None)


@bighead_bp.route("/api/bighead/active", methods=["PUT"])
def set_active():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400

    _ensure_dir()
    _active_file().write_text(json.dumps(data, indent=2))
    return jsonify({"ok": True})
