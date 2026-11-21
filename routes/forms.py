"""
routes/forms.py — one common per-tenant form-capture route for canvas pages.

Why this exists (SEC-062): /uploads/ is served without a login on most tenants,
so a canvas page that collected sign-ups by POSTing a JSON file to /api/upload
put names, phones and emails in a publicly fetchable place. This blueprint is
the one sanctioned way for a canvas page to collect a small form from an
anonymous visitor (a group registration, a waitlist, a callback request).

  POST /api/forms/<form_id>        anonymous. JSON or URL-encoded. Validated
                                   against the form definition, appended to the
                                   tenant's private store.
  PUT  /api/forms/<form_id>/def    agent key or Clerk. Creates/replaces the form
                                   definition (the field allowlist).
  GET  /api/forms/<form_id>/def    agent key or Clerk.
  GET  /api/forms/<form_id>        agent key or Clerk. The submissions.
  GET  /api/forms                  agent key or Clerk. Every form + its row count.

Only the POST is public, and only on the exact path /api/forms/<form_id>
(app.py _PUBLIC_WRITE_PATTERNS). Every read goes through the app-wide
require_auth gate AND re-checks the caller here (a second wall, so a gate
change can never open the submissions).

Where the data lives — the tenant's durable db dir, the same directory the
business backend keeps business.db in (services.biz.store.resolve_location:
BIZ_DB_DIR, else the dir of OVUI_DB_PATH; /mnt/clients/<t>/openvoiceui/db on a
JamBot tenant, mounted only into openvoiceui-<t>):

  <db_dir>/forms/<form_id>.def.json    the definition
  <db_dir>/forms/<form_id>.jsonl       one JSON object per accepted submission

NOT RUNTIME_DIR/forms: on a hosted tenant /app/runtime itself is the
container's writable layer (only subdirs are bind-mounted), so every row would
be lost on the next image roll. A hosted tenant with no durable db dir gets 503,
never a silent write to the writable layer (same rule as business.db). The
store is refused (503) if it would sit inside a served tree (uploads,
canvas-pages). The forms dir is created 0700 and the files 0600, owned by the
server's uid; a dir or file owned by anyone else is refused, and every open is
no-follow (services.safe_open), because the db mount is world-writable on the
host.

Guards on the anonymous POST, in order:
  1. form_id must match FORM_ID_RE (no dots, no slashes: no traversal).
  2. The definition must exist, belong to this tenant, and be enabled -> else 404.
  3. Origin (or, without one, Referer) must name this host -> else 403.
  4. Per-IP rate limit (per minute + per day, per form) and a per-form daily
     ceiling for all IPs together -> 429. The IP comes from CF-Connecting-IP
     (Cloudflare fronts every tenant; nginx's remote_addr is the edge IP),
     which a caller reaching the origin directly can forge, hence the ceiling.
  5. Body size <= the definition's max_bytes -> else 413 (checked on the
     declared length and again on the bytes actually read).
  6. Only application/json or application/x-www-form-urlencoded -> else 415.
     multipart is refused: a text form never needs file parts.
  7. Honeypot field filled -> 201 {"ok": true} and NOTHING stored.
  8. Unknown field -> 400 (names only in the answer, never values).
  9. Type / length / required / enum checks -> 400.
 10. A card or bank number anywhere -> 422 (the business backend's Luhn scan).
 11. Append under an exclusive flock, one write(2) of the whole line.

Logging: form_id, counts and the status code only. Never a value, never an IP.
"""

from __future__ import annotations

import errno
import fcntl
import ipaddress
import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from flask import Blueprint, g, jsonify, request

from services import safe_open

logger = logging.getLogger(__name__)

forms_bp = Blueprint('forms', __name__)

API = '/api/forms'

# ── identifiers ──────────────────────────────────────────────────────────────
FORM_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}\Z')
FIELD_RE = re.compile(r'^[a-z][a-z0-9_]{0,40}\Z')          # same as services/biz/schema.py FIELD_RE
HONEYPOT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,40}\Z')

# ── field types (a subset of the business backend's vocabulary, same names) ──
# value = the hard cap on max_length for that type.
TYPE_MAX_LENGTH = {
    'string': 1000,
    'text': 5000,
    'email': 320,
    'phone': 40,
    'url': 2000,
    'enum': 200,
    'date': 10,
    'integer': 20,
    'number': 40,
    'boolean': 5,
}
_DEF_KEYS = {'form_id', 'label', 'description', 'enabled', 'fields', 'honeypot', 'max_bytes', 'rate_limit'}
_FIELD_KEYS = {'type', 'label', 'description', 'required', 'max_length', 'values'}
_RATE_KEYS = {'per_minute', 'per_day', 'form_per_day'}
MAX_FIELDS = 50
MAX_ENUM_VALUES = 200
DEFAULT_HONEYPOT = 'company_website'
DEFAULT_MAX_BYTES = 8192
MIN_MAX_BYTES, MAX_MAX_BYTES = 256, 65536
DEFAULT_RATE = {'per_minute': 5, 'per_day': 50, 'form_per_day': 1000}
RATE_CAPS = {'per_minute': 60, 'per_day': 1000, 'form_per_day': 10000}
MAX_DEF_BYTES = 65536
DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT = 500, 5000

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+\Z')
_PHONE_RE = re.compile(r'^[0-9+().\-\s xX#*]+\Z')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\Z')
_INT_RE = re.compile(r'^-?\d{1,18}\Z')
# Control characters: never in any value; newline/tab allowed only in 'text'.
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_CTRL_LINE_RE = re.compile(r'[\x00-\x1f\x7f]')


class FormError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def response(self):
        return jsonify({'error': self.message, 'code': self.code, **self.extra}), self.status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _log(form_id: str, status: int, **counts) -> None:
    """The ONLY log line this module writes for a request: id, counts, status. No values, no IPs."""
    extra = ' '.join(f'{k}={v}' for k, v in counts.items())
    logger.info('forms: form_id=%s status=%s %s', form_id, status, extra)


# ── store location ───────────────────────────────────────────────────────────
def _hosted() -> bool:
    if (os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip():
        return True
    return os.getenv('CANVAS_REQUIRE_AUTH', '').strip().lower() == 'true'


def _served_roots() -> list[Path]:
    from services.paths import UPLOADS_DIR, CANVAS_PAGES_DIR, RUNTIME_DIR
    roots = [UPLOADS_DIR, CANVAS_PAGES_DIR, RUNTIME_DIR / 'canvas-data',
             RUNTIME_DIR / 'generated_music', RUNTIME_DIR / 'music']
    try:
        import routes.static_files as _sf          # tests point UPLOADS_DIR elsewhere
        roots.append(Path(_sf.UPLOADS_DIR))
    except Exception:                               # noqa: BLE001 — import order in odd contexts
        pass
    return [Path(os.path.realpath(str(r))) for r in roots]


def store_root() -> tuple[str, Path]:
    """(tenant, db_dir). The forms dir is <db_dir>/forms. Raises FormError(503) when unsafe."""
    from services.biz.store import resolve_location
    from services.biz.schema import SchemaError
    try:
        tenant, db_dir = resolve_location(hosted=_hosted())
    except SchemaError as e:
        raise FormError(503, 'forms_storage_unavailable',
                        'Form storage unavailable: ' + e.message.split(': ', 1)[-1]) from None
    real = Path(os.path.realpath(str(db_dir)))
    forms_real = real / 'forms'
    for served in _served_roots():
        if forms_real == served or served in forms_real.parents:
            raise FormError(503, 'forms_storage_served',
                            'Form storage unavailable: the store would sit inside a served directory')
    return tenant, real


def _forms_dir_fd(create: bool) -> tuple[str, int]:
    """(tenant, fd on <db_dir>/forms). The dir must be a plain 0700 dir owned by this uid."""
    tenant, db_dir = store_root()
    if create:
        db_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = safe_open.open_dir_beneath(db_dir, 'forms', create=create, dir_mode=0o700)
    except FileNotFoundError:
        raise
    except safe_open.UnsafePath:
        raise FormError(503, 'forms_store_unsafe', 'Form storage unavailable') from None
    st = os.fstat(fd)
    if st.st_uid != os.geteuid():
        os.close(fd)
        raise FormError(503, 'forms_store_unsafe', 'Form storage unavailable')
    if stat.S_IMODE(st.st_mode) != 0o700:
        try:
            os.fchmod(fd, 0o700)
        except OSError:
            os.close(fd)
            raise FormError(503, 'forms_store_unsafe', 'Form storage unavailable') from None
    return tenant, fd


def _check_owned(fd: int) -> None:
    st = os.fstat(fd)
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise FormError(503, 'forms_store_unsafe', 'Form storage unavailable')


def _read_in_dir(dfd: int, name: str, max_bytes: int | None = None) -> bytes | None:
    try:
        fd = safe_open.open_in_dir(dfd, name, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except safe_open.UnsafePath:
        raise FormError(503, 'forms_store_unsafe', 'Form storage unavailable') from None
    try:
        _check_owned(fd)
        fcntl.flock(fd, fcntl.LOCK_SH)
        try:
            return safe_open.read_fd(fd, max_bytes)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def load_def(form_id: str) -> dict | None:
    """The definition of form_id for THIS tenant, or None (missing, other tenant, unreadable)."""
    try:
        tenant, dfd = _forms_dir_fd(create=False)
    except FileNotFoundError:
        return None
    try:
        raw = _read_in_dir(dfd, f'{form_id}.def.json', MAX_DEF_BYTES)
    finally:
        os.close(dfd)
    if raw is None:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        logger.error('forms: form_id=%s definition unreadable', form_id)
        return None
    if not isinstance(d, dict) or d.get('tenant') != tenant or d.get('form_id') != form_id:
        return None
    return d


def append_row(form_id: str, row: dict) -> None:
    """Append one row: O_APPEND + exclusive flock + a single write(2) of the whole line."""
    line = (json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    _tenant, dfd = _forms_dir_fd(create=True)
    try:
        try:
            fd = safe_open.open_in_dir(dfd, f'{form_id}.jsonl',
                                       os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except safe_open.UnsafePath:
            raise FormError(503, 'forms_store_unsafe', 'Form storage unavailable') from None
    finally:
        os.close(dfd)
    try:
        _check_owned(fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            n = os.write(fd, line)
            if n != len(line):                      # never on a regular file; refuse a torn row loudly
                raise OSError(errno.EIO, 'short write')
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_rows(form_id: str) -> list[dict]:
    try:
        _tenant, dfd = _forms_dir_fd(create=False)
    except FileNotFoundError:
        return []
    try:
        raw = _read_in_dir(dfd, f'{form_id}.jsonl')
    finally:
        os.close(dfd)
    rows = []
    for ln in (raw or b'').splitlines():
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    return rows


# ── definition validation (PUT) ──────────────────────────────────────────────
def _int_in(v, lo, hi, name):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise FormError(422, 'bad_definition', f'{name} must be a whole number from {lo} to {hi}')
    return v


def normalize_def(form_id: str, body) -> dict:
    if not isinstance(body, dict):
        raise FormError(422, 'bad_definition', 'The definition must be a JSON object')
    unknown = sorted(set(body) - _DEF_KEYS - {'tenant', 'updated_at', 'updated_by', 'created_at'})
    if unknown:
        raise FormError(422, 'bad_definition', 'Unknown definition keys', keys=unknown)
    if body.get('form_id', form_id) != form_id:
        raise FormError(422, 'bad_definition', 'form_id in the body does not match the path')
    enabled = body.get('enabled', True)
    if not isinstance(enabled, bool):
        raise FormError(422, 'bad_definition', 'enabled must be true or false')
    fields = body.get('fields')
    if not isinstance(fields, dict) or not 1 <= len(fields) <= MAX_FIELDS:
        raise FormError(422, 'bad_definition', f'fields must be an object with 1 to {MAX_FIELDS} fields')
    out_fields = {}
    for name, spec in fields.items():
        if not isinstance(name, str) or not FIELD_RE.match(name):
            raise FormError(422, 'bad_definition', 'Field names are lower-case letters, digits and _',
                            field=str(name)[:60])
        if not isinstance(spec, dict):
            raise FormError(422, 'bad_definition', 'A field spec must be an object', field=name)
        bad = sorted(set(spec) - _FIELD_KEYS)
        if bad:
            raise FormError(422, 'bad_definition', 'Unknown field spec keys', field=name, keys=bad)
        t = spec.get('type', 'string')
        if t not in TYPE_MAX_LENGTH:
            raise FormError(422, 'bad_definition', 'Unknown field type', field=name,
                            allowed=sorted(TYPE_MAX_LENGTH))
        cap = TYPE_MAX_LENGTH[t]
        max_length = _int_in(spec.get('max_length', cap), 1, cap, f'{name}.max_length')
        required = spec.get('required', False)
        if not isinstance(required, bool):
            raise FormError(422, 'bad_definition', 'required must be true or false', field=name)
        f = {'type': t, 'required': required, 'max_length': max_length}
        for k in ('label', 'description'):
            if k in spec:
                if not isinstance(spec[k], str) or len(spec[k]) > 500:
                    raise FormError(422, 'bad_definition', f'{k} must be text up to 500 characters', field=name)
                f[k] = spec[k]
        if t == 'enum':
            vals = spec.get('values')
            if (not isinstance(vals, list) or not 1 <= len(vals) <= MAX_ENUM_VALUES
                    or not all(isinstance(v, str) and 0 < len(v) <= max_length for v in vals)
                    or len(set(vals)) != len(vals)):
                raise FormError(422, 'bad_definition',
                                f'an enum needs "values": 1 to {MAX_ENUM_VALUES} distinct strings', field=name)
            f['values'] = vals
        elif 'values' in spec:
            raise FormError(422, 'bad_definition', '"values" is only for type enum', field=name)
        out_fields[name] = f
    honeypot = body.get('honeypot', DEFAULT_HONEYPOT)
    if not isinstance(honeypot, str) or not HONEYPOT_RE.match(honeypot) or honeypot in out_fields:
        raise FormError(422, 'bad_definition',
                        'honeypot must be a field name that is NOT one of the fields')
    max_bytes = _int_in(body.get('max_bytes', DEFAULT_MAX_BYTES), MIN_MAX_BYTES, MAX_MAX_BYTES, 'max_bytes')
    rate_in = body.get('rate_limit', {})
    if not isinstance(rate_in, dict) or set(rate_in) - _RATE_KEYS:
        raise FormError(422, 'bad_definition', f'rate_limit takes only {sorted(_RATE_KEYS)}')
    rate = {k: _int_in(rate_in.get(k, DEFAULT_RATE[k]), 1, RATE_CAPS[k], f'rate_limit.{k}') for k in DEFAULT_RATE}
    out = {'form_id': form_id, 'enabled': enabled, 'fields': out_fields, 'honeypot': honeypot,
           'max_bytes': max_bytes, 'rate_limit': rate}
    for k in ('label', 'description'):
        if k in body:
            if not isinstance(body[k], str) or len(body[k]) > 500:
                raise FormError(422, 'bad_definition', f'{k} must be text up to 500 characters')
            out[k] = body[k]
    return out


# ── submission validation (POST) ─────────────────────────────────────────────
def _coerce(spec: dict, value):
    """(clean value, None) or (None, reason). Empty string -> (None, None): treated as absent."""
    t = spec['type']
    if t == 'boolean':
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            v = value.strip().lower()
            if v == '':
                return None, None
            if v in ('true', 'on', '1', 'yes'):
                return True, None
            if v in ('false', 'off', '0', 'no'):
                return False, None
        return None, 'must be true or false'
    if t in ('integer', 'number') and not isinstance(value, bool) and isinstance(value, (int, float)):
        value = repr(value) if isinstance(value, float) else str(value)
    if not isinstance(value, str):
        return None, 'must be text'
    if len(value) > spec['max_length']:
        return None, f'longer than {spec["max_length"]} characters'
    if (_CTRL_RE if t == 'text' else _CTRL_LINE_RE).search(value):
        return None, 'contains control characters'
    v = value.strip()
    if v == '':
        return None, None
    if t == 'email' and not _EMAIL_RE.match(v):
        return None, 'not an email address'
    if t == 'phone' and (not _PHONE_RE.match(v) or sum(c.isdigit() for c in v) < 7):
        return None, 'not a phone number'
    if t == 'url' and not v.startswith(('http://', 'https://')):
        return None, 'not a link (http:// or https://)'
    if t == 'enum' and v not in spec['values']:
        return None, 'not one of the allowed values'
    if t == 'date':
        if not _DATE_RE.match(v):
            return None, 'must be a date YYYY-MM-DD'
        try:
            datetime.strptime(v, '%Y-%m-%d')
        except ValueError:
            return None, 'not a real date'
    if t == 'integer':
        if not _INT_RE.match(v):
            return None, 'must be a whole number'
        return int(v), None
    if t == 'number':
        try:
            n = float(v)
        except ValueError:
            return None, 'must be a number'
        if n != n or n in (float('inf'), float('-inf')):
            return None, 'must be a number'
        return n, None
    return v, None


def _parse_body(defn: dict) -> dict:
    max_bytes = defn['max_bytes']
    if request.content_length is not None and request.content_length > max_bytes:
        raise FormError(413, 'too_large', f'Submission too large ({max_bytes} bytes max)')
    raw = request.stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise FormError(413, 'too_large', f'Submission too large ({max_bytes} bytes max)')
    mt = (request.mimetype or '').lower()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise FormError(400, 'bad_encoding', 'The submission must be UTF-8') from None
    if mt == 'application/json':
        try:
            data = json.loads(text) if text.strip() else None
        except ValueError:
            raise FormError(400, 'bad_json', 'The submission is not valid JSON') from None
        if not isinstance(data, dict):
            raise FormError(400, 'bad_json', 'The submission must be a JSON object')
        return data
    if mt == 'application/x-www-form-urlencoded':
        try:
            pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=bool(text), max_num_fields=MAX_FIELDS + 1)
        except ValueError:
            raise FormError(400, 'bad_form', 'The submission is not valid form data') from None
        data: dict = {}
        for k, v in pairs:
            if k in data:
                raise FormError(400, 'duplicate_field', 'A field was sent more than once',
                                fields=[k[:60]] if FIELD_RE.match(k) else [])
            data[k] = v
        return data
    raise FormError(415, 'unsupported_media_type',
                    'Send application/json or application/x-www-form-urlencoded '
                    '(e.g. new URLSearchParams(new FormData(form)))')


def validate_submission(defn: dict, data: dict) -> tuple[dict | None, bool]:
    """(clean data, honeypot_hit). Raises FormError on a refusal."""
    hp = defn['honeypot']
    hp_val = data.pop(hp, None)
    if hp_val not in (None, '', False):
        return None, True
    fields = defn['fields']
    unknown = sorted(k for k in data if k not in fields)
    if unknown:
        named = [k[:60] for k in unknown if isinstance(k, str) and FIELD_RE.match(k)]
        raise FormError(400, 'unknown_fields', 'The submission has fields this form does not accept',
                        fields=named, count=len(unknown))
    clean, errors = {}, {}
    for name, spec in fields.items():
        if name in data:
            val, err = _coerce(spec, data[name])
            if err:
                errors[name] = err
                continue
            if val is not None:
                clean[name] = val
        if spec['required'] and name not in clean and name not in errors:
            errors[name] = 'required'
    if errors:
        raise FormError(400, 'validation_failed', 'Some fields are missing or invalid', fields=errors)
    from services.biz.schema import contains_card_number
    if contains_card_number({k: v for k, v in clean.items() if isinstance(v, str)}):
        raise FormError(422, 'card_number_refused', 'Card and bank numbers are never accepted on a form.')
    return clean, False


# ── origin + rate limit ──────────────────────────────────────────────────────
def origin_ok() -> bool:
    host = (request.host or '').lower()
    origin = request.headers.get('Origin')
    if origin is not None:
        if origin.strip().lower() == 'null':
            return False
        return bool(host) and urlparse(origin).netloc.lower() == host
    referer = request.headers.get('Referer')
    if referer:
        return bool(host) and urlparse(referer).netloc.lower() == host
    return False


def client_ip() -> str:
    cf = (request.headers.get('CF-Connecting-IP') or '').strip()
    if cf:
        try:
            return str(ipaddress.ip_address(cf))
        except ValueError:
            pass
    return request.remote_addr or 'unknown'


_rate_lock = threading.Lock()
_rate_hits: dict[tuple, deque] = {}
_MINUTE, _DAY = 60.0, 86400.0


def _hits_in(key, now, window) -> deque:
    dq = _rate_hits.get(key)
    if dq is None:
        dq = _rate_hits[key] = deque()
    while dq and now - dq[0] >= window:
        dq.popleft()
    return dq


def rate_check(form_id: str, ip: str, rate: dict) -> str | None:
    """None if allowed (and records the hit), else the limit that refused it."""
    now = time.time()
    with _rate_lock:
        if len(_rate_hits) > 20000:                     # bound memory under a spray of IPs
            for k in [k for k, dq in _rate_hits.items() if not dq or now - dq[-1] >= _DAY]:
                del _rate_hits[k]
        ip_min = _hits_in(('m', form_id, ip), now, _MINUTE)
        ip_day = _hits_in(('d', form_id, ip), now, _DAY)
        form_day = _hits_in(('f', form_id), now, _DAY)
        if len(ip_min) >= rate['per_minute']:
            return 'per_minute'
        if len(ip_day) >= rate['per_day']:
            return 'per_day'
        if len(form_day) >= rate['form_per_day']:
            return 'form_per_day'
        ip_min.append(now)
        ip_day.append(now)
        form_day.append(now)
        return None


def _reset_rate_limits_for_tests() -> None:
    with _rate_lock:
        _rate_hits.clear()


# ── auth for the read/definition routes (second wall behind require_auth) ────
def _caller() -> str | None:
    from services.rate_limits import caller_has_verified_credential
    if not caller_has_verified_credential():
        return None
    uid = g.get('clerk_user_id')
    return f'clerk:{uid}' if uid else 'agent'


def _require_caller():
    by = _caller()
    if by is None:
        raise FormError(401, 'auth_required', 'Unauthorized')
    return by


def _check_form_id(form_id: str) -> None:
    if not isinstance(form_id, str) or not FORM_ID_RE.match(form_id):
        raise FormError(404, 'form_not_found', 'No such form')


# ── routes ───────────────────────────────────────────────────────────────────
@forms_bp.route(f'{API}/<form_id>', methods=['POST'])
def submit_form(form_id):
    status = 500
    nfields = 0
    try:
        _check_form_id(form_id)
        defn = load_def(form_id)
        if defn is None or not defn.get('enabled', False):
            raise FormError(404, 'form_not_found', 'No such form')
        if not origin_ok():
            raise FormError(403, 'origin_mismatch', 'This form only accepts submissions from its own page')
        limit = rate_check(form_id, client_ip(), defn['rate_limit'])
        if limit:
            raise FormError(429, 'rate_limited', 'Too many submissions. Please wait and try again.')
        data = _parse_body(defn)
        nfields = len(data)
        clean, honeypot_hit = validate_submission(defn, data)
        if honeypot_hit:
            status = 201
            _log(form_id, status, fields=nfields, stored=0, dropped='honeypot')
            return jsonify({'ok': True}), 201
        tenant, _db = store_root()
        row = {
            'id': str(uuid.uuid4()),
            'tenant': tenant,
            'form_id': form_id,
            'received_at': _now(),
            'data': clean,
            # the business backend's provenance shape, so it can front this store later
            'source': {'channel': 'web', 'ref': f'form:{form_id}'},
        }
        append_row(form_id, row)
        status = 201
        _log(form_id, status, fields=len(clean), stored=1)
        return jsonify({'ok': True}), 201
    except FormError as e:
        status = e.status
        _log(form_id if FORM_ID_RE.match(str(form_id)) else '<invalid>', status, fields=nfields, code=e.code)
        return e.response()
    except OSError as e:
        status = 503
        _log(form_id, status, fields=nfields, code='store_error', errno=e.errno)
        return jsonify({'error': 'Could not save the submission. Please try again.', 'code': 'store_error'}), 503


@forms_bp.route(f'{API}/<form_id>', methods=['GET'])
def list_submissions(form_id):
    try:
        _require_caller()
        _check_form_id(form_id)
        defn = load_def(form_id)
        if defn is None:
            raise FormError(404, 'form_not_found', 'No such form')
        try:
            limit = int(request.args.get('limit', DEFAULT_LIST_LIMIT))
        except ValueError:
            raise FormError(400, 'bad_limit', 'limit must be a whole number') from None
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        rows = [r for r in read_rows(form_id) if r.get('tenant') == defn['tenant']]
        _log(form_id, 200, rows=len(rows))
        return jsonify({'form_id': form_id, 'count': len(rows), 'rows': rows[-limit:]}), 200
    except FormError as e:
        _log(form_id if FORM_ID_RE.match(str(form_id)) else '<invalid>', e.status, code=e.code)
        return e.response()


@forms_bp.route(f'{API}/<form_id>/def', methods=['GET'])
def get_definition(form_id):
    try:
        _require_caller()
        _check_form_id(form_id)
        defn = load_def(form_id)
        if defn is None:
            raise FormError(404, 'form_not_found', 'No such form')
        return jsonify(defn), 200
    except FormError as e:
        return e.response()


@forms_bp.route(f'{API}/<form_id>/def', methods=['PUT'])
def put_definition(form_id):
    try:
        by = _require_caller()
        _check_form_id(form_id)
        if request.content_length is not None and request.content_length > MAX_DEF_BYTES:
            raise FormError(413, 'too_large', 'Definition too large')
        body = request.get_json(silent=True)
        out = normalize_def(form_id, body)
        tenant, db_dir = store_root()
        prior = load_def(form_id)
        now = _now()
        out['tenant'] = tenant
        out['created_at'] = (prior or {}).get('created_at', now)
        out['updated_at'] = now
        out['updated_by'] = by
        _t, dfd = _forms_dir_fd(create=True)
        os.close(dfd)
        safe_open.atomic_write_beneath(db_dir, f'forms/{form_id}.def.json',
                                       json.dumps(out, indent=2).encode('utf-8'), mode=0o600)
        _log(form_id, 201 if prior is None else 200, fields=len(out['fields']), enabled=out['enabled'])
        return jsonify(out), (201 if prior is None else 200)
    except FormError as e:
        _log(form_id if FORM_ID_RE.match(str(form_id)) else '<invalid>', e.status, code=e.code)
        return e.response()


@forms_bp.route(API, methods=['GET'])
def list_forms():
    try:
        _require_caller()
        try:
            tenant, dfd = _forms_dir_fd(create=False)
        except FileNotFoundError:
            return jsonify({'forms': []}), 200
        try:
            names = sorted(os.listdir(dfd))
        finally:
            os.close(dfd)
        forms = []
        for n in names:
            if not n.endswith('.def.json'):
                continue
            fid = n[:-len('.def.json')]
            if not FORM_ID_RE.match(fid):
                continue
            d = load_def(fid)
            if d is None:
                continue
            forms.append({'form_id': fid, 'enabled': d.get('enabled', False), 'label': d.get('label', ''),
                          'count': sum(1 for r in read_rows(fid) if r.get('tenant') == tenant)})
        return jsonify({'forms': forms}), 200
    except FormError as e:
        return e.response()
