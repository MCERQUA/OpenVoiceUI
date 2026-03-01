# routes package — Flask Blueprints for ai-eyes2 (Phase 2 refactor)

import os
from pathlib import Path

# Base directory for all persistent data (uploads, music, faces, etc.).
# Default: app root (backward-compatible with non-Docker installs).
# Docker sets DATA_DIR=/app/data via docker-compose environment.
APP_ROOT = Path(__file__).parent.parent
DATA_DIR = Path(os.getenv('DATA_DIR', str(APP_ROOT)))
