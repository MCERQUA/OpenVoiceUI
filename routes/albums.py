"""
routes/albums.py — Job Photo Albums API

Provides CompanyCam-style photo album management:
  GET  /api/albums              — list all albums
  POST /api/albums              — create new album
  GET  /api/albums/<id>         — album detail + photos
  PUT  /api/albums/<id>         — update album name/description
  POST /api/albums/<id>/photos  — upload photo to album (multipart + GPS)
  GET  /api/albums/<id>/photos/<photo_id> — get single photo metadata
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from services.paths import UPLOADS_DIR

logger = logging.getLogger(__name__)

albums_bp = Blueprint('albums', __name__)

ALBUMS_DIR = UPLOADS_DIR / 'albums'
ALBUMS_INDEX = ALBUMS_DIR / 'index.json'

ALLOWED_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.gif'}


def _ensure_albums_dir():
    ALBUMS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list:
    _ensure_albums_dir()
    if not ALBUMS_INDEX.exists():
        return []
    try:
        return json.loads(ALBUMS_INDEX.read_text(encoding='utf-8'))
    except Exception:
        return []


def _save_index(index: list) -> None:
    _ensure_albums_dir()
    ALBUMS_INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding='utf-8')
    try:
        ALBUMS_INDEX.chmod(0o666)
    except OSError:
        pass


def _load_album(album_id: str) -> dict | None:
    album_file = ALBUMS_DIR / album_id / 'album.json'
    if not album_file.exists():
        return None
    try:
        return json.loads(album_file.read_text(encoding='utf-8'))
    except Exception:
        return None


def _save_album(album_id: str, data: dict) -> None:
    album_dir = ALBUMS_DIR / album_id
    album_dir.mkdir(parents=True, exist_ok=True)
    album_file = album_dir / 'album.json'
    album_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    try:
        album_file.chmod(0o666)
    except OSError:
        pass


def _update_index_entry(album_id: str, data: dict) -> None:
    """Sync album summary fields into the flat index."""
    index = _load_index()
    entry = {
        'id': album_id,
        'name': data.get('name', ''),
        'description': data.get('description', ''),
        'created_at': data.get('created_at', ''),
        'updated_at': data.get('updated_at', ''),
        'photo_count': len(data.get('photos', [])),
        'cover_url': data['photos'][0]['url'] if data.get('photos') else None,
    }
    for i, item in enumerate(index):
        if item.get('id') == album_id:
            index[i] = entry
            _save_index(index)
            return
    index.append(entry)
    _save_index(index)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@albums_bp.route('/api/albums', methods=['GET'])
def list_albums():
    """Return all albums, newest first."""
    index = _load_index()
    index_sorted = sorted(index, key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify({'albums': index_sorted})


@albums_bp.route('/api/albums', methods=['POST'])
def create_album():
    """Create a new album. Body: {name, description?}"""
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    album_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    data = {
        'id': album_id,
        'name': name,
        'description': body.get('description', '').strip(),
        'created_at': now,
        'updated_at': now,
        'photos': [],
    }
    _save_album(album_id, data)
    _update_index_entry(album_id, data)
    logger.info('album created: %s "%s"', album_id, name)
    return jsonify(data), 201


@albums_bp.route('/api/albums/<album_id>', methods=['GET'])
def get_album(album_id: str):
    """Return album detail including all photos."""
    data = _load_album(album_id)
    if not data:
        return jsonify({'error': 'album not found'}), 404
    return jsonify(data)


@albums_bp.route('/api/albums/<album_id>', methods=['PUT'])
def update_album(album_id: str):
    """Update album name/description."""
    data = _load_album(album_id)
    if not data:
        return jsonify({'error': 'album not found'}), 404
    body = request.get_json(silent=True) or {}
    if 'name' in body:
        data['name'] = (body['name'] or '').strip() or data['name']
    if 'description' in body:
        data['description'] = body.get('description', '').strip()
    data['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    _save_album(album_id, data)
    _update_index_entry(album_id, data)
    return jsonify(data)


@albums_bp.route('/api/albums/<album_id>/photos', methods=['POST'])
def upload_photo(album_id: str):
    """Upload a photo to an album.

    Multipart form fields:
      file     — image file (required)
      lat      — GPS latitude (optional)
      lng      — GPS longitude (optional)
      note     — caption/note (optional)
    """
    import mimetypes
    import re as _re

    data = _load_album(album_id)
    if not data:
        return jsonify({'error': 'album not found'}), 404

    if 'file' not in request.files:
        return jsonify({'error': 'no file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty filename'}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return jsonify({'error': f'unsupported file type: {ext}'}), 415

    # Size check
    f.stream.seek(0, 2)
    file_size = f.stream.tell()
    f.stream.seek(0)
    if file_size > 100 * 1024 * 1024:
        return jsonify({'error': 'file too large (100 MB max)'}), 413

    photo_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    safe_name = f'photo-{photo_id}{ext}'
    album_dir = ALBUMS_DIR / album_id
    album_dir.mkdir(parents=True, exist_ok=True)
    dest = album_dir / safe_name
    f.save(str(dest))
    try:
        dest.chmod(0o666)
    except OSError:
        pass

    # HEIC → JPG conversion
    if ext in ('.heic', '.heif'):
        jpg_name = f'photo-{photo_id}.jpg'
        jpg_dest = album_dir / jpg_name
        try:
            import pillow_heif
            from PIL import Image as _Image
            pillow_heif.register_heif_opener()
            with _Image.open(str(dest)) as _img:
                _img.convert('RGB').save(str(jpg_dest), 'JPEG', quality=90)
            dest = jpg_dest
            safe_name = jpg_name
            ext = '.jpg'
        except Exception as exc:
            logger.warning('HEIC→JPG failed for album photo: %s', exc)

    lat = request.form.get('lat', '').strip()
    lng = request.form.get('lng', '').strip()
    note = request.form.get('note', '').strip()

    photo_url = f'/uploads/albums/{album_id}/{safe_name}'
    photo_entry = {
        'id': photo_id,
        'filename': safe_name,
        'url': photo_url,
        'url_full': f'https://{request.host}/uploads/albums/{album_id}/{safe_name}',
        'lat': lat or None,
        'lng': lng or None,
        'note': note,
        'size': file_size,
        'taken_at': now,
    }

    data['photos'].append(photo_entry)
    data['updated_at'] = now
    _save_album(album_id, data)
    _update_index_entry(album_id, data)

    logger.info('photo added to album %s: %s (gps=%s,%s)', album_id, safe_name, lat or '-', lng or '-')
    return jsonify({'photo': photo_entry, 'album_id': album_id}), 201


@albums_bp.route('/api/albums/<album_id>/photos/<photo_id>', methods=['GET'])
def get_photo(album_id: str, photo_id: str):
    """Get single photo metadata."""
    data = _load_album(album_id)
    if not data:
        return jsonify({'error': 'album not found'}), 404
    for p in data.get('photos', []):
        if p.get('id') == photo_id:
            return jsonify(p)
    return jsonify({'error': 'photo not found'}), 404


# Serve album photos directly (albums are subdirs of uploads)
@albums_bp.route('/uploads/albums/<album_id>/<filename>')
def serve_album_photo(album_id: str, filename: str):
    """Serve photo files from album directories."""
    safe_id = ''.join(c for c in album_id if c.isalnum())
    safe_file = Path(filename).name
    dest = ALBUMS_DIR / safe_id / safe_file
    if not dest.exists() or not dest.is_file():
        return 'Not found', 404
    return send_file(str(dest))
