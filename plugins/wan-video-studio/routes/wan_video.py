"""
routes/wan_video.py — Video Studio plugin backend (image-to-video).

THE BRIDGE (browser -> host generation):
  The OVU container CANNOT reach /mnt/agent-mesh, but each tenant's uploads/ dir
  IS shared OVU<->host. So this route is pure file-I/O (NO gradio_client, NO new
  deps): it drops a request JSON into  <uploads>/.wan-requests/<rid>.json  and the
  host drain (scripts/wan-video-drain.sh, cron */1) sweeps it, runs the proven
  wan-video.sh wrapper (gradio_client + HF_TOKEN + ZeroGPU on the host), saves the
  .mp4 into the tenant uploads (served at /uploads/<f>), and writes the result back
  to  <uploads>/.wan-requests/<rid>.result.json  which this route polls.

Endpoints (registered with full paths — the blueprint is mounted without a url_prefix):
  POST /api/wan-video               -> queue a generation, returns {rid}
  GET  /api/wan-video/result/<rid>  -> {status:"pending"} or the drain's result json
  GET  /api/wan-video/gallery       -> recent ai-gen-wan-*.mp4 in the tenant uploads

No-follow I/O (review 4 of OpenVoiceUI PR #597, R4-B2): uploads/ is writable by the
tenant's openclaw agent, which can plant `uploads/.wan-requests -> /app/routes`. The old
code then ran os.chmod(<that>, 0o777) BY NAME, making a server directory world-writable
(the terminal sandbox could replace /app/routes/*.py), and read `<rid>.result.json`
through any planted symlink. Every file and directory here is now opened without
following a symlink, relative to a pinned directory fd, and modes are set with fchmod
on those fds. With services/safe_open (OVU >= PR #597) it uses Anchored; on an older
image (this plugin dir is a per-tenant mount, rolled separately from the image) it uses
its own fd-based no-follow helpers below.
"""

import errno
import fnmatch
import json
import logging
import os
import re
import stat
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

try:
    from services.paths import UPLOADS_DIR
except Exception:  # pragma: no cover - fallback if import path differs
    UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "/app/runtime/uploads"))

logger = logging.getLogger(__name__)
wan_video_bp = Blueprint("wan_video", __name__)

try:   # no-follow, beneath-root I/O (OVU >= PR #597)
    from services.safe_open import Anchored as _Anchored
except ImportError:   # an older OVU image: fall back to the fd-based helpers below
    _Anchored = None

REQUESTS_DIR = UPLOADS_DIR / ".wan-requests"
_RESULT_MAX_BYTES = 1024 * 1024

# Tenant for the request envelope / drain routing (same source fal.py uses).
_TENANT = (os.environ.get("JAMBOT_TENANT")
           or os.environ.get("HOST_TENANT", "")
           or "host")

# The Space's scheduler whitelist (defensive validation — no arbitrary strings).
_SCHEDULERS = {
    "FlowMatchEulerDiscrete", "SASolver", "DEISMultistep",
    "DPMSolverMultistepInverse", "UniPCMultistep",
    "DPMSolverMultistep", "DPMSolverSinglestep",
}
_FRAME_MULTIPLIERS = {16, 32, 64, 128}


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _uploads_fd() -> int:
    """fd on the uploads MOUNT point (its own path is not agent-replaceable; same as
    safe_open.open_root). Everything below it is opened relative to this fd, no-follow."""
    return os.open(str(UPLOADS_DIR), _DIR_FLAGS)


def _requests_dir_fd(create: bool) -> int:
    """fd on uploads/.wan-requests, never following a symlink (ELOOP/ENOTDIR -> OSError)."""
    rfd = _uploads_fd()
    try:
        try:
            return os.open(REQUESTS_DIR.name, _DIR_FLAGS, dir_fd=rfd)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(REQUESTS_DIR.name, 0o777, dir_fd=rfd)
            except FileExistsError:
                pass
            return os.open(REQUESTS_DIR.name, _DIR_FLAGS, dir_fd=rfd)
    finally:
        os.close(rfd)


def _ensure_requests_dir() -> None:
    """Create the shared request dir world-writable so the HOST drain (uid mike)
    can write result.json back into a dir this container (uid appuser) created.
    The mode is set on a no-follow fd: a planted `.wan-requests` symlink is refused
    (OSError), never followed and never chmod'ed."""
    if _Anchored is not None:
        d = _Anchored(UPLOADS_DIR, REQUESTS_DIR.name)
        d.mkdir(mode=0o777, exist_ok=True)
        try:
            d.chmod(0o777)
        except OSError:
            pass   # not ours (e.g. created by the host): leave its mode
        return
    dfd = _requests_dir_fd(create=True)
    try:
        try:
            os.fchmod(dfd, 0o777)
        except OSError:
            pass
    finally:
        os.close(dfd)


def _write_request(rid: str, text: str) -> None:
    """uploads/.wan-requests/<rid>.json: random O_EXCL|O_NOFOLLOW temp in the pinned dir,
    fchmod 0666, rename (atomic: the drain never sees a half-written request)."""
    if _Anchored is not None:
        _Anchored(UPLOADS_DIR, f"{REQUESTS_DIR.name}/{rid}.json").atomic_write_text(text, mode=0o666)
        return
    dfd = _requests_dir_fd(create=True)
    try:
        for _ in range(16):
            tmp = f".{rid}.{os.urandom(8).hex()}.json.tmp"
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o666, dir_fd=dfd)
                break
            except FileExistsError:
                continue
        else:
            raise OSError(errno.EEXIST, "wan-video: could not create a temp file")
        try:
            try:
                os.fchmod(fd, 0o666)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, f"{rid}.json", src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def _read_result(rid: str):
    """Text of uploads/.wan-requests/<rid>.result.json, or None if absent.
    Refuses a symlink / non-regular file (OSError) and anything over 1 MiB."""
    name = f"{rid}.result.json"
    if _Anchored is not None:
        a = _Anchored(UPLOADS_DIR, f"{REQUESTS_DIR.name}/{name}")
        return a.read_text(max_bytes=_RESULT_MAX_BYTES) if a.is_file() else None
    try:
        dfd = _requests_dir_fd(create=False)
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > _RESULT_MAX_BYTES:
                raise OSError(errno.EINVAL, "wan-video: result is not a small regular file")
            return f.read(_RESULT_MAX_BYTES + 1).decode("utf-8")
    finally:
        os.close(dfd)


def _basename_from_upload_url(u: str) -> str:
    """Resolve a '/uploads/<f>' (or bare filename) to its basename. Returns '' for
    anything that escapes the uploads dir (path traversal guard)."""
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u  # pass URLs straight through; wan-video.sh handle_file() accepts them
    name = os.path.basename(u.split("?")[0])
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return ""
    return name


def _clamp(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


@wan_video_bp.route("/api/wan-video", methods=["POST"])
def queue_generation():
    body = request.get_json(silent=True) or {}

    image = _basename_from_upload_url(body.get("image_url") or body.get("image") or "")
    if not image:
        return jsonify({"error": "image_url is required (upload an image first)"}), 400

    last_image = _basename_from_upload_url(body.get("last_image_url") or body.get("last_image") or "")

    prompt = (body.get("prompt") or "").strip() or \
        "make this image come alive, cinematic motion, smooth animation"
    prompt = prompt[:2000]

    rid = uuid.uuid4().hex

    # Build the request envelope. Advanced params are only included when the caller
    # set them; the host wrapper falls back to the Space defaults for anything absent.
    req = {
        "id": rid,
        "tenant": _TENANT,
        "image": image,                       # basename (or URL) — drain resolves to host path
        "image_url": body.get("image_url") or f"/uploads/{image}",
        "prompt": prompt,
        "duration": _clamp(body.get("duration"), 1.0, 8.0, 3.5),
        "steps": int(_clamp(body.get("steps"), 1, 20, 6)),
        "source": "video-studio",
        "created_at": int(time.time()),
    }
    if last_image:
        req["last_image"] = last_image

    neg = (body.get("negative_prompt") or "").strip()
    if neg:
        req["negative_prompt"] = neg[:1000]

    sched = (body.get("scheduler") or "").strip()
    if sched in _SCHEDULERS:
        req["scheduler"] = sched

    if body.get("guidance_scale") not in (None, ""):
        req["guidance_scale"] = _clamp(body.get("guidance_scale"), 0.0, 20.0, 1.0)
    if body.get("guidance_scale_2") not in (None, ""):
        req["guidance_scale_2"] = _clamp(body.get("guidance_scale_2"), 0.0, 20.0, 1.0)
    if body.get("quality") not in (None, ""):
        req["quality"] = int(_clamp(body.get("quality"), 1, 10, 6))
    if body.get("flow_shift") not in (None, ""):
        req["flow_shift"] = _clamp(body.get("flow_shift"), 0.0, 12.0, 3.0)

    fm = body.get("frame_multiplier")
    if fm not in (None, ""):
        try:
            fm = int(fm)
            if fm in _FRAME_MULTIPLIERS:
                req["frame_multiplier"] = fm
        except (TypeError, ValueError):
            pass

    # Seed handling: explicit seed disables randomize; otherwise randomize.
    if body.get("seed") not in (None, "") and not body.get("randomize_seed", False):
        try:
            req["seed"] = int(float(body.get("seed")))
            req["randomize_seed"] = False
        except (TypeError, ValueError):
            req["randomize_seed"] = True
    else:
        req["randomize_seed"] = True

    if body.get("safe_mode") is False:
        req["safe_mode"] = False

    try:
        _ensure_requests_dir()
        _write_request(rid, json.dumps(req, indent=2))
    except Exception as e:
        logger.exception("wan-video: failed to write request")
        return jsonify({"error": f"could not queue request: {e}"}), 500

    logger.info("wan-video: queued %s (image=%s, tenant=%s)", rid, image, _TENANT)
    return jsonify({"rid": rid, "status": "queued"})


@wan_video_bp.route("/api/wan-video/result/<rid>", methods=["GET"])
def get_result(rid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid or ""):
        return jsonify({"error": "bad rid"}), 400
    try:
        text = _read_result(rid)
    except Exception:
        return jsonify({"status": "error", "reason": "could not read result"}), 200
    if text is not None:
        try:
            data = json.loads(text)
            data.setdefault("status", "ok")
            return jsonify(data)
        except Exception:
            return jsonify({"status": "error", "reason": "could not read result"}), 200
    return jsonify({"status": "pending"})


@wan_video_bp.route("/api/wan-video/gallery", methods=["GET"])
def gallery():
    limit = 24
    try:
        limit = min(int(request.args.get("limit", 24)), 100)
    except (TypeError, ValueError):
        pass
    items = []
    try:
        # lstat only: a planted `ai-gen-wan-x.mp4 -> /anything` is a link, not listed
        rfd = _uploads_fd()
        try:
            with os.scandir(rfd) as it:
                for e in it:
                    if not fnmatch.fnmatchcase(e.name, "ai-gen-wan-*.mp4"):
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    st = e.stat(follow_symlinks=False)
                    items.append({
                        "url": f"/uploads/{e.name}",
                        "name": e.name,
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                    })
        finally:
            os.close(rfd)
    except Exception as e:
        logger.warning("wan-video: gallery scan failed: %s", e)
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"videos": items[:limit], "count": len(items)})
