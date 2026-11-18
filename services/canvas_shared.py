"""Seed shared canvas modules (canvas-shared/*) into <canvas-pages>/_shared/.

Canvas pages import these from /pages/_shared/<file> (for example html-surfaces.js, used by the
bundled threejs-html-surfaces skill). They are app-owned infrastructure, like the default pages:
a copy that differs from the shipped one is backed up to _shared/.backups/ and replaced.
Best-effort: a file that cannot be written is logged and skipped, never fatal to boot.
"""
import logging
import time
from pathlib import Path

from services.safe_open import Anchored

logger = logging.getLogger(__name__)


def seed_canvas_shared(src_dir: Path, pages_dir: Path) -> list:
    """Copy every file in src_dir into pages_dir/_shared/. Returns the names written."""
    written = []
    if not src_dir.is_dir():
        return written
    Path(pages_dir).mkdir(parents=True, exist_ok=True)   # the mount itself (trusted path)
    for src in sorted(src_dir.iterdir()):
        if not src.is_file():
            continue
        # canvas-pages is agent-writable: every read/write below the pages_dir MOUNT
        # is a no-follow walk (a symlink planted at _shared/<name> or .backups is
        # refused instead of written through; PR #597 review 2)
        dest = Anchored(pages_dir, f'_shared/{src.name}')
        try:
            shipped = src.read_bytes()
            if dest.is_file():
                current = dest.read_bytes()
                if current == shipped:
                    continue
                Anchored(pages_dir, f'_shared/.backups/{src.name}.{int(time.time())}').write_bytes(
                    current, create_dirs=True)
            dest.write_bytes(shipped, create_dirs=True)
            written.append(src.name)
            logger.info("Seeded shared canvas module: _shared/%s", src.name)
        except (PermissionError, OSError) as exc:
            logger.warning("Skipped seeding shared canvas module %s (non-fatal): %s", src.name, exc)
    return written
