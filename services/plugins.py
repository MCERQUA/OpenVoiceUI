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

Plugin sources (checked in order):
  1. Local catalog at /app/plugin-catalog (volume mount, used by multi-tenant hosts)
  2. Remote catalog at GitHub (MCERQUA/openvoiceui-plugins registry.json)
     Downloaded on-demand when a user clicks "Install" in the admin panel.

Lifecycle hooks: plugins with "lifecycle" in their manifest can trigger
host-side operations (container provisioning) during install/uninstall.
"""

import json
import logging
import importlib.util
import os
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Where installed plugins live (volume-mounted per client)
PLUGIN_DIR = Path("/app/plugins")

# Where the plugin catalog lives (read-only, shared across clients)
PLUGIN_CATALOG_DIR = Path("/app/plugin-catalog")

# Remote plugin registry — community plugins on GitHub
REMOTE_REGISTRY_URL = os.getenv(
    "PLUGIN_REGISTRY_URL",
    "https://raw.githubusercontent.com/MCERQUA/openvoiceui-plugins/main/registry.json",
)
REMOTE_REPO_URL = os.getenv(
    "PLUGIN_REPO_URL",
    "https://github.com/MCERQUA/openvoiceui-plugins",
)
REMOTE_FETCH_TIMEOUT = 30

# Host provisioning service for plugins that need containers
PROVISION_PORT = os.getenv("PROVISION_SERVICE_PORT", "5200")
PROVISION_TIMEOUT = 120

# Cache for remote registry (avoid re-fetching every request)
_remote_registry_cache: Optional[List[dict]] = None
_remote_registry_age: float = 0

# ─── ovui-bridge plasmoid install (Cycle 6 Phase 0) ─────────────────────
# When a plugin ships a plasmoid/ subdir, we POST it to ovui-bridge so KDE
# Plasma picks it up as a real desktop widget. Bridge + bun live on the
# jambot-ubuntu-os network for the ubuntu-os tenant; env override lets
# other deployments point elsewhere. Missing env → skip silently; plugin
# loads fine without the plasmoid.
OVUI_BRIDGE_URL = os.getenv("OVUI_BRIDGE_URL", "http://webtop-ubuntu-os:8090")
OVUI_BRIDGE_AUTH_TOKEN = os.getenv("OVUI_BRIDGE_AUTH_TOKEN", "").strip()
OVUI_BRIDGE_TIMEOUT = int(os.getenv("OVUI_BRIDGE_TIMEOUT", "20"))


def _zip_plasmoid_dir(plasmoid_dir: Path) -> BytesIO:
    """Zip the plasmoid/ subtree in-memory. Preserves the directory structure
    relative to plasmoid_dir itself (metadata.desktop, contents/, icons/).
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(plasmoid_dir):
            for f in files:
                full = Path(root) / f
                arc = str(full.relative_to(plasmoid_dir))
                zf.write(full, arc)
    buf.seek(0)
    return buf


def _install_plasmoid_via_bridge(plugin_id: str, plasmoid_dir: Path) -> None:
    """Fire-and-forget POST to ovui-bridge /api/install-plasmoid.

    Never fails plugin load — logs warning on any error (unreachable bridge,
    auth failure, extract error). The plugin's server routes still register
    and the canvas page still loads; just the desktop widget won't appear.
    """
    if not OVUI_BRIDGE_AUTH_TOKEN:
        logger.info(
            f"[Plugin:{plugin_id}] plasmoid/ dir present but "
            f"OVUI_BRIDGE_AUTH_TOKEN not set - skipping bridge install"
        )
        return
    try:
        zip_buf = _zip_plasmoid_dir(plasmoid_dir)
        resp = requests.post(
            f"{OVUI_BRIDGE_URL}/api/install-plasmoid",
            headers={"Authorization": f"Bearer {OVUI_BRIDGE_AUTH_TOKEN}"},
            files={"plasmoid": ("plasmoid.zip", zip_buf, "application/zip")},
            data={"plugin_id": plugin_id},
            timeout=OVUI_BRIDGE_TIMEOUT,
        )
        if resp.ok:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            logger.info(
                f"[Plugin:{plugin_id}] plasmoid installed via bridge: "
                f"{body.get('installed_at', OVUI_BRIDGE_URL)}"
            )
        else:
            logger.warning(
                f"[Plugin:{plugin_id}] bridge install-plasmoid HTTP {resp.status_code}: "
                f"{resp.text[:300]}"
            )
    except requests.ConnectionError as e:
        logger.warning(
            f"[Plugin:{plugin_id}] bridge unreachable at {OVUI_BRIDGE_URL}: {e}"
        )
    except Exception as e:  # noqa: BLE001 - never fail plugin load
        logger.warning(
            f"[Plugin:{plugin_id}] plasmoid install unexpected error: {e}"
        )


def _get_provision_url() -> str:
    """Get the provisioning service URL, auto-detecting the Docker gateway IP."""
    host = os.getenv("PROVISION_SERVICE_HOST", "")
    if host:
        return f"http://{host}:{PROVISION_PORT}"
    # Auto-detect: read default gateway from /proc/net/route
    try:
        with open("/proc/net/route") as f:
            for line in f:
                fields = line.strip().split()
                if fields[1] == "00000000":  # default route
                    # /proc/net/route stores hex in little-endian on x86
                    h = fields[2]
                    ip = f"{int(h[6:8],16)}.{int(h[4:6],16)}.{int(h[2:4],16)}.{int(h[0:2],16)}"
                    return f"http://{ip}:{PROVISION_PORT}"
    except Exception:
        pass
    return f"http://172.17.0.1:{PROVISION_PORT}"

# Runtime registry: plugin_id → manifest dict + status
_registry: Dict[str, dict] = {}


def get_installed_plugins() -> List[dict]:
    """Return list of installed plugin manifests with status."""
    return list(_registry.values())


def get_plugin(plugin_id: str) -> Optional[dict]:
    """Get a single installed plugin by ID."""
    return _registry.get(plugin_id)


def get_available_plugins() -> List[dict]:
    """Return plugins available in the catalog but not yet installed.

    Checks local catalog first, then merges in remote plugins (from GitHub)
    that aren't already listed locally. This ensures standalone/Pinokio
    installs see community plugins even without a mounted plugin-catalog volume.
    """
    installed_ids = set(_registry.keys())
    available = []
    local_ids = set()

    # 1. Local catalog (volume-mounted, used by multi-tenant hosts)
    if PLUGIN_CATALOG_DIR.is_dir():
        for d in sorted(PLUGIN_CATALOG_DIR.iterdir()):
            manifest_path = d / "plugin.json"
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text())
                    pid = manifest.get("id", d.name)
                    local_ids.add(pid)
                    if pid not in installed_ids:
                        manifest["_catalog_path"] = str(d)
                        manifest["_source"] = "local"
                        manifest["_status"] = "available"
                        available.append(manifest)
                except Exception as e:
                    logger.warning(f"Failed to read catalog plugin {d.name}: {e}")

    # 2. Remote registry (GitHub) — only plugins not in local catalog
    remote = _fetch_remote_registry()
    for entry in remote:
        pid = entry.get("id", "")
        if pid and pid not in installed_ids and pid not in local_ids:
            entry["_source"] = "remote"
            entry["_status"] = "available"
            available.append(entry)

    return available


def _fetch_remote_registry() -> List[dict]:
    """Fetch the remote plugin registry from GitHub. Cached for 5 minutes."""
    global _remote_registry_cache, _remote_registry_age
    import time

    now = time.time()
    if _remote_registry_cache is not None and (now - _remote_registry_age) < 300:
        return _remote_registry_cache

    try:
        resp = requests.get(REMOTE_REGISTRY_URL, timeout=REMOTE_FETCH_TIMEOUT)
        if resp.ok:
            data = resp.json()
            plugins = data.get("plugins", [])
            _remote_registry_cache = plugins
            _remote_registry_age = now
            logger.info(f"Remote plugin registry: {len(plugins)} plugin(s) fetched")
            return plugins
        else:
            logger.warning(f"Remote registry returned HTTP {resp.status_code}")
    except requests.ConnectionError:
        logger.debug("Remote plugin registry unreachable (offline mode)")
    except Exception as e:
        logger.warning(f"Failed to fetch remote plugin registry: {e}")

    return _remote_registry_cache or []


def _download_remote_plugin(plugin_id: str) -> Optional[Path]:
    """Download a plugin from the remote GitHub repo into a temp directory.

    Uses the GitHub zipball API to download just the plugin subdirectory.
    Returns the path to the extracted plugin directory, or None on failure.
    """
    # Download the repo archive for the main branch
    zip_url = f"{REMOTE_REPO_URL}/archive/refs/heads/main.zip"
    try:
        logger.info(f"Downloading plugin '{plugin_id}' from {REMOTE_REPO_URL}")
        resp = requests.get(zip_url, timeout=REMOTE_FETCH_TIMEOUT, stream=True)
        if not resp.ok:
            logger.error(f"Failed to download repo archive: HTTP {resp.status_code}")
            return None

        # Extract the specific plugin directory from the zip
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            # Zip contains a top-level dir like "openvoiceui-plugins-main/"
            prefix = None
            for name in zf.namelist():
                parts = name.split("/")
                if len(parts) >= 2 and parts[1] == plugin_id:
                    if prefix is None:
                        prefix = parts[0]
                    break

            if prefix is None:
                logger.error(f"Plugin '{plugin_id}' not found in remote repo archive")
                return None

            # Extract plugin files to a temp directory
            tmp_dir = Path(tempfile.mkdtemp(prefix=f"plugin_{plugin_id}_"))
            plugin_prefix = f"{prefix}/{plugin_id}/"
            for member in zf.namelist():
                if member.startswith(plugin_prefix) and not member.endswith("/"):
                    # Strip the repo prefix to get the relative path within the plugin
                    rel_path = member[len(plugin_prefix):]
                    dest_file = tmp_dir / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    dest_file.write_bytes(zf.read(member))

            # Verify it has a plugin.json
            if not (tmp_dir / "plugin.json").is_file():
                logger.error(f"Downloaded plugin '{plugin_id}' has no plugin.json")
                shutil.rmtree(str(tmp_dir))
                return None

            logger.info(f"Plugin '{plugin_id}' downloaded to {tmp_dir}")
            return tmp_dir

    except Exception as e:
        logger.error(f"Failed to download remote plugin '{plugin_id}': {e}")
        return None


# ── Lifecycle hooks ─────────────────────────────────────────────────────


def _get_username() -> str:
    """Derive the current username from env vars available in the container."""
    # Explicit override
    username = os.getenv("JAMBOT_USERNAME", "")
    if username:
        return username
    # CLIENT_NAME is set in every client's compose .env (e.g. "Test-dev", "Nick")
    client_name = os.getenv("CLIENT_NAME", "")
    if client_name:
        return client_name.lower().replace(" ", "-")
    # Fallback: try container name pattern (openvoiceui-<user>)
    import socket
    hostname = socket.gethostname()
    if hostname.startswith("openvoiceui-"):
        return hostname[len("openvoiceui-"):]
    return ""


def _run_lifecycle_hook(manifest: dict, hook_name: str, config: dict = None) -> dict:
    """
    Run a plugin lifecycle hook (post_install or pre_uninstall).
    Calls the host provisioning service if the plugin requires a container.
    Returns {"ok": True} on success or {"ok": False, "error": "..."} on failure.
    """
    lifecycle = manifest.get("lifecycle", {})
    hook = lifecycle.get(hook_name, {})
    if not hook:
        return {"ok": True}

    if hook_name == "post_install" and hook.get("requires_container"):
        plugin_type = hook.get("provision", "")
        if not plugin_type:
            return {"ok": True}
        username = _get_username()
        if not username:
            logger.warning("Cannot provision: unable to determine username")
            return {"ok": False, "error": "Cannot determine username for provisioning"}
        try:
            logger.info(f"Provisioning container: {plugin_type} for {username} (config: {bool(config)})")
            resp = requests.post(
                f"{_get_provision_url()}/provision/{plugin_type}/{username}",
                json=config if config else None,
                headers={"Content-Type": "application/json"} if config else {},
                timeout=PROVISION_TIMEOUT,
            )
            result = resp.json()
            if resp.ok and result.get("ok"):
                logger.info(f"Provisioned: {result}")
                return {"ok": True, "container": result.get("container"), "status": result.get("status")}
            else:
                error = result.get("error", f"HTTP {resp.status_code}")
                logger.error(f"Provisioning failed: {error}")
                return {"ok": False, "error": error}
        except requests.ConnectionError:
            logger.warning("Provisioning service not reachable (port 5200). Container must be created manually.")
            return {"ok": True, "warning": "Provisioning service not available. Create hermes container manually."}
        except Exception as e:
            logger.error(f"Provisioning error: {e}")
            return {"ok": False, "error": str(e)}

    elif hook_name == "pre_uninstall" and hook.get("deprovision"):
        plugin_type = hook["deprovision"]
        username = _get_username()
        if not username:
            return {"ok": True}
        try:
            logger.info(f"Deprovisioning container: {plugin_type} for {username}")
            resp = requests.post(
                f"{_get_provision_url()}/deprovision/{plugin_type}/{username}",
                timeout=60,
            )
            logger.info(f"Deprovision result: {resp.json()}")
        except Exception as e:
            logger.warning(f"Deprovision error (non-blocking): {e}")
        return {"ok": True}

    return {"ok": True}


# ── Install / Uninstall ────────────────────────────────────────────────


def install_plugin(plugin_id: str, config: dict = None) -> Optional[dict]:
    """Install a plugin from the local catalog or remote repo.

    Checks local catalog first. If not found, downloads from the remote
    GitHub repository. This allows standalone installs (Pinokio, Docker)
    to install community plugins without a mounted plugin-catalog volume.

    Args:
        config: Optional configuration dict (API keys, provider selection)
                passed through to the provisioning service for gateway plugins.
    """
    dest = PLUGIN_DIR / plugin_id
    if dest.exists():
        return None  # Already installed

    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)

    # Try local catalog first
    catalog_dir = PLUGIN_CATALOG_DIR / plugin_id
    remote_tmp = None
    if (catalog_dir / "plugin.json").is_file():
        shutil.copytree(str(catalog_dir), str(dest))
        logger.info(f"Plugin installed from local catalog: {plugin_id} → {dest}")
    else:
        # Fallback: download from remote repo
        remote_tmp = _download_remote_plugin(plugin_id)
        if remote_tmp is None:
            return None
        shutil.copytree(str(remote_tmp), str(dest))
        shutil.rmtree(str(remote_tmp))
        logger.info(f"Plugin installed from remote repo: {plugin_id} → {dest}")

    # Load manifest
    manifest = json.loads((dest / "plugin.json").read_text())

    # Run post_install lifecycle hook (container provisioning, etc.)
    hook_result = _run_lifecycle_hook(manifest, "post_install", config=config)
    if not hook_result.get("ok") and "error" in hook_result:
        logger.error(f"Post-install hook failed for {plugin_id}, rolling back")
        shutil.rmtree(str(dest))
        return {"_error": hook_result["error"]}

    manifest["_status"] = "installed_pending_restart"
    manifest["_path"] = str(dest)
    if hook_result.get("container"):
        manifest["_container"] = hook_result["container"]
        manifest["_container_status"] = hook_result.get("status", "unknown")
    if hook_result.get("warning"):
        manifest["_warning"] = hook_result["warning"]

    # Deploy lore/knowledge files at install time (same logic as load_plugins)
    lore = manifest.get("lore", {})
    if lore:
        workspace_dir = Path("/app/runtime/workspace/Agent")
        if workspace_dir.is_dir():
            lore_dest = workspace_dir / plugin_id
            lore_count = 0
            for section_key in ("transcripts", "characters", "memories", "files"):
                for file_rel in lore.get(section_key, []):
                    src = dest / file_rel
                    rel_from_lore = file_rel[5:] if file_rel.startswith("lore/") else file_rel
                    dst = lore_dest / rel_from_lore
                    if src.is_file() and not dst.exists():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src), str(dst))
                        lore_count += 1
            if lore_count > 0:
                logger.info(f"Deployed {lore_count} lore files to workspace/{plugin_id}/")

    _registry[plugin_id] = manifest
    return manifest


def uninstall_plugin(plugin_id: str) -> bool:
    """Uninstall a plugin by removing its directory."""
    dest = PLUGIN_DIR / plugin_id
    if not dest.exists():
        return False

    # Run pre_uninstall lifecycle hook (container deprovisioning)
    manifest_path = dest / "plugin.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
            _run_lifecycle_hook(manifest, "pre_uninstall")
        except Exception as e:
            logger.warning(f"Pre-uninstall hook error for {plugin_id}: {e}")

    shutil.rmtree(str(dest))
    _registry.pop(plugin_id, None)
    logger.info(f"Plugin uninstalled: {plugin_id}")
    return True


def get_container_status(plugin_id: str) -> Optional[dict]:
    """Check container status for a plugin that requires infrastructure."""
    plugin = _registry.get(plugin_id)
    if not plugin:
        return None
    lifecycle = plugin.get("lifecycle", {})
    post_install = lifecycle.get("post_install", {})
    if not post_install.get("requires_container"):
        return None
    plugin_type = post_install.get("provision", "")
    username = _get_username()
    if not plugin_type or not username:
        return None
    try:
        resp = requests.get(f"{_get_provision_url()}/status/{plugin_type}/{username}", timeout=10)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return {"status": "unknown", "error": "Provisioning service not reachable"}


# ── Plugin loading (Flask startup) ─────────────────────────────────────


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
                        # Register in manifest so icon meta tag gets extracted
                        try:
                            from routes.canvas import add_page_to_manifest
                            page_name = Path(page_file).name
                            title = page_entry.get("title", Path(page_file).stem.replace('-', ' ').title())
                            content = dst.read_text(errors='ignore')[:2048] if dst.exists() else ''
                            add_page_to_manifest(page_name, title, content=content)
                            logger.info(f"[Plugin:{plugin_id}] Registered {page_name} in manifest")
                        except Exception as e:
                            logger.warning(f"[Plugin:{plugin_id}] Manifest registration failed: {e}")

            # ── Serve face scripts + CSS as static assets under /plugins/<id>/ ──
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

            # ── Deploy lore/knowledge files to openclaw workspace ──
            # Plugins can ship agent knowledge (transcripts, character profiles,
            # memories, world-building) that gets copied into the openclaw workspace
            # so agents can read them. Deployed to workspace/<plugin_id>/ to avoid
            # collisions between plugins. Uses cp -n semantics (no overwrite).
            #
            # The workspace mount may be read-only from OVU's perspective, so we
            # try multiple paths: writable runtime dir first, then read-only mount.
            # For JamBot: /app/runtime/workspace/Agent is :ro but the plugin dir
            # itself at /app/plugins/<id>/lore/ is always readable by the agent
            # via the shared skills mount or direct workspace symlink.
            lore = manifest.get("lore", {})
            if lore:
                # Try writable workspace paths in order of preference
                workspace_candidates = [
                    Path("/app/runtime/workspace/Agent"),  # Direct workspace mount
                    Path("/app/runtime/openclaw-workspace"),  # Alt writable mount
                ]
                workspace_dir = None
                for candidate in workspace_candidates:
                    if candidate.is_dir():
                        # Test if writable
                        test_file = candidate / ".plugin-write-test"
                        try:
                            test_file.touch()
                            test_file.unlink()
                            workspace_dir = candidate
                            break
                        except (OSError, PermissionError):
                            continue

                if workspace_dir:
                    lore_dest = workspace_dir / plugin_id
                    lore_count = 0
                    for section_key in ("transcripts", "characters", "memories", "files"):
                        for file_rel in lore.get(section_key, []):
                            src = plugin_dir / file_rel
                            rel_from_lore = file_rel[5:] if file_rel.startswith("lore/") else file_rel
                            dst = lore_dest / rel_from_lore
                            if src.is_file() and not dst.exists():
                                dst.parent.mkdir(parents=True, exist_ok=True)
                                try:
                                    shutil.copy2(str(src), str(dst))
                                    lore_count += 1
                                except (OSError, PermissionError) as e:
                                    logger.warning(f"[Plugin:{plugin_id}] Cannot copy lore file {file_rel}: {e}")
                    if lore_count > 0:
                        logger.info(f"[Plugin:{plugin_id}] Deployed {lore_count} lore files to {lore_dest}")
                else:
                    logger.info(f"[Plugin:{plugin_id}] Lore available at /app/plugins/{plugin_id}/lore/ (workspace not writable)")

            # ── Install plasmoid/ subdir into KDE via ovui-bridge ──
            # Cycle 6 Phase 0: any plugin that ships a plasmoid/ subdir gets
            # its widget auto-pushed to the webtop KDE session. Never fails
            # plugin load; just logs a warning if the bridge isn't reachable.
            plasmoid_dir = plugin_dir / "plasmoid"
            if plasmoid_dir.is_dir():
                _install_plasmoid_via_bridge(plugin_id, plasmoid_dir)

            _registry[plugin_id] = manifest
            count += 1
            logger.info(f"[Plugin:{plugin_id}] Loaded successfully (v{manifest.get('version', '?')})")

        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_dir.name}: {e}")

    logger.info(f"Plugin system: {count} plugin(s) loaded from {PLUGIN_DIR}")


# ── Plugin config read/write (proxy to provisioning service) ──────────


def get_plugin_config(plugin_id: str) -> Optional[dict]:
    """Read current configuration from a gateway plugin via provisioning service."""
    plugin = _registry.get(plugin_id)
    if not plugin:
        return None
    lifecycle = plugin.get("lifecycle", {})
    post_install = lifecycle.get("post_install", {})
    if not post_install.get("requires_container"):
        return None

    plugin_type = post_install.get("provision", "")
    username = _get_username()
    if not plugin_type or not username:
        return None

    try:
        resp = requests.get(
            f"{_get_provision_url()}/config/{plugin_type}/{username}",
            timeout=10,
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to read config for {plugin_id}: {e}")
    return None


def update_plugin_config(plugin_id: str, config: dict) -> dict:
    """Update configuration for an installed gateway plugin. Restarts container."""
    plugin = _registry.get(plugin_id)
    if not plugin:
        return {"ok": False, "error": "Plugin not installed"}
    lifecycle = plugin.get("lifecycle", {})
    post_install = lifecycle.get("post_install", {})
    if not post_install.get("requires_container"):
        return {"ok": False, "error": "Plugin doesn't use containers"}

    plugin_type = post_install.get("provision", "")
    username = _get_username()
    if not plugin_type or not username:
        return {"ok": False, "error": "Cannot determine username"}

    try:
        resp = requests.put(
            f"{_get_provision_url()}/config/{plugin_type}/{username}",
            json=config,
            headers={"Content-Type": "application/json"},
            timeout=PROVISION_TIMEOUT,
        )
        return resp.json()
    except requests.ConnectionError:
        return {"ok": False, "error": "Provisioning service not reachable"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
