"""
routes/custom_faces.py — Custom Face management API.

Custom faces are HTML pages stored in runtime/faces/ that render inside
the face-box via an iframe. They receive mood/amplitude/theme data via
postMessage and survive app updates (runtime dir is gitignored).

Endpoints:
  GET    /api/custom-faces              — list custom faces (auto-syncs manifest)
  POST   /api/custom-faces              — create or update a custom face
  GET    /api/custom-faces/<face_id>    — get single face metadata
  DELETE /api/custom-faces/<face_id>    — archive a custom face (.bak)
  GET    /api/custom-faces/template            — return the starter face template
  POST   /api/custom-faces/promote             — promote a canvas page to a face
  GET    /faces/custom/<filename>       — serve face HTML for iframe loading
"""

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from services.paths import FACES_DIR, FACE_MANIFEST_PATH, CANVAS_PAGES_DIR, APP_ROOT

logger = logging.getLogger(__name__)

custom_faces_bp = Blueprint("custom_faces", __name__)

# ── Manifest management ──────────────────────────────────────────────────────

_manifest_lock = threading.Lock()
_manifest_cache = None
_manifest_mtime = 0
_last_sync = 0
_SYNC_THROTTLE = 60  # seconds

_META_RE = {
    "name": re.compile(r'<meta\s+name=["\']face-name["\']\s+content=["\']([^"\']+)["\']', re.I),
    "description": re.compile(r'<meta\s+name=["\']face-description["\']\s+content=["\']([^"\']+)["\']', re.I),
    "author": re.compile(r'<meta\s+name=["\']face-author["\']\s+content=["\']([^"\']+)["\']', re.I),
}


def _extract_face_meta(html_path):
    """Extract face metadata from <meta> tags in the first 2KB of an HTML file."""
    meta = {}
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            head = f.read(2048)
        for key, pattern in _META_RE.items():
            m = pattern.search(head)
            if m:
                meta[key] = m.group(1).strip()
    except (OSError, UnicodeDecodeError):
        pass
    return meta


def _sanitize_filename(name):
    """Sanitize a face filename: strip traversal, ensure .html extension."""
    name = os.path.basename(name)
    name = re.sub(r'[^\w\-.]', '-', name)
    if not name.endswith('.html'):
        name = re.sub(r'\.[^.]+$', '', name) + '.html'
    return name


def _slug_from_title(title):
    """Generate a slug filename from a title string."""
    slug = re.sub(r'[^\w\s-]', '', title.lower().strip())
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return (slug or 'custom-face') + '.html'


def load_face_manifest():
    """Load the face manifest, using mtime-based caching."""
    global _manifest_cache, _manifest_mtime

    with _manifest_lock:
        try:
            mtime = FACE_MANIFEST_PATH.stat().st_mtime
        except OSError:
            mtime = 0

        if _manifest_cache is not None and mtime == _manifest_mtime:
            return _manifest_cache

        if FACE_MANIFEST_PATH.exists():
            try:
                data = json.loads(FACE_MANIFEST_PATH.read_text(encoding="utf-8"))
                _manifest_cache = data
                _manifest_mtime = mtime
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load face manifest: %s", e)

        # Return empty manifest
        empty = {"version": 1, "last_updated": None, "faces": {}}
        _manifest_cache = empty
        return empty


def save_face_manifest(manifest):
    """Atomically save the face manifest."""
    global _manifest_cache, _manifest_mtime

    manifest["last_updated"] = datetime.now(timezone.utc).isoformat()
    FACES_DIR.mkdir(parents=True, exist_ok=True)

    tmp = FACE_MANIFEST_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FACE_MANIFEST_PATH)

    with _manifest_lock:
        _manifest_cache = manifest
        try:
            _manifest_mtime = FACE_MANIFEST_PATH.stat().st_mtime
        except OSError:
            _manifest_mtime = 0


def sync_face_manifest(force=False):
    """Scan runtime/faces/ for HTML files and update manifest."""
    global _last_sync

    now = time.time()
    if not force and (now - _last_sync) < _SYNC_THROTTLE:
        return load_face_manifest()

    _last_sync = now
    FACES_DIR.mkdir(parents=True, exist_ok=True)

    manifest = load_face_manifest()
    faces = manifest.get("faces", {})
    changed = False

    # Discover new files
    on_disk = set()
    for html_file in sorted(FACES_DIR.glob("*.html")):
        face_id = html_file.stem
        on_disk.add(face_id)

        if face_id not in faces:
            meta = _extract_face_meta(html_file)
            stat = html_file.stat()
            faces[face_id] = {
                "filename": html_file.name,
                "name": meta.get("name", face_id.replace("-", " ").title()),
                "description": meta.get("description", ""),
                "author": meta.get("author", ""),
                "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
            changed = True
            logger.info("Discovered custom face: %s", face_id)
        else:
            # Update metadata from file if it changed
            stat = html_file.stat()
            file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            if faces[face_id].get("modified") != file_mtime:
                meta = _extract_face_meta(html_file)
                if meta.get("name"):
                    faces[face_id]["name"] = meta["name"]
                if meta.get("description"):
                    faces[face_id]["description"] = meta["description"]
                if meta.get("author"):
                    faces[face_id]["author"] = meta["author"]
                faces[face_id]["modified"] = file_mtime
                changed = True

    # Remove entries for files that no longer exist
    removed = set(faces.keys()) - on_disk
    for face_id in removed:
        del faces[face_id]
        changed = True
        logger.info("Removed stale face from manifest: %s", face_id)

    if changed:
        manifest["faces"] = faces
        save_face_manifest(manifest)

    return manifest


def add_face_to_manifest(face_id, filename, name, description="", author=""):
    """Add or update a single face entry in the manifest."""
    manifest = load_face_manifest()
    faces = manifest.get("faces", {})
    now = datetime.now(timezone.utc).isoformat()

    if face_id in faces:
        faces[face_id]["name"] = name
        faces[face_id]["description"] = description
        faces[face_id]["modified"] = now
        if author:
            faces[face_id]["author"] = author
    else:
        faces[face_id] = {
            "filename": filename,
            "name": name,
            "description": description,
            "author": author,
            "created": now,
            "modified": now,
        }

    manifest["faces"] = faces
    save_face_manifest(manifest)
    return faces[face_id]


# ── API Endpoints ─────────────────────────────────────────────────────────────

@custom_faces_bp.route("/api/custom-faces", methods=["GET"])
def list_custom_faces():
    """Return the face manifest, auto-syncing with filesystem."""
    force = request.args.get("sync") == "1"
    manifest = sync_face_manifest(force=force)
    return jsonify(manifest)


@custom_faces_bp.route("/api/custom-faces", methods=["POST"])
def create_custom_face():
    """Create or update a custom face HTML file.

    Body: {
        "html": "<!DOCTYPE html>...",
        "filename": "my-face.html" (optional, derived from title),
        "title": "My Face" (optional),
        "description": "A cool face" (optional)
    }
    """
    data = request.get_json(silent=True)
    if not data or not data.get("html"):
        return jsonify({"error": "Missing 'html' field"}), 400

    html = data["html"]
    title = data.get("title", "Custom Face")
    description = data.get("description", "")

    # Determine filename
    filename = data.get("filename")
    if filename:
        filename = _sanitize_filename(filename)
    else:
        filename = _slug_from_title(title)

    face_id = filename.rsplit(".", 1)[0]

    # Write file
    FACES_DIR.mkdir(parents=True, exist_ok=True)
    dest = FACES_DIR / filename
    dest.write_text(html, encoding="utf-8")

    # Extract metadata from the HTML itself (overrides title/description if present)
    meta = _extract_face_meta(dest)
    name = meta.get("name", title)
    desc = meta.get("description", description)
    author = meta.get("author", "")

    # Update manifest
    entry = add_face_to_manifest(face_id, filename, name, desc, author)

    return jsonify({
        "ok": True,
        "filename": filename,
        "face_id": face_id,
        "url": f"/faces/custom/{filename}",
        "name": name,
        "description": desc,
    }), 201


@custom_faces_bp.route("/api/custom-faces/<face_id>", methods=["GET"])
def get_custom_face(face_id):
    """Get metadata + source code for a single custom face.

    ?source=1 includes the full HTML source in the response.
    """
    manifest = load_face_manifest()
    face = manifest.get("faces", {}).get(face_id)
    if not face:
        return jsonify({"error": f"Face '{face_id}' not found"}), 404

    result = {"face_id": face_id, **face}

    # Include source HTML if requested
    if request.args.get("source") == "1":
        filename = face.get("filename", face_id + ".html")
        src_path = FACES_DIR / filename
        try:
            result["source"] = src_path.read_text(encoding="utf-8")
        except OSError:
            result["source"] = None

    return jsonify(result)


@custom_faces_bp.route("/api/custom-faces/<face_id>", methods=["DELETE"])
def delete_custom_face(face_id):
    """Archive a custom face by renaming to .bak."""
    manifest = load_face_manifest()
    face = manifest.get("faces", {}).get(face_id)
    if not face:
        return jsonify({"error": f"Face '{face_id}' not found"}), 404

    filename = face.get("filename", face_id + ".html")
    src = FACES_DIR / filename
    if src.exists():
        bak = src.with_suffix(".html.bak")
        src.rename(bak)
        logger.info("Archived custom face: %s -> %s", src.name, bak.name)

    # Remove from manifest
    del manifest["faces"][face_id]
    save_face_manifest(manifest)

    return jsonify({"ok": True, "archived": str(bak) if src.exists() else None})


@custom_faces_bp.route("/api/custom-faces/template", methods=["GET"])
def get_face_template():
    """Return the starter face template HTML."""
    template_path = APP_ROOT / "default-faces" / "template.html"
    if not template_path.exists():
        return jsonify({"error": "Template not found"}), 404
    return template_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}


# ── Performance attestation gate (Mike, 2026-08-18) ─────────────────────────
# A face runs PERMANENTLY beside voice/STT/TTS. "Done" is not done until it is
# measured running efficiently AT FULL LOAD on a real browser. The probe (see
# /mnt/shared-skills/custom-faces/SKILL.md) forces the speaking state, samples
# per-second fps for >=30s in the viewer's browser, and POSTs the result here.
# promote_canvas_to_face() then REFUSES (HTTP 428) unless a report exists that:
#   - matches the CURRENT page content (sha256 — edit the page, re-test),
#   - is fresh (<7 days),
#   - has fps_low (5th-percentile of per-second samples) >= FACE_MIN_FPS (50).
# {"force": true} overrides with a logged warning — deliberate, never silent.
FACE_MIN_FPS = int(os.environ.get("FACE_MIN_FPS", "50"))
PERF_DIR_NAME = ".perf"


def _perf_dir():
    d = FACES_DIR / PERF_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _page_sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@custom_faces_bp.route("/api/custom-faces/perf-report", methods=["POST"])
def perf_report():
    """Store a full-load fps attestation for a canvas page or face.

    Body: { "page_id": "...", "samples": [fps,...>=20], "user_agent": optional }
    fps_low = 5th percentile of samples — robust against one clean second.
    """
    data = request.get_json(silent=True) or {}
    page_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(data.get("page_id", "")))
    samples = data.get("samples")
    if not page_id or not isinstance(samples, list) or len(samples) < 20:
        return jsonify({"error": "Need page_id + >=20 per-second fps samples "
                                 "(measure >=30s at FULL LOAD — forced speaking state)"}), 400
    try:
        vals = sorted(float(x) for x in samples)
    except (TypeError, ValueError):
        return jsonify({"error": "samples must be numbers"}), 400
    fps_low = vals[max(0, int(len(vals) * 0.05) - 1)] if len(vals) else 0.0
    src = CANVAS_PAGES_DIR / (page_id + ".html")
    if not src.exists():
        src = FACES_DIR / (page_id + ".html")
    sha = _page_sha(src)
    report = {
        "page_id": page_id,
        "fps_low": round(fps_low, 1),
        "fps_median": round(vals[len(vals) // 2], 1),
        "samples": len(vals),
        "content_sha256": sha,
        "user_agent": str(data.get("user_agent", request.headers.get("User-Agent", "")))[:200],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    (_perf_dir() / (page_id + ".json")).write_text(json.dumps(report, indent=1))
    passed = fps_low >= FACE_MIN_FPS
    return jsonify({"ok": True, "passed": passed, "fps_low": report["fps_low"],
                    "threshold": FACE_MIN_FPS,
                    "verdict": "PASS — promotable" if passed else
                               f"FAIL — fps_low {report['fps_low']} < {FACE_MIN_FPS}; optimize and re-test"})


def _check_perf_attestation(page_id, src_path):
    """Return (ok, http_status, payload) for the promote gate."""
    rp = _perf_dir() / (page_id + ".json")
    how = ("Run the full-load probe from /mnt/shared-skills/custom-faces/SKILL.md while the page "
           "is open in the user's browser (it POSTs to /api/custom-faces/perf-report), then retry.")
    if not rp.exists():
        return False, 428, {"error": "perf attestation required — this face has never been measured",
                            "threshold_fps": FACE_MIN_FPS, "how": how}
    try:
        rep = json.loads(rp.read_text())
    except (OSError, ValueError):
        return False, 428, {"error": "perf attestation unreadable — re-run the probe", "how": how}
    if rep.get("content_sha256") != _page_sha(src_path):
        return False, 428, {"error": "perf attestation is for an OLDER version of this page — "
                                     "the content changed since it was measured; re-test",
                            "measured_at": rep.get("recorded_at"), "how": how}
    try:
        age_d = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(rep["recorded_at"])).days
    except (KeyError, ValueError):
        age_d = 999
    if age_d > 7:
        return False, 428, {"error": f"perf attestation is {age_d} days old — re-test", "how": how}
    if float(rep.get("fps_low", 0)) < FACE_MIN_FPS:
        return False, 428, {"error": f"face FAILED the perf bar: fps_low {rep.get('fps_low')} < "
                                     f"{FACE_MIN_FPS} at full load — optimize (lower particle "
                                     "counts / cap DPR / pause-when-hidden), re-test, retry",
                            "report": rep}
    return True, 200, {"perf": {"fps_low": rep.get("fps_low"), "measured_at": rep.get("recorded_at")}}


@custom_faces_bp.route("/api/custom-faces/promote", methods=["POST"])
def promote_canvas_to_face():
    """Promote a canvas page to a custom face by copying it.

    Body: { "canvas_page_id": "my-page" }
    """
    data = request.get_json(silent=True)
    if not data or not data.get("canvas_page_id"):
        return jsonify({"error": "Missing 'canvas_page_id'"}), 400

    page_id = data["canvas_page_id"]
    src = CANVAS_PAGES_DIR / (page_id + ".html")
    if not src.exists():
        return jsonify({"error": f"Canvas page '{page_id}' not found"}), 404

    # NOT DONE UNTIL MEASURED: refuse promotion without a fresh full-load perf pass.
    if not data.get("force"):
        ok, status, payload = _check_perf_attestation(page_id, src)
        if not ok:
            return jsonify(payload), status
    else:
        logger.warning("custom-faces: promote FORCED without perf attestation: %s", page_id)

    # Copy to faces dir
    FACES_DIR.mkdir(parents=True, exist_ok=True)
    dest = FACES_DIR / src.name
    shutil.copy2(src, dest)

    # Extract metadata and register
    meta = _extract_face_meta(dest)
    name = meta.get("name", page_id.replace("-", " ").title())
    desc = meta.get("description", "Promoted from canvas page")
    author = meta.get("author", "")
    face_id = page_id

    entry = add_face_to_manifest(face_id, src.name, name, desc, author)

    return jsonify({
        "ok": True,
        "face_id": face_id,
        "filename": src.name,
        "url": f"/faces/custom/{src.name}",
        "name": name,
        "promoted_from": page_id,
    }), 201


# ── Serve face HTML files ─────────────────────────────────────────────────────

@custom_faces_bp.route("/faces/custom/<path:filename>")
def serve_custom_face(filename):
    """Serve a custom face HTML file from runtime/faces/."""
    # Path traversal protection
    safe = os.path.basename(filename)
    if safe != filename or ".." in filename:
        return jsonify({"error": "Invalid path"}), 400

    FACES_DIR.mkdir(parents=True, exist_ok=True)
    return send_from_directory(str(FACES_DIR), safe)
