"""
routes/plugins.py — Plugin management API.

Endpoints:
  GET    /api/plugins              — list installed plugins
  GET    /api/plugins/available    — list catalog plugins not yet installed
  GET    /api/plugins/assets       — get script/CSS URLs for installed face plugins
  POST   /api/plugins/<id>/install — install from catalog
  DELETE /api/plugins/<id>         — uninstall a plugin
"""

import logging
from flask import Blueprint, jsonify

from services.plugins import (
    get_installed_plugins,
    get_available_plugins,
    get_plugin,
    install_plugin,
    uninstall_plugin,
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


@plugins_bp.route("/api/plugins/<plugin_id>/install", methods=["POST"])
def install(plugin_id):
    """Install a plugin from the catalog."""
    result = install_plugin(plugin_id)
    if result is None:
        existing = get_plugin(plugin_id)
        if existing:
            return jsonify({"error": f"Plugin '{plugin_id}' is already installed"}), 409
        return jsonify({"error": f"Plugin '{plugin_id}' not found in catalog"}), 404
    return jsonify({
        "ok": True,
        "plugin": result.get("name"),
        "status": result.get("_status"),
        "note": "Restart the container to activate routes and face scripts"
    }), 201


@plugins_bp.route("/api/plugins/<plugin_id>", methods=["DELETE"])
def uninstall(plugin_id):
    """Uninstall a plugin."""
    if not uninstall_plugin(plugin_id):
        return jsonify({"error": f"Plugin '{plugin_id}' not installed"}), 404
    return jsonify({
        "ok": True,
        "note": "Restart the container to fully deactivate"
    })
