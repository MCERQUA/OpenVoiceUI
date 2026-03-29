"""Canonical path constants for all runtime and asset directories."""
import os
from pathlib import Path

APP_ROOT = Path(__file__).parent.parent

# Runtime data (gitignored, docker-mounted)
RUNTIME_DIR = APP_ROOT / "runtime"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
CANVAS_PAGES_DIR = Path(os.getenv("CANVAS_PAGES_DIR", str(RUNTIME_DIR / "canvas-pages")))
KNOWN_FACES_DIR = RUNTIME_DIR / "known_faces"
MUSIC_DIR = RUNTIME_DIR / "music"
GENERATED_MUSIC_DIR = RUNTIME_DIR / "generated_music"
FACES_DIR = RUNTIME_DIR / "faces"
TRANSCRIPTS_DIR = RUNTIME_DIR / "transcripts"
DB_PATH = RUNTIME_DIR / "usage.db"
CANVAS_MANIFEST_PATH = RUNTIME_DIR / "canvas-manifest.json"
VOICE_CLONES_DIR = RUNTIME_DIR / "voice-clones"
VOICE_SESSION_FILE = str(RUNTIME_DIR / ".voice-session-counter")
ACTIVE_PROFILE_FILE = RUNTIME_DIR / "profiles" / ".active-profile"
WORKSPACE_DIR = Path(os.getenv('WORKSPACE_DIR', str(RUNTIME_DIR / 'workspace')))

# Bundled assets (git-tracked, stay at root)
SOUNDS_DIR = APP_ROOT / "sounds"
STATIC_DIR = APP_ROOT / "static"
DEFAULT_PAGES_DIR = APP_ROOT / "default-pages"
