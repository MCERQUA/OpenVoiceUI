"""
Workspace file browser — read-only access to the openclaw workspace directory.

GET /api/workspace/browse?path=<relative>   → directory listing (files + dirs)
GET /api/workspace/file?path=<relative>     → file content (text, 500 KB limit)
GET /api/workspace/raw?path=<relative>      → raw file (images, PDFs, etc.)
GET /api/workspace/tree?path=<relative>     → dirs only (for sidebar tree)
"""

import os
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file
from services.paths import RUNTIME_DIR

workspace_bp = Blueprint('workspace', __name__)

WORKSPACE_DIR = Path(os.getenv('WORKSPACE_DIR', str(RUNTIME_DIR / 'workspace')))

MAX_FILE_SIZE = 500 * 1024  # 500 KB preview limit

TEXT_EXTENSIONS = {
    '.md', '.txt', '.json', '.csv', '.yaml', '.yml',
    '.html', '.js', '.ts', '.py', '.sh', '.toml', '.log',
    '.env', '.gitignore', '.sql', '.xml', '.ini', '.cfg',
    '.lock', '.rst', '.njk', '.jsx', '.tsx',
}

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico', '.bmp'}
MEDIA_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.m4a', '.mp4', '.webm', '.mov', '.pdf'}
RAW_EXTENSIONS = IMAGE_EXTENSIONS | MEDIA_EXTENSIONS
MAX_RAW_SIZE = 20 * 1024 * 1024  # 20 MB limit for raw serving

HIDDEN_PREFIXES = {'.git', '__pycache__', 'node_modules', '.venv', 'venv'}

# Workspace-name → writable runtime path mapping
# workspace/Uploads/foo → RUNTIME_DIR/uploads/foo (writable)
# workspace/Agent/...   → read-only, no mapping
_WRITABLE_MAP = {
    'Agent':        RUNTIME_DIR / 'workspace' / 'Agent',
    'Uploads':      RUNTIME_DIR / 'uploads',
    'Canvas':       RUNTIME_DIR / 'canvas-pages',
    'Music':        RUNTIME_DIR / 'music',
    'AI-Music':     RUNTIME_DIR / 'generated_music',
    'Transcripts':  RUNTIME_DIR / 'transcripts',
    'Voice-Clones': RUNTIME_DIR / 'voice-clones',
    'Icons':        RUNTIME_DIR / 'icons',
}


def _writable_path(rel: str):
    """Map a workspace-relative path to its writable runtime path, or None if read-only."""
    parts = rel.strip('/').split('/', 1)
    if not parts or not parts[0]:
        return None
    prefix = parts[0]
    base = _WRITABLE_MAP.get(prefix)
    if base is None:
        return None
    sub = parts[1] if len(parts) > 1 else ''
    target = (base / sub).resolve()
    # Safety: ensure it stays within the writable base
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target


def _resolve(rel: str):
    """Resolve relative path safely within WORKSPACE_DIR. Returns None on traversal."""
    try:
        p = (WORKSPACE_DIR / rel.lstrip('/')).resolve()
        p.relative_to(WORKSPACE_DIR.resolve())  # raises ValueError if outside
        return p
    except (ValueError, Exception):
        return None


def _entry(item: Path, count_children: bool = True) -> dict:
    """Build a single directory entry dict."""
    try:
        st = item.stat()
    except (PermissionError, OSError):
        return {
            'name': item.name,
            'type': 'dir' if item.is_dir() else 'file',
            'size': 0,
            'modified': 0,
            'ext': '',
        }
    e = {
        'name': item.name,
        'type': 'dir' if item.is_dir() else 'file',
        'size': st.st_size if item.is_file() else 0,
        'modified': int(st.st_mtime),
        'ext': item.suffix.lower() if item.is_file() else '',
    }
    if item.is_dir() and count_children:
        try:
            children = [c for c in item.iterdir() if not c.name.startswith('.')]
            e['children'] = len(children)
        except PermissionError:
            e['children'] = 0
    return e


def _is_hidden(name: str) -> bool:
    return name.startswith('.') or name in HIDDEN_PREFIXES


@workspace_bp.route('/api/workspace/browse')
def browse():
    rel = request.args.get('path', '')
    target = _resolve(rel)

    if target is None:
        return jsonify({'error': 'Invalid path'}), 400

    if not WORKSPACE_DIR.exists():
        return jsonify({'path': '', 'entries': [], 'unavailable': True})

    if not target.exists():
        return jsonify({'error': 'Path not found'}), 404

    if not target.is_dir():
        return jsonify({'error': 'Not a directory'}), 400

    try:
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if _is_hidden(item.name):
                continue
            try:
                entries.append(_entry(item))
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403

    try:
        rel_path = str(target.relative_to(WORKSPACE_DIR.resolve()))
        if rel_path == '.':
            rel_path = ''
    except ValueError:
        rel_path = ''

    return jsonify({'path': rel_path, 'entries': entries})


@workspace_bp.route('/api/workspace/tree')
def tree():
    """Return only directories for the sidebar tree (one level deep)."""
    rel = request.args.get('path', '')
    target = _resolve(rel)

    if not WORKSPACE_DIR.exists():
        return jsonify({'dirs': []})

    if target is None or not target.exists() or not target.is_dir():
        return jsonify({'dirs': []})

    try:
        dirs = []
        for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if item.is_dir() and not _is_hidden(item.name):
                try:
                    rel_p = str(item.relative_to(WORKSPACE_DIR.resolve()))
                except ValueError:
                    continue
                # Check if this dir has subdirectories (for expand arrow)
                try:
                    has_subdirs = any(
                        c.is_dir() and not _is_hidden(c.name)
                        for c in item.iterdir()
                    )
                except PermissionError:
                    has_subdirs = False
                dirs.append({'name': item.name, 'path': rel_p, 'has_subdirs': has_subdirs})
        return jsonify({'dirs': dirs})
    except Exception:
        return jsonify({'dirs': []})


@workspace_bp.route('/api/workspace/file')
def read_file():
    rel = request.args.get('path', '')
    target = _resolve(rel)

    if target is None:
        return jsonify({'error': 'Invalid path'}), 400

    if not target.exists():
        return jsonify({'error': 'File not found'}), 404

    if target.is_dir():
        return jsonify({'error': 'Is a directory'}), 400

    size = target.stat().st_size
    ext = target.suffix.lower()

    if size > MAX_FILE_SIZE:
        return jsonify({
            'error': f'File too large for preview ({size // 1024} KB). Max is 500 KB.',
            'size': size,
            'too_large': True,
        })

    if ext not in TEXT_EXTENSIONS and ext != '':
        return jsonify({'error': 'Binary file — no preview available', 'binary': True})

    try:
        content = target.read_text(encoding='utf-8', errors='replace')
        return jsonify({
            'path': rel,
            'name': target.name,
            'ext': ext,
            'size': size,
            'content': content,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_bp.route('/api/workspace/raw')
def raw_file():
    """Serve a raw file (images, audio, video, PDF) with correct content-type."""
    rel = request.args.get('path', '')
    target = _resolve(rel)

    if target is None:
        return jsonify({'error': 'Invalid path'}), 400

    if not target.exists():
        return jsonify({'error': 'File not found'}), 404

    if target.is_dir():
        return jsonify({'error': 'Is a directory'}), 400

    size = target.stat().st_size
    if size > MAX_RAW_SIZE:
        return jsonify({'error': f'File too large ({size // (1024*1024)} MB). Max is 20 MB.'}), 413

    try:
        return send_file(str(target), conditional=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_bp.route('/api/workspace/mkdir', methods=['POST'])
def mkdir():
    """Create a new folder inside a writable workspace area."""
    import re
    data = request.get_json(silent=True) or {}
    rel = data.get('path', '').strip()
    if not rel:
        return jsonify({'error': 'Missing "path" field'}), 400

    target = _writable_path(rel)
    if target is None:
        return jsonify({'error': 'Cannot create folders here (read-only area)'}), 403

    # Validate the folder name (last component)
    folder_name = target.name
    if not folder_name or re.search(r'[<>:"|?*\\]', folder_name):
        return jsonify({'error': 'Invalid folder name'}), 400
    if folder_name.startswith('.'):
        return jsonify({'error': 'Folder names cannot start with a dot'}), 400

    if target.exists():
        return jsonify({'error': 'Already exists'}), 409

    try:
        target.mkdir(parents=True, exist_ok=False)
        return jsonify({'path': rel, 'created': True})
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_bp.route('/api/workspace/file', methods=['PUT'])
def write_file():
    """Write content to a file in a writable workspace area."""
    rel = request.args.get('path', '')
    if not rel:
        return jsonify({'error': 'Missing path parameter'}), 400

    target = _writable_path(rel)
    if target is None:
        return jsonify({'error': 'Cannot write here (read-only area)'}), 403

    data = request.get_json(silent=True) or {}
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'Missing content field'}), 400

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        return jsonify({'ok': True, 'path': rel, 'size': len(content)})
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_bp.route('/api/workspace/file', methods=['DELETE'])
def delete_file():
    """Archive a file in a writable workspace area (renames to .deleted)."""
    rel = request.args.get('path', '')
    if not rel:
        return jsonify({'error': 'Missing path parameter'}), 400

    target = _writable_path(rel)
    if target is None:
        return jsonify({'error': 'Cannot delete here (read-only area)'}), 403

    if not target.exists():
        return jsonify({'error': 'File not found'}), 404

    if target.is_dir():
        return jsonify({'error': 'Cannot delete directories'}), 400

    try:
        renamed = target.with_suffix(target.suffix + '.deleted')
        target.rename(renamed)
        return jsonify({'ok': True, 'archived': renamed.name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@workspace_bp.route('/api/workspace/writable')
def check_writable():
    """Check if a workspace path is in a writable area."""
    rel = request.args.get('path', '')
    target = _writable_path(rel) if rel else None
    # Root is not writable, but top-level writable dirs are
    if not rel:
        return jsonify({'writable': False, 'writable_areas': list(_WRITABLE_MAP.keys())})
    return jsonify({'writable': target is not None, 'path': rel})
