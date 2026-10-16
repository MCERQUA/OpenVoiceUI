"""Deployment-specific Content-Security-Policy sources.

The app's CSP (app.py) and the canvas-page CSP (routes/canvas.py) are the same for every
install except for the hosts a deployment runs its own services on. Those come from one
setting instead of being written into the policy:

    CSP_TRUSTED_HOSTS="https://*.example.com https://games.example.com"

Space- or comma-separated CSP host sources. Each is added to script-src, style-src (canvas
pages), connect-src and frame-src; an https:// source also gets its wss:// form in
connect-src. Empty by default, so a stock install trusts no extra hosts. Anything that is
not a plain scheme://host[:port] source (quotes, semicolons, keywords, paths) is ignored
with a warning, so the setting cannot inject a directive.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

_HOST_SOURCE_RE = re.compile(r'^(https?|wss?)://(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:\d{1,5})?$')
_cache = {'raw': None, 'hosts': []}


def trusted_hosts() -> list:
    """The validated CSP_TRUSTED_HOSTS sources, in order, without duplicates."""
    raw = os.environ.get('CSP_TRUSTED_HOSTS', '')
    if raw == _cache['raw']:
        return list(_cache['hosts'])
    hosts = []
    for token in re.split(r'[\s,]+', raw.strip()):
        if not token:
            continue
        if not _HOST_SOURCE_RE.match(token):
            logger.warning('CSP_TRUSTED_HOSTS: ignoring %r (expected scheme://host[:port])', token)
            continue
        if token not in hosts:
            hosts.append(token)
    _cache['raw'], _cache['hosts'] = raw, hosts
    return list(hosts)


def sources(*, websockets: bool = False) -> str:
    """The trusted hosts as a CSP source list fragment: ' a b c', or '' when none.

    websockets=True adds the wss:// form of every https:// source (for connect-src).
    """
    hosts = trusted_hosts()
    if websockets:
        extra = ['wss://' + h[len('https://'):] for h in hosts if h.startswith('https://')]
        hosts = hosts + [h for h in extra if h not in hosts]
    return ''.join(' ' + h for h in hosts)
