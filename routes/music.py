"""
routes/music.py — Music System Blueprint (P2-T4)

Extracted from server.py during Phase 2 blueprint split.
Registers routes:
  GET  /music/<filename>
  GET  /generated_music/<filename>
  GET  /api/music                     (action: list|play|pause|resume|stop|skip|next|next_up|volume|status|shuffle|sync|confirm)
  POST /api/music/transition          (DJ transition pre-queue)
  GET  /api/music/transition          (check pending transition)
  POST /api/music/upload              (upload a track)
  GET  /api/music/playlists           (CRUD: list playlists with track counts)
  DELETE /api/music/track/<playlist>/<filename>  (CRUD: delete a track)
  PUT  /api/music/track/<playlist>/<filename>/metadata  (CRUD: update track metadata)
"""

import json
import random
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from routes.static_files import _safe_path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

from routes import DATA_DIR

MUSIC_DIR = DATA_DIR / "music"
MUSIC_DIR.mkdir(exist_ok=True)

GENERATED_MUSIC_DIR = DATA_DIR / "generated_music"
GENERATED_MUSIC_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Shared music state (in-process; single-worker deployments only)
# ---------------------------------------------------------------------------

_music_state_lock = threading.Lock()

current_music_state = {
    "playing": False,
    "current_track": None,
    "volume": 0.3,          # 0.0 – 1.0
    "queue": [],
    "shuffle": False,
    "track_started_at": None,
    "dj_transition_pending": False,
    "next_track": None,
    # Track reservation — prevents race conditions between tool calls and text detection
    "reserved_track": None,
    "reserved_at": None,
    "reservation_id": None,
    "current_playlist": "library",  # 'library' | 'generated'
}

# ---------------------------------------------------------------------------
# Reservation helpers
# ---------------------------------------------------------------------------

def reserve_track(track):
    """Reserve a track the agent has announced; expires after 30 s."""
    with _music_state_lock:
        current_music_state["reserved_track"] = track
        current_music_state["reserved_at"] = time.time()
        current_music_state["reservation_id"] = str(uuid.uuid4())[:8]
        rid = current_music_state["reservation_id"]
    print(f"🎵 Track reserved: {track.get('name', 'Unknown')} (ID: {rid})")
    return rid


def get_reserved_track():
    """Return the reserved track if still valid (30-second window)."""
    with _music_state_lock:
        if not current_music_state.get("reserved_track"):
            return None
        reserved_at = current_music_state.get("reserved_at", 0)
        if time.time() - reserved_at > 30:
            print(f"🎵 Track reservation expired (was {current_music_state['reserved_track'].get('name', 'Unknown')})")
            current_music_state["reserved_track"] = None
            current_music_state["reserved_at"] = None
            current_music_state["reservation_id"] = None
            return None
        return current_music_state["reserved_track"]


def clear_reservation():
    """Clear the active reservation (called when frontend confirms playback)."""
    with _music_state_lock:
        if current_music_state.get("reserved_track"):
            print(f"🎵 Reservation cleared: {current_music_state['reserved_track'].get('name', 'Unknown')}")
        current_music_state["reserved_track"] = None
        current_music_state["reserved_at"] = None
        current_music_state["reservation_id"] = None

# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def load_music_metadata():
    """Load library playlist metadata from JSON file."""
    metadata_file = MUSIC_DIR / "music_metadata.json"
    if metadata_file.exists():
        try:
            with open(metadata_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading music metadata: {e}")
    return {}


def load_generated_music_metadata():
    """Load AI-generated playlist metadata from JSON file."""
    metadata_file = GENERATED_MUSIC_DIR / "generated_metadata.json"
    if metadata_file.exists():
        try:
            with open(metadata_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading generated music metadata: {e}")
    return {}


def save_music_metadata(metadata):
    """Persist library playlist metadata (atomic write — safe against mid-write crashes)."""
    metadata_file = MUSIC_DIR / "music_metadata.json"
    tmp = metadata_file.with_suffix('.tmp')
    tmp.write_text(json.dumps(metadata, indent=2))
    tmp.replace(metadata_file)


def save_generated_music_metadata(metadata):
    """Persist AI-generated playlist metadata (atomic write — safe against mid-write crashes)."""
    metadata_file = GENERATED_MUSIC_DIR / "generated_metadata.json"
    tmp = metadata_file.with_suffix('.tmp')
    tmp.write_text(json.dumps(metadata, indent=2))
    tmp.replace(metadata_file)


def load_playlist_order(playlist):
    """Load saved track order for the given playlist (list of filenames)."""
    music_dir = GENERATED_MUSIC_DIR if playlist == "generated" else MUSIC_DIR
    order_file = music_dir / "order.json"
    if order_file.exists():
        try:
            with open(order_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_playlist_order(playlist, order):
    """Persist track order for the given playlist (atomic write)."""
    music_dir = GENERATED_MUSIC_DIR if playlist == "generated" else MUSIC_DIR
    order_file = music_dir / "order.json"
    tmp = order_file.with_suffix('.tmp')
    tmp.write_text(json.dumps(order, indent=2))
    tmp.replace(order_file)


def get_music_files(playlist="library"):
    """
    Return list of track dicts for the given playlist.
    playlist: 'library' | 'generated' | 'spotify'
    Respects saved order from order.json if present.
    """
    if playlist == "spotify":
        return []

    music_extensions = {".mp3", ".wav", ".ogg", ".m4a", ".webm"}

    if playlist == "generated":
        music_dir = GENERATED_MUSIC_DIR
        metadata = load_generated_music_metadata()
        url_prefix = "/generated_music/"
        default_artist = "Jam-Bot"
    else:
        music_dir = MUSIC_DIR
        metadata = load_music_metadata()
        url_prefix = "/music/"
        default_artist = "AI DJ"

    files = []
    for f in music_dir.iterdir():
        if f.is_file() and f.suffix.lower() in music_extensions:
            track_info = {
                "filename": f.name,
                "name": f.stem,
                "size_bytes": f.stat().st_size,
                "format": f.suffix.lower()[1:],
                "url_prefix": url_prefix,
                "playlist": playlist,
            }
            if f.name in metadata:
                meta = metadata[f.name]
                track_info.update({
                    "title": meta.get("title", f.stem),
                    "artist": meta.get("artist", default_artist),
                    "duration_seconds": meta.get("duration_seconds", 120),
                    "description": meta.get("description", ""),
                    "phone_number": meta.get("phone_number"),
                    "ad_copy": meta.get("ad_copy", ""),
                    "fun_facts": meta.get("fun_facts", []),
                    "genre": meta.get("genre", "Unknown"),
                    "energy": meta.get("energy", "medium"),
                    "dj_intro_hints": meta.get("dj_intro_hints", []),
                })
            else:
                track_info.update({
                    "title": f.stem,
                    "artist": default_artist,
                    "duration_seconds": 120,
                    "description": "A track from the music library!" if playlist == "library" else "An AI-generated original!",
                    "phone_number": None,
                    "ad_copy": "",
                    "fun_facts": [],
                    "genre": "Unknown",
                    "energy": "medium",
                    "dj_intro_hints": [],
                })
            files.append(track_info)

    # Apply saved order if present; unordered tracks fall to end alphabetically
    saved_order = load_playlist_order(playlist)
    if saved_order:
        order_index = {name: i for i, name in enumerate(saved_order)}
        fallback = len(saved_order)
        files.sort(key=lambda x: (order_index.get(x["filename"], fallback), x["name"].lower()))
    else:
        files.sort(key=lambda x: x["name"].lower())
    return files


def _build_dj_hints(track):
    """Build a DJ hints string from track metadata."""
    title = track.get("title", track["name"])
    description = track.get("description", "")
    phone = track.get("phone_number")
    ad_copy = track.get("ad_copy", "")
    fun_facts = track.get("fun_facts", [])
    duration = track.get("duration_seconds", 120)
    duration_str = f"{int(duration // 60)}:{int(duration % 60):02d}"

    hints = f"Title: {title}. Duration: {duration_str}."
    if description:
        hints += f" About: {description}"
    if phone:
        hints += f" Call: {phone}"
    if ad_copy:
        hints += f" Ad: {ad_copy}"
    if fun_facts:
        hints += f" Fun fact: {random.choice(fun_facts)}"
    return hints

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

music_bp = Blueprint("music", __name__)

# MIME type map shared by file-serving routes
_AUDIO_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
}


@music_bp.route("/music/<filename>")
def serve_music_file(filename):
    """Serve library music files."""
    music_path = _safe_path(MUSIC_DIR, filename)
    if music_path is None or not music_path.exists():
        return jsonify({"error": "Track not found"}), 404
    mime_type = _AUDIO_MIME_TYPES.get(music_path.suffix.lower(), "audio/mpeg")
    return send_file(music_path, mimetype=mime_type)


@music_bp.route("/generated_music/<filename>")
def serve_generated_music_file(filename):
    """Serve AI-generated music files."""
    music_path = _safe_path(GENERATED_MUSIC_DIR, filename)
    if music_path is None or not music_path.exists():
        return jsonify({"error": "Generated track not found"}), 404
    mime_type = _AUDIO_MIME_TYPES.get(music_path.suffix.lower(), "audio/mpeg")
    return send_file(music_path, mimetype=mime_type)


@music_bp.route("/api/music", methods=["GET"])
def handle_music():
    """
    All-in-one music endpoint.
    Query params:
      action   : list | play | pause | resume | stop | skip | next | next_up |
                 volume | status | shuffle | sync | confirm
      track    : track name or filename (for play)
      volume   : 0-100 (for volume action)
      playlist : 'library' | 'generated'
    """
    action = request.args.get("action", "list")
    track_param = request.args.get("track", "")
    volume_param = request.args.get("volume", "")
    playlist = request.args.get(
        "playlist", current_music_state.get("current_playlist", "library")
    )

    if playlist in ("library", "generated", "spotify"):
        current_music_state["current_playlist"] = playlist

    # ── SPOTIFY ───────────────────────────────────────────────────────────
    # Handle before get_music_files — Spotify has no local files
    if action == "spotify":
        track = request.args.get("track", "Unknown Track")
        artist = request.args.get("artist", "Spotify")
        album = request.args.get("album", "")
        current_music_state["current_playlist"] = "spotify"
        current_music_state["playing"] = True
        current_music_state["track_started_at"] = time.time()
        spotify_track = {
            "title": track,
            "name": track,
            "artist": artist,
            "album": album,
            "playlist": "spotify",
            "source": "spotify",
            "filename": None,
        }
        current_music_state["current_track"] = spotify_track
        print(f"🎵 Spotify mode: '{track}' by {artist}")
        return jsonify({
            "action": "spotify",
            "track": spotify_track,
            "playlist": "spotify",
            "source": "spotify",
            "response": f"Now streaming '{track}' by {artist} from Spotify.",
        })

    try:
        music_files = get_music_files(playlist)

        # ── LIST ──────────────────────────────────────────────────────────
        if action == "list":
            if playlist == "spotify":
                return jsonify({
                    "tracks": [],
                    "count": 0,
                    "playlist": "spotify",
                    "available_playlists": ["library", "generated", "spotify"],
                    "source": "spotify",
                    "response": "Spotify streaming mode. Ask me to play any song, album, or playlist on Spotify.",
                })
            if not music_files:
                return jsonify({
                    "tracks": [],
                    "count": 0,
                    "playlist": playlist,
                    "available_playlists": ["library", "generated", "spotify"],
                    "response": "I don't have any music yet! Upload some MP3s to my music folder and I'll spin them for you.",
                })
            track_names = [t["name"] for t in music_files]
            return jsonify({
                "tracks": music_files,
                "count": len(music_files),
                "playlist": playlist,
                "available_playlists": ["library", "generated", "spotify"],
                "response": (
                    f"I've got {len(music_files)} track{'s' if len(music_files) != 1 else ''} ready to spin: "
                    f"{', '.join(track_names[:5])}{'...' if len(track_names) > 5 else ''}"
                ),
            })

        # ── PLAY ──────────────────────────────────────────────────────────
        elif action == "play":
            if not music_files:
                return jsonify({"action": "error", "response": "No music files! My DJ booth is empty. Get me some tunes!"})

            selected = None
            if track_param:
                track_lower = track_param.lower()
                for t in music_files:
                    if (track_lower in t["name"].lower()
                            or track_lower in t["filename"].lower()
                            or track_lower in t.get("title", "").lower()):
                        selected = t
                        break
                if not selected:
                    return jsonify({
                        "action": "error",
                        "response": f"Can't find a track matching '{track_param}'. Try 'list music' to see what I have.",
                    })
            else:
                selected = random.choice(music_files)

            current_music_state["playing"] = True
            current_music_state["current_track"] = selected
            current_music_state["track_started_at"] = time.time()
            reservation_id = reserve_track(selected)

            title = selected.get("title", selected["name"])
            description = selected.get("description", "")
            duration = selected.get("duration_seconds", 120)

            return jsonify({
                "action": "play",
                "track": selected,
                "url": f"{selected.get('url_prefix', '/music/')}{selected['filename']}",
                "playlist": playlist,
                "duration_seconds": duration,
                "dj_hints": _build_dj_hints(selected),
                "reservation_id": reservation_id,
                "response": f"Now playing '{title}'! {description if description else 'Lets gooo!'}",
            })

        # ── PAUSE ─────────────────────────────────────────────────────────
        elif action == "pause":
            current_music_state["playing"] = False
            track_name = (current_music_state.get("current_track") or {}).get("name", "the music")
            return jsonify({"action": "pause", "response": f"Pausing {track_name}. Taking a breather."})

        # ── RESUME ────────────────────────────────────────────────────────
        elif action == "resume":
            current_music_state["playing"] = True
            track_name = (current_music_state.get("current_track") or {}).get("name", "the music")
            return jsonify({"action": "resume", "response": f"Resuming {track_name}. Back on the air!"})

        # ── STOP ──────────────────────────────────────────────────────────
        elif action == "stop":
            current_music_state["playing"] = False
            current_music_state["current_track"] = None
            return jsonify({"action": "stop", "response": "Music stopped. Silence... beautiful, terrible silence."})

        # ── SKIP / NEXT ───────────────────────────────────────────────────
        elif action in ("skip", "next"):
            if not music_files:
                return jsonify({"action": "error", "response": "No music to skip to!"})

            current_name = (current_music_state.get("current_track") or {}).get("name")
            available = [t for t in music_files if t["name"] != current_name] or music_files
            selected = random.choice(available)

            current_music_state["playing"] = True
            current_music_state["current_track"] = selected
            current_music_state["track_started_at"] = time.time()
            reservation_id = reserve_track(selected)

            title = selected.get("title", selected["name"])
            description = selected.get("description", "")
            duration = selected.get("duration_seconds", 120)

            return jsonify({
                "action": "play",
                "track": selected,
                "url": f"{selected.get('url_prefix', '/music/')}{selected['filename']}",
                "playlist": playlist,
                "duration_seconds": duration,
                "dj_hints": _build_dj_hints(selected),
                "reservation_id": reservation_id,
                "response": f"Skipping! Next up: '{title}'! {description if description else ''}",
            })

        # ── NEXT_UP ───────────────────────────────────────────────────────
        elif action == "next_up":
            if not music_files:
                return jsonify({"action": "error", "response": "No tracks available!"})

            current_name = (current_music_state.get("current_track") or {}).get("name")
            available = [t for t in music_files if t["name"] != current_name] or music_files
            selected = random.choice(available)
            current_music_state["next_track"] = selected

            title = selected.get("title", selected["name"])
            duration = selected.get("duration_seconds", 120)

            return jsonify({
                "action": "next_up",
                "track": selected,
                "duration_seconds": duration,
                "dj_hints": _build_dj_hints(selected),
                "response": f"Coming up next: '{title}'!",
            })

        # ── VOLUME ────────────────────────────────────────────────────────
        elif action == "volume":
            if not volume_param:
                current_vol = int(current_music_state["volume"] * 100)
                return jsonify({"action": "volume", "volume": current_vol, "response": f"Volume is at {current_vol}%."})

            try:
                new_vol = max(0, min(100, int(volume_param)))
                current_music_state["volume"] = new_vol / 100
                if new_vol >= 80:
                    comment = "Cranking it up! Let's make some noise!"
                elif new_vol >= 50:
                    comment = "Nice and loud. I like it."
                elif new_vol >= 20:
                    comment = "Background vibes. Got it."
                else:
                    comment = "Barely a whisper. You sure you want music?"
                return jsonify({"action": "volume", "volume": new_vol, "response": f"Volume set to {new_vol}%. {comment}"})
            except ValueError:
                return jsonify({"action": "error", "response": f"'{volume_param}' isn't a valid volume. Give me a number 0-100."})

        # ── STATUS ────────────────────────────────────────────────────────
        elif action == "status":
            track = current_music_state.get("current_track")
            playing = current_music_state.get("playing", False)
            vol = int(current_music_state["volume"] * 100)
            started_at = current_music_state.get("track_started_at")

            # Spotify mode — no local file, no timeline
            if track and track.get("source") == "spotify":
                title = track.get("title", "Unknown")
                artist = track.get("artist", "")
                artist_str = f" by {artist}" if artist else ""
                return jsonify({
                    "action": "status",
                    "playing": playing,
                    "track": track,
                    "source": "spotify",
                    "volume": vol,
                    "response": f"{'Streaming' if playing else 'Paused'}:'{title}'{artist_str} on Spotify.",
                })

            if track and playing:
                duration = track.get("duration_seconds", 120)
                elapsed = time.time() - started_at if started_at else 0
                remaining = max(0, duration - elapsed)
                title = track.get("title", track["name"])
                return jsonify({
                    "action": "status",
                    "playing": True,
                    "track": track,
                    "volume": vol,
                    "duration_seconds": duration,
                    "elapsed_seconds": int(elapsed),
                    "remaining_seconds": int(remaining),
                    "response": f"Now playing: '{title}' at {vol}% volume. About {int(remaining)}s remaining.",
                })
            elif track:
                title = track.get("title", track["name"])
                return jsonify({
                    "action": "status",
                    "playing": False,
                    "track": track,
                    "volume": vol,
                    "response": f"'{title}' is paused. Volume at {vol}%.",
                })
            else:
                return jsonify({
                    "action": "status",
                    "playing": False,
                    "track": None,
                    "volume": vol,
                    "response": "Nothing playing right now. Say 'play music' to get the party started!",
                })

        # ── SHUFFLE ───────────────────────────────────────────────────────
        elif action == "shuffle":
            current_music_state["shuffle"] = not current_music_state["shuffle"]
            state = "on" if current_music_state["shuffle"] else "off"
            return jsonify({
                "action": "shuffle",
                "shuffle": current_music_state["shuffle"],
                "response": f"Shuffle is {state}. {'Random chaos enabled!' if current_music_state['shuffle'] else 'Back to order.'}",
            })

        # ── SYNC ──────────────────────────────────────────────────────────
        elif action == "sync":
            reserved = get_reserved_track()
            if reserved:
                title = reserved.get("title", reserved["name"])
                duration = reserved.get("duration_seconds", 120)
                print(f"🎵 SYNC returning reserved track: {title}")
                return jsonify({
                    "action": "play",
                    "track": reserved,
                    "url": f"/music/{reserved['filename']}",
                    "duration_seconds": duration,
                    "reservation_id": current_music_state.get("reservation_id"),
                    "synced": True,
                    "response": f"Synced to '{title}'",
                })

            track = current_music_state.get("current_track")
            if track and current_music_state.get("playing"):
                title = track.get("title", track["name"])
                duration = track.get("duration_seconds", 120)
                print(f"🎵 SYNC returning current track: {title}")
                return jsonify({
                    "action": "play",
                    "track": track,
                    "url": f"/music/{track['filename']}",
                    "duration_seconds": duration,
                    "synced": True,
                    "response": f"Synced to '{title}'",
                })

            print("🎵 SYNC: No track to sync")
            return jsonify({"action": "none", "synced": True, "response": "No track to sync to"})

        # ── CONFIRM ───────────────────────────────────────────────────────
        elif action == "confirm":
            res_id = request.args.get("reservation_id", "")
            current_res_id = current_music_state.get("reservation_id", "")
            if res_id and res_id == current_res_id:
                track = current_music_state.get("reserved_track")
                title = track.get("title", track["name"]) if track else "Unknown"
                clear_reservation()
                print(f"🎵 Playback confirmed for: {title}")
                return jsonify({"action": "confirmed", "response": f"Playback confirmed: {title}"})
            else:
                return jsonify({"action": "error", "response": "Invalid or expired reservation"})

        else:
            return jsonify({
                "action": "error",
                "response": f"Unknown action '{action}'. Try: list, play, pause, stop, skip, volume, status, sync",
            })

    except Exception as e:
        print(f"Music error: {e}")
        return jsonify({"action": "error", "response": "Music playback error"})


@music_bp.route("/api/music/transition", methods=["POST", "GET"])
def handle_dj_transition():
    """
    DJ transition endpoint.
    POST: Frontend signals song is ending; pre-queue next track.
    GET : Agent polls for pending transition.
    """
    if request.method == "POST":
        data = request.get_json() or {}
        remaining = data.get("remaining_seconds", 10)

        music_files = get_music_files()
        current_name = (current_music_state.get("current_track") or {}).get("name")
        available = [t for t in music_files if t["name"] != current_name] or music_files

        if available:
            selected = random.choice(available)
            current_music_state["next_track"] = selected
            current_music_state["dj_transition_pending"] = True

            title = selected.get("title", selected["name"])
            description = selected.get("description", "")
            fun_facts = selected.get("fun_facts", [])

            return jsonify({
                "status": "transition_queued",
                "next_track": selected,
                "remaining_seconds": remaining,
                "response": f"Coming up next: '{title}'! {random.choice(fun_facts) if fun_facts else description}",
            })
        else:
            return jsonify({"status": "no_tracks", "response": "No more tracks to play!"})

    else:  # GET
        if current_music_state.get("dj_transition_pending") and current_music_state.get("next_track"):
            track = current_music_state["next_track"]
            current_music_state["dj_transition_pending"] = False

            title = track.get("title", track["name"])
            duration = track.get("duration_seconds", 120)

            return jsonify({
                "transition_pending": True,
                "next_track": track,
                "dj_hints": _build_dj_hints(track),
                "response": f"Hey! Song's ending soon. Coming up next: '{title}'!",
            })
        else:
            return jsonify({"transition_pending": False, "response": "No transition pending."})


@music_bp.route("/api/music/upload", methods=["POST"])
def upload_music():
    """Upload a music file to the library playlist."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    allowed_extensions = {".mp3", ".wav", ".ogg", ".m4a", ".webm"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_extensions:
        return jsonify({"error": f"Invalid format. Allowed: {', '.join(allowed_extensions)}"}), 400

    safe_name = "".join(c for c in Path(file.filename).stem if c.isalnum() or c in " _-")
    safe_name = safe_name[:50] + ext

    save_path = MUSIC_DIR / safe_name
    file.save(save_path)

    return jsonify({
        "status": "success",
        "filename": safe_name,
        "response": f"Track '{safe_name}' uploaded! Ready to spin.",
    })


# ---------------------------------------------------------------------------
# Playlist CRUD endpoints (P2-T4 requirement)
# ---------------------------------------------------------------------------

@music_bp.route("/api/music/playlists", methods=["GET"])
def list_playlists():
    """List all available playlists with track counts and total sizes."""
    playlists = []
    for name, music_dir in (("library", MUSIC_DIR), ("generated", GENERATED_MUSIC_DIR)):
        music_extensions = {".mp3", ".wav", ".ogg", ".m4a", ".webm"}
        tracks = [f for f in music_dir.iterdir() if f.is_file() and f.suffix.lower() in music_extensions]
        playlists.append({
            "name": name,
            "track_count": len(tracks),
            "total_size_bytes": sum(f.stat().st_size for f in tracks),
            "active": current_music_state.get("current_playlist") == name,
        })
    # Spotify is a virtual playlist — no local files
    playlists.append({
        "name": "spotify",
        "track_count": None,
        "total_size_bytes": None,
        "active": current_music_state.get("current_playlist") == "spotify",
        "source": "spotify",
        "description": "Stream any song, album, or playlist from Spotify",
    })
    return jsonify({"playlists": playlists})


@music_bp.route("/api/music/track/<playlist>/<filename>", methods=["DELETE"])
def delete_track(playlist, filename):
    """Delete a track from a playlist."""
    if playlist == "generated":
        music_dir = GENERATED_MUSIC_DIR
        load_meta = load_generated_music_metadata
        save_meta = save_generated_music_metadata
    elif playlist == "library":
        music_dir = MUSIC_DIR
        load_meta = load_music_metadata
        save_meta = save_music_metadata
    else:
        return jsonify({"error": f"Unknown playlist '{playlist}'"}), 400

    safe_filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
    track_path = music_dir / safe_filename

    if not track_path.exists():
        return jsonify({"error": "Track not found"}), 404

    track_path.unlink()

    # Remove from metadata if present
    metadata = load_meta()
    if safe_filename in metadata:
        del metadata[safe_filename]
        save_meta(metadata)

    # Clear from state if this was the active/reserved track
    current = current_music_state.get("current_track") or {}
    if current.get("filename") == safe_filename:
        current_music_state["current_track"] = None
        current_music_state["playing"] = False
    reserved = current_music_state.get("reserved_track") or {}
    if reserved.get("filename") == safe_filename:
        clear_reservation()

    return jsonify({"status": "deleted", "filename": safe_filename, "playlist": playlist})


@music_bp.route("/api/music/playlist/<playlist>/order", methods=["GET", "POST"])
def playlist_order(playlist):
    """
    GET : Return the saved track order for the playlist (list of filenames).
    POST: Save a new track order.  Body: {"order": ["file1.mp3", "file2.mp3", ...]}
    """
    if playlist not in ("library", "generated"):
        return jsonify({"error": f"Unknown playlist '{playlist}'"}), 400

    if request.method == "GET":
        return jsonify({"playlist": playlist, "order": load_playlist_order(playlist)})

    data = request.get_json()
    if not data or not isinstance(data.get("order"), list):
        return jsonify({"error": "Body must be JSON with 'order' array of filenames"}), 400

    order = [str(f) for f in data["order"]]
    save_playlist_order(playlist, order)
    return jsonify({"status": "saved", "playlist": playlist, "order": order})


@music_bp.route("/api/music/track/<playlist>/<filename>/metadata", methods=["PUT"])
def update_track_metadata(playlist, filename):
    """Update metadata for a track (title, artist, description, etc.)."""
    if playlist == "generated":
        load_meta = load_generated_music_metadata
        save_meta = save_generated_music_metadata
        music_dir = GENERATED_MUSIC_DIR
    elif playlist == "library":
        load_meta = load_music_metadata
        save_meta = save_music_metadata
        music_dir = MUSIC_DIR
    else:
        return jsonify({"error": f"Unknown playlist '{playlist}'"}), 400

    safe_filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
    track_path = music_dir / safe_filename
    if not track_path.exists():
        return jsonify({"error": "Track not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body provided"}), 400

    allowed_fields = {"title", "artist", "description", "duration_seconds", "phone_number",
                      "ad_copy", "fun_facts", "genre", "energy", "dj_intro_hints"}
    metadata = load_meta()
    entry = metadata.get(safe_filename, {})
    for field in allowed_fields:
        if field in data:
            entry[field] = data[field]
    metadata[safe_filename] = entry
    save_meta(metadata)

    return jsonify({"status": "updated", "filename": safe_filename, "playlist": playlist, "metadata": entry})
