"""
services/plugins.py — Plugin auto-discovery and loading.

Scans PLUGIN_DIR for directories containing plugin.json manifests.
For each installed plugin:
  - Registers Flask blueprints (routes)
  - Copies canvas pages to runtime dir
  - Serves face scripts and CSS as static files
  - Builds an in-memory registry for the API

Plugins are installed by copying a plugin directory into PLUGIN_DIR.
Uninstalled by removing it. No Docker rebuild needed — just restart.
"""

import json
import logging
import importlib.util
import shutil
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Where installed plugins live (volume-mounted per client)
PLUGIN_DIR = Path("/app/plugins")

# Where the plugin catalog lives (read-only, shared across clients)
PLUGIN_CATALOG_DIR = Path("/app/plugin-catalog")

# Runtime registry: plugin_id → manifest dict + status
_registry: Dict[str, dict] = {}


def get_installed_plugins() -> List[dict]:
    """Return list of installed plugin manifests with status."""
    return list(_registry.values())


def get_plugin(plugin_id: str) -> Optional[dict]:
    """Get a single installed plugin by ID."""
    return _registry.get(plugin_id)


def get_available_plugins() -> List[dict]:
    """Return plugins available in the catalog but not yet installed."""
    installed_ids = set(_registry.keys())
    available = []
    if PLUGIN_CATALOG_DIR.is_dir():
        for d in sorted(PLUGIN_CATALOG_DIR.iterdir()):
            manifest_path = d / "plugin.json"
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text())
                    pid = manifest.get("id", d.name)
                    if pid not in installed_ids:
                        manifest["_catalog_path"] = str(d)
                        manifest["_status"] = "available"
                        available.append(manifest)
                except Exception as e:
                    logger.warning(f"Failed to read catalog plugin {d.name}: {e}")
    return available


def install_plugin(plugin_id: str) -> Optional[dict]:
    """Install a plugin from the catalog by copying its directory."""
    catalog_dir = PLUGIN_CATALOG_DIR / plugin_id
    if not (catalog_dir / "plugin.json").is_file():
        return None

    dest = PLUGIN_DIR / plugin_id
    if dest.exists():
        return None  # Already installed

    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(catalog_dir), str(dest))
    logger.info(f"Plugin installed: {plugin_id} → {dest}")

    # Load it into the registry (routes won't be active until restart)
    manifest = json.loads((dest / "plugin.json").read_text())
    manifest["_status"] = "installed_pending_restart"
    manifest["_path"] = str(dest)
    _registry[plugin_id] = manifest
    return manifest


def uninstall_plugin(plugin_id: str) -> bool:
    """Uninstall a plugin by removing its directory."""
    dest = PLUGIN_DIR / plugin_id
    if not dest.exists():
        return False
    shutil.rmtree(str(dest))
    _registry.pop(plugin_id, None)
    logger.info(f"Plugin uninstalled: {plugin_id}")
    return True


def load_plugins(app):
    """
    Called at Flask startup. Scans PLUGIN_DIR and loads each plugin:
      - Registers Flask blueprints
      - Copies canvas pages to runtime
      - Serves plugin static assets
      - Builds the plugin registry
    """
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)

    count = 0
    for plugin_dir in sorted(PLUGIN_DIR.iterdir()):
        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.is_file():
            continue

        try:
            manifest = json.loads(manifest_path.read_text())
            plugin_id = manifest.get("id", plugin_dir.name)
            manifest["_path"] = str(plugin_dir)
            manifest["_status"] = "active"

            # ── Register Flask blueprints ──
            for route_entry in manifest.get("routes", []):
                module_rel = route_entry.get("module")
                bp_name = route_entry.get("blueprint")
                if module_rel and bp_name:
                    module_path = plugin_dir / module_rel
                    if module_path.is_file():
                        spec = importlib.util.spec_from_file_location(
                            f"plugin_{plugin_id}_{bp_name}", str(module_path)
                        )
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        bp = getattr(mod, bp_name, None)
                        if bp:
                            app.register_blueprint(bp)
                            logger.info(f"[Plugin:{plugin_id}] Registered blueprint: {bp_name}")

            # ── Copy canvas pages to runtime ──
            from services.paths import CANVAS_PAGES_DIR
            for page_entry in manifest.get("pages", []):
                page_file = page_entry.get("file")
                if page_file:
                    src = plugin_dir / page_file
                    dst = CANVAS_PAGES_DIR / Path(page_file).name
                    if src.is_file() and not dst.exists():
                        shutil.copy2(str(src), str(dst))
                        logger.info(f"[Plugin:{plugin_id}] Copied page: {Path(page_file).name}")

            # ── Serve face scripts + CSS as static assets under /plugins/<id>/ ──
            # Flask static_folder for the plugin
            faces = manifest.get("faces", [])
            if faces:
                from flask import Blueprint
                static_bp = Blueprint(
                    f"plugin_static_{plugin_id}",
                    plugin_id,
                    static_folder=str(plugin_dir),
                    static_url_path=f"/plugins/{plugin_id}"
                )
                app.register_blueprint(static_bp)
                logger.info(f"[Plugin:{plugin_id}] Static assets at /plugins/{plugin_id}/")

            # ── Copy example profiles (only if they don't exist yet) ──
            profiles_dir = Path("/app/runtime/profiles")
            if profiles_dir.is_dir():
                for profile_rel in manifest.get("profiles", []):
                    src = plugin_dir / profile_rel
                    dst = profiles_dir / Path(profile_rel).name
                    if src.is_file() and not dst.exists():
                        shutil.copy2(str(src), str(dst))
                        logger.info(f"[Plugin:{plugin_id}] Copied profile: {Path(profile_rel).name}")

            _registry[plugin_id] = manifest
            count += 1
            logger.info(f"[Plugin:{plugin_id}] Loaded successfully (v{manifest.get('version', '?')})")

        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_dir.name}: {e}")

    logger.info(f"Plugin system: {count} plugin(s) loaded from {PLUGIN_DIR}")
