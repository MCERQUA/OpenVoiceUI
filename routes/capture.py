"""
routes/capture.py -- Camera Capture Blueprint

CompanyCam-style field photo management for contractors.

Storage layout:
  runtime/captures/
    index.json                     -- lightweight album summaries
    albums/<album_id>/
      album.json                   -- full record with photos[]
      media/<photo_id>.jpg|.mp4
      thumbs/<photo_id>.jpg        -- 400px thumbnail
      exports/                     -- reports, reels
    shares/<token>.json
    queue-receipts/<client_uuid>.json  -- offline idempotency ledger

All mutations to index.json and album.json are protected with fcntl.flock
to prevent the data-loss race that happens when concurrent uploads read,
modify, and write the same JSON file.
"""

import base64
import fcntl
import json
import logging
import math
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests as http_requests
from flask import Blueprint, jsonify, request, send_file
from PIL import Image, ImageOps

from services.paths import RUNTIME_DIR

logger = logging.getLogger(__name__)
capture_bp = Blueprint('capture', __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAPTURES_DIR = RUNTIME_DIR / "captures"
ALBUMS_SUBDIR = CAPTURES_DIR / "albums"
SHARES_DIR = CAPTURES_DIR / "shares"
RECEIPTS_DIR = CAPTURES_DIR / "queue-receipts"
INDEX_PATH = CAPTURES_DIR / "index.json"

ALLOWED_PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.gif', '.mp4', '.mov'}
ALLOWED_VIDEO_EXTS = {'.mp4', '.mov'}
ALLOWED_ANNOTATION_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}

THUMB_SIZE = 400
THUMB_QUALITY = 85
DEFAULT_FOLDERS = ["Before", "During", "After", "Damage", "Completion"]

# Per-album geofence radius in metres. A house lot is 20-40m wide, so 120m
# already spans several neighbours -- deliberately loose enough to catch a
# parked truck, tight enough to exclude the next street. Commercial sites
# override this per album; /albums/nearby honours the per-album value.
DEFAULT_GEOFENCE_M = 120

# Vision model keys -- haiku tier (cheap, fast)
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')


# ---------------------------------------------------------------------------
# File-locking helpers
# ---------------------------------------------------------------------------

@contextmanager
def _locked_json(path, default=None):
    """Context manager: open path with exclusive lock, yield (data, file).

    On exit the caller's mutations are written back and the lock released.
    If the file does not exist yet *default* is used as the initial value
    and the file is created.  The directory is ensured automatically.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(path, 'a+')          # create if missing
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        fd.seek(0)
        raw = fd.read()
        data = json.loads(raw) if raw.strip() else (default if default is not None else {})
        yield data, fd
        fd.seek(0)
        fd.truncate()
        json.dump(data, fd, indent=2, default=str, ensure_ascii=False)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def _read_json_safe(path, default=None):
    """Read a JSON file without locking (read-only paths like receipts)."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json_safe(path, data):
    """Write a JSON file atomically-ish (for small one-off writes like receipts)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding='utf-8')


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _new_album_id():
    return secrets.token_hex(6)   # 12 hex chars


def _new_photo_id():
    return secrets.token_hex(8)    # 16 hex chars


def _new_share_token():
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Thumbnail generation + EXIF stripping
# ---------------------------------------------------------------------------

def _strip_exif_keep_orientation(src_path: Path, dst_path: Path) -> None:
    """Strip all EXIF except orientation, re-save as JPEG."""
    try:
        img = ImageOps.exif_transpose(Image.open(src_path))
        # Convert to RGB if needed (handles RGBA, P modes)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.save(dst_path, 'JPEG', quality=92)
    except Exception as exc:
        logger.warning("EXIF strip failed for %s: %s -- copying raw", src_path, exc)
        # Fail-soft: copy the original bytes through
        import shutil
        shutil.copy2(src_path, dst_path)


def _make_thumbnail(src_path: Path, dst_path: Path) -> None:
    """Generate a 400px thumbnail on longest side, JPEG quality 85."""
    try:
        img = ImageOps.exif_transpose(Image.open(src_path))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst_path, 'JPEG', quality=THUMB_QUALITY)
    except Exception as exc:
        logger.warning("Thumbnail generation failed for %s: %s", src_path, exc)


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def _album_summary(album_data: dict) -> dict:
    """Build a lightweight summary dict for index.json from a full album record."""
    photos = [p for p in album_data.get('photos', []) if not p.get('deleted', False)]
    # Pick cover: first non-deleted photo
    cover_url = None
    for p in photos:
        if p.get('type') == 'photo':
            cover_url = f"/api/capture/albums/{album_data['id']}/thumb/{p['id']}"
            break
    return {
        'id': album_data['id'],
        'name': album_data.get('name', ''),
        'client_name': album_data.get('client_name', ''),
        'address': album_data.get('address', ''),
        'status': album_data.get('status', 'in_progress'),
        'photo_count': len(photos),
        'cover_url': cover_url,
        'updated_at': album_data.get('updated_at', ''),
        # Geo fields MUST be in the summary: /albums/nearby matches against
        # index.json only, and skips any entry whose lat is None. Omitting these
        # made GPS auto-suggest return [] for every album at every distance —
        # a silently dead feature, not a miss. Keep them here.
        'lat': album_data.get('lat'),
        'lng': album_data.get('lng'),
        'geofence_m': album_data.get('geofence_m', DEFAULT_GEOFENCE_M),
    }


def _update_index_for_album(album_data: dict) -> None:
    """Rebuild one album's entry in index.json under flock."""
    summary = _album_summary(album_data)
    with _locked_json(INDEX_PATH, default=[]) as (index, _):
        # Upsert by id
        found = False
        for i, entry in enumerate(index):
            if entry.get('id') == album_data['id']:
                index[i] = summary
                found = True
                break
        if not found:
            index.append(summary)


# ---------------------------------------------------------------------------
# Geospatial helper
# ---------------------------------------------------------------------------

def _haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance between two GPS points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Album endpoints
# ---------------------------------------------------------------------------

@capture_bp.route('/albums', methods=['GET'])
def list_albums():
    """List albums from index.json. Supports ?status= and ?q= search."""
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    status_filter = request.args.get('status', '').strip().lower()
    q = request.args.get('q', '').strip().lower()

    index = _read_json_safe(INDEX_PATH, default=[])
    results = index

    if status_filter:
        results = [a for a in results if a.get('status', '') == status_filter]

    if q:
        results = [a for a in results
                   if q in a.get('name', '').lower()
                   or q in a.get('client_name', '').lower()
                   or q in a.get('address', '').lower()]

    return jsonify(results)


@capture_bp.route('/albums', methods=['POST'])
def create_album():
    """Create a new photo album."""
    body = request.get_json(force=True, silent=True) or {}
    name = body.get('name', '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    album_id = _new_album_id()
    now = datetime.now(timezone.utc).isoformat()

    album_data = {
        'id': album_id,
        'name': name,
        'client_name': body.get('client_name', ''),
        'address': body.get('address', ''),
        'lat': body.get('lat'),
        'lng': body.get('lng'),
        'geofence_m': body.get('geofence_m', DEFAULT_GEOFENCE_M),
        'status': 'in_progress',
        'folders': list(body.get('folders')) if body.get('folders') else list(DEFAULT_FOLDERS),
        'share': {'token': None, 'enabled': False, 'scope': 'all'},
        'photos': [],
        'created_at': now,
        'updated_at': now,
    }

    album_dir = ALBUMS_SUBDIR / album_id
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / 'media').mkdir(exist_ok=True)
    (album_dir / 'thumbs').mkdir(exist_ok=True)
    (album_dir / 'exports').mkdir(exist_ok=True)
    _write_json_safe(album_dir / 'album.json', album_data)

    _update_index_for_album(album_data)

    return jsonify(album_data), 201


@capture_bp.route('/albums/<album_id>', methods=['GET'])
def get_album(album_id):
    """Full album record + photos."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404
    album_data = _read_json_safe(album_path)
    return jsonify(album_data)


@capture_bp.route('/albums/<album_id>', methods=['PATCH'])
def update_album(album_id):
    """Update album fields (merge semantics -- never blank existing values)."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    body = request.get_json(force=True, silent=True) or {}
    mergeable_keys = {'name', 'client_name', 'address', 'lat', 'lng', 'geofence_m', 'status'}

    with _locked_json(album_path) as (album_data, _):
        for key in mergeable_keys:
            if key in body and body[key] not in (None, ''):
                album_data[key] = body[key]
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    # Refresh the album from disk for index update
    updated = _read_json_safe(album_path)
    _update_index_for_album(updated)
    return jsonify(updated)


@capture_bp.route('/albums/<album_id>/archive', methods=['POST'])
def archive_album(album_id):
    """Soft-archive an album (set status='archived')."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    with _locked_json(album_path) as (album_data, _):
        album_data['status'] = 'archived'
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    updated = _read_json_safe(album_path)
    _update_index_for_album(updated)
    return jsonify(updated)


@capture_bp.route('/albums/nearby', methods=['GET'])
def albums_nearby():
    """Return albums within radius of given GPS coordinates."""
    try:
        lat = float(request.args.get('lat', 0))
        lng = float(request.args.get('lng', 0))
        radius_km = float(request.args.get('radius', 200)) / 1000.0
    except (ValueError, TypeError):
        return jsonify({'error': 'lat, lng, and radius (meters) query params required'}), 400

    index = _read_json_safe(INDEX_PATH, default=[])
    results = []
    for entry in index:
        a_lat = entry.get('lat')
        a_lng = entry.get('lng')
        fence_m = entry.get('geofence_m')

        # Self-heal entries written before geo fields were added to the summary.
        # Falling back to album.json means no migration pass is needed and a
        # stale index degrades to "slower", never to "silently no matches".
        if a_lat is None or a_lng is None or fence_m is None:
            album_path = ALBUMS_SUBDIR / str(entry.get('id', '')) / 'album.json'
            if album_path.exists():
                full = _read_json_safe(album_path, default={}) or {}
                if a_lat is None:
                    a_lat = full.get('lat')
                if a_lng is None:
                    a_lng = full.get('lng')
                if fence_m is None:
                    fence_m = full.get('geofence_m')

        if a_lat is None or a_lng is None:
            continue

        # Match against the album's OWN fence, bounded by the caller's radius.
        # A single caller-wide radius is wrong in both directions: 120m is right
        # for a house and far too tight for a 40-acre commercial site.
        try:
            fence_km = float(fence_m) / 1000.0
        except (TypeError, ValueError):
            fence_km = DEFAULT_GEOFENCE_M / 1000.0
        limit_km = min(radius_km, fence_km) if fence_km > 0 else radius_km

        dist = _haversine_km(lat, lng, a_lat, a_lng)
        if dist <= limit_km:
            result = dict(entry)
            result['lat'] = a_lat
            result['lng'] = a_lng
            result['geofence_m'] = fence_m
            result['distance_m'] = round(dist * 1000, 1)
            results.append(result)

    results.sort(key=lambda x: x.get('distance_m', 0))
    return jsonify(results)


@capture_bp.route('/albums/<album_id>/folders', methods=['POST'])
def add_folder(album_id):
    """Add a sub-folder to an album."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    body = request.get_json(force=True, silent=True) or {}
    folder_name = body.get('name', '').strip()
    if not folder_name:
        return jsonify({'error': 'name is required'}), 400

    with _locked_json(album_path) as (album_data, _):
        if folder_name not in album_data.get('folders', []):
            album_data.setdefault('folders', []).append(folder_name)
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    updated = _read_json_safe(album_path)
    return jsonify(updated)


# ---------------------------------------------------------------------------
# Photo endpoints
# ---------------------------------------------------------------------------

@capture_bp.route('/albums/<album_id>/photos', methods=['POST'])
def upload_photo(album_id):
    """Upload a photo to an album. Supports multipart form data.

    Fields: file, captured_at, lat, lng, folder, client_uuid, seq
    Idempotent on client_uuid -- if a receipt exists for that uuid,
    returns the existing photo record (200, not 201).
    """
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    # --- Idempotency check on client_uuid ---
    client_uuid = request.form.get('client_uuid', '').strip()
    if client_uuid:
        receipt_path = RECEIPTS_DIR / f"{client_uuid}.json"
        existing = _read_json_safe(receipt_path)
        if existing:
            logger.info("Idempotent upload -- client_uuid=%s already ingested as %s",
                        client_uuid, existing.get('photo_id'))
            return jsonify(existing), 200

    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'file is required'}), 400

    original_name = file.filename or 'photo.jpg'
    ext = Path(original_name).suffix.lower()
    is_video = ext in ALLOWED_VIDEO_EXTS

    photo_id = _new_photo_id()
    now = datetime.now(timezone.utc).isoformat()
    captured_at = request.form.get('captured_at', now)
    seq = request.form.get('seq')
    seq_val = int(seq) if seq and seq.isdigit() else None

    photo_record = {
        'id': photo_id,
        'type': 'video' if is_video else 'photo',
        'folder': request.form.get('folder', 'Before').strip(),
        'captured_at': captured_at,
        'uploaded_at': now,
        'lat': float(request.form['lat']) if request.form.get('lat') else None,
        'lng': float(request.form['lng']) if request.form.get('lng') else None,
        'gps_accuracy_m': float(request.form['gps_accuracy_m']) if request.form.get('gps_accuracy_m') else None,
        'note': '',
        'tags': [],
        'ai_caption': '',
        'annotated': False,
        'annotated_url': None,
        'pair_before_id': None,
        'hidden_from_share': False,
        'client_uuid': client_uuid or None,
        'seq': seq_val,
        'deleted': False,
    }

    album_dir = ALBUMS_SUBDIR / album_id
    media_path = album_dir / 'media' / f"{photo_id}{ext}"

    # Save the uploaded file
    file.save(str(media_path))

    # Strip EXIF and generate thumbnail for photos
    if not is_video:
        clean_path = album_dir / 'media' / f"{photo_id}.jpg"
        _strip_exif_keep_orientation(media_path, clean_path)
        # If we converted to .jpg and the original was not .jpg, remove the original
        if clean_path != media_path:
            try:
                media_path.unlink()
            except OSError:
                pass
            media_path = clean_path
            photo_record['id'] = photo_id  # stays the same

        thumb_path = album_dir / 'thumbs' / f"{photo_id}.jpg"
        _make_thumbnail(media_path, thumb_path)

    # Append photo record to album.json under flock
    with _locked_json(album_path) as (album_data, _):
        album_data.setdefault('photos', []).append(photo_record)
        album_data['updated_at'] = now

    # Write queue receipt for idempotency
    if client_uuid:
        RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        receipt = {
            'client_uuid': client_uuid,
            'album_id': album_id,
            'photo_id': photo_id,
            'created_at': now,
        }
        _write_json_safe(RECEIPTS_DIR / f"{client_uuid}.json", receipt)

    # Update index.json
    updated_album = _read_json_safe(album_path)
    _update_index_for_album(updated_album)

    return jsonify(photo_record), 201


@capture_bp.route('/albums/<album_id>/media/<photo_id>', methods=['GET'])
def serve_media(album_id, photo_id):
    """Serve a photo or video file. Includes Accept-Ranges for video seeking."""
    album_dir = ALBUMS_SUBDIR / album_id
    if not album_dir.exists():
        return jsonify({'error': 'Album not found'}), 404

    media_dir = album_dir / 'media'
    # Search for any file matching the photo_id prefix
    match = None
    if media_dir.exists():
        for candidate in media_dir.iterdir():
            if candidate.stem == photo_id and not candidate.name.startswith('.'):
                match = candidate
                break

    if match is None:
        return jsonify({'error': 'Media not found'}), 404

    mime_type = 'image/jpeg'
    suffix = match.suffix.lower()
    if suffix in ('.mp4',):
        mime_type = 'video/mp4'
    elif suffix in ('.mov',):
        mime_type = 'video/quicktime'
    elif suffix in ('.png',):
        mime_type = 'image/png'
    elif suffix in ('.webp',):
        mime_type = 'image/webp'

    resp = send_file(str(match), mimetype=mime_type)
    resp.headers['Accept-Ranges'] = 'bytes'
    return resp


@capture_bp.route('/albums/<album_id>/thumb/<photo_id>', methods=['GET'])
def serve_thumb(album_id, photo_id):
    """Serve a 400px thumbnail."""
    thumb_path = ALBUMS_SUBDIR / album_id / 'thumbs' / f"{photo_id}.jpg"
    if not thumb_path.exists():
        return jsonify({'error': 'Thumbnail not found'}), 404

    resp = send_file(str(thumb_path), mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@capture_bp.route('/albums/<album_id>/photos/<photo_id>', methods=['PATCH'])
def update_photo(album_id, photo_id):
    """Update photo metadata (note, tags, folder, hidden_from_share, pair_before_id)."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    body = request.get_json(force=True, silent=True) or {}
    mergeable_keys = {'note', 'tags', 'folder', 'hidden_from_share', 'pair_before_id'}

    found = False
    with _locked_json(album_path) as (album_data, _):
        for p in album_data.get('photos', []):
            if p['id'] == photo_id:
                for key in mergeable_keys:
                    if key in body:
                        p[key] = body[key]
                found = True
                break
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    if not found:
        return jsonify({'error': 'Photo not found'}), 404

    updated = _read_json_safe(album_path)
    return jsonify(updated)


@capture_bp.route('/albums/<album_id>/photos/<photo_id>/annotate', methods=['POST'])
def annotate_photo(album_id, photo_id):
    """Receive an annotation overlay image, save as .annotated.jpg."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'file (annotation image) is required'}), 400

    # Verify the photo exists in the album
    found = False
    with _locked_json(album_path) as (album_data, _):
        for p in album_data.get('photos', []):
            if p['id'] == photo_id:
                found = True
                break

    if not found:
        return jsonify({'error': 'Photo not found'}), 404

    # Save the annotation overlay, convert to JPG
    album_dir = ALBUMS_SUBDIR / album_id
    annotated_path = album_dir / 'media' / f"{photo_id}.annotated.jpg"
    save_path = album_dir / 'media' / f"_annotation_tmp_{photo_id}.png"
    file.save(str(save_path))

    try:
        img = Image.open(save_path)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.save(annotated_path, 'JPEG', quality=90)
    except Exception as exc:
        logger.error("Failed to process annotation image: %s", exc)
        return jsonify({'error': 'Failed to process annotation image'}), 500
    finally:
        try:
            save_path.unlink()
        except OSError:
            pass

    # Update album record
    with _locked_json(album_path) as (album_data, _):
        for p in album_data.get('photos', []):
            if p['id'] == photo_id:
                p['annotated'] = True
                p['annotated_url'] = f"/api/capture/albums/{album_id}/media/{photo_id}.annotated.jpg"
                break
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    updated = _read_json_safe(album_path)
    return jsonify(updated)


@capture_bp.route('/albums/<album_id>/photos/<photo_id>/move', methods=['POST'])
def move_photo(album_id, photo_id):
    """Move a photo to a different folder."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    body = request.get_json(force=True, silent=True) or {}
    folder = body.get('folder', '').strip()
    if not folder:
        return jsonify({'error': 'folder is required'}), 400

    found = False
    with _locked_json(album_path) as (album_data, _):
        for p in album_data.get('photos', []):
            if p['id'] == photo_id:
                p['folder'] = folder
                found = True
                break
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    if not found:
        return jsonify({'error': 'Photo not found'}), 404

    updated = _read_json_safe(album_path)
    return jsonify(updated)


@capture_bp.route('/albums/<album_id>/photos/<photo_id>', methods=['DELETE'])
def delete_photo(album_id, photo_id):
    """SOFT DELETE ONLY -- sets deleted:true, file stays on disk."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    found = False
    with _locked_json(album_path) as (album_data, _):
        for p in album_data.get('photos', []):
            if p['id'] == photo_id:
                p['deleted'] = True
                found = True
                break
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    if not found:
        return jsonify({'error': 'Photo not found'}), 404

    updated = _read_json_safe(album_path)
    _update_index_for_album(updated)
    return jsonify(updated)


# ---------------------------------------------------------------------------
# AI endpoints
# ---------------------------------------------------------------------------

DESCRIBE_PROMPT = (
    'Describe this construction/insulation job photo in one sentence. '
    'Then list 3-5 relevant tags as comma-separated words only on a new line.'
)


def _parse_caption_and_tags(text: str) -> dict:
    """Split a vision response into {caption, tags}.

    Convention: first line is the sentence, second line is comma-separated tags.
    """
    lines = (text or '').strip().split('\n')
    caption = lines[0].strip() if lines else ''
    tags = []
    if len(lines) > 1:
        tags = [t.strip() for t in lines[1].split(',') if t.strip()]
    return {'caption': caption, 'tags': tags[:8]}


def _describe_with_vision(image_path: Path) -> dict:
    """Send image to a vision model, return {caption, tags}.

    Delegates to routes.vision._call_vision, which is the ONE place provider
    selection lives. Capture media is always on disk, so this takes the
    file_path branch: headless Claude Code on the subscription, no marginal cost.

    Why delegate rather than call a provider directly: this function used to
    duplicate its own Groq call pinned to 'llama-4-scout-17b-16e-instruct'.
    Groq decommissioned that model; d2a3dbb fixed the copy in routes/vision.py
    and this copy kept 404ing, so every AI caption in the capture app failed
    while the identical feature worked elsewhere. Two copies of provider logic
    means one of them is always the stale one -- keep it at one.
    """
    from routes.vision import _call_vision

    text = _call_vision('', DESCRIBE_PROMPT, file_path=str(image_path))
    return _parse_caption_and_tags(text)


def _describe_with_vision_legacy_direct(image_path: Path) -> dict:
    """Superseded by _describe_with_vision. Retained for reference/fallback only.

    Direct provider calls, kept per the never-delete rule. Do NOT wire this
    back in without first re-checking that the pinned model ids are still
    served -- both were stale as of 2026-07-30.
    """
    ext = image_path.suffix.lower()
    mime = 'image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/png'

    with open(image_path, 'rb') as f:
        b64_data = base64.b64encode(f.read()).decode('utf-8')
    data_url = f"data:{mime};base64,{b64_data}"

    if GROQ_API_KEY:
        # Groq Llama vision -- haiku-tier cheap
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            'Authorization': f'Bearer {GROQ_API_KEY}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': 'llama-4-scout-17b-16e-instruct',
            'max_tokens': 300,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text':
                         'Describe this construction/insulation job photo in one sentence. '
                         'Then list 3-5 relevant tags as comma-separated words only on a new line.'},
                        {'type': 'image_url', 'image_url': {'url': data_url}},
                    ],
                }
            ],
        }
    elif ANTHROPIC_API_KEY:
        # Anthropic Claude Haiku
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': 'claude-3-haiku-20240307',
            'max_tokens': 300,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': mime,
                                'data': b64_data,
                            },
                        },
                        {
                            'type': 'text',
                            'text': ('Describe this construction/insulation job photo in one sentence. '
                                     'Then list 3-5 relevant tags as comma-separated words only on a new line.'),
                        },
                    ],
                }
            ],
        }
    else:
        raise RuntimeError("No vision API key configured (GROQ_API_KEY or ANTHROPIC_API_KEY)")

    resp = http_requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()

    # Extract text from provider-specific response
    if GROQ_API_KEY:
        text = body.get('choices', [{}])[0].get('message', {}).get('content', '')
    else:
        text = body.get('content', [{}])[0].get('text', '')

    # Parse caption and tags
    lines = text.strip().split('\n')
    caption = lines[0].strip() if lines else ''
    tags = []
    if len(lines) > 1:
        tags = [t.strip() for t in lines[1].split(',') if t.strip()]

    return {'caption': caption, 'tags': tags}


@capture_bp.route('/albums/<album_id>/photos/<photo_id>/describe', methods=['POST'])
def describe_photo(album_id, photo_id):
    """Server-side vision description of a single photo."""
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    # Find the media file
    album_dir = ALBUMS_SUBDIR / album_id
    media_dir = album_dir / 'media'
    media_file = None
    if media_dir.exists():
        for candidate in media_dir.iterdir():
            if candidate.stem == photo_id and candidate.suffix.lower() in ('.jpg', '.jpeg', '.png'):
                media_file = candidate
                break

    if media_file is None:
        return jsonify({'error': 'Photo media not found'}), 404

    if not GROQ_API_KEY and not ANTHROPIC_API_KEY:
        return jsonify({'error': 'No vision API key configured'}), 503

    try:
        result = _describe_with_vision(media_file)
    except Exception as exc:
        logger.error("Vision describe failed for %s: %s", photo_id, exc)
        return jsonify({'error': f'Vision API error: {exc}'}), 502

    # Update photo record under flock
    with _locked_json(album_path) as (album_data, _):
        for p in album_data.get('photos', []):
            if p['id'] == photo_id:
                p['ai_caption'] = result['caption']
                p['tags'] = result.get('tags', [])
                break
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    updated = _read_json_safe(album_path)
    return jsonify(result)


@capture_bp.route('/albums/<album_id>/ai/batch-describe', methods=['POST'])
def batch_describe(album_id):
    """Bulk AI describe for undescribed photos in an album.

    Processes up to 5 synchronously; returns a job-style response for larger batches.
    """
    album_path = ALBUMS_SUBDIR / album_id / 'album.json'
    if not album_path.exists():
        return jsonify({'error': 'Album not found'}), 404

    if not GROQ_API_KEY and not ANTHROPIC_API_KEY:
        return jsonify({'error': 'No vision API key configured'}), 503

    album_data = _read_json_safe(album_path)
    photos = [p for p in album_data.get('photos', []) if not p.get('deleted', False)]

    # Collect photos that need describing (photos without ai_caption)
    to_describe = []
    album_dir = ALBUMS_SUBDIR / album_id
    media_dir = album_dir / 'media'
    for p in photos:
        if not p.get('ai_caption') and p.get('type') != 'video':
            # Find the media file
            for candidate in media_dir.iterdir() if media_dir.exists() else []:
                if candidate.stem == p['id'] and candidate.suffix.lower() in ('.jpg', '.jpeg', '.png'):
                    to_describe.append((p['id'], candidate))
                    break

    total = len(to_describe)
    if total == 0:
        return jsonify({'message': 'No photos to describe', 'total': 0, 'described': 0})

    if total > 5:
        # For large batches, process first 5 synchronously and return job-style response
        sync_batch = to_describe[:5]
        described_count = 0
        errors = []

        with _locked_json(album_path) as (album_data, _):
            for photo_id, media_file in sync_batch:
                try:
                    result = _describe_with_vision(media_file)
                    for p in album_data.get('photos', []):
                        if p['id'] == photo_id:
                            p['ai_caption'] = result['caption']
                            p['tags'] = result.get('tags', [])
                            break
                    described_count += 1
                except Exception as exc:
                    errors.append({'photo_id': photo_id, 'error': str(exc)})
            album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

        return jsonify({
            'status': 'partial',
            'total': total,
            'described': described_count,
            'remaining': total - described_count,
            'errors': errors,
            'message': f'Described {described_count} of {total} photos. Run again to continue.',
        }), 202

    # 5 or fewer -- process all synchronously
    described_count = 0
    errors = []
    with _locked_json(album_path) as (album_data, _):
        for photo_id, media_file in to_describe:
            try:
                result = _describe_with_vision(media_file)
                for p in album_data.get('photos', []):
                    if p['id'] == photo_id:
                        p['ai_caption'] = result['caption']
                        p['tags'] = result.get('tags', [])
                        break
                described_count += 1
            except Exception as exc:
                errors.append({'photo_id': photo_id, 'error': str(exc)})
        album_data['updated_at'] = datetime.now(timezone.utc).isoformat()

    return jsonify({
        'status': 'complete',
        'total': total,
        'described': described_count,
        'errors': errors,
    })


@capture_bp.route('/search', methods=['GET'])
def search_photos():
    """Semantic-ish search across captions, tags, notes, and album names.

    Query param ?q= -- simple substring match across all indexed text.
    """
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify({'error': 'q parameter is required'}), 400

    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    index = _read_json_safe(INDEX_PATH, default=[])
    for summary in index:
        album_id = summary.get('id', '')
        album_path = ALBUMS_SUBDIR / album_id / 'album.json'
        if not album_path.exists():
            continue
        album_data = _read_json_safe(album_path)

        # Check album-level text
        album_text = ' '.join([
            summary.get('name', ''),
            summary.get('client_name', ''),
            summary.get('address', ''),
        ]).lower()
        album_matches = q in album_text

        # Check photo-level text
        matching_photos = []
        for p in album_data.get('photos', []):
            if p.get('deleted', False):
                continue
            photo_text = ' '.join([
                p.get('note', ''),
                p.get('ai_caption', ''),
                ' '.join(p.get('tags', [])),
                p.get('folder', ''),
            ]).lower()
            if q in photo_text or album_matches:
                matching_photos.append(p)

        if matching_photos or album_matches:
            results.append({
                'album_id': album_id,
                'album_name': summary.get('name', ''),
                'album': summary,
                'photos': matching_photos,
            })

    return jsonify({'query': q, 'results': results, 'total_albums': len(results)})


# ---------------------------------------------------------------------------
# Share endpoints (Phase 3 -- stubs)
# ---------------------------------------------------------------------------

@capture_bp.route('/albums/<album_id>/share', methods=['POST'])
def create_share(album_id):
    """Create a share token for an album. Phase 3 stub."""
    return jsonify({'error': 'Sharing not yet implemented'}), 501


@capture_bp.route('/albums/<album_id>/share', methods=['DELETE'])
def revoke_share(album_id):
    """Revoke a share token. Phase 3 stub."""
    return jsonify({'error': 'Sharing not yet implemented'}), 501
