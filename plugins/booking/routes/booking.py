"""
routes/booking.py — Booking (Cal.diy) Blueprint

Bridges OpenVoiceUI to the shared Cal.diy instance at cal.jam-bot.com.
Cal.diy is Cal.com-lineage: REST API v2 with Bearer auth, webhooks signed
with HMAC-SHA256 in the `x-cal-signature-256` header.

All endpoints are prefixed `/api/booking/`. The canvas pages and the voice
agent skill call THESE routes — never Cal.diy directly — so the per-tenant
API key stays server-side.

Architecture: docs/jambot/booking-system-cal-diy.md
  - ONE shared Cal.diy instance, one user account per client.
  - The webhook receiver fans out BOOKING_* events to InkBox SMS + Twenty CRM.

Config resolution order (per value):
  1. Server-side config file (UPLOADS_DIR/.booking-config.json, legacy
     UPLOADS_DIR/booking-config.json read when the dotfile is absent) — set via the
     setup wizard, survives container restarts (bind-mounted volume).
  2. Environment variable (CAL_API_KEY / CAL_API_URL / CAL_USERNAME).
  3. Hard default (CAL_API_URL only → https://cal.jam-bot.com).

The Cal.diy service is NOT deployed yet. Every endpoint degrades gracefully:
when no API key is configured, status reports configured=false and the proxy
endpoints return a clear 503 "not configured" instead of crashing.

Endpoints registered:
  GET  /api/booking/status                     — configured? reachable?
  GET  /api/booking/slots                      — available slots (proxy)
  GET  /api/booking/bookings                   — list bookings (proxy)
  POST /api/booking/bookings                   — create a booking (proxy)
  POST /api/booking/bookings/<uid>/cancel      — cancel a booking (proxy)
  POST /api/booking/block                      — reserve/block slots for a day
  POST /api/booking/webhook                    — signed Cal.diy webhook receiver (no login;
                                                 HMAC required, fails closed)
  GET  /api/booking/config                     — read saved config (key masked)
  POST /api/booking/config                     — save config server-side
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

import requests as http_requests
from flask import Blueprint, jsonify, request

from services.paths import UPLOADS_DIR

try:   # no-follow, beneath-root I/O (OVU >= PR #597)
    from services.safe_open import Anchored as _Anchored
except ImportError:   # an older OVU image: this plugin dir is mounted per tenant and is
    _Anchored = None  # rolled separately from the image, so it must work on both

logger = logging.getLogger(__name__)

booking_bp = Blueprint("booking", __name__)

# ---------------------------------------------------------------------------
# Configuration (env defaults at module load; config file overrides at request
# time so the setup wizard can change things without a container restart)
# ---------------------------------------------------------------------------

# Public default so the plugin is usable the moment Cal.diy is deployed.
CAL_API_URL_DEFAULT = os.environ.get("CAL_API_URL", "https://cal.jam-bot.com").rstrip("/")
CAL_API_KEY_ENV = os.environ.get("CAL_API_KEY", "")
CAL_USERNAME_ENV = os.environ.get("CAL_USERNAME", "")
# Webhook signing secret. Shared per-tenant secret registered with Cal.diy when
# the webhook subscription is created. Falls back to the config file value.
CAL_WEBHOOK_SECRET_ENV = os.environ.get("CAL_WEBHOOK_SECRET", "")
CAL_TIMEOUT = int(os.environ.get("CAL_API_TIMEOUT", "20"))
# Cal.com / Cal.diy REST API v2 requires a version header on many endpoints.
CAL_API_VERSION = os.environ.get("CAL_API_VERSION", "2024-08-13")

# Server-side config + the per-tenant booking event log both live under
# UPLOADS_DIR (confirmed bind-mounted per-tenant to
# /mnt/clients/<user>/openvoiceui/uploads — the bare RUNTIME_DIR root is NOT
# itself a single bind mount, only specific named subdirectories under it are;
# a file written directly at RUNTIME_DIR root would silently live only in the
# container's writable layer and be LOST on the next recreate. Caught and
# fixed 2026-07-23 before this plugin was ever actually deployed to a tenant.)
#
# 2026-11-19 (SEC-092): both are DOTFILES. routes/static_files.py serve_upload refuses every
# dotfile in every UPLOADS_AUTH_MODE, while the old names were downloadable unauthenticated at
# /uploads/booking-config.json (Cal API key + Cal login password) on every tenant whose uploads
# mode was not "enforce". The legacy file is still READ when no dotfile exists yet (never
# deleted, never written again); the first save or the host provisioning script creates the
# dotfile. scripts/cal-provision-tenant.sh writes the dotfile for new tenants.
CONFIG_PATH = UPLOADS_DIR / ".booking-config.json"
LEGACY_CONFIG_PATH = UPLOADS_DIR / "booking-config.json"
WEBHOOK_LOG_PATH = UPLOADS_DIR / ".booking-events.jsonl"
# Outbound notify requests are dropped here for a host-side cron to drain and
# send via InkBox — OVU containers do NOT have /mnt/agent-mesh mounted (only
# openclaw does), so the usual broker-queue-under-agent-mesh pattern doesn't
# reach from here. UPLOADS_DIR is bind-mounted and host-globbable instead:
# scripts/booking-sms-notify-drain.sh globs
# /mnt/clients/*/openvoiceui/uploads/.booking-notify-queue/*.json fleet-wide.
NOTIFY_QUEUE_DIR = UPLOADS_DIR / ".booking-notify-queue"
# The webhook signing secret lives in its OWN dotfile, never in booking-config.json:
# routes/static_files.py serve_upload refuses every dotfile in every UPLOADS_AUTH_MODE,
# while booking-config.json itself is reachable at /uploads/booking-config.json on a
# tenant whose uploads mode is not "enforce" (measured 2026-11-19). A secret anyone can
# download signs nothing. Written by the host provisioning script (or POST /config).
WEBHOOK_SECRET_PATH = UPLOADS_DIR / ".booking-webhook-secret"
# The webhook is the one booking route reachable without a login (see
# _install_public_webhook_gate). Cal.com payloads are a few KB; refuse anything big
# before hashing it.
WEBHOOK_PATH = "/api/booking/webhook"
WEBHOOK_MAX_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# Config persistence (mirror of routes/onboarding.py + airadio_bridge.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# No-follow file I/O. uploads/ is agent-writable (the tenant's openclaw agent
# mounts it read-write), so every file this plugin touches there is opened
# without following symlinks, relative to a pinned directory fd, and replaced
# through an UNPREDICTABLE O_EXCL temp. The old `booking-config.json.tmp` /
# `<ts>.json.tmp` names were predictable: the agent could pre-plant one as a
# symlink to /app/server.py (or the vault, or the server's private dir) and
# POST /api/booking/config wrote agent-chosen JSON into it (review 3 of
# OpenVoiceUI PR #597, B3).
# ---------------------------------------------------------------------------

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _root_fd(root) -> int:
    """fd on the uploads MOUNT point (its own path is not agent-replaceable)."""
    return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def _subdir_fd(root, sub, create=False) -> int:
    """fd on root/sub (one component, '' = root), never following a symlink."""
    rfd = _root_fd(root)
    if not sub:
        return rfd
    try:
        try:
            return os.open(sub, _DIR_FLAGS, dir_fd=rfd)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(sub, 0o755, dir_fd=rfd)
            except FileExistsError:
                pass
            return os.open(sub, _DIR_FLAGS, dir_fd=rfd)
    finally:
        os.close(rfd)


def _split(rel):
    parts = [p for p in str(rel).split("/") if p]
    if not parts or len(parts) > 2 or any(p in (".", "..") for p in parts):
        raise ValueError(f"booking: bad relative path {rel!r}")
    return ("/".join(parts[:-1]), parts[-1])


def _read_text(root, rel):
    """root/rel as text, or None if absent. Refuses a symlink."""
    if _Anchored is not None:
        a = _Anchored(root, rel)
        return a.read_text() if a.is_file() else None
    sub, name = _split(rel)
    dfd = _subdir_fd(root, sub)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        os.close(dfd)


def _atomic_write_text(root, rel, text, create_dirs=False) -> None:
    """Replace root/rel: random O_EXCL|O_NOFOLLOW temp in the pinned dir, then rename."""
    if _Anchored is not None:
        _Anchored(root, rel).atomic_write_text(text, create_dirs=create_dirs)
        return
    sub, name = _split(rel)
    dfd = _subdir_fd(root, sub, create=create_dirs)
    try:
        for _ in range(16):
            tmp = f".{name[:40]}.{os.urandom(8).hex()}.tmp"
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o644, dir_fd=dfd)
                break
            except FileExistsError:
                continue
        else:
            raise OSError("booking: could not create a temp file")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def _append_text(root, rel, text) -> None:
    """Append to root/rel, creating it; refuses a symlink."""
    if _Anchored is not None:
        _Anchored(root, rel).append_text(text)
        return
    sub, name = _split(rel)
    dfd = _subdir_fd(root, sub)
    try:
        fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o644, dir_fd=dfd)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(text)
    finally:
        os.close(dfd)


def _load_config() -> dict:
    """Read the server-side config: the dotfile, else the legacy served-name file. {} if neither."""
    for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        try:
            raw = _read_text(path.parent, path.name)
            if raw is not None:
                return json.loads(raw)
        except (OSError, ValueError) as e:
            logger.warning("booking: cannot read config file %s: %s", path.name, e)
    return {}


def _save_config(data: dict) -> None:
    """Persist the config file atomically (no-follow temp, then rename)."""
    _atomic_write_text(CONFIG_PATH.parent, CONFIG_PATH.name, json.dumps(data, indent=2))


def _api_url() -> str:
    cfg = _load_config()
    return (cfg.get("api_url") or CAL_API_URL_DEFAULT).rstrip("/")


def _api_key() -> str:
    """Resolve the Cal.diy API key: config file first, then env."""
    cfg = _load_config()
    return cfg.get("api_key") or CAL_API_KEY_ENV


def _username() -> str:
    cfg = _load_config()
    return cfg.get("username") or CAL_USERNAME_ENV


def _webhook_secret() -> str:
    """Signing secret: the dotfile first (never served), then a legacy config value, then env."""
    try:
        raw = _read_text(WEBHOOK_SECRET_PATH.parent, WEBHOOK_SECRET_PATH.name)
        if raw and raw.strip():
            return raw.strip()
    except (OSError, ValueError) as e:
        logger.warning("booking: cannot read webhook secret file: %s", e)
    cfg = _load_config()
    return cfg.get("webhook_secret") or CAL_WEBHOOK_SECRET_ENV


def _mask(secret: str) -> str:
    """Mask a secret for safe display (keep last 4 chars)."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "•" * len(secret)
    return "•" * (len(secret) - 4) + secret[-4:]


def _cal_headers(json_body: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "cal-api-version": CAL_API_VERSION,
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _not_configured():
    """Standard 503 payload when no API key is set yet."""
    return jsonify({
        "ok": False,
        "configured": False,
        "error": "Booking is not configured. Open Booking Setup and enter your "
                 "Cal.diy API key and username.",
    }), 503


def _proxy(method: str, path: str, *, params=None, json_body=None):
    """
    Proxy a request to the Cal.diy REST API v2, attaching the tenant's key.
    Returns a (flask_response, status_code) tuple. Degrades to a clear error
    when unconfigured or when Cal.diy is unreachable (service not deployed).
    """
    if not _api_key():
        return _not_configured()

    url = f"{_api_url()}{path}"
    try:
        resp = http_requests.request(
            method,
            url,
            headers=_cal_headers(json_body=json_body is not None),
            params=params,
            json=json_body,
            timeout=CAL_TIMEOUT,
        )
    except http_requests.ConnectionError:
        return jsonify({
            "ok": False,
            "reachable": False,
            "error": "Cannot reach the Cal.diy booking service. It may not be "
                     "deployed yet, or the URL is wrong.",
        }), 502
    except http_requests.Timeout:
        return jsonify({"ok": False, "error": "Cal.diy request timed out."}), 504
    except Exception as e:  # noqa: BLE001 — surface any client error cleanly
        logger.error("booking proxy error %s %s: %s", method, path, e)
        return jsonify({"ok": False, "error": str(e)}), 500

    # Pass Cal.diy's JSON body and status straight through.
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return jsonify(body), resp.status_code


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@booking_bp.route("/api/booking/status", methods=["GET"])
def status():
    """Report whether booking is configured and whether Cal.diy is reachable."""
    key = _api_key()
    configured = bool(key)
    out = {
        "configured": configured,
        "api_url": _api_url(),
        "username": _username(),
        "webhook_configured": bool(_webhook_secret()),
    }
    if not configured:
        out["reachable"] = False
        out["note"] = "No Cal.diy API key set. Configure it in Booking Setup."
        return jsonify(out)

    # Light reachability probe — list one booking. Any HTTP answer (even 401)
    # means the service is up; only a connection failure means "unreachable".
    try:
        resp = http_requests.get(
            f"{_api_url()}/v2/bookings",
            headers=_cal_headers(),
            params={"take": 1},
            timeout=CAL_TIMEOUT,
        )
        out["reachable"] = True
        out["authenticated"] = resp.status_code not in (401, 403)
        out["http_status"] = resp.status_code
    except http_requests.RequestException:
        out["reachable"] = False
        out["note"] = "Cal.diy not reachable (service may not be deployed yet)."
    return jsonify(out)


# ---------------------------------------------------------------------------
# Availability / slots
# ---------------------------------------------------------------------------

@booking_bp.route("/api/booking/slots", methods=["GET"])
def slots():
    """Proxy GET /v2/slots — available slots for an event type in a date range."""
    params = {}
    for k in ("eventTypeId", "eventTypeSlug", "username", "start", "end", "timeZone", "duration"):
        v = request.args.get(k)
        if v:
            params[k] = v
    # Default username to the tenant's own Cal.diy account when not specified.
    if "username" not in params and "eventTypeId" not in params and _username():
        params["username"] = _username()
    return _proxy("GET", "/v2/slots", params=params)


# ---------------------------------------------------------------------------
# Bookings — list / create / cancel
# ---------------------------------------------------------------------------

@booking_bp.route("/api/booking/bookings", methods=["GET"])
def list_bookings():
    """Proxy GET /v2/bookings — list bookings, default status=upcoming."""
    params = {"status": request.args.get("status", "upcoming")}
    for k in ("take", "skip", "eventTypeId", "attendeeEmail"):
        v = request.args.get(k)
        if v:
            params[k] = v
    return _proxy("GET", "/v2/bookings", params=params)


@booking_bp.route("/api/booking/bookings", methods=["POST"])
def create_booking():
    """Proxy POST /v2/bookings — create a booking."""
    body = request.get_json(silent=True) or {}
    return _proxy("POST", "/v2/bookings", json_body=body)


@booking_bp.route("/api/booking/bookings/<uid>/cancel", methods=["POST"])
def cancel_booking(uid):
    """Proxy POST /v2/bookings/<uid>/cancel — cancel a booking by uid."""
    body = request.get_json(silent=True) or {}
    return _proxy("POST", f"/v2/bookings/{uid}/cancel", json_body=body)


# ---------------------------------------------------------------------------
# Block time ("I'm busy Wednesday")
# ---------------------------------------------------------------------------

@booking_bp.route("/api/booking/block", methods=["POST"])
def block_time():
    """
    Reserve/block slots for a day so customers can't book them.

    Body: {"start": "2026-06-10T00:00:00.000Z",
            "end":   "2026-06-10T23:59:59.000Z",
            "eventTypeId": 123,            (optional)
            "reason": "Out of office"}     (optional, for the log)

    Cal.com/Cal.diy reserves a slot via POST /v2/slots/reservations. We forward
    the start/end window; one reservation marks the agent busy for that period.
    """
    body = request.get_json(silent=True) or {}
    if not body.get("start") or not body.get("end"):
        return jsonify({"ok": False, "error": "start and end are required"}), 400

    reservation = {
        "slotStart": body["start"],
        "slotEnd": body["end"],
    }
    if body.get("eventTypeId"):
        reservation["eventTypeId"] = body["eventTypeId"]
    # Reservation duration in seconds — keep the block long-lived.
    reservation["reservationDuration"] = int(body.get("reservationDuration", 86400))

    return _proxy("POST", "/v2/slots/reservations", json_body=reservation)


# ---------------------------------------------------------------------------
# Webhook receiver — verify HMAC, log, fan out
# ---------------------------------------------------------------------------

def _verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """
    Verify the x-cal-signature-256 header. Cal.com signs the raw request body
    with HMAC-SHA256 using the webhook secret; the header is the hex digest.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Accept either bare hex or a "sha256=" prefix, just in case.
    candidate = signature.split("=", 1)[1] if "=" in signature else signature
    return hmac.compare_digest(expected, candidate.strip())


def _log_event(record: dict) -> None:
    """Append a webhook event to the per-tenant JSONL log."""
    try:
        _append_text(WEBHOOK_LOG_PATH.parent, WEBHOOK_LOG_PATH.name, json.dumps(record) + "\n")
    except OSError as e:
        logger.error("booking: cannot write event log: %s", e)


def _booking_contact(payload: dict) -> list:
    """[(label, value), ...] — the customer's phone, email and address from a Cal.com/Cal.diy
    webhook payload, for the OWNER's booking notice. Reads the first attendee, then the booking
    form `responses` (Cal.com puts a phone question in `attendeePhoneNumber` and an in-person
    address in `location.optionValue`). Missing fields are simply left out."""
    attendees = payload.get("attendees") or []
    att = attendees[0] if attendees and isinstance(attendees[0], dict) else {}
    responses = payload.get("responses") if isinstance(payload.get("responses"), dict) else {}

    def _resp(key):
        v = responses.get(key)
        if isinstance(v, dict):
            v = v.get("value")
        return str(v).strip() if isinstance(v, (str, int)) and str(v).strip() else ""

    loc = responses.get("location")
    loc_kind = loc.get("value") if isinstance(loc, dict) else None
    loc_opt = str(loc.get("optionValue") or "").strip() if isinstance(loc, dict) else ""
    # location "phone" = "the organizer calls the attendee": optionValue is the CUSTOMER's number
    # ("userPhone" is the organizer's own number and is not used).
    phone = (str(att.get("phoneNumber") or "").strip() or _resp("attendeePhoneNumber")
             or _resp("phone") or _resp("phoneNumber") or (loc_opt if loc_kind == "phone" else ""))
    email = str(att.get("email") or "").strip() or _resp("email")
    address = loc_opt if loc_kind in ("attendeeInPerson", "inPerson", "attendeeAddress") else ""
    address = address or _resp("address") or _resp("attendeeAddress")
    out = []
    for label, value in (("Phone", phone), ("Email", email), ("Address", address)):
        if value:
            out.append((label, value))
    return out


def _notify_phones(raw) -> list:
    """notify_phone may be one string or a list of strings (co-owners). Returns the
    de-duplicated, order-preserving list of non-empty numbers."""
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out = []
    for item in items:
        num = str(item).strip() if isinstance(item, (str, int)) else ""
        if num and num not in out:
            out.append(num)
    return out


def notify_sms(event_type: str, payload: dict) -> None:
    """
    Notify the CLIENT (business owner, not the customer) that their calendar
    changed — via InkBox SMS. Customer-facing confirmations are sent by
    Cal.diy's own email/SMS — do NOT double-send to the customer here.

    Drops a request file into NOTIFY_QUEUE_DIR (bind-mounted, host-globbable)
    rather than calling InkBox directly: OVU containers don't have InkBox
    identities/credentials, and sms-router (which holds them) binds
    127.0.0.1-only for security, unreachable from inside any container. A
    host-side cron (scripts/booking-sms-notify-drain.sh) globs every tenant's
    queue dir and sends via that tenant's registered notify phone + InkBox
    identity, read from booking-config.json's own notify_phone/identity
    fields (set via the setup wizard, same place the API key lives).
    """
    cfg = _load_config()
    phones = _notify_phones(cfg.get("notify_phone", ""))
    identity = cfg.get("notify_identity", "setup")
    if not phones:
        logger.info("booking: notify_sms skipped for %s — no notify_phone configured "
                    "(set it in Booking Setup)", event_type)
        return

    attendees = payload.get("attendees") or []
    customer_name = (attendees[0].get("name") if attendees else None) or "A customer"
    title = payload.get("title") or "an appointment"
    start_time = payload.get("startTime", "")
    verb = {
        "BOOKING_CREATED": "booked",
        "BOOKING_CANCELLED": "cancelled",
        "BOOKING_RESCHEDULED": "rescheduled",
    }.get(event_type, "updated")
    msg = f"{customer_name} {verb} '{title}'" + (f" — {start_time}" if start_time and verb != "cancelled" else "") + "."
    # The owner gets the customer's contact details with the notice (never masked: the owner is the
    # one who calls the customer back). Only fields the booking actually carries are added.
    msg += "".join(f"\n{label}: {value}" for label, value in _booking_contact(payload))

    # One queue file (= one SMS) per number; the host drain checks each file's single notify_phone.
    for idx, notify_phone in enumerate(phones):
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + f"-{idx}"
            # temp is a dotfile (`.<ts>.json.<random>.tmp`): the host drain's *.json glob skips it
            _atomic_write_text(NOTIFY_QUEUE_DIR.parent, f"{NOTIFY_QUEUE_DIR.name}/{ts}.json", json.dumps({
                "notify_phone": notify_phone, "identity": identity, "body": msg,
                "event": event_type, "queued_at": datetime.now(timezone.utc).isoformat(),
            }), create_dirs=True)
            logger.info("booking: notify_sms queued for %s -> ...%s", event_type, notify_phone[-4:])
        except OSError as e:
            logger.error("booking: notify_sms queue write failed: %s", e)


def crm_record(event_type: str, payload: dict) -> None:
    """
    Fan-out stub: record a booking in Twenty CRM.

    TODO(Twenty): create/update a Person from the attendee, then log an Activity
    on BOOKING_CREATED (and a note on CANCELLED/RESCHEDULED). Use TWENTY_CRM_API_KEY
    against https://crm.jam-bot.com/rest (see skills/crm/SKILL.md). One-way only:
    we write into OUR shadow CRM, never the client's own system.
    """
    logger.info("booking: crm_record stub — %s (TODO: wire Twenty CRM)", event_type)


# Cal.com trigger names we act on. Others are logged but not fanned out.
_FANOUT_EVENTS = {"BOOKING_CREATED", "BOOKING_CANCELLED", "BOOKING_RESCHEDULED"}


@booking_bp.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    """
    Receive a signed Cal.diy webhook, verify it, log it, and fan out to
    InkBox SMS + Twenty CRM for booking lifecycle events.

    FAILS CLOSED (2026-11-19). This route is reachable without a login (Cal's
    servers carry no Clerk session), so the HMAC is its only credential:
      - no secret configured            -> 403, nothing logged, nothing sent
      - missing or wrong signature      -> 401, nothing logged, nothing sent
    Before this, a tenant with no secret accepted ANY body as verified=false and
    still queued the owner SMS.
    """
    if (request.content_length or 0) > WEBHOOK_MAX_BYTES:
        return jsonify({"ok": False, "error": "payload too large"}), 413
    secret = _webhook_secret()
    if not secret:
        logger.warning("booking webhook: refused, no webhook secret configured")
        return jsonify({"ok": False, "error": "webhook not configured",
                        "code": "webhook_not_configured"}), 403
    raw = request.get_data()  # raw bytes — required for HMAC over the exact body
    if len(raw) > WEBHOOK_MAX_BYTES:
        return jsonify({"ok": False, "error": "payload too large"}), 413
    signature = request.headers.get("x-cal-signature-256", "")
    if not _verify_signature(raw, signature, secret):
        logger.warning("booking webhook: signature verification FAILED (%s)",
                       "missing" if not signature else "mismatch")
        return jsonify({"ok": False, "error": "invalid signature",
                        "code": "invalid_signature"}), 401

    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400

    event_type = body.get("triggerEvent") or body.get("type") or "UNKNOWN"
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        payload = {}

    record = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "verified": True,
        "payload": payload,
    }
    _log_event(record)

    if event_type in _FANOUT_EVENTS:
        try:
            notify_sms(event_type, payload)
            crm_record(event_type, payload)
        except Exception as e:  # noqa: BLE001 — never let a fan-out error 500 the webhook
            logger.error("booking webhook fan-out error: %s", e)

    return jsonify({"ok": True, "event": event_type, "verified": True})


def _public_webhook_gate():
    """Pre-auth dispatch for EXACTLY `POST /api/booking/webhook`; every other request
    returns None and goes on to the app's normal Clerk gate."""
    if request.method != "POST" or request.path != WEBHOOK_PATH:
        return None
    return webhook()


@booking_bp.record_once
def _install_public_webhook_gate(state):
    """Make the webhook reachable by Cal's servers (no Clerk session) WITHOUT an image change.

    app.py's require_auth answers 401 to any path not in its public lists, and a plugin
    blueprint's own before_request hooks run after it. So the plugin places
    _public_webhook_gate in the app's before_request chain IMMEDIATELY BEFORE require_auth:
    the rate limiter, staff-origin gate and WebSocket guard ahead of it still run, the
    exemption is one exact path + one method, and the handler's HMAC check is the
    credential. If require_auth is not found (Clerk off = open local mode, or the
    unconfigured fail-closed mode) nothing is installed and the route keeps whatever
    protection the app gives it.
    """
    funcs = state.app.before_request_funcs.setdefault(None, [])
    if any(getattr(f, "__name__", "") == "_public_webhook_gate" for f in funcs):
        return
    for i, f in enumerate(funcs):
        if getattr(f, "__name__", "") == "require_auth":
            funcs.insert(i, _public_webhook_gate)
            logger.info("booking: %s exempted from the login gate (HMAC-verified)", WEBHOOK_PATH)
            return
    logger.info("booking: no require_auth gate found; %s left to the app's own auth", WEBHOOK_PATH)


# ---------------------------------------------------------------------------
# Config — server-side persistence (NEVER localStorage, per CLAUDE.md)
# ---------------------------------------------------------------------------

@booking_bp.route("/api/booking/config", methods=["GET"])
def get_config():
    """Return the saved config with the API key + webhook secret masked."""
    cfg = _load_config()
    return jsonify({
        "configured": bool(_api_key()),
        "api_url": _api_url(),
        "username": _username(),
        "api_key_masked": _mask(_api_key()),
        "webhook_secret_masked": _mask(_webhook_secret()),
        "webhook_url": f"{request.host_url.rstrip('/')}/api/booking/webhook",
        "notify_phone": cfg.get("notify_phone", ""),
        "notify_identity": cfg.get("notify_identity", "setup"),
        "source": "config_file" if cfg.get("api_key") else ("env" if CAL_API_KEY_ENV else "none"),
    })


@booking_bp.route("/api/booking/config", methods=["POST"])
def save_config():
    """
    Persist config server-side. Body keys (all optional, merged into existing):
      api_url, api_key, username, webhook_secret, notify_phone, notify_identity
    Empty strings are ignored (so saving the wizard without re-typing the key
    keeps the existing key). Send the literal "__CLEAR__" to wipe a field.
    notify_phone = the business owner's own number (or a list of co-owner numbers), notified on each booking
    event (NOT the customer's number — that's per-booking in the payload).
    """
    body = request.get_json(silent=True) or {}
    cfg = _load_config()
    # The signing secret goes to its own never-served dotfile, not booking-config.json.
    if "webhook_secret" in body:
        val = body["webhook_secret"]
        try:
            if val == "__CLEAR__":
                _atomic_write_text(WEBHOOK_SECRET_PATH.parent, WEBHOOK_SECRET_PATH.name, "")
                cfg.pop("webhook_secret", None)
            elif isinstance(val, str) and val.strip():
                _atomic_write_text(WEBHOOK_SECRET_PATH.parent, WEBHOOK_SECRET_PATH.name, val.strip())
                cfg.pop("webhook_secret", None)
        except OSError as e:
            logger.error("booking: cannot save webhook secret: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
    for key in ("api_url", "api_key", "username", "notify_phone", "notify_identity"):
        if key in body:
            val = body[key]
            if val == "__CLEAR__":
                cfg.pop(key, None)
            elif key == "notify_phone" and isinstance(val, list):
                nums = _notify_phones(val)
                if nums:
                    cfg[key] = nums
            elif isinstance(val, str) and val.strip():
                cfg[key] = val.strip()
    try:
        _save_config(cfg)
    except OSError as e:
        logger.error("booking: cannot save config: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "configured": bool(_api_key())})


# ---------------------------------------------------------------------------
# Manage info — everything the consumer "Cal Setup" canvas page needs in one
# call, derived from the host-provisioned config. NO v2 API dependency: the
# public booking page + embed are served by the Cal.diy web app directly, so
# this works even while the v2 REST API is not deployed (web-app-only MVP,
# 2026-07-23). The cal_login_* fields are the tenant OWNER's own calendar
# credentials; the canvas page is Clerk-admin-gated so only the owner sees them.
# ---------------------------------------------------------------------------

@booking_bp.route("/api/booking/manage", methods=["GET"])
def manage_info():
    """Consumer-page payload: booking link, embed base, and owner Cal login."""
    cfg = _load_config()
    base = _api_url().rstrip("/")
    user = _username()
    provisioned = bool(user)
    return jsonify({
        "provisioned": provisioned,
        "username": user,
        "api_url": base,
        "booking_url": f"{base}/{user}" if provisioned else "",
        "event_url": f"{base}/{user}/30min" if provisioned else "",
        "embed_base": base,
        "login_url": f"{base}/auth/login",
        "cal_login_email": cfg.get("cal_login_email", ""),
        "cal_login_password": cfg.get("cal_login_password", ""),
        "provisioned_at": cfg.get("provisioned_at", ""),
    })
