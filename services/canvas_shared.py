"""Seed shared canvas modules (canvas-shared/*) into <canvas-pages>/_shared/.

Canvas pages import these from /pages/_shared/<file> (for example html-surfaces.js, used by the
bundled threejs-html-surfaces skill). They are app-owned infrastructure, like the default pages:
a copy that differs from the shipped one is backed up to _shared/.backups/ and replaced.
Best-effort: a file that cannot be written is logged and skipped, never fatal to boot.
"""
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def seed_canvas_shared(src_dir: Path, pages_dir: Path) -> list:
    """Copy every file in src_dir into pages_dir/_shared/. Returns the names written."""
    written = []
    if not src_dir.is_dir():
        return written
    dest_dir = pages_dir / '_shared'
    for src in sorted(src_dir.iterdir()):
        if not src.is_file():
            continue
        dest = dest_dir / src.name
        try:
            if dest.exists():
                if dest.read_bytes() == src.read_bytes():
                    continue
                backup_dir = dest_dir / '.backups'
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup_dir / f"{src.name}.{int(time.time())}")
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            written.append(src.name)
            logger.info("Seeded shared canvas module: _shared/%s", src.name)
        except (PermissionError, OSError) as exc:
            logger.warning("Skipped seeding shared canvas module %s (non-fatal): %s", src.name, exc)
    return written
