"""
routes/static_files.py — Static Asset Serving Blueprint (P2-T8)

Extracted from server.py during Phase 2 blueprint split.
Registers routes:
  GET  /sounds/<path:filepath>         — sound effect files
  GET  /uploads/<path:filename>        — uploaded user files
  GET  /src/<path:filepath>            — frontend JS/CSS source modules
  GET  /known_faces/<name>/<filename>  — face recognition photos
  GET  /api/dj-sound                   — DJ soundboard API (list/play)
"""

import random
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

static_files_bp = Blueprint('static_files', __name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

from routes import APP_ROOT, DATA_DIR

SOUNDS_DIR = APP_ROOT / "sounds"
UPLOADS_DIR = DATA_DIR / "uploads"
KNOWN_FACES_DIR = DATA_DIR / "known_faces"
STATIC_DIR = APP_ROOT / "static"

UPLOADS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# DJ Sounds catalogue
# ---------------------------------------------------------------------------

DJ_SOUNDS = {
    'air_horn': {
        'description': 'Classic stadium air horn - ba ba baaaa!',
        'when_to_use': 'Before drops, hype moments, celebrating wins, hip-hop DJ style'
    },
    'scratch_long': {
        'description': 'Extended DJ scratch solo - wicka wicka',
        'when_to_use': 'Transitions, hip-hop moments, showing off DJ skills'
    },
    'rewind': {
        'description': 'DJ rewind - pull up selecta!',
        'when_to_use': 'Going back, replaying something fire, dancehall pull-ups'
    },
    'record_stop': {
        'description': 'Record stopping abruptly',
        'when_to_use': 'Stopping everything, dramatic pause, cutting the music'
    },
    'impact': {
        'description': 'Punchy cinematic impact hit',
        'when_to_use': 'Punctuating statements, transitions, emphasis'
    },
    'crowd_cheer': {
        'description': 'Nightclub crowd cheering and going wild',
        'when_to_use': 'Big wins, amazing moments, festival energy, applause'
    },
    'crowd_hype': {
        'description': 'Hyped up rave crowd losing their minds',
        'when_to_use': 'Peak energy moments, party atmosphere'
    },
    'yeah': {
        'description': 'Hype man YEAH! vocal shot',
        'when_to_use': 'Hyping up, agreement, energy boost'
    },
    'lets_go': {
        'description': 'LETS GO! vocal chant',
        'when_to_use': 'Starting something, getting pumped, motivation'
    },
    'laser': {
        'description': 'Retro arcade laser zap - pew pew',
        'when_to_use': 'Sci-fi moments, gaming references, 80s vibes'
    },
    'gunshot': {
        'description': 'Dancehall gunshot sound - gun finger!',
        'when_to_use': 'Reggae/dancehall vibes, shooting down bad ideas'
    },
    'bruh': {
        'description': 'Classic bruh sound effect',
        'when_to_use': 'Facepalm moments, disappointment, when someone says something dumb'
    },
    'sad_trombone': {
        'description': 'Sad trombone wah wah wah - womp womp',
        'when_to_use': 'Fails, disappointments, when things go wrong'
    },
}

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _safe_path(base_dir: Path, *parts) -> Path | None:
    """
    Resolve a path within base_dir, rejecting any traversal outside it.
    Returns the resolved Path on success, or None if traversal is detected.
    """
    try:
        resolved = (base_dir / Path(*parts)).resolve()
        base_resolved = base_dir.resolve()
        if resolved == base_resolved or base_resolved in resolved.parents:
            return resolved
    except Exception:
        pass
    return None


@static_files_bp.route('/sounds/<path:filepath>')
def serve_sound(filepath):
    """Serve sound effect files (including subdirectories like DJ-clips/)"""
    sound_path = _safe_path(SOUNDS_DIR, filepath)
    if sound_path is None:
        return jsonify({"error": "Invalid path"}), 400
    if sound_path.exists():
        return send_file(sound_path, mimetype='audio/mpeg')
    return jsonify({"error": "Sound not found"}), 404


@static_files_bp.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve uploaded files (path traversal guarded)."""
    upload_path = _safe_path(UPLOADS_DIR, filename)
    if upload_path is None:
        return jsonify({"error": "Invalid path"}), 400
    if not upload_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(upload_path)


@static_files_bp.route('/src/<path:filepath>')
def serve_src(filepath):
    """Serve frontend source files (JS, CSS modules)"""
    # P7-T3 security: prevent path traversal (same guard used by serve_sound)
    src_path = _safe_path(APP_ROOT / 'src', filepath)
    if src_path is None:
        return jsonify({"error": "Invalid path"}), 400
    if not src_path.exists():
        return jsonify({"error": "File not found"}), 404

    mime_types = {
        '.js': 'application/javascript',
        '.css': 'text/css',
        '.html': 'text/html',
        '.json': 'application/json',
    }
    mime_type = mime_types.get(src_path.suffix.lower(), 'text/plain')
    resp = send_file(src_path, mimetype=mime_type)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@static_files_bp.route('/known_faces/<name>/<filename>')
def serve_face_photo(name, filename):
    """Serve face photos for the My Face section"""
    photo_path = _safe_path(KNOWN_FACES_DIR, name, filename)
    if photo_path is None:
        return jsonify({"error": "Invalid path"}), 400
    if photo_path.exists():
        return send_file(photo_path)
    return jsonify({"error": "Photo not found"}), 404


@static_files_bp.route('/api/dj-sound', methods=['GET'])
def handle_dj_sound():
    """
    DJ Soundboard endpoint.
    Query params:
      - action: 'list' or 'play'
      - sound: sound name (e.g., 'air_horn', 'scratch', 'siren_rise')
    Returns sound info or triggers playback.
    """
    action = request.args.get('action', 'list')
    sound = request.args.get('sound', '')

    if action == 'list':
        sounds_list = [
            {
                'name': name,
                'description': info['description'],
                'when_to_use': info['when_to_use'],
                'available': (SOUNDS_DIR / f"{name}.mp3").exists(),
            }
            for name, info in DJ_SOUNDS.items()
        ]
        return jsonify({
            'action': 'list',
            'sounds': sounds_list,
            'count': len(sounds_list),
            'response': (
                f"Soundboard loaded! {len(sounds_list)} effects ready. "
                "I got air horns, sirens, scratches, crowd effects, and more!"
            ),
        })

    if action == 'play':
        if not sound:
            sound = random.choice(list(DJ_SOUNDS.keys()))

        sound_lower = sound.lower().replace(' ', '_').replace('-', '_')

        matched = next(
            (name for name in DJ_SOUNDS if sound_lower in name or name in sound_lower),
            None,
        )
        if not matched:
            matched = next(
                (name for name in DJ_SOUNDS
                 if any(word in name for word in sound_lower.split('_'))),
                None,
            )

        if not matched:
            return jsonify({
                'action': 'error',
                'response': (
                    f"No sound matching '{sound}'. "
                    "Try: air_horn, siren, scratch, applause, bass_drop, rewind..."
                ),
            })

        sound_file = SOUNDS_DIR / f"{matched}.mp3"
        if not sound_file.exists():
            return jsonify({
                'action': 'error',
                'response': f"Sound file for '{matched}' not found. Need to generate it first!",
            })

        info = DJ_SOUNDS[matched]
        return jsonify({
            'action': 'play',
            'sound': matched,
            'description': info['description'],
            'url': f"/sounds/{matched}.mp3",
            'response': f"*{info['description'].upper()}* 🎵",
        })

    return jsonify({'error': 'Unknown action'}), 400


@static_files_bp.route('/manifest.json')
def serve_manifest():
    """PWA Web App Manifest"""
    path = STATIC_DIR / 'manifest.json'
    resp = send_file(path, mimetype='application/manifest+json')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


@static_files_bp.route('/sw.js')
def serve_sw():
    """PWA Service Worker — must be served from root scope"""
    path = STATIC_DIR / 'sw.js'
    resp = send_file(path, mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@static_files_bp.route('/static/icons/<filename>')
def serve_icon(filename):
    """PWA icons"""
    icon_path = _safe_path(STATIC_DIR / 'icons', filename)
    if icon_path is None or not icon_path.exists():
        return jsonify({"error": "Icon not found"}), 404
    return send_file(icon_path, mimetype='image/png')


@static_files_bp.route('/install')
def serve_install():
    """PWA install landing page"""
    path = STATIC_DIR / 'install.html'
    resp = send_file(path, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@static_files_bp.route('/admin')
def serve_admin():
    """Serve the OpenUI admin dashboard"""
    admin_path = APP_ROOT / 'src' / 'admin.html'
    if not admin_path.exists():
        return jsonify({"error": "Admin dashboard not found"}), 404
    resp = send_file(admin_path, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp
