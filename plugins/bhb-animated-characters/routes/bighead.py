"""
routes/bighead.py — BigHead Avatar Character API (bhb-animated-characters plugin)

Endpoints (registered with full paths; the blueprint is mounted without a url_prefix):
  GET    /api/bighead/characters        — list saved characters
  POST   /api/bighead/characters        — save a new character
  PUT    /api/bighead/characters/<id>   — update a character
  DELETE /api/bighead/characters/<id>   — delete a character
  POST   /api/bighead/create-agent      — write SOUL.md / memory / character.json for a character
  GET    /api/bighead/active            — get the active character config
  PUT    /api/bighead/active            — set the active character

Storage: characters.json + active.json in RUNTIME_DIR/bighead/; agent files in
RUNTIME_DIR/workspace/characters/<id>/.

No-follow I/O (review 5 of OpenVoiceUI PR #597, follow-up): /api/bighead/create-agent is
not an admin prefix, so the tenant agent's key reaches it. The old code joined the request
`id` unvalidated under the workspace (`Path('/a') / '/abs' == '/abs'`, and
`../../uploads/<planted symlink>` climbed out and followed the link), then mkdir'ed and
wrote SOUL.md, memory/core-memories.md and character.json BY NAME: three files with the
caller's text in any directory the server could write, including /app. Now:
  * every id that names a path is a slug, `[A-Za-z0-9_-]{1,64}` (400 otherwise);
  * every file and directory is opened without following a symlink, walked one component
    at a time from an fd on the anchoring root (the workspace dir, or RUNTIME_DIR for
    bighead/), and files are checked to be regular before they are read or written.
With services/safe_open (OVU >= PR #597) it uses Anchored; on an older image (this plugin
dir is a per-tenant mount, rolled separately from the image) it uses its own fd-based
no-follow helpers below.
"""

import errno
import json
import logging
import os
import re
import stat
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

try:
    from services.paths import RUNTIME_DIR
except Exception:  # pragma: no cover - fallback if import path differs
    RUNTIME_DIR = Path(os.getenv("RUNTIME_DIR", "/app/runtime"))

try:   # no-follow, beneath-root I/O (OVU >= PR #597)
    from services.safe_open import Anchored as _Anchored
except ImportError:   # an older OVU image: fall back to the fd-based helpers below
    _Anchored = None

logger = logging.getLogger(__name__)

bighead_bp = Blueprint("bighead", __name__)

# A character/agent id becomes a directory name: a slug, nothing that can climb or be absolute.
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_STATE_MAX_BYTES = 16 * 1024 * 1024
_BIGHEAD = "bighead"
_OPENCLAW_WORKSPACE = Path("/home/node/.openclaw/workspace")

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _valid_id(value) -> bool:
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


# ── no-follow fallback (an OVU image without services/safe_open) ──────────────────────

def _root_fd(root) -> int:
    """fd on the anchoring root itself; its own last component is not followed (ELOOP)."""
    return os.open(str(root), _DIR_FLAGS)


def _walk_dirs(root, parts, create: bool) -> int:
    """fd on root/parts[0]/.../parts[-1], every step O_NOFOLLOW relative to the previous fd."""
    fd = _root_fd(root)
    try:
        for name in parts:
            if not name or name in (".", "..") or "/" in name or "\x00" in name:
                raise OSError(errno.EINVAL, "bighead: invalid path component")
            try:
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _fallback_write(root, parts, data: bytes, mode=0o644) -> None:
    """Write root/<parts>: create O_EXCL (exact mode) or reopen the existing regular file
    O_NOFOLLOW|O_NONBLOCK and keep its mode; a symlink, FIFO or dir at the name is refused."""
    dfd = _walk_dirs(root, parts[:-1], create=True)
    name = parts[-1]
    try:
        created = True
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         mode, dir_fd=dfd)
        except FileExistsError:
            created = False
            fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "bighead: not a regular file")
        if created:
            try:
                os.fchmod(fd, mode)
            except OSError:
                pass
        else:
            os.ftruncate(fd, 0)
        os.set_blocking(fd, True)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def _fallback_read(root, parts, max_bytes: int) -> bytes:
    dfd = _walk_dirs(root, parts[:-1], create=False)
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            raise OSError(errno.EINVAL, "bighead: not a small regular file")
        data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OSError(errno.EFBIG, "bighead: file too large")
        return data


# ── one interface over both paths ─────────────────────────────────────────────────────

def _write_text(root, rel: str, text: str) -> None:
    if _Anchored is not None:
        _Anchored(root, rel).write_text(text, create_dirs=True)
        return
    _fallback_write(root, rel.split("/"), text.encode("utf-8"))


def _read_text(root, rel: str):
    """Text of root/rel, or None if it does not exist. A symlink / non-regular / oversized
    file raises OSError (never followed)."""
    if _Anchored is not None:
        a = _Anchored(root, rel)
        if not a.exists():
            return None
        return a.read_text(max_bytes=_STATE_MAX_BYTES)
    try:
        return _fallback_read(root, rel.split("/"), _STATE_MAX_BYTES).decode("utf-8")
    except FileNotFoundError:
        return None


def _mkdirs(root, rel: str) -> None:
    if _Anchored is not None:
        _Anchored(root, rel).mkdir(mode=0o755, parents=True, exist_ok=True)
        return
    os.close(_walk_dirs(root, rel.split("/"), create=True))


# ── bighead/ state (characters.json, active.json) ─────────────────────────────────────

def _load_json(name: str, default):
    try:
        text = _read_text(RUNTIME_DIR, f"{_BIGHEAD}/{name}")
    except (OSError, ValueError):
        return default
    if text is None:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _load_characters():
    chars = _load_json("characters.json", [])
    return chars if isinstance(chars, list) else []


def _save_characters(chars) -> bool:
    try:
        _write_text(RUNTIME_DIR, f"{_BIGHEAD}/characters.json", json.dumps(chars, indent=2))
        return True
    except OSError as e:   # a symlink / FIFO / dir at the name: refused, never followed
        logger.warning("BigHead: characters.json not written: %s", e)
        return False


def _write_failed():
    return jsonify({"error": "could not save"}), 409


# ── Endpoints ────────────────────────────────────────────────────────────────

@bighead_bp.route("/api/bighead/characters", methods=["GET"])
def list_characters():
    return jsonify(_load_characters())


@bighead_bp.route("/api/bighead/characters", methods=["POST"])
def create_character():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "BODY" not in data:
        return jsonify({"error": "Invalid character config"}), 400

    # The id is stored and later sent back to /create-agent, where it names a directory.
    char_id = data.get("id") or f"bighead-{int(time.time())}"
    if not _valid_id(char_id):
        return jsonify({"error": "invalid id (letters, digits, '-' and '_', 1-64)"}), 400

    chars = _load_characters()
    data["id"] = char_id
    data.setdefault("name", f"Character {len(chars) + 1}")
    data["created"] = time.time()

    chars.append(data)
    if not _save_characters(chars):
        return _write_failed()

    return jsonify(data), 201


# PUT/DELETE <char_id>: the id is only compared with stored entries, never used as a path.
@bighead_bp.route("/api/bighead/characters/<char_id>", methods=["PUT"])
def update_character(char_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return jsonify({"error": "No data"}), 400

    chars = _load_characters()
    for i, c in enumerate(chars):
        if isinstance(c, dict) and c.get("id") == char_id:
            chars[i] = {**c, **data, "id": char_id}
            if not _save_characters(chars):
                return _write_failed()
            return jsonify(chars[i])

    return jsonify({"error": "Character not found"}), 404


@bighead_bp.route("/api/bighead/characters/<char_id>", methods=["DELETE"])
def delete_character(char_id):
    chars = _load_characters()
    new_chars = [c for c in chars if not (isinstance(c, dict) and c.get("id") == char_id)]
    if len(new_chars) == len(chars):
        return jsonify({"error": "Character not found"}), 404
    if not _save_characters(new_chars):
        return _write_failed()
    logger.info("Deleted BigHead character: %s", char_id)
    return "", 204


def _workspace_root() -> Path:
    """The workspace dir the agent files go under (same choice as before). It is the
    anchoring root: its own last component is not followed, nothing below it is."""
    workspace = RUNTIME_DIR / "workspace"
    if workspace.is_dir():
        return workspace
    if _OPENCLAW_WORKSPACE.is_dir():
        return _OPENCLAW_WORKSPACE
    _mkdirs(RUNTIME_DIR, "workspace")
    return workspace


@bighead_bp.route("/api/bighead/create-agent", methods=["POST"])
def create_agent():
    """Create openclaw agent files for a BigHead character.

    Writes SOUL.md (personality), memory files, and character identity
    into the openclaw workspace so the agent has its own persistent context.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get("id") or not data.get("name"):
        return jsonify({"error": "id and name required"}), 400

    agent_id = data["id"]
    if not _valid_id(agent_id):
        return jsonify({"error": "invalid id (letters, digits, '-' and '_', 1-64)"}), 400
    name = data["name"]
    personality = data.get("personality", "")
    backstory = data.get("backstory", "")
    memories_text = data.get("memories", "")
    archetype = data.get("archetype", "custom")
    traits = data.get("traits", {})
    if not isinstance(traits, dict):
        traits = {}

    agent_rel = f"characters/{agent_id}"

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

    char_config = {
        "id": agent_id,
        "name": name,
        "archetype": archetype,
        "traits": traits,
        "created": time.time()
    }

    try:
        ws_root = _workspace_root()
        # Create agent directory structure
        _mkdirs(ws_root, f"{agent_rel}/memory")
        _write_text(ws_root, f"{agent_rel}/SOUL.md", soul)
        # Write memories as individual files
        if memories_text:
            _write_text(ws_root, f"{agent_rel}/memory/core-memories.md",
                        f"# {name}'s Core Memories\n\n{memories_text}\n")
        # Write character config
        _write_text(ws_root, f"{agent_rel}/character.json", json.dumps(char_config, indent=2))
    except OSError as e:
        logger.warning("BigHead create-agent %s refused: %s", agent_id, e)
        return jsonify({"error": "could not write agent files"}), 409

    agent_dir = f"{ws_root}/{agent_rel}"
    logger.info("Created BigHead agent: %s at %s", agent_id, agent_dir)
    return jsonify({"ok": True, "agent_dir": agent_dir}), 201


@bighead_bp.route("/api/bighead/active", methods=["GET"])
def get_active():
    return jsonify(_load_json("active.json", None))


@bighead_bp.route("/api/bighead/active", methods=["PUT"])
def set_active():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400

    try:
        _write_text(RUNTIME_DIR, f"{_BIGHEAD}/active.json", json.dumps(data, indent=2))
    except OSError as e:   # a symlink / FIFO / dir at the name: refused, never followed
        logger.warning("BigHead: active.json not written: %s", e)
        return _write_failed()
    return jsonify({"ok": True})
