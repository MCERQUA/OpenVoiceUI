"""
services/upload_links.py — signed, expiring links to files under /uploads/.

A signed link lets something WITHOUT a login session (an MMS provider fetching
media, a script on another machine, a recipient opening an emailed link) read
ONE upload until a fixed time:

    /uploads/<path>?exp=<unix seconds>&sig=<hex>
    sig = HMAC-SHA256(UPLOADS_SIGNING_KEY, "/uploads/<path>|<exp>")

Properties:
  - Per-instance secret. UPLOADS_SIGNING_KEY is read at call time. If it is
    unset (or too short to be a real secret) signed links are simply NOT a
    valid grant: verify() reports "unconfigured" and mint refuses. It never
    raises in the request path and never means "allow all".
  - Scoped to one path. The signature covers the full URL path, so a valid
    signature for one file is worthless for any other.
  - Bounded lifetime. A link may not live longer than MAX_TTL_SECONDS. Mint
    refuses a longer TTL, and verify() also rejects an exp beyond the cap, so
    the cap holds even for a link that was not made by this helper.
  - Constant-time comparison (hmac.compare_digest).

Standard library only, so host scripts can import or vendor it as-is.

CLI:
    python -m services.upload_links sign <path-or-url> --ttl 7d [--base-url https://host]
    python -m services.upload_links verify <signed-url>
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import re
import sys
import time
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

SIGNING_KEY_ENV = 'UPLOADS_SIGNING_KEY'
MIN_KEY_LENGTH = 16                      # shorter than this is not treated as a secret
MAX_TTL_SECONDS = 30 * 24 * 3600         # 30 days
UPLOADS_PREFIX = '/uploads/'

# verify() results. OK is the only granting value.
OK = 'ok'
ABSENT = 'absent'                # no exp/sig on the request
UNCONFIGURED = 'unconfigured'    # this instance has no usable signing key
MALFORMED = 'malformed'          # exp/sig present but not well-formed
EXPIRED = 'expired'              # exp is not in the future
BEYOND_CAP = 'beyond-cap'        # exp is further out than MAX_TTL_SECONDS
INVALID = 'invalid'              # signature does not match (forged, or a different path)

_HEX_SIG = re.compile(r'[0-9a-f]{64}')
_EXP = re.compile(r'[0-9]{1,12}')
_TTL = re.compile(r'^\s*(\d+)\s*([smhdw]?)\s*$', re.IGNORECASE)
_TTL_UNITS = {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 7 * 86400}


class UploadLinkError(ValueError):
    """A signed link could not be minted (no key, bad path, TTL out of range)."""


def _signing_key(key: str | None = None) -> bytes | None:
    raw = (key if key is not None else os.getenv(SIGNING_KEY_ENV, '')).strip()
    if len(raw) < MIN_KEY_LENGTH:
        return None
    return raw.encode('utf-8')


def signing_configured() -> bool:
    return _signing_key() is not None


def _signature(key: bytes, path: str, exp: int) -> str:
    return hmac.new(key, f'{path}|{exp}'.encode('utf-8'), hashlib.sha256).hexdigest()


def canonical_path(path: str) -> str:
    """Return the URL path a link signs, always '/uploads/<relative path>'.

    `path` is a DECODED path: either '/uploads/...' or relative to the uploads
    directory ('logo.png', 'job/mockup.jpg'). Refuses anything that could not
    be served: traversal, dotfiles, or an absolute path outside /uploads/.
    """
    s = (path or '').strip()
    if s.startswith('/'):
        if not s.startswith(UPLOADS_PREFIX):
            raise UploadLinkError(f'not an /uploads/ path: {s!r}')
        s = s[len(UPLOADS_PREFIX):]
    parts = [p for p in s.split('/') if p]
    if not parts:
        raise UploadLinkError('empty upload path')
    if any(p.startswith('.') for p in parts):
        raise UploadLinkError(f'refusing traversal or dotfile path: {s!r}')
    return UPLOADS_PREFIX + '/'.join(parts)


def _path_from_input(value: str) -> str:
    """CLI/mint input -> decoded path. Parsed as a URL (and percent-decoded) ONLY
    when it is an http(s) URL, or an '/uploads/...' path carrying a signed-link
    query (exp=/sig=). Anything else is a literal filename, so a name containing
    '?' or '%' is signed exactly as written."""
    s = (value or '').strip()
    if re.match(r'(?i)https?://', s):
        return unquote(urlsplit(s).path)
    if s.startswith(UPLOADS_PREFIX) and '?' in s:
        query = s.split('?', 1)[1]
        if 'exp' in parse_qs(query) or 'sig' in parse_qs(query):
            return unquote(urlsplit(s).path)
    return s


def parse_ttl(value) -> int:
    """'7d' / '12h' / '30m' / '45s' / '1w' / '3600' -> seconds."""
    if isinstance(value, int):
        return value
    m = _TTL.match(str(value))
    if not m:
        raise UploadLinkError(f'bad TTL {value!r} (use e.g. 3600, 30m, 12h, 7d)')
    return int(m.group(1)) * _TTL_UNITS[m.group(2).lower()]


def sign(path_or_url: str, ttl, *, key: str | None = None, now: int | None = None) -> dict:
    """Mint a signature. Returns {'path', 'exp', 'sig', 'url'} where 'url' is the
    relative signed URL ('/uploads/...?exp=...&sig=...')."""
    k = _signing_key(key)
    if k is None:
        raise UploadLinkError(
            f'{SIGNING_KEY_ENV} is not set (or shorter than {MIN_KEY_LENGTH} chars); '
            'signed links are disabled on this instance')
    seconds = parse_ttl(ttl)
    if seconds <= 0:
        raise UploadLinkError('TTL must be positive')
    if seconds > MAX_TTL_SECONDS:
        raise UploadLinkError(
            f'TTL {seconds}s exceeds the cap of {MAX_TTL_SECONDS}s ({MAX_TTL_SECONDS // 86400}d)')
    path = canonical_path(_path_from_input(path_or_url))
    exp = int(now if now is not None else time.time()) + seconds
    sig = _signature(k, path, exp)
    url = quote(path, safe='/') + '?' + urlencode({'exp': exp, 'sig': sig})
    return {'path': path, 'exp': exp, 'sig': sig, 'url': url}


def signed_url(path_or_url: str, ttl, *, base_url: str | None = None,
               key: str | None = None, now: int | None = None) -> str:
    """Mint and return the signed URL, absolute when base_url is given."""
    rel = sign(path_or_url, ttl, key=key, now=now)['url']
    return base_url.rstrip('/') + rel if base_url else rel


def verify(path: str, exp, sig, *, key: str | None = None, now: int | None = None) -> str:
    """Check a presented signature for a request path. Returns OK or the reason it
    is not a grant. Never raises."""
    try:
        if exp in (None, '') and sig in (None, ''):
            return ABSENT
        k = _signing_key(key)
        if k is None:
            return UNCONFIGURED
        exp_s, sig_s = str(exp or '').strip(), str(sig or '').strip().lower()
        if not _EXP.fullmatch(exp_s) or not _HEX_SIG.fullmatch(sig_s):
            return MALFORMED
        exp_i = int(exp_s)
        t = int(now if now is not None else time.time())
        if exp_i <= t:
            return EXPIRED
        if exp_i > t + MAX_TTL_SECONDS:
            return BEYOND_CAP
        try:
            p = canonical_path(path)
        except UploadLinkError:
            return INVALID
        if not hmac.compare_digest(_signature(k, p, exp_i), sig_s):
            return INVALID
        return OK
    except Exception:
        return MALFORMED


def _verify_url(url: str) -> str:
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    return verify(unquote(parts.path), (q.get('exp') or [''])[0], (q.get('sig') or [''])[0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m services.upload_links',
                                 description='Mint or check signed /uploads/ links.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('sign', help='print a signed URL for one upload')
    s.add_argument('path', help="'logo.png', '/uploads/logo.png' or a full URL")
    s.add_argument('--ttl', default='7d', help='lifetime, e.g. 3600, 30m, 12h, 7d (max 30d)')
    s.add_argument('--base-url', default=None, help='prefix, e.g. https://example.com')
    v = sub.add_parser('verify', help='check a signed URL against this instance key')
    v.add_argument('url')
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'sign':
            print(signed_url(args.path, args.ttl, base_url=args.base_url))
            return 0
        result = _verify_url(args.url)
        print(result)
        return 0 if result == OK else 1
    except UploadLinkError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
