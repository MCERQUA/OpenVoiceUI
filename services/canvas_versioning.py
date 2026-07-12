"""
Canvas Page Versioning — automatic version history for canvas pages.

Watches the canvas-pages directory for file changes and saves previous
versions to a .versions/ subdirectory. This catches ALL writes regardless
of source (agent tool, API, manual edit).

Usage:
    from services.canvas_versioning import start_version_watcher, list_versions, restore_version

Version files are stored as:
    canvas-pages/.versions/<page-stem>.<unix-timestamp>.html

Auto-cleanup keeps the last MAX_VERSIONS_PER_PAGE versions per page.
"""

import hashlib
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from services.paths import CANVAS_PAGES_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_VERSIONS_PER_PAGE = 20          # Keep last N versions per page
CHECK_INTERVAL_SECONDS = 15         # How often to scan for changes
VERSIONS_DIRNAME = '.versions'      # Subdirectory name inside canvas-pages
# Which asset types get version history. HTML pages + small text/vector assets
# (svg icons, json, css, js, md). Large binaries (png/jpg/glb/mp4/pdf) are
# deliberately EXCLUDED — versioning them would bloat .versions/ and the disk
# (2026-07-08: a non-HTML svg icon vanished on wilson with NO version safety net —
# versioning was *.html-only; this closes that gap without the binary-bloat risk).
# Files above MAX_VERSION_BYTES are skipped regardless of type.
VERSIONED_SUFFIXES = {'.html', '.svg', '.json', '.css', '.js', '.md', '.txt', '.xml', '.csv', '.yaml', '.yml'}
MAX_VERSION_BYTES = 2_000_000       # 2 MB — skip versioning files larger than this

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_file_hashes: dict[str, str] = {}    # filename -> content hash
_file_contents: dict[str, bytes] = {}  # filename -> last-known content (for saving)
_watcher_thread: threading.Thread | None = None
_stop_event = threading.Event()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _versions_dir() -> Path:
    """Return the .versions/ directory path, creating it if needed."""
    vdir = CANVAS_PAGES_DIR / VERSIONS_DIRNAME
    vdir.mkdir(parents=True, exist_ok=True)
    return vdir


def _content_hash(data: bytes) -> str:
    """SHA-256 hash of file content."""
    return hashlib.sha256(data).hexdigest()


def _save_version(filename: str, old_content: bytes) -> Path | None:
    """Save old_content as a timestamped version file."""
    try:
        p = Path(filename)
        stem = p.stem
        suffix = p.suffix or '.html'   # preserve original extension (svg/json/… not just html)
        timestamp = int(time.time())
        version_name = f'{stem}.{timestamp}{suffix}'
        version_path = _versions_dir() / version_name
        version_path.write_bytes(old_content)
        logger.info(f'Canvas version saved: {version_name} ({len(old_content)} bytes)')
        _cleanup_versions(stem, suffix)
        return version_path
    except Exception as exc:
        logger.error(f'Failed to save canvas version for {filename}: {exc}')
        return None


def _cleanup_versions(page_stem: str, suffix: str = '.html') -> None:
    """Keep only the latest MAX_VERSIONS_PER_PAGE versions for a page+type.

    Keyed on (stem, suffix) so foo.html and foo.svg keep independent histories
    and don't prune each other.
    """
    vdir = _versions_dir()
    versions = sorted(
        vdir.glob(f'{page_stem}.*{suffix}'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old_version in versions[MAX_VERSIONS_PER_PAGE:]:
        try:
            old_version.unlink()
            logger.debug(f'Pruned old version: {old_version.name}')
        except Exception:
            pass


def _initial_scan() -> None:
    """Scan all existing pages and record their hashes (no versioning on startup)."""
    if not CANVAS_PAGES_DIR.exists():
        return
    for page_path in CANVAS_PAGES_DIR.iterdir():
        if not page_path.is_file() or page_path.suffix.lower() not in VERSIONED_SUFFIXES:
            continue
        try:
            if page_path.stat().st_size > MAX_VERSION_BYTES:
                continue
            content = page_path.read_bytes()
            _file_hashes[page_path.name] = _content_hash(content)
            _file_contents[page_path.name] = content
        except Exception as exc:
            logger.debug(f'Initial scan: could not read {page_path.name}: {exc}')


def _check_for_changes() -> None:
    """Scan canvas-pages for modified files and save versions."""
    if not CANVAS_PAGES_DIR.exists():
        return

    current_files = set()
    for page_path in CANVAS_PAGES_DIR.iterdir():
        if not page_path.is_file() or page_path.suffix.lower() not in VERSIONED_SUFFIXES:
            continue
        filename = page_path.name
        try:
            if page_path.stat().st_size > MAX_VERSION_BYTES:
                continue
        except OSError:
            continue
        current_files.add(filename)
        try:
            content = page_path.read_bytes()
            new_hash = _content_hash(content)

            if filename in _file_hashes:
                if new_hash != _file_hashes[filename]:
                    # File changed — save the OLD content as a version
                    old_content = _file_contents.get(filename)
                    if old_content:
                        _save_version(filename, old_content)
                    _file_hashes[filename] = new_hash
                    _file_contents[filename] = content
            else:
                # New file — just record it (no previous version to save)
                _file_hashes[filename] = new_hash
                _file_contents[filename] = content

        except Exception as exc:
            logger.debug(f'Version check: could not read {filename}: {exc}')

    # Note: we don't remove deleted files from tracking — they might come back


# ---------------------------------------------------------------------------
# Background watcher
# ---------------------------------------------------------------------------

def _watcher_loop() -> None:
    """Background thread that periodically checks for file changes."""
    logger.info(f'Canvas version watcher started (interval={CHECK_INTERVAL_SECONDS}s, max_versions={MAX_VERSIONS_PER_PAGE})')
    _initial_scan()

    while not _stop_event.is_set():
        try:
            _check_for_changes()
        except Exception as exc:
            logger.error(f'Canvas version watcher error: {exc}')
        _stop_event.wait(CHECK_INTERVAL_SECONDS)

    logger.info('Canvas version watcher stopped')


def start_version_watcher() -> None:
    """Start the background version watcher thread."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        logger.debug('Canvas version watcher already running')
        return
    _stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_watcher_loop,
        name='canvas-version-watcher',
        daemon=True,
    )
    _watcher_thread.start()


def stop_version_watcher() -> None:
    """Stop the background version watcher thread."""
    _stop_event.set()
    if _watcher_thread:
        _watcher_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Public API (used by canvas.py routes)
# ---------------------------------------------------------------------------

def list_versions(page_id: str) -> list[dict]:
    """List all saved versions for a page, newest first."""
    vdir = _versions_dir()
    versions = []
    for vpath in sorted(vdir.glob(f'{page_id}.*.html'), key=lambda p: p.stat().st_mtime, reverse=True):
        # Parse timestamp from filename: page-id.1709510400.html
        parts = vpath.stem.rsplit('.', 1)
        if len(parts) == 2:
            try:
                ts = int(parts[1])
            except ValueError:
                ts = int(vpath.stat().st_mtime)
        else:
            ts = int(vpath.stat().st_mtime)

        versions.append({
            'filename': vpath.name,
            'timestamp': ts,
            'iso': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)),
            'size': vpath.stat().st_size,
        })
    return versions


def restore_version(page_id: str, timestamp: int) -> bool:
    """Restore a specific version, saving the current as a new version first."""
    version_file = _versions_dir() / f'{page_id}.{timestamp}.html'
    current_file = CANVAS_PAGES_DIR / f'{page_id}.html'

    if not version_file.exists():
        logger.error(f'Version not found: {version_file}')
        return False

    # Save current as a version before restoring
    if current_file.exists():
        current_content = current_file.read_bytes()
        _save_version(current_file.name, current_content)

    # Restore the old version
    restored_content = version_file.read_bytes()
    current_file.write_bytes(restored_content)

    # Update tracking
    _file_hashes[current_file.name] = _content_hash(restored_content)
    _file_contents[current_file.name] = restored_content

    logger.info(f'Restored canvas page {page_id} to version {timestamp}')
    return True


def get_version_content(page_id: str, timestamp: int) -> bytes | None:
    """Get the content of a specific version."""
    version_file = _versions_dir() / f'{page_id}.{timestamp}.html'
    if version_file.exists():
        return version_file.read_bytes()
    return None
