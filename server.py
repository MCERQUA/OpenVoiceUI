#!/usr/bin/env python3
"""
OpenVoiceUI Server — Entry Point

Initialises the Flask application and registers all route blueprints.
Routes are split into focused blueprints under routes/; this file handles
startup wiring, session management, usage tracking, and standalone endpoints
that don't belong to a specific feature blueprint.

Start:
    venv/bin/python3 server.py

See README.md for full setup instructions.
"""

import asyncio
import base64
import faulthandler
import json
import logging
import os
import queue
import re
import requests
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import websockets
from dotenv import load_dotenv
from flask import Response, request, jsonify

faulthandler.enable()  # print traceback on hard crashes (SIGSEGV etc.)

# Load environment variables before anything else
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path, override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVER_START_TIME = time.time()


# ---------------------------------------------------------------------------
# Faster-Whisper — lazy-loaded on first /api/stt/local request
# ---------------------------------------------------------------------------

_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(
                "Local STT requires faster-whisper. Install it with: "
                "pip install faster-whisper"
            )
        logger.info("Loading Faster-Whisper model (first STT request)...")
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="float32")
        logger.info("Faster-Whisper model ready.")
    return _whisper_model


# ---------------------------------------------------------------------------
# Flask app factory + blueprint registration
# ---------------------------------------------------------------------------

from app import create_app
app, sock = create_app()

from routes.music import music_bp
app.register_blueprint(music_bp)

from routes.canvas import (
    canvas_bp,
    canvas_context,
    update_canvas_context,
    extract_canvas_page_content,
    get_canvas_context,
    load_canvas_manifest,
    save_canvas_manifest,
    add_page_to_manifest,
    sync_canvas_manifest,
    CANVAS_MANIFEST_PATH,
    CANVAS_PAGES_DIR,
    CATEGORY_ICONS,
    CATEGORY_COLORS,
)
app.register_blueprint(canvas_bp)

# Seed default pages into canvas-pages on startup (ships with the app image)
# Default pages are app infrastructure (e.g. desktop menu) — auth is skipped in canvas.py.
# Pages with a <!-- openvoiceui-version: X --> comment are re-seeded when version changes.
from services.paths import DEFAULT_PAGES_DIR

_VERSION_RE = re.compile(r'<!--\s*openvoiceui-version:\s*(\S+)\s*-->')

def _extract_page_version(path, max_lines=5):
    """Read version stamp from the first few lines of an HTML file."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for _, line in zip(range(max_lines), f):
                m = _VERSION_RE.search(line)
                if m:
                    return m.group(1)
    except (OSError, UnicodeDecodeError):
        pass
    return None

if DEFAULT_PAGES_DIR.is_dir():
    CANVAS_PAGES_DIR.mkdir(parents=True, exist_ok=True)
    for src in DEFAULT_PAGES_DIR.iterdir():
        if not src.is_file():
            continue
        dest = CANVAS_PAGES_DIR / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
            logger.info("Seeded default page: %s", src.name)
        else:
            # Re-seed if the shipped version is newer than the runtime copy
            src_ver = _extract_page_version(src)
            if src_ver is not None:
                dest_ver = _extract_page_version(dest)
                if dest_ver != src_ver:
                    shutil.copy2(src, dest)
                    logger.info(
                        "Re-seeded default page %s (version %s -> %s)",
                        src.name, dest_ver, src_ver,
                    )

from routes.static_files import static_files_bp, DJ_SOUNDS, SOUNDS_DIR
app.register_blueprint(static_files_bp)

from routes.admin import admin_bp
app.register_blueprint(admin_bp)

from routes.theme import theme_bp
app.register_blueprint(theme_bp)

from routes.conversation import conversation_bp, clean_for_tts
app.register_blueprint(conversation_bp)

from routes.profiles import profiles_bp
app.register_blueprint(profiles_bp)

from routes.elevenlabs_hybrid import elevenlabs_hybrid_bp
app.register_blueprint(elevenlabs_hybrid_bp)

from routes.instructions import instructions_bp
app.register_blueprint(instructions_bp)

from routes.greetings import greetings_bp
app.register_blueprint(greetings_bp)

from routes.suno import suno_bp
app.register_blueprint(suno_bp)

from routes.vision import vision_bp
app.register_blueprint(vision_bp)

from routes.transcripts import transcripts_bp
app.register_blueprint(transcripts_bp)

from routes.pi import pi_bp
app.register_blueprint(pi_bp)

from routes.onboarding import onboarding_bp
app.register_blueprint(onboarding_bp)

from routes.image_gen import image_gen_bp
app.register_blueprint(image_gen_bp)

from routes.chat import chat_bp
app.register_blueprint(chat_bp)

from routes.workspace import workspace_bp
app.register_blueprint(workspace_bp)

from routes.icons import icons_bp
from routes.report_issue import report_issue_bp
app.register_blueprint(icons_bp)
app.register_blueprint(report_issue_bp)

from routes.registry import registry_bp
app.register_blueprint(registry_bp)

from routes.chatgpt_import import chatgpt_import_bp
app.register_blueprint(chatgpt_import_bp)

# Plugin system — auto-discover and load installed plugins
from routes.plugins import plugins_bp
app.register_blueprint(plugins_bp)

from services.plugins import load_plugins
load_plugins(app)

# Auto-sync canvas manifest on startup so any pages written outside the API
# are picked up immediately without a restart.
try:
    sync_canvas_manifest()
    logger.info("Canvas manifest synced on startup.")
except Exception as _e:
    logger.warning(f"Canvas manifest auto-sync failed (non-critical): {_e}")

# Start canvas page version watcher (auto-saves versions when pages change)
try:
    from services.canvas_versioning import start_version_watcher
    start_version_watcher()
    logger.info("Canvas version watcher started.")
except Exception as _e:
    logger.warning(f"Canvas version watcher failed to start (non-critical): {_e}")


# ---------------------------------------------------------------------------
# Voice session management
# ---------------------------------------------------------------------------

from services.paths import VOICE_SESSION_FILE as _VSF_PATH, DB_PATH, UPLOADS_DIR
VOICE_SESSION_FILE = Path(_VSF_PATH)
_consecutive_empty_responses = 0


def _save_session_counter(counter: int) -> None:
    VOICE_SESSION_FILE.write_text(str(counter))


def get_voice_session_key() -> str:
    """Return the current voice session key, e.g. 'voice-main-6'."""
    prefix = os.getenv("VOICE_SESSION_PREFIX", "voice-main")
    try:
        counter = int(VOICE_SESSION_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        counter = 1
        _save_session_counter(counter)
    return f"{prefix}-{counter}"


def bump_voice_session() -> str:
    """Increment the session counter and return the new session key."""
    global _consecutive_empty_responses
    prefix = os.getenv("VOICE_SESSION_PREFIX", "voice-main")
    try:
        counter = int(VOICE_SESSION_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        counter = 1
    counter += 1
    _save_session_counter(counter)
    _consecutive_empty_responses = 0
    new_key = f"{prefix}-{counter}"
    logger.info(f"Session bumped → {new_key}")
    return new_key


# ---------------------------------------------------------------------------
# User usage tracking (SQLite)
# ---------------------------------------------------------------------------

MONTHLY_LIMIT = int(os.getenv("MONTHLY_USAGE_LIMIT", "20"))
UNLIMITED_USERS: list = [
    u.strip() for u in os.getenv("UNLIMITED_USER_IDS", "").split(",") if u.strip()
]
from services.db_pool import SQLitePool
db_pool = SQLitePool(DB_PATH, pool_size=5)


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            user_id TEXT PRIMARY KEY,
            message_count INTEGER DEFAULT 0,
            month TEXT,
            updated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT 'default',
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            tts_provider TEXT,
            voice TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversation_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT 'default',
            profile TEXT,
            model TEXT,
            handshake_ms INTEGER,
            llm_inference_ms INTEGER,
            tts_generation_ms INTEGER,
            total_ms INTEGER,
            user_message_len INTEGER,
            response_len INTEGER,
            tts_text_len INTEGER,
            tts_provider TEXT,
            tts_success INTEGER DEFAULT 1,
            tts_error TEXT,
            tool_count INTEGER DEFAULT 0,
            fallback_used INTEGER DEFAULT 0,
            error TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def get_user_usage(user_id: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT message_count, month FROM usage WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        count, month = row
        return count if month == get_current_month() else 0
    return 0


def increment_usage(user_id: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    current_month = get_current_month()
    now = datetime.now().isoformat()
    c.execute("SELECT month FROM usage WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        if row[0] != current_month:
            c.execute(
                "UPDATE usage SET message_count = 1, month = ?, updated_at = ? WHERE user_id = ?",
                (current_month, now, user_id),
            )
        else:
            c.execute(
                "UPDATE usage SET message_count = message_count + 1, updated_at = ? WHERE user_id = ?",
                (now, user_id),
            )
    else:
        c.execute(
            "INSERT INTO usage (user_id, message_count, month, updated_at) VALUES (?, 1, ?, ?)",
            (user_id, current_month, now),
        )
    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------------
# Upload directory
# ---------------------------------------------------------------------------

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Routes — index
# ---------------------------------------------------------------------------

@app.route("/")
def serve_index():
    """Serve index.html with injected runtime config.

    Set AGENT_SERVER_URL in .env to override the backend URL the frontend
    connects to. Defaults to window.location.origin (correct for same-origin
    deployments).
    """
    import pathlib
    html = pathlib.Path("index.html").read_text()
    server_url = os.environ.get("AGENT_SERVER_URL", "").strip().rstrip("/")
    clerk_key = (os.environ.get("CLERK_PUBLISHABLE_KEY") or os.environ.get("VITE_CLERK_PUBLISHABLE_KEY", "")).strip()
    client_name = os.environ.get("CLIENT_NAME", "").strip()
    import json as _json
    devsite_map_raw = os.environ.get("DEVSITE_MAP", "{}").strip()
    try:
        devsite_map = _json.loads(devsite_map_raw)
    except Exception:
        devsite_map = {}
    config_parts = []
    config_parts.append(f'serverUrl:"{server_url}"' if server_url else 'serverUrl:window.location.origin')
    if clerk_key:
        config_parts.append(f'clerkPublishableKey:"{clerk_key}"')
    if devsite_map:
        config_parts.append(f'devsiteMap:{_json.dumps(devsite_map)}')
    if client_name:
        config_parts.append(f'clientName:{_json.dumps(client_name)}')
    config_block = f'<script>window.AGENT_CONFIG={{{",".join(config_parts)}}};</script>'
    html = html.replace("<head>", f"<head>\n  {config_block}", 1)
    # Replace PWA title and apple-mobile-web-app-title with client name
    if client_name:
        html = html.replace("<title>OpenVoiceUI</title>", f"<title>{client_name}</title>")
        html = html.replace('content="OpenVoiceUI"', f'content="{client_name}"')
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ---------------------------------------------------------------------------
# Routes — version info
# ---------------------------------------------------------------------------

_VERSION_INFO = {"commit": "unknown", "branch": "unknown", "date": "unknown"}
_version_file = Path(__file__).parent / "version.json"
if _version_file.exists():
    try:
        _VERSION_INFO = json.loads(_version_file.read_text())
    except Exception:
        pass

# Read package.json version as authoritative version string
_PACKAGE_VERSION = "unknown"
_package_file = Path(__file__).parent / "package.json"
if _package_file.exists():
    try:
        _PACKAGE_VERSION = json.loads(_package_file.read_text()).get("version", "unknown")
    except Exception:
        pass
_VERSION_INFO["version"] = _PACKAGE_VERSION


@app.route("/api/version", methods=["GET"])
def get_version():
    """Return build version and check GitHub for latest release."""
    data = {**_VERSION_INFO, "uptime_seconds": round(time.time() - SERVER_START_TIME)}
    # Check for newer release on GitHub (cached, non-blocking)
    latest = _get_latest_release_info()
    if latest:
        data["latest_commit"] = latest["sha"]
        data["latest_date"] = latest["date"]
        data["latest_message"] = latest["message"]
        data["latest_version"] = latest.get("latest_version", "")
        # Check if we need an update. Three strategies:
        # 1. git merge-base (if .git exists) — handles being ahead of release
        # 2. Commit SHA comparison — direct match
        # 3. package.json version vs release tag — fallback when commit unknown
        current = _VERSION_INFO.get("commit", "unknown")
        data["update_available"] = False
        if current != "unknown" and latest.get("sha"):
            app_dir = Path(__file__).parent
            if (app_dir / ".git").is_dir():
                try:
                    result = subprocess.run(
                        ["git", "merge-base", "--is-ancestor",
                         latest["sha"], "HEAD"],
                        cwd=str(app_dir), capture_output=True, timeout=5,
                    )
                    # returncode 0 = release is ancestor of HEAD (up to date)
                    # returncode 1 = release is NOT ancestor (need update)
                    data["update_available"] = result.returncode != 0
                except Exception:
                    data["update_available"] = not latest["sha"].startswith(current)
            else:
                data["update_available"] = not latest["sha"].startswith(current)
        elif current == "unknown" and latest.get("latest_version"):
            # Commit unknown (Docker build without args, etc.)
            # Fall back to comparing package.json version against release tag.
            pkg_ver = _PACKAGE_VERSION
            rel_tag = latest["latest_version"]
            if pkg_ver != "unknown" and rel_tag:
                rel_ver = rel_tag.lstrip("v")
                data["update_available"] = rel_ver.split("-")[0] != pkg_ver.split("-")[0] or rel_ver > pkg_ver
    return jsonify(data)


# Cache GitHub check for 5 minutes to avoid hammering the API
_github_cache = {"data": None, "expires": 0}


_GITHUB_REPO = os.environ.get("GITHUB_REPO", "MCERQUA/OpenVoiceUI").strip()


def _get_latest_release_info():
    """Fetch the latest GitHub release and its commit SHA.

    Only compares against tagged releases — not every commit on main.
    This prevents false "update available" banners from routine merges.
    """
    now = time.time()
    if _github_cache["data"] and now < _github_cache["expires"]:
        return _github_cache["data"]
    try:
        # Get the latest release
        rel = requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5,
        )
        if rel.status_code != 200:
            return _github_cache.get("data")

        rel_data = rel.json()
        tag_name = rel_data.get("tag_name", "")

        # Get the commit SHA for this release tag
        tag_resp = requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/git/ref/tags/{tag_name}",
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5,
        )
        sha = ""
        if tag_resp.status_code == 200:
            tag_obj = tag_resp.json().get("object", {})
            sha = tag_obj.get("sha", "")[:7]

        result = {
            "sha": sha,
            "date": rel_data.get("published_at", ""),
            "message": rel_data.get("name", tag_name),
            "latest_version": tag_name,
        }
        _github_cache["data"] = result
        _github_cache["expires"] = now + 300  # 5 min cache
        return result
    except Exception:
        pass
    return _github_cache.get("data")


def _self_update():
    """Intelligent update: analyse → detect agent → review → apply → verify.

    Instead of a blind ``git pull``, this:
    1. Analyses what changed upstream vs what's customised locally
    2. Searches for an available CLI coding agent (claude, codex, z-code, etc.)
    3. If an agent is found AND there are conflicts → spawns it with a
       comprehensive prompt that enumerates every possible breaking point
    4. If no agent or low risk → uses heuristic-based smart update with
       automatic backup/rollback
    5. Verifies health after every update; rolls back on failure

    Works in all deployment scenarios: native, Docker, multi-tenant, Pinokio.
    """
    from services.update_manager import UpdateManager

    app_dir = Path(__file__).parent
    mgr = UpdateManager(app_dir)
    return mgr.apply_update()


@app.route("/api/version/preview", methods=["GET"])
def preview_update():
    """Return a preview of what an update would change, without applying it.

    The frontend can call this to show the user:
    - What files will change
    - What local customisations exist
    - What conflicts were detected
    - Which update method will be used (AI agent or smart fallback)
    - Risk level (low / medium / high)
    """
    from services.update_manager import UpdateManager

    app_dir = Path(__file__).parent
    mgr = UpdateManager(app_dir)
    return jsonify(mgr.get_update_preview())


@app.route("/api/version/update", methods=["POST"])
def trigger_update():
    """Update the app to the latest version.

    Tries three strategies in order:
    1. Intelligent self-update (analyses diffs, detects agent, backs up,
       verifies) — works for native/Pinokio/dev/Docker installs with .git
    2. Host update service (JamBot / managed Docker hosting)
    3. Returns instructions as last resort
    """
    # Check if already current
    latest = _get_latest_release_info()
    current = _VERSION_INFO.get("commit", "unknown")
    if latest and current != "unknown" and latest["sha"].startswith(current):
        return jsonify({"status": "current", "message": "Already up to date"})

    # Strategy 1: Intelligent self-update if this is a git repo
    app_dir = Path(__file__).parent
    if (app_dir / ".git").is_dir():
        result = _self_update()
        status = result.get("status", "error")

        if status == "success":
            return jsonify({
                "status": "updating",
                "message": "Update applied — app will restart in a moment",
                "method": result.get("method", "unknown"),
                "details": {
                    "files_updated": len(result.get("files_updated", [])),
                    "customisations_preserved": result.get("customisations_preserved", []),
                    "warnings": result.get("warnings", []),
                },
            })
        elif status == "current":
            return jsonify({"status": "current", "message": "Already up to date"})
        elif status == "rolled_back":
            return jsonify({
                "status": "rolled_back",
                "message": "Update was rolled back due to issues",
                "reason": result.get("reason", "Verification failed"),
                "method": result.get("method", "unknown"),
            }), 409
        else:
            return jsonify({
                "status": "error",
                "error": result.get("reason", result.get("error", "Update failed")),
                "method": result.get("method", "unknown"),
            }), 500

    # Strategy 2: Host update service (JamBot / managed Docker)
    client_name = os.environ.get("CLIENT_NAME", "").strip()
    if client_name:
        for host in ["host.docker.internal", "172.17.0.1"]:
            try:
                r = requests.post(f"http://{host}:5199/update/{client_name}", timeout=5)
                if r.status_code == 200:
                    return jsonify({"status": "updating", "message": "Updating — your system will restart in a moment"})
            except Exception:
                pass

    # Strategy 3: Manual — shouldn't happen often
    return jsonify({"status": "manual", "message": "Pull the latest version and rebuild to update"})


# ---------------------------------------------------------------------------
# Routes — health probes
# ---------------------------------------------------------------------------

from services.health import health_checker as _health_checker


@app.route("/health/live", methods=["GET"])
def health_live():
    """Liveness probe — always 200 while the process is running."""
    result = _health_checker.liveness()
    return jsonify({"healthy": result.healthy, "message": result.message, "details": result.details}), 200


@app.route("/health/ready", methods=["GET"])
def health_ready():
    """Readiness probe — 200 only when Gateway and TTS are available."""
    result = _health_checker.readiness()
    code = 200 if result.healthy else 503
    return jsonify({"healthy": result.healthy, "message": result.message, "details": result.details}), code


@app.route("/api/memory-status", methods=["GET"])
def memory_status():
    """Process memory usage — for watchdog monitoring."""
    import resource
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    current_mb = rusage.ru_maxrss / 1024  # ru_maxrss is KB on Linux
    return jsonify({"process": {"current_mb": round(current_mb, 1)}})


# ---------------------------------------------------------------------------
# Routes — session
# ---------------------------------------------------------------------------

@app.route("/api/session", methods=["GET"])
def session_info():
    """Return the current voice session key and consecutive-empty-response count."""
    return jsonify({
        "sessionKey": get_voice_session_key(),
        "consecutiveEmpty": _consecutive_empty_responses,
    })


@app.route("/api/session/reset", methods=["POST"])
def session_reset():
    """Reset the voice session context.

    Body (JSON, optional):
      { "mode": "soft" }  — bump session key only (default)
      { "mode": "hard" }  — bump session key and pre-warm the new session
    """
    from services.gateway import gateway_connection

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "soft")
    if mode not in ("soft", "hard"):
        return jsonify({"error": f"Invalid mode '{mode}'. Use 'soft' or 'hard'."}), 400

    old_key = get_voice_session_key()
    new_key = bump_voice_session()

    if mode == "hard":
        def _prewarm():
            try:
                gateway_connection.stream_to_queue(
                    queue.Queue(),
                    "[SYSTEM: session pre-warm, reply with exactly: ok]",
                    new_key,
                    [],
                )
                logger.info(f"Pre-warm complete for {new_key}")
            except Exception as e:
                logger.warning(f"Pre-warm failed: {e}")
        threading.Thread(target=_prewarm, daemon=True).start()

    return jsonify({
        "old": old_key,
        "new": new_key,
        "mode": mode,
        "message": f"Session reset ({mode})." + (" Pre-warming new session..." if mode == "hard" else ""),
    })


# ---------------------------------------------------------------------------
# Routes — diagnostics
# ---------------------------------------------------------------------------

@app.route("/api/diagnostics", methods=["GET"])
def diagnostics():
    """Diagnostic dashboard — uptime, active config, recent timing metrics."""
    import resource

    uptime_seconds = int(time.time() - SERVER_START_TIME)
    uptime_h = uptime_seconds // 3600
    uptime_m = (uptime_seconds % 3600) // 60
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    memory_mb = round(rusage.ru_maxrss / 1024, 1)

    state = {
        "server": {
            "uptime": f"{uptime_h}h {uptime_m}m",
            "uptime_seconds": uptime_seconds,
            "memory_mb": memory_mb,
            "pid": os.getpid(),
            "started_at": datetime.fromtimestamp(SERVER_START_TIME).isoformat(),
        },
        "config": {
            "gateway_url": os.getenv("CLAWDBOT_GATEWAY_URL", "ws://127.0.0.1:18791"),
            "session_key": get_voice_session_key(),
            "tts_provider": os.getenv("DEFAULT_TTS_PROVIDER", "groq"),
            "port": os.getenv("PORT", "5001"),
        },
    }

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT profile, model, handshake_ms, llm_inference_ms,
                   tts_generation_ms, total_ms, user_message_len,
                   response_len, tts_text_len, tts_provider, tts_success,
                   tts_error, tool_count, fallback_used, error, created_at
            FROM conversation_metrics
            ORDER BY id DESC LIMIT 10
        """)
        state["recent_conversations"] = [dict(r) for r in c.fetchall()]
        c.execute("""
            SELECT COUNT(*) as total_conversations,
                   AVG(total_ms) as avg_total_ms,
                   AVG(llm_inference_ms) as avg_llm_ms,
                   AVG(tts_generation_ms) as avg_tts_ms,
                   AVG(handshake_ms) as avg_handshake_ms,
                   SUM(CASE WHEN tts_success = 0 THEN 1 ELSE 0 END) as tts_failures,
                   SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as errors,
                   MAX(total_ms) as max_total_ms,
                   MIN(total_ms) as min_total_ms
            FROM conversation_metrics
            WHERE created_at > datetime('now', '-1 hour')
        """)
        stats = dict(c.fetchone() or {})
        for key in ("avg_total_ms", "avg_llm_ms", "avg_tts_ms", "avg_handshake_ms"):
            if stats.get(key) is not None:
                stats[key] = round(stats[key])
        state["last_hour_stats"] = stats
        conn.close()
    except Exception as e:
        state["metrics_error"] = str(e)

    return jsonify(state)


# ---------------------------------------------------------------------------
# Routes — Hume EVI token (used by src/adapters/hume-evi.js)
# ---------------------------------------------------------------------------

@app.route("/api/hume/token", methods=["GET"])
def get_hume_token():
    """Return a short-lived Hume access token for EVI WebSocket connections.

    Returns 403 when Hume credentials are not configured — the frontend
    adapter treats this as 'Hume unavailable' rather than an error.
    """
    api_key = os.getenv("HUME_API_KEY")
    secret_key = os.getenv("HUME_SECRET_KEY")

    if not api_key or not secret_key:
        return jsonify({"error": "Hume API credentials not configured", "available": False}), 403

    try:
        credentials = f"{api_key}:{secret_key}"
        encoded = base64.b64encode(credentials.encode()).decode()
        response = requests.post(
            "https://api.hume.ai/oauth2-cc/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {encoded}",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
        if response.status_code != 200:
            logger.error(f"Hume token request failed: {response.status_code} — {response.text}")
            return jsonify({"error": "Failed to get Hume access token", "available": False}), 500
        token_data = response.json()
        return jsonify({
            "access_token": token_data.get("access_token"),
            "expires_in": token_data.get("expires_in", 3600),
            "config_id": os.getenv("HUME_CONFIG_ID"),
            "available": True,
        })
    except Exception as e:
        logger.error(f"Hume token error: {e}")
        return jsonify({"error": "Failed to retrieve token", "available": False}), 500


# ---------------------------------------------------------------------------
# Routes — STT (Speech-to-Text)
# ---------------------------------------------------------------------------

@app.route("/api/stt/groq", methods=["POST"])
def groq_stt():
    """Transcribe audio using Groq Whisper Large v3 Turbo (cloud, fast)."""
    from services.tts import get_groq_client as _get_groq_client

    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    groq = _get_groq_client()
    if not groq:
        return jsonify({"error": "Groq client not available — check GROQ_API_KEY"}), 500

    try:
        audio_bytes = audio_file.read()
        audio_tuple = (
            audio_file.filename or "audio.webm",
            audio_bytes,
            audio_file.content_type or "audio/webm",
        )
        transcription = groq.audio.transcriptions.create(
            file=audio_tuple,
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            language="en",
            temperature=0,
            prompt="",
        )
        # Filter segments with high no_speech_prob or low confidence (Whisper hallucinations)
        import re as _re
        segments = getattr(transcription, 'segments', None)
        if segments:
            filtered_texts = []
            for seg in segments:
                _nsp = seg.get('no_speech_prob', 0) if isinstance(seg, dict) else getattr(seg, 'no_speech_prob', 0)
                _alp = seg.get('avg_logprob', 0) if isinstance(seg, dict) else getattr(seg, 'avg_logprob', 0)
                _stxt = (seg.get('text', '') if isinstance(seg, dict) else seg.text).strip()
                # Reject: high no-speech probability OR very low confidence
                if _nsp >= 0.2:
                    logger.debug(f"Groq STT: dropping segment (no_speech_prob={_nsp:.2f}): {_stxt!r}")
                    continue
                if _alp < -1.0:
                    logger.debug(f"Groq STT: dropping segment (avg_logprob={_alp:.2f}): {_stxt!r}")
                    continue
                filtered_texts.append(_stxt)
            text = ' '.join(filtered_texts).strip()
        else:
            text = (transcription.text or "").strip()
        logger.info(f"Groq STT: {text!r}")

        # --- Whisper hallucination filtering ---
        _WHISPER_HALLUCINATIONS = {
            "thank you", "thanks for watching", "thanks for listening",
            "i'm here with closed captioning", "closed captioning",
            "subscribe", "please subscribe", "like and subscribe",
            "you", "bye", "the end", "subtitles by", "translated by",
            "voice command for ai assistant", "voice command for ai",
            "alright", "all right", "okay", "ok", "yeah", "yes",
            "um", "uh", "hmm", "huh", "oh", "ah",
            "so", "well", "right", "sure", "hey",
            "thanks", "thank you so much",
            "i don't know", "i'm sorry",
        }
        # Substrings that indicate prompt-echo or known garbage
        _HALLUCINATION_SUBSTRINGS = [
            "voice command for ai",
            "thanks for watching", "thanks for listening",
            "like and subscribe", "please subscribe",
            "subtitles by", "translated by", "closed captioning",
            "coupo foundation",  # known recurring hallucination
        ]
        text_lower = text.lower().rstrip('.!?,;:')
        _meaningful = _re.sub(r'[^a-zA-Z0-9]', '', text)

        def _is_hallucination(t, t_lower):
            # Exact match against known phrases
            if t_lower in _WHISPER_HALLUCINATIONS:
                return True
            # Too short to be real speech
            if len(_meaningful) < 3:
                return True
            # Prompt text or known garbage appears anywhere in transcription
            for sub in _HALLUCINATION_SUBSTRINGS:
                if sub in t_lower:
                    return True
            # Repetitive pattern: same word/phrase repeated many times
            words = _re.findall(r'[a-zA-Z]+', t)
            if len(words) >= 4:
                from collections import Counter
                counts = Counter(w.lower() for w in words)
                most_common_count = counts.most_common(1)[0][1]
                if most_common_count / len(words) >= 0.5:
                    return True
            return False

        if _is_hallucination(text, text_lower):
            logger.info(f"Groq STT: FILTERED hallucination/garbage: {text!r}")
            return jsonify({"transcript": "", "success": True, "filtered": True})

        return jsonify({"transcript": text, "success": True})
    except Exception as e:
        logger.error(f"Groq STT error: {e}")
        return jsonify({"error": "Speech-to-text failed"}), 500


@app.route("/api/stt/deepgram/token", methods=["GET"])
def deepgram_stt_token():
    """Return the Deepgram API key for browser-side WebSocket streaming.

    The browser needs the key to open a direct WebSocket to Deepgram's
    live transcription API.  The key is passed via the WebSocket sub-protocol
    header so it never appears in URLs or logs.

    NOTE: Deepgram supports scoped / short-lived project keys — if you want
    tighter security, create a key with only 'usage:write' permission and
    rotate it.  For now we hand out the configured key since the UI is
    already authenticated.
    """
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        return jsonify({"error": "DEEPGRAM_API_KEY not configured"}), 500
    return jsonify({"token": api_key})


@app.route("/api/stt/deepgram", methods=["POST"])
def deepgram_stt():
    """Transcribe audio using Deepgram Nova-2 API (reliable, low-cost)."""
    import re as _re

    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        return jsonify({"error": "DEEPGRAM_API_KEY not configured"}), 500

    audio_file = request.files["audio"]
    try:
        audio_bytes = audio_file.read()
        content_type = audio_file.content_type or "audio/webm"

        import requests as _requests
        resp = _requests.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "model": "nova-2",
                "language": "en",
                "smart_format": "true",
                "punctuate": "true",
            },
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": content_type,
            },
            data=audio_bytes,
            timeout=15,
        )

        if resp.status_code != 200:
            logger.error(f"Deepgram API error {resp.status_code}: {resp.text[:300]}")
            return jsonify({"error": f"Deepgram API error: {resp.status_code}"}), 502

        result = resp.json()
        channels = result.get("results", {}).get("channels", [])
        if not channels:
            return jsonify({"transcript": "", "success": True})

        alt = channels[0].get("alternatives", [{}])[0]
        text = alt.get("transcript", "").strip()
        confidence = alt.get("confidence", 0)

        logger.info(f"Deepgram STT: {text!r} (confidence={confidence:.2f})")

        # Low confidence filter
        if confidence < 0.3 and text:
            logger.info(f"Deepgram STT: FILTERED low confidence ({confidence:.2f}): {text!r}")
            return jsonify({"transcript": "", "success": True, "filtered": True})

        # Hallucination filtering (same as Groq)
        _HALLUCINATIONS = {
            "thank you", "thanks for watching", "thanks for listening",
            "subscribe", "please subscribe", "like and subscribe",
            "the end", "subtitles by", "translated by", "closed captioning",
            "voice command for ai assistant", "voice command for ai",
            "thanks", "thank you so much",
        }
        _HALLUCINATION_SUBSTRINGS = [
            "voice command for ai", "thanks for watching", "thanks for listening",
            "like and subscribe", "please subscribe",
            "subtitles by", "translated by", "closed captioning",
        ]
        text_lower = text.lower().rstrip('.!?,;:')
        meaningful = _re.sub(r'[^a-zA-Z0-9]', '', text)

        if text_lower in _HALLUCINATIONS:
            logger.info(f"Deepgram STT: FILTERED hallucination: {text!r}")
            return jsonify({"transcript": "", "success": True, "filtered": True})
        if len(meaningful) < 3:
            logger.info(f"Deepgram STT: FILTERED too short: {text!r}")
            return jsonify({"transcript": "", "success": True, "filtered": True})
        for sub in _HALLUCINATION_SUBSTRINGS:
            if sub in text_lower:
                logger.info(f"Deepgram STT: FILTERED hallucination substring: {text!r}")
                return jsonify({"transcript": "", "success": True, "filtered": True})

        return jsonify({"transcript": text, "success": True, "confidence": confidence})
    except Exception as e:
        logger.error(f"Deepgram STT error: {e}")
        return jsonify({"error": "Speech-to-text failed"}), 500


@app.route("/api/stt/local", methods=["POST"])
def local_stt():
    """Transcribe audio using local Faster-Whisper with Silero VAD.

    Requires faster-whisper and ffmpeg. Uses the 'tiny' model to keep
    memory usage low.
    """
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        wav_path = tmp_path.replace(".webm", ".wav")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(f"FFmpeg conversion failed, transcribing original: {result.stderr.decode()}")
            wav_path = tmp_path

        segments, info = get_whisper_model().transcribe(
            wav_path,
            language="en",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "threshold": 0.5},
        )
        transcript = " ".join(seg.text for seg in segments).strip()
        logger.info(f"Local STT: {transcript!r} ({info.duration:.1f}s)")
        return jsonify({"transcript": transcript, "success": True})
    except Exception as e:
        logger.error(f"Local STT error: {e}")
        return jsonify({"error": "Speech-to-text failed"}), 500
    finally:
        for f in [tmp_path, tmp_path.replace(".webm", ".wav")]:
            try:
                os.unlink(f)
            except OSError:
                pass


@app.route("/api/stt/external", methods=["POST"])
def external_stt():
    """Transcribe audio using an external STT service (bring your own).

    Sends audio to the URL configured in STT_API_URL. Supports OpenAI-compatible
    and generic Whisper ASR formats. Configure via .env or admin panel.
    """
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    language = request.form.get("language", "en")

    try:
        from providers.registry import registry, ProviderType
        provider = registry.get_provider(ProviderType.STT, "external")
        result = provider.transcribe(audio_bytes, language=language)

        if not result.text:
            return jsonify({"transcript": "", "success": True, "filtered": True})

        return jsonify({
            "transcript": result.text,
            "success": True,
            "confidence": result.confidence,
            "duration_ms": result.duration_ms,
        })
    except Exception as e:
        logger.error(f"External STT error: {e}")
        return jsonify({"error": f"External STT failed: {e}"}), 500


# ---------------------------------------------------------------------------
# Routes — web search
# ---------------------------------------------------------------------------

@app.route("/api/search/brave", methods=["GET"])
def brave_search():
    """Web search via Brave Search API. Requires BRAVE_API_KEY in .env."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    brave_api_key = os.getenv("BRAVE_API_KEY")
    if not brave_api_key:
        return jsonify({"error": "BRAVE_API_KEY not configured"}), 500

    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": brave_api_key},
            params={"q": query, "count": 10, "search_lang": "en", "freshness": "pw"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
            }
            for r in data.get("web", {}).get("results", [])[:5]
        ]
        return jsonify({"query": query, "results": results, "success": True})
    except Exception as e:
        logger.error(f"Brave Search error: {e}")
        return jsonify({"error": "Search failed"}), 500


@app.route("/api/search", methods=["GET", "POST"])
def web_search():
    """Web search via DuckDuckGo (no API key required)."""
    import urllib.request
    import urllib.parse
    from html.parser import HTMLParser

    if request.method == "POST":
        query = (request.get_json() or {}).get("query")
    else:
        query = request.args.get("query")

    if not query:
        return jsonify({"error": "query required"}), 400

    class _DDGParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.results = []
            self._current = {}
            self._capture = False
            self._text = ""

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "a" and d.get("class") == "result__a":
                self._current = {"url": d.get("href", ""), "title": "", "snippet": ""}
                self._capture = True
            elif tag == "a" and d.get("class") == "result__snippet":
                self._capture = True

        def handle_endtag(self, tag):
            if tag == "a" and self._capture:
                if self._current and not self._current.get("title"):
                    self._current["title"] = self._text.strip()
                elif self._current.get("title") and not self._current.get("snippet"):
                    self._current["snippet"] = self._text.strip()
                    if self._current["title"] and self._current["url"]:
                        self.results.append(self._current)
                    self._current = {}
                self._capture = False
                self._text = ""

        def handle_data(self, data):
            if self._capture:
                self._text += data

    try:
        encoded = urllib.parse.quote_plus(query)
        req = urllib.request.Request(
            f"https://html.duckduckgo.com/html/?q={encoded}",
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
        parser = _DDGParser()
        parser.feed(html)
        results = parser.results[:5]
        return jsonify({"query": query, "results": results, "success": True})
    except Exception as e:
        logger.error(f"DuckDuckGo search error: {e}")
        return jsonify({"error": "Search failed"}), 500


# ---------------------------------------------------------------------------
# Routes — usage quotas
# ---------------------------------------------------------------------------

@app.route("/api/usage/<user_id>", methods=["GET"])
def check_usage(user_id):
    """Return the current month's usage for a user."""
    if user_id in UNLIMITED_USERS:
        return jsonify({
            "user_id": user_id,
            "used": get_user_usage(user_id),
            "limit": -1,
            "remaining": -1,
            "allowed": True,
            "unlimited": True,
        })
    count = get_user_usage(user_id)
    return jsonify({
        "user_id": user_id,
        "used": count,
        "limit": MONTHLY_LIMIT,
        "remaining": max(0, MONTHLY_LIMIT - count),
        "allowed": count < MONTHLY_LIMIT,
    })


@app.route("/api/usage/<user_id>/increment", methods=["POST"])
def track_usage(user_id):
    """Increment usage count for a user (called after each agent response)."""
    if user_id in UNLIMITED_USERS:
        increment_usage(user_id)
        return jsonify({
            "user_id": user_id,
            "used": get_user_usage(user_id),
            "limit": -1,
            "remaining": -1,
            "unlimited": True,
        })
    count = get_user_usage(user_id)
    if count >= MONTHLY_LIMIT:
        return jsonify({"error": "Monthly limit reached", "used": count, "limit": MONTHLY_LIMIT}), 429
    increment_usage(user_id)
    new_count = count + 1
    return jsonify({
        "user_id": user_id,
        "used": new_count,
        "limit": MONTHLY_LIMIT,
        "remaining": max(0, MONTHLY_LIMIT - new_count),
    })


# ---------------------------------------------------------------------------
# Routes — server commands (whitelisted)
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS = {
    "git_status":     {"cmd": ["git", "status"],                                "desc": "Git working tree status"},
    "git_log":        {"cmd": ["git", "log", "--oneline", "-10"],               "desc": "Last 10 commits"},
    "disk_usage":     {"cmd": ["df", "-h", "/"],                                "desc": "Disk usage"},
    "memory":         {"cmd": ["free", "-h"],                                   "desc": "Memory usage"},
    "uptime":         {"cmd": ["uptime"],                                       "desc": "System uptime"},
    "date":           {"cmd": ["date"],                                         "desc": "Current date/time"},
    "whoami":         {"cmd": ["whoami"],                                       "desc": "Current user"},
    "nginx_status":   {"cmd": ["systemctl", "status", "nginx", "--no-pager"],   "desc": "Nginx status"},
    "service_status": {"cmd": ["systemctl", "status", "openvoiceui", "--no-pager"], "desc": "OpenVoiceUI service status"},
    "network":        {"cmd": ["ss", "-tuln"],                                  "desc": "Active network listeners"},
    "processes":      {"cmd": ["ps", "aux", "--sort=-%cpu"],                    "desc": "Running processes by CPU"},
    "hostname":       {"cmd": ["hostname"],                                     "desc": "Server hostname"},
    "ip_address":     {"cmd": ["hostname", "-I"],                               "desc": "Server IP addresses"},
}

_COMMAND_KEYWORDS = {
    "git": "git_status", "commit": "git_log", "disk": "disk_usage",
    "space": "disk_usage", "memory": "memory", "ram": "memory",
    "time": "date", "date": "date", "nginx": "nginx_status",
    "web": "nginx_status", "service": "service_status",
    "openvoiceui": "service_status", "network": "network",
    "ports": "network", "process": "processes", "cpu": "processes",
    "running": "processes", "host": "hostname", "ip": "ip_address",
    "address": "ip_address", "uptime": "uptime",
}


@app.route("/api/command", methods=["GET", "POST"])
def run_command():
    """Run a whitelisted server command. Accepts a command key or natural language."""
    if request.method == "POST":
        command = (request.get_json() or {}).get("command")
    else:
        command = request.args.get("command")

    if not command:
        return jsonify({
            "available_commands": [{"name": k, "description": v["desc"]} for k, v in ALLOWED_COMMANDS.items()],
        })

    key = command.lower().replace(" ", "_").replace("-", "_")
    matched = key if key in ALLOWED_COMMANDS else next(
        (v for k, v in _COMMAND_KEYWORDS.items() if k in key), None
    )

    if not matched:
        return jsonify({"error": "command not in whitelist", "available": list(ALLOWED_COMMANDS.keys())}), 400

    cmd_info = ALLOWED_COMMANDS[matched]
    try:
        result = subprocess.run(
            cmd_info["cmd"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent),
        )
        output = (result.stdout.strip() or result.stderr.strip())[:1500]
        return jsonify({
            "command": matched,
            "description": cmd_info["desc"],
            "output": output,
            "return_code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": f"'{matched}' timed out after 30s"}), 504
    except Exception as e:
        logger.error(f"Command error ({matched}): {e}")
        return jsonify({"error": "Command execution failed"}), 500


@app.route("/api/commands", methods=["GET"])
def list_commands():
    """List all whitelisted commands."""
    return jsonify({
        "commands": [{"name": k, "description": v["desc"]} for k, v in ALLOWED_COMMANDS.items()]
    })


# ---------------------------------------------------------------------------
# Routes — file upload
# ---------------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def upload_file():
    """Upload a file for the voice agent (images, text, code, etc.)."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    allowed_exts = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
        ".pdf", ".txt", ".md", ".json", ".csv",
        ".html", ".js", ".py", ".ts", ".css",
    }
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_exts:
        return jsonify({"error": f"File type '{ext}' not allowed"}), 400

    safe_name = re.sub(r"[^\w\-.]", "_", file.filename)[:80]
    save_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    save_path = UPLOADS_DIR / save_name
    file.save(save_path)

    is_image = ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    result = {
        "filename": save_name,
        "original_name": file.filename,
        "path": str(save_path),
        "type": "image" if is_image else "text",
        "size": save_path.stat().st_size,
        "url": f"/uploads/{save_name}",
    }
    if not is_image and ext != ".pdf":
        try:
            result["content_preview"] = save_path.read_text(encoding="utf-8", errors="replace")[:2000]
        except Exception:
            pass

    logger.info(f"Upload: {file.filename} → {save_path} ({result['size']} bytes)")
    return jsonify(result)


@app.route("/api/uploads", methods=["GET"])
def list_uploads():
    """List uploaded files. Optional ?type=image|text filter."""
    file_type = request.args.get("type")  # "image", "text", or None for all
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    text_exts = {".pdf", ".txt", ".md", ".json", ".csv", ".html", ".js", ".py", ".ts", ".css"}

    files = []
    for f in sorted(UPLOADS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_dir() or f.name.startswith("."):
            continue
        ext = f.suffix.lower()
        is_image = ext in image_exts
        kind = "image" if is_image else ("text" if ext in text_exts else "other")
        if file_type and kind != file_type:
            continue
        stat = f.stat()
        files.append({
            "filename": f.name,
            "url": f"/uploads/{f.name}",
            "type": kind,
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })

    return jsonify({"files": files, "count": len(files)})


# ---------------------------------------------------------------------------
# WebSocket — Gateway proxy (/ws/clawdbot)
# ---------------------------------------------------------------------------

from services.tts import generate_tts_b64 as _generate_tts_b64


def _tts_bytes(text: str) -> bytes:
    """Generate TTS audio bytes for the WebSocket proxy."""
    b64 = _generate_tts_b64(text, voice="M1")
    if b64 is None:
        raise RuntimeError("TTS generation returned no audio")
    return base64.b64decode(b64)


@sock.route("/ws/clawdbot")
def clawdbot_websocket(ws):
    """WebSocket proxy between the frontend and the OpenClaw Gateway.

    Connects to CLAWDBOT_GATEWAY_URL, performs the protocol-3 handshake,
    then bridges messages bidirectionally — generating TTS audio for every
    assistant response before forwarding to the client.
    """
    # --- Clerk auth check (uses same cookie/header as HTTP routes) ---
    from services.auth import verify_clerk_token, get_token_from_request
    token = get_token_from_request()
    user_id = verify_clerk_token(token) if token else None
    if not user_id:
        logger.warning("WebSocket rejected — no valid Clerk token")
        ws.send(json.dumps({"type": "error", "message": "Unauthorized"}))
        ws.close()
        return
    logger.info(f"WebSocket authenticated: user_id={user_id}")

    gateway_url = os.getenv("CLAWDBOT_GATEWAY_URL", "ws://127.0.0.1:18791")
    auth_token = os.getenv("CLAWDBOT_AUTH_TOKEN")

    if not auth_token:
        logger.error("CLAWDBOT_AUTH_TOKEN not set — WebSocket rejected")
        ws.send(json.dumps({"type": "error", "message": "Server configuration error"}))
        ws.close()
        return

    async def _run():
        try:
            async with websockets.connect(gateway_url) as gw:
                logger.info(f"WebSocket connected to Gateway at {gateway_url}")

                # Handshake
                challenge = json.loads(await asyncio.wait_for(gw.recv(), timeout=10.0))
                logger.debug(f"Gateway challenge: {challenge.get('event')}")

                from services.gateways.compat import build_connect_params
                connect_params = build_connect_params(
                    auth_token=auth_token,
                    client_id="webchat",
                    client_mode="webchat",
                    platform="web",
                    user_agent="openvoice-ui-webchat/1.0.0",
                    caps=[],
                )
                await gw.send(json.dumps({
                    "type": "req",
                    "id": f"connect-{uuid.uuid4()}",
                    "method": "connect",
                    "params": connect_params,
                }))

                resp = json.loads(await asyncio.wait_for(gw.recv(), timeout=10.0))
                if resp.get("type") != "res" or not resp.get("ok"):
                    logger.error(f"Gateway handshake failed: {resp}")
                    ws.send(json.dumps({"type": "error", "message": "Gateway handshake failed"}))
                    ws.close()
                    return

                logger.info("Gateway handshake OK")
                ws.send(json.dumps({"type": "connected", "message": "Connected to OpenClaw Gateway"}))

                async def _from_client():
                    while True:
                        msg = ws.receive()
                        if not msg:
                            break
                        data = json.loads(msg)
                        if data.get("type") == "chat.send":
                            await gw.send(json.dumps({
                                "type": "req",
                                "id": f"chat-{uuid.uuid4()}",
                                "method": "chat.send",
                                "params": {
                                    "content": data.get("content", ""),
                                    "sessionKey": data.get("sessionKey", "main"),
                                },
                            }))

                async def _from_gateway():
                    while True:
                        try:
                            data = json.loads(await asyncio.wait_for(gw.recv(), timeout=120.0))
                        except asyncio.TimeoutError:
                            logger.warning("Gateway recv() timed out after 120s — closing connection")
                            ws.send(json.dumps({"type": "error", "message": "Gateway connection timed out"}))
                            return
                        if data.get("type") != "event":
                            continue
                        event = data.get("event")
                        payload = data.get("payload", {})

                        if event == "agent.message":
                            content = payload.get("content", "")
                            if content:
                                try:
                                    audio_b64 = base64.b64encode(_tts_bytes(content)).decode()
                                    ws.send(json.dumps({
                                        "type": "assistant_message",
                                        "text": content,
                                        "audio": audio_b64,
                                    }))
                                except Exception as e:
                                    logger.error(f"TTS failed in WebSocket handler: {e}")
                                    ws.send(json.dumps({"type": "assistant_message", "text": content}))

                        elif event == "agent.stream.delta":
                            ws.send(json.dumps({
                                "type": "text_delta",
                                "delta": payload.get("delta", ""),
                            }))

                        elif event == "agent.stream.end":
                            ws.send(json.dumps({"type": "stream_end"}))

                await asyncio.gather(_from_client(), _from_gateway())

        except (ConnectionRefusedError, OSError) as e:
            logger.error(f"Cannot reach Gateway at {gateway_url}: {e}")
            ws.send(json.dumps({"type": "error", "message": "Cannot connect to Gateway"}))
            ws.close()
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            ws.send(json.dumps({"type": "error", "message": "Connection error"}))
            ws.close()

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error(f"Fatal WebSocket error: {e}")


# ---------------------------------------------------------------------------
# OpenClaw Control UI — WebSocket proxy
# ---------------------------------------------------------------------------
# Accepts browser WS (Clerk-authed via __session cookie), connects to the
# internal openclaw gateway, and relays all frames bidirectionally.
# Transparently injects the gateway auth token into the connect handshake
# so the user never has to enter it.

@sock.route("/openclaw-ui")
def openclaw_ui_websocket(ws):
    """WebSocket proxy for OpenClaw Control UI behind Clerk auth."""
    from services.auth import verify_clerk_token, get_token_from_request
    token = get_token_from_request()
    user_id = verify_clerk_token(token) if token else None
    if not user_id:
        logger.warning("OpenClaw UI WebSocket rejected — no valid Clerk token")
        ws.send(json.dumps({"type": "error", "message": "Unauthorized"}))
        ws.close()
        return
    logger.info(f"OpenClaw UI WebSocket authenticated: user_id={user_id}")

    gateway_url = os.getenv("CLAWDBOT_GATEWAY_URL", "ws://127.0.0.1:18791")
    auth_token = os.getenv("CLAWDBOT_AUTH_TOKEN")

    if not auth_token:
        logger.error("CLAWDBOT_AUTH_TOKEN not set — OpenClaw UI WebSocket rejected")
        ws.send(json.dumps({"type": "error", "message": "Server configuration error"}))
        ws.close()
        return

    async def _run():
        try:
            async with websockets.connect(gateway_url, origin="http://localhost:18789") as gw:
                logger.info(f"OpenClaw UI: connected to Gateway at {gateway_url}")

                async def _from_client():
                    """Relay browser → openclaw, injecting auth token on connect."""
                    while True:
                        msg = ws.receive()
                        if not msg:
                            break
                        try:
                            data = json.loads(msg)
                            if data.get("type") == "req" and data.get("method") == "connect":
                                if "params" not in data:
                                    data["params"] = {}
                                data["params"]["auth"] = {"token": auth_token}
                                msg = json.dumps(data)
                        except (json.JSONDecodeError, TypeError):
                            pass  # Non-JSON frame — relay as-is
                        await gw.send(msg)

                async def _from_gateway():
                    """Relay openclaw → browser (raw, no transformation)."""
                    while True:
                        try:
                            msg = await asyncio.wait_for(gw.recv(), timeout=300.0)
                            ws.send(msg)
                        except asyncio.TimeoutError:
                            logger.warning("OpenClaw UI: gateway recv() timed out")
                            ws.send(json.dumps({"type": "error", "message": "Gateway timeout"}))
                            return

                await asyncio.gather(_from_client(), _from_gateway())

        except (ConnectionRefusedError, OSError) as e:
            logger.error(f"OpenClaw UI: cannot reach Gateway at {gateway_url}: {e}")
            ws.send(json.dumps({"type": "error", "message": "Cannot connect to Gateway"}))
            ws.close()
        except Exception as e:
            logger.error(f"OpenClaw UI WebSocket error: {e}")
            ws.send(json.dumps({"type": "error", "message": "Connection error"}))
            ws.close()

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error(f"OpenClaw UI: fatal WebSocket error: {e}")


# ---------------------------------------------------------------------------
# Game Library API
# ---------------------------------------------------------------------------
# Serves game catalog data to canvas pages (same-origin, no CORS needed).
# Reads /app/runtime/game-catalog.json (mounted from /mnt/game-drive/catalog.json).
# Falls back to HTTP proxy at host:6360 if file not mounted.
_GAME_CATALOG_PATH = os.getenv("GAME_CATALOG_PATH", "/app/runtime/game-catalog.json")
_GAME_SERVER_URL = os.getenv("GAME_SERVER_URL", "http://172.19.0.1:6360")

def _load_game_catalog():
    """Load catalog from mounted file. Returns list or None on failure."""
    # Check primary path, then uploads fallback (uploads/ is always mounted)
    for path in [_GAME_CATALOG_PATH, "/app/runtime/uploads/game-catalog.json"]:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            continue
    return None

@app.route("/api/games", methods=["GET"])
def games_api():
    catalog = _load_game_catalog()
    if catalog is not None:
        system = request.args.get("system", "").lower()
        genre = request.args.get("genre", "").lower()
        search = request.args.get("search", "").lower()
        games = [g for g in catalog if g.get("status") == "downloaded"]
        if system:
            games = [g for g in games if g.get("system", "").lower() == system]
        if genre:
            games = [g for g in games if g.get("genre", "").lower() == genre]
        if search:
            games = [g for g in games if
                     search in g.get("title", "").lower() or
                     search in g.get("description", "").lower()]
        return jsonify(games)
    try:
        import requests as _req
        r = _req.get(f"{_GAME_SERVER_URL}/api/games", params=dict(request.args), timeout=10)
        return Response(r.content, status=r.status_code, mimetype="application/json")
    except Exception as e:
        return jsonify({"error": f"Game server unreachable: {e}"}), 502

@app.route("/api/games/stats", methods=["GET"])
def games_stats_api():
    catalog = _load_game_catalog()
    if catalog is not None:
        total = len(catalog)
        downloaded = sum(1 for g in catalog if g.get("status") == "downloaded")
        pending = sum(1 for g in catalog if g.get("status") == "pending")
        failed = sum(1 for g in catalog if g.get("status") == "failed")
        by_system, by_genre = {}, {}
        for g in catalog:
            if g.get("status") == "downloaded":
                s = g.get("system", "unknown")
                by_system[s] = by_system.get(s, 0) + 1
                gr = g.get("genre", "unknown")
                by_genre[gr] = by_genre.get(gr, 0) + 1
        return jsonify({"total": total, "downloaded": downloaded,
                        "pending": pending, "failed": failed,
                        "bySystem": by_system, "byGenre": by_genre})
    try:
        import requests as _req
        r = _req.get(f"{_GAME_SERVER_URL}/api/stats", timeout=10)
        return Response(r.content, status=r.status_code, mimetype="application/json")
    except Exception as e:
        return jsonify({"error": f"Game server unreachable: {e}"}), 502

@app.route("/api/games/systems", methods=["GET"])
def games_systems_api():
    catalog = _load_game_catalog()
    if catalog is not None:
        systems = sorted(set(g.get("system", "") for g in catalog if g.get("system")))
        return jsonify(systems)
    try:
        import requests as _req
        r = _req.get(f"{_GAME_SERVER_URL}/api/systems", timeout=10)
        return Response(r.content, status=r.status_code, mimetype="application/json")
    except Exception as e:
        return jsonify({"error": f"Game server unreachable: {e}"}), 502


# ---------------------------------------------------------------------------
# Per-endpoint rate limits (applied after all routes are registered)
# ---------------------------------------------------------------------------
# limiter.limit() returns a wrapped function — must assign it back into
# app.view_functions or the limit is silently discarded.
_limiter = getattr(app, 'limiter', None)
if _limiter:
    for _endpoint, _rate in {
        'conversation.conversation': '30/minute',
        'conversation.tts_generate': '10/minute',
        'conversation.tts_preview':  '10/minute',
        'upload_file':               '5/minute',
        'groq_stt':                  '60/minute',
        'local_stt':                 '60/minute',
        'external_stt':              '60/minute',
    }.items():
        _view_fn = app.view_functions.get(_endpoint)
        if _view_fn:
            app.view_functions[_endpoint] = _limiter.limit(_rate)(_view_fn)
        else:
            logger.warning("Rate limit: endpoint %r not found — skipping", _endpoint)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))

    # Clean SIGTERM shutdown so systemd stop/restart works correctly.
    # Restart=on-failure only triggers on non-zero exit — os._exit(0) prevents that.
    def _handle_sigterm(signum, frame):
        logger.info("SIGTERM received — shutting down.")
        os._exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    try:
        from services.gateways.openclaw import OPENCLAW_TESTED_VERSION
        _oc_ver = OPENCLAW_TESTED_VERSION
    except ImportError:
        _oc_ver = "unknown"

    logger.info(f"OpenVoiceUI starting on port {port}")
    logger.info(f"  Frontend  → http://localhost:{port}/")
    logger.info(f"  Health    → http://localhost:{port}/health/ready")
    logger.info(f"  Admin     → http://localhost:{port}/src/admin.html")
    logger.info(f"  Gateway   → {os.getenv('CLAWDBOT_GATEWAY_URL', 'ws://127.0.0.1:18791')}")
    logger.info(f"  Tested OpenClaw version: {_oc_ver}")

    host = os.getenv("HOST", "127.0.0.1")  # Docker sets HOST=0.0.0.0; VPS stays loopback
    app.run(host=host, port=port, debug=False, threaded=True)
