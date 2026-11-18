"""Dev sandboxes: separate business databases on a DEV server, one per outside builder.

OFF unless the container sets BIZ_SANDBOXES (only the platform's dev tenant does).
A production tenant never sets it, and then every sandbox path below answers "no".

Why it exists: another business's agent needs to build and test pages against the
real engine before its own container gets it. Handing it the dev tenant's own
AGENT_API_KEY would give it that tenant's whole API, and two builders would share
one database. Instead each sandbox has:

  its own key     BIZ_SANDBOX_KEY_<NAME>  (A-Z, 0-9, _), at least 32 characters;
                  the key IS the identity (no request names a sandbox with a key)
  its own db      <db dir>/sandboxes/<name>/business.db, meta.tenant = <tenant>-sbx-<name>
  its own pages   <canvas pages>/sandbox/<name>/*.html  (served at /pages/sandbox/<name>/...)

A sandbox key reaches /api/biz/ only (app.py lets nothing else through with it).
"""
from __future__ import annotations

import hmac
import os
import re

NAME_RE = re.compile(r'^[a-z][a-z0-9-]{1,30}$')
MIN_KEY_LEN = 32


def _env_name(name: str) -> str:
    return 'BIZ_SANDBOX_KEY_' + name.upper().replace('-', '_')


def configured(env=None) -> dict:
    """{sandbox name: key} for every sandbox with a valid name and a strong enough key."""
    env = os.environ if env is None else env
    raw = (env.get('BIZ_SANDBOXES') or '').strip()
    if not raw:
        return {}
    out = {}
    for name in raw.split(','):
        name = name.strip()
        if not NAME_RE.match(name):
            continue
        key = (env.get(_env_name(name)) or '').strip()
        if len(key) >= MIN_KEY_LEN:
            out[name] = key
    return out


def for_key(presented, env=None):
    """The sandbox a presented X-Agent-Key belongs to, or None. Constant-time per key."""
    if not presented:
        return None
    hit = None
    for name, key in configured(env).items():
        if hmac.compare_digest(presented.encode(), key.encode()):
            hit = name
    return hit


def tenant_id(tenant: str, name: str) -> str:
    return f'{tenant}-sbx-{name}'


PAGE_PATH_RE = re.compile(r'^sandbox/([a-z][a-z0-9-]{1,30})/([A-Za-z0-9][A-Za-z0-9_-]{0,119}\.html)$')


def split_page_path(rel_path: str):
    """'sandbox/<name>/<file>.html' -> (name, file) when <name> is configured, else None."""
    m = PAGE_PATH_RE.match(rel_path or '')
    if not m or m.group(1) not in configured():
        return None
    return m.group(1), m.group(2)
