---
sidebar_position: 1
title: Plugin System
---

# Plugin System

OpenVoiceUI has a plugin system that lets you add new AI faces, canvas pages, and server routes without modifying core application code. Plugins are directories with a `plugin.json` manifest that the server auto-discovers at startup. No Docker rebuild is required to add or remove plugins -- just drop the directory in place and restart the container.

## Plugin Types

There are three types of plugins, each extending a different part of the system:

### Face Plugins

Face plugins add new animated character faces to the voice UI. A face is the visual representation that appears when the AI is speaking -- it can be a simple animated SVG, a complex WebGL character, or anything that responds to audio and mood signals.

Face plugins provide:
- A JavaScript file that extends the `BaseFace` class (or implements the face interface)
- Optional CSS for styling
- Mood support (e.g., neutral, happy, sad, angry, thinking, surprised, listening)
- Optional features like lip-sync, blink animation, audio reactivity, and customization

The server dynamically injects face plugin scripts and stylesheets into the main page at load time via the `/api/plugins/assets` endpoint.

### Page Plugins

Page plugins bundle canvas pages with the plugin. When the plugin is installed, its HTML pages are copied into the runtime `canvas-pages/` directory and become available through the standard canvas system (manifest, iframe display, voice navigation).

### Gateway Plugins

Gateway plugins provide Flask blueprints that register new server-side API routes. This allows plugins to add backend functionality -- custom API endpoints, data processing, integrations with external services -- that canvas pages or the face can call.

## Plugin Manifest (`plugin.json`)

Every plugin must have a `plugin.json` file in its root directory. This manifest describes the plugin, declares its type, and lists all its components.

### Full Example

Below is the manifest from the BHB Animated Characters plugin, which demonstrates all available fields:

```json
{
  "id": "bhb-animated-characters",
  "name": "BHB Animated Characters",
  "version": "1.0.0",
  "description": "Animated BigHead Billionaires character avatars with lip-sync, mood expressions, character builder, and show lore",
  "author": "bhaleyart",
  "type": "face",
  "license": "MIT",
  "requires": "openvoiceui >= 1.0",
  "repository": "https://github.com/MCERQUA/openvoiceui-plugins",

  "faces": [{
    "id": "bighead",
    "name": "BigHead Avatar",
    "script": "faces/BigHeadFace.js",
    "css": "faces/bighead.css",
    "preview": "faces/previews/bighead.svg",
    "moods": ["neutral", "happy", "sad", "angry", "thinking", "surprised", "listening"],
    "features": ["lip-sync", "mood-eyes", "blink", "customizable", "audio-reactive"],
    "configurable": true,
    "config_page": "bighead-builder.html"
  }],

  "pages": [{
    "file": "pages/bighead-builder.html",
    "name": "Character Builder",
    "icon": "..."
  }],

  "routes": [{
    "module": "routes/bighead.py",
    "blueprint": "bighead_bp",
    "prefix": "/api/bighead"
  }],

  "profiles": [
    "profiles/kyle-valhalla.json",
    "profiles/bryce-bighead.json"
  ]
}
```

### Manifest Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique plugin identifier (lowercase, hyphens). Used as directory name and API key. |
| `name` | Yes | Human-readable display name. |
| `version` | Yes | Semver version string. |
| `description` | Yes | Short description of what the plugin does. |
| `author` | No | Plugin author name or handle. |
| `type` | No | Primary plugin type: `"face"`, `"page"`, or `"gateway"`. Informational -- the actual behavior is determined by which arrays are present. |
| `license` | No | License identifier (e.g., `"MIT"`). |
| `requires` | No | Minimum OpenVoiceUI version requirement. |
| `repository` | No | URL to the plugin's source repository. |
| `faces` | No | Array of face definitions (see below). |
| `pages` | No | Array of canvas page definitions (see below). |
| `routes` | No | Array of Flask blueprint definitions (see below). |
| `profiles` | No | Array of profile JSON file paths to copy into the runtime profiles directory. |

### Face Definition

Each entry in the `faces` array:

| Field | Description |
|-------|-------------|
| `id` | Unique face identifier within the plugin. |
| `name` | Display name for the face selector. |
| `script` | Path to the JavaScript file (relative to plugin root). Served at `/plugins/<plugin-id>/<script>`. |
| `css` | Path to the CSS file (relative to plugin root). Served at `/plugins/<plugin-id>/<css>`. |
| `preview` | Path to a preview image for the face selector. |
| `moods` | Array of supported mood strings. |
| `features` | Array of supported feature strings (lip-sync, blink, audio-reactive, etc.). |
| `configurable` | Boolean. If `true`, the face has a configuration/builder page. |
| `config_page` | Filename of the canvas page used for configuration. |

### Page Definition

Each entry in the `pages` array:

| Field | Description |
|-------|-------------|
| `file` | Path to the HTML file (relative to plugin root). Copied to `canvas-pages/` on load. |
| `name` | Display name for the page. |
| `icon` | Icon string for menus. |

### Route Definition

Each entry in the `routes` array:

| Field | Description |
|-------|-------------|
| `module` | Path to the Python module (relative to plugin root). |
| `blueprint` | Name of the Flask `Blueprint` variable in the module. |
| `prefix` | URL prefix for the blueprint's routes. |

## Plugin Lifecycle

### Discovery

At Flask startup, the `load_plugins()` function scans the plugin directory (`/app/plugins`) for subdirectories containing a `plugin.json` manifest. Each valid manifest is processed in alphabetical order.

### Registration

For each discovered plugin, the system:

1. **Registers Flask blueprints.** Each entry in `routes` is dynamically loaded via `importlib`. The Python module is loaded from the plugin directory, and the named Blueprint variable is registered with the Flask app.

2. **Copies canvas pages.** Each entry in `pages` has its HTML file copied from the plugin directory to the runtime `canvas-pages/` directory. Files are only copied if they do not already exist at the destination (existing pages are not overwritten).

3. **Serves static assets.** If the plugin defines any `faces`, a static file Blueprint is registered to serve the plugin's entire directory at `/plugins/<plugin-id>/`. This makes face scripts, CSS, preview images, and any other assets accessible via URL.

4. **Copies profile files.** Each entry in `profiles` is copied to the runtime profiles directory if it does not already exist.

5. **Updates the registry.** The plugin manifest is stored in an in-memory registry with `_status: "active"`.

### Runtime

Once loaded, the plugin's components are live:

- Face scripts and CSS are injected into the main page via `/api/plugins/assets`.
- Canvas pages appear in the manifest and can be opened via `[CANVAS:page-id]`.
- Blueprint routes handle HTTP requests at their registered paths.
- The plugin shows up in `GET /api/plugins` with status `"active"`.

### Install from Catalog

Plugins can be installed at runtime from a read-only catalog directory (`/app/plugin-catalog`). The catalog contains plugin directories that are not yet installed. Installing copies the directory from the catalog to the plugins directory. Routes and face scripts require a container restart to become fully active; the plugin is registered with status `"installed_pending_restart"` until then.

### Uninstall

Uninstalling removes the plugin directory from `/app/plugins` and clears it from the in-memory registry. A container restart fully deactivates any registered blueprints.

## API Endpoints

### List Installed Plugins

```
GET /api/plugins
```

Returns all installed plugins with their status, face IDs, and page names.

**Response:**

```json
{
  "plugins": [
    {
      "id": "bhb-animated-characters",
      "name": "BHB Animated Characters",
      "version": "1.0.0",
      "description": "Animated BigHead Billionaires character avatars...",
      "type": "face",
      "author": "bhaleyart",
      "status": "active",
      "faces": ["bighead"],
      "pages": ["Character Builder"]
    }
  ]
}
```

### List Available Plugins

```
GET /api/plugins/available
```

Returns plugins in the catalog that are not yet installed. Same response shape as installed, without `status`, `faces`, or `pages`.

### Get Plugin Assets

```
GET /api/plugins/assets
```

Returns the script and CSS URLs for all installed face plugins. The frontend uses this to dynamically inject plugin scripts at page load.

**Response:**

```json
{
  "scripts": ["/plugins/bhb-animated-characters/faces/BigHeadFace.js"],
  "styles": ["/plugins/bhb-animated-characters/faces/bighead.css"]
}
```

### Install a Plugin

```
POST /api/plugins/<plugin_id>/install
```

Installs a plugin from the catalog. Returns `201` on success, `409` if already installed, `404` if not found in catalog.

**Response (201):**

```json
{
  "ok": true,
  "plugin": "BHB Animated Characters",
  "status": "installed_pending_restart",
  "note": "Restart the container to activate routes and face scripts"
}
```

### Uninstall a Plugin

```
DELETE /api/plugins/<plugin_id>
```

Removes a plugin. Returns `200` on success, `404` if not installed.

**Response:**

```json
{
  "ok": true,
  "note": "Restart the container to fully deactivate"
}
```

## Building a Plugin

### Directory Structure

A minimal face plugin:

```
my-face-plugin/
  plugin.json
  faces/
    MyFace.js
    myface.css
```

A minimal page plugin:

```
my-pages-plugin/
  plugin.json
  pages/
    my-tool.html
```

A minimal gateway plugin:

```
my-gateway-plugin/
  plugin.json
  routes/
    api.py
```

### Minimal Face Plugin Example

**`plugin.json`:**

```json
{
  "id": "my-custom-face",
  "name": "My Custom Face",
  "version": "1.0.0",
  "description": "A custom animated face for OpenVoiceUI",
  "author": "your-name",
  "type": "face",

  "faces": [{
    "id": "custom",
    "name": "Custom Face",
    "script": "faces/CustomFace.js",
    "css": "faces/custom.css",
    "moods": ["neutral", "happy", "thinking"],
    "features": ["lip-sync"]
  }]
}
```

**`faces/CustomFace.js`:**

```javascript
class CustomFace extends BaseFace {
  constructor(container) {
    super(container);
    // Initialize your face rendering here
  }

  setMood(mood) {
    // Update visual state based on mood string
  }

  onAudioData(analyserNode) {
    // React to audio for lip-sync / audio-reactive visuals
  }
}

// Register the face so the system can discover it
window.registerFace?.('custom', CustomFace);
```

### Minimal Gateway Plugin Example

**`plugin.json`:**

```json
{
  "id": "example-gateway",
  "name": "Example Gateway (Echo)",
  "version": "1.0.0",
  "description": "Reference implementation - echoes messages back.",

  "routes": [{
    "module": "routes/echo.py",
    "blueprint": "echo_bp"
  }]
}
```

**`routes/echo.py`:**

```python
from flask import Blueprint, jsonify, request

echo_bp = Blueprint("echo", __name__)

@echo_bp.route("/api/echo", methods=["POST"])
def echo():
    data = request.get_json() or {}
    return jsonify({"echo": data.get("message", ""), "ok": True})
```

### Combining Types

A single plugin can include faces, pages, and routes together. The BHB Animated Characters plugin is a real-world example: it provides a face (animated BigHead avatar), a canvas page (character builder), Flask routes (profile and asset APIs), and profile data -- all in one package.

### Additional Content

Plugins can include arbitrary additional data that the face scripts, pages, or routes reference. The BHB plugin includes:

- **`profiles/`** -- Character preset JSON files copied to the runtime profiles directory.
- **`lore/`** -- Show transcripts, character descriptions, and episode memories that the agent can access for character knowledge.
- **`voice-samples/`** -- Audio files for voice cloning or reference.

These are referenced in `plugin.json` and accessed at runtime via the static asset URL (`/plugins/<plugin-id>/...`) or through custom routes.

## Plugin Directories

| Path | Purpose |
|------|---------|
| `/app/plugins/` | Installed plugins (read-write, per-client volume mount) |
| `/app/plugin-catalog/` | Available plugins catalog (read-only, shared across clients) |

In a multi-tenant deployment, each client has their own `/app/plugins/` directory mounted from their data volume. The catalog is shared read-only across all clients so everyone can browse and install from the same set of available plugins.

## Community Plugins

The [openvoiceui-plugins](https://github.com/MCERQUA/openvoiceui-plugins) repository is the home for community-contributed plugins. The BHB Animated Characters plugin -- featuring animated BigHead Billionaires character avatars with lip-sync, mood expressions, and a character builder -- is the first community plugin and serves as the reference implementation.

To submit a plugin:

1. Fork the [openvoiceui-plugins](https://github.com/MCERQUA/openvoiceui-plugins) repository.
2. Add your plugin directory with a complete `plugin.json` manifest.
3. Include a `README.md` in your plugin directory with setup instructions and screenshots.
4. Open a pull request.

Accepted plugins are added to the shared catalog and become installable by all OpenVoiceUI users through the plugin API.
