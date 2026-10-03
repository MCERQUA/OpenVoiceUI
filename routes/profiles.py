"""
routes/profiles.py — Agent Profile API Blueprint (P5-T4)

Endpoints:
  GET    /api/profiles            — list all profiles (summary)
  GET    /api/profiles/active     — get the currently active profile (full)
  GET    /api/profiles/<id>       — get a single profile by id (full)
  POST   /api/profiles            — create a new profile
  POST   /api/profiles/activate   — activate a profile by id
  PUT    /api/profiles/<id>       — partial-update an existing profile
  DELETE /api/profiles/<id>       — delete a profile (default profile protected)

ADR-002: Profile storage as JSON files.
"""

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from profiles.manager import get_profile_manager, Profile

logger = logging.getLogger(__name__)

profiles_bp = Blueprint("profiles", __name__)

_DEFAULT_PROFILE = "default"
from services.paths import ACTIVE_PROFILE_FILE as _ACTIVE_PROFILE_FILE


def _load_active_profile_id() -> str:
    """Read persisted active profile from disk, fall back to default."""
    try:
        if _ACTIVE_PROFILE_FILE.exists():
            saved = _ACTIVE_PROFILE_FILE.read_text().strip()
            if saved:
                return saved
    except Exception as exc:
        logger.warning("Could not read .active-profile: %s", exc)
    return _DEFAULT_PROFILE


def _save_active_profile_id(profile_id: str) -> None:
    """Persist active profile to disk so it survives service restarts."""
    try:
        _ACTIVE_PROFILE_FILE.write_text(profile_id)
    except Exception as exc:
        logger.warning("Could not save .active-profile: %s", exc)


# Load persisted selection on startup (survives service restarts)
_active_profile_id = _load_active_profile_id()


# ---------------------------------------------------------------------------
# GET /api/profiles
# ---------------------------------------------------------------------------

@profiles_bp.route("/api/profiles", methods=["GET"])
def list_profiles():
    """Return summary list of all profiles plus the active profile id."""
    manager = get_profile_manager()
    return jsonify({
        "profiles": manager.list_profiles(),
        "active": _active_profile_id,
    })


# ---------------------------------------------------------------------------
# GET /api/profiles/active
# ---------------------------------------------------------------------------

@profiles_bp.route("/api/profiles/active", methods=["GET"])
def get_active_profile():
    """Return the full currently active profile object."""
    manager = get_profile_manager()
    profile = manager.get_profile(_active_profile_id)
    if not profile:
        return jsonify({"error": f"Active profile '{_active_profile_id}' not found"}), 404
    return jsonify(profile.to_dict())


# ---------------------------------------------------------------------------
# GET /api/profiles/<profile_id>
# ---------------------------------------------------------------------------

@profiles_bp.route("/api/profiles/<profile_id>", methods=["GET"])
def get_profile(profile_id):
    """Return a single profile by id."""
    manager = get_profile_manager()
    profile = manager.get_profile(profile_id)
    if not profile or profile.id != profile_id:
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
    return jsonify(profile.to_dict())


# ---------------------------------------------------------------------------
# POST /api/profiles  — create
# ---------------------------------------------------------------------------

@profiles_bp.route("/api/profiles", methods=["POST"])
def create_profile():
    """
    Create a new profile from a JSON body.

    Required fields: id, name, system_prompt, llm.provider, voice.tts_provider
    Returns 201 with the created profile on success, 400 on validation error,
    409 if a profile with the same id already exists.
    """
    manager = get_profile_manager()
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    errors = manager.validate_profile(data)
    if errors:
        return jsonify({"errors": errors}), 400

    profile_id = data["id"]
    if manager.profile_exists(profile_id):
        return jsonify({"error": f"Profile '{profile_id}' already exists. Use PUT to update."}), 409

    profile = Profile.from_dict(data)
    if not manager.save_profile(profile):
        return jsonify({"error": "Failed to save profile"}), 500

    logger.info("Created profile: %s", profile_id)
    return jsonify(profile.to_dict()), 201


# ---------------------------------------------------------------------------
# POST /api/profiles/activate
# ---------------------------------------------------------------------------

@profiles_bp.route("/api/profiles/activate", methods=["POST"])
def activate_profile():
    """
    Activate a profile.

    Request body: {"profile_id": "default"}
    Response:     {"ok": true, "active": "default", "profile": {...}}
    """
    global _active_profile_id

    data = request.get_json(silent=True) or {}
    profile_id = data.get("profile_id", "").strip()

    if not profile_id:
        return jsonify({"ok": False, "error": "Missing 'profile_id'"}), 400

    manager = get_profile_manager()
    profile = manager.get_profile(profile_id)
    if not profile or profile.id != profile_id:
        return jsonify({"ok": False, "error": f"Profile '{profile_id}' not found"}), 404

    _active_profile_id = profile_id
    _save_active_profile_id(profile_id)
    logger.info("Profile activated: %s", profile_id)

    return jsonify({
        "ok": True,
        "active": _active_profile_id,
        "profile": profile.to_dict(),
    })


# ---------------------------------------------------------------------------
# PUT /api/profiles/<profile_id>  — partial update
# ---------------------------------------------------------------------------

@profiles_bp.route("/api/profiles/<profile_id>", methods=["PUT"])
def update_profile(profile_id):
    """
    Partially update an existing profile.  Only the supplied fields are changed.
    Sub-objects (llm, voice, etc.) are merged one level deep.
    Returns 200 with the updated profile on success.
    """
    manager = get_profile_manager()
    if not manager.profile_exists(profile_id):
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # Prevent changing the id via update body
    data.pop("id", None)

    try:
        updated = manager.apply_partial_update(profile_id, data)
    except ValueError as exc:
        msg = f"voice.voice_id: {exc}"
        return jsonify({"error": msg, "errors": [msg]}), 400   # admin.html reads `error`
    if updated is None:
        return jsonify({"error": "Failed to update profile"}), 500

    logger.info("Updated profile: %s", profile_id)
    return jsonify(updated.to_dict())


# ---------------------------------------------------------------------------
# DELETE /api/profiles/<profile_id>
# ---------------------------------------------------------------------------

@profiles_bp.route("/api/profiles/<profile_id>", methods=["DELETE"])
def delete_profile(profile_id):
    """
    Delete a profile.  The default profile ('default') cannot be deleted.
    Returns 204 on success, 400 if protected, 404 if not found.
    """
    manager = get_profile_manager()
    if not manager.profile_exists(profile_id):
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404

    if not manager.delete_profile(profile_id):
        return jsonify({"error": f"Cannot delete profile '{profile_id}' (protected)"}), 400

    logger.info("Deleted profile: %s", profile_id)
    return "", 204
