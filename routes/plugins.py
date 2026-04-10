"""
routes/plugins.py — Plugin management API.

Endpoints:
  GET    /api/plugins              — list installed plugins
  GET    /api/plugins/available    — list catalog plugins not yet installed
  GET    /api/plugins/assets       — get script/CSS URLs for installed face plugins
  POST   /api/plugins/<id>/install — install from catalog (triggers container provisioning + auto-restart)
  DELETE /api/plugins/<id>         — uninstall a plugin (triggers container deprovisioning)
  GET    /api/plugins/<id>/container — check container status for gateway plugins
  POST   /api/plugins/restart      — restart the app to activate newly installed plugins
"""

import logging
import os
import signal
import threading
from flask import Blueprint, jsonify, request

from services.plugins import (
    get_installed_plugins,
    get_available_plugins,
    get_plugin,
    install_plugin,
    uninstall_plugin,
    get_container_status,
    get_plugin_config,
    update_plugin_config,
)

logger = logging.getLogger(__name__)

plugins_bp = Blueprint("plugins", __name__)


@plugins_bp.route("/api/plugins", methods=["GET"])
def list_installed():
    """Return all installed plugins with status."""
    plugins = get_installed_plugins()
    return jsonify({
        "plugins": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "version": p.get("version"),
                "description": p.get("description"),
                "type": p.get("type"),
                "author": p.get("author"),
                "status": p.get("_status", "active"),
                "faces": [f.get("id") for f in p.get("faces", [])],
                "pages": [pg.get("name") for pg in p.get("pages", [])],
                "install_config": p.get("install_config"),
                "has_container": bool(
                    p.get("lifecycle", {}).get("post_install", {}).get("requires_container")
                ),
            }
            for p in plugins
        ]
    })


@plugins_bp.route("/api/plugins/available", methods=["GET"])
def list_available():
    """Return plugins in the catalog that aren't installed yet."""
    plugins = get_available_plugins()
    return jsonify({
        "plugins": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "version": p.get("version"),
                "description": p.get("description"),
                "type": p.get("type"),
                "author": p.get("author"),
                "install_config": p.get("install_config"),
                "has_container": bool(
                    p.get("lifecycle", {}).get("post_install", {}).get("requires_container")
                ),
            }
            for p in plugins
        ]
    })


@plugins_bp.route("/api/plugins/assets", methods=["GET"])
def plugin_assets():
    """
    Return script and CSS URLs for installed face plugins.
    Used by index.html to dynamically inject plugin scripts.
    """
    scripts = []
    styles = []
    for p in get_installed_plugins():
        pid = p.get("id")
        for face in p.get("faces", []):
            script = face.get("script")
            if script:
                scripts.append(f"/plugins/{pid}/{script}")
            css = face.get("css")
            if css:
                styles.append(f"/plugins/{pid}/{css}")
    return jsonify({"scripts": scripts, "styles": styles})


def _schedule_restart(delay=2):
    """Schedule an app restart after a delay so the HTTP response can be sent first."""
    def _do_restart():
        import time
        time.sleep(delay)
        logger.info("Restarting app to activate plugin changes...")
        os.kill(os.getpid(), signal.SIGTERM)
    t = threading.Thread(target=_do_restart, daemon=True)
    t.start()


@plugins_bp.route("/api/plugins/restart", methods=["POST"])
def restart_app():
    """Restart the app to activate newly installed/removed plugins.
    Docker restart policy (unless-stopped) will bring the container back up."""
    logger.info("Manual restart requested via /api/plugins/restart")
    _schedule_restart(delay=1)
    return jsonify({"ok": True, "note": "Restarting in ~2 seconds..."})


@plugins_bp.route("/api/plugins/<plugin_id>/install", methods=["POST"])
def install(plugin_id):
    """Install a plugin from the catalog. Handles container provisioning if needed."""
    body = request.get_json(silent=True) or {}
    config = body.get("config")
    result = install_plugin(plugin_id, config=config)
    if result is None:
        existing = get_plugin(plugin_id)
        if existing:
            return jsonify({"error": f"Plugin '{plugin_id}' is already installed"}), 409
        return jsonify({"error": f"Plugin '{plugin_id}' not found in catalog"}), 404

    # Check if install_plugin returned an error (provisioning failed)
    if result.get("_error"):
        return jsonify({"error": result["_error"]}), 500

    # Auto-restart unless ?no_restart=1 is passed
    auto_restart = request.args.get("no_restart") != "1"

    response = {
        "ok": True,
        "plugin": result.get("name"),
        "status": result.get("_status"),
        "restarting": auto_restart,
    }
    if result.get("_container"):
        response["container"] = result["_container"]
        response["container_status"] = result.get("_container_status", "unknown")
    if result.get("_warning"):
        response["warning"] = result["_warning"]

    if auto_restart:
        response["note"] = "App restarting in ~3 seconds to activate plugin..."
        _schedule_restart(delay=3)
    else:
        response["note"] = "Restart the container to activate routes and face scripts"

    return jsonify(response), 201


@plugins_bp.route("/api/plugins/<plugin_id>", methods=["DELETE"])
def uninstall(plugin_id):
    """Uninstall a plugin. Handles container deprovisioning if needed."""
    if not uninstall_plugin(plugin_id):
        return jsonify({"error": f"Plugin '{plugin_id}' not installed"}), 404
    return jsonify({
        "ok": True,
        "note": "Restart the container to fully deactivate"
    })


@plugins_bp.route("/api/plugins/<plugin_id>/container", methods=["GET"])
def plugin_container_status(plugin_id):
    """Check container status for a plugin that requires infrastructure."""
    status = get_container_status(plugin_id)
    if status is None:
        return jsonify({"error": "Plugin not found or doesn't use containers"}), 404
    return jsonify(status)


@plugins_bp.route("/api/plugins/<plugin_id>/config", methods=["GET"])
def get_config(plugin_id):
    """Read current config from a running gateway plugin."""
    config = get_plugin_config(plugin_id)
    if config is None:
        return jsonify({"error": "Plugin not found or has no configuration"}), 404
    return jsonify(config)


@plugins_bp.route("/api/plugins/<plugin_id>/config", methods=["PUT"])
def save_config(plugin_id):
    """Update config for an installed gateway plugin. Restarts container."""
    body = request.get_json(silent=True) or {}
    config = body.get("config")
    if not config:
        return jsonify({"error": "config is required"}), 400

    result = update_plugin_config(plugin_id, config)
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Update failed")}), 500
    return jsonify(result)
