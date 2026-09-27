"""
One-click connectors: the provider-specific half of the OAuth vault.

services/vault.py owns the generic OAuth plumbing (catalog, authorize URL, CSRF
nonce, token exchange, refresh, storage). This module owns what happens AFTER a
connection for providers that do more than store a token:

  - Supabase: list the organizations and projects the user granted, let them
    pick the project their app uses, and fill the existing `supabase`
    credential (url / anon_key / service_role_key) from the Management API.
    Nobody pastes a key.
  - Supabase key validation for the paste path, which stays as the fallback.
    Three verdicts, never two: pass / fail / cannot_tell.
  - The single-callback OAuth relay: providers that accept one (or a few)
    redirect URIs send every tenant's user back to one platform URL, which
    forwards the browser to the tenant named in `state`. The tenant still
    verifies its own single-use CSRF nonce (F-6); the relay touches nothing.

Why this exists (2026-09-27): a client spent a day pasting Supabase keys that
were all "rejected". His keys were valid, for his own project; his app ran on a
different project that another tool owned. Nothing in the UI could tell him
that. The validator below names that case in plain words, and the connector
removes the need to paste at all.

Docs this is built against (verified 2026-09-27):
  https://supabase.com/docs/guides/integrations/build-a-supabase-oauth-integration
  https://supabase.com/docs/guides/integrations/build-a-supabase-oauth-integration/oauth-scopes
  https://api.supabase.com/api/v1-json  (Management API OpenAPI spec)
"""
import base64
import hashlib
import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlencode, urlparse

import requests

logger = logging.getLogger(__name__)

SUPABASE_API = 'https://api.supabase.com'
SUPABASE_CONNECTOR_ID = 'supabase_connect'
SUPABASE_CREDENTIAL_ID = 'supabase'

_REF_RE = re.compile(r'^[a-z]{20}$')
_SUPABASE_HOST_RE = re.compile(r'^([a-z]{20})\.supabase\.(co|in)$')
_DASHBOARD_RE = re.compile(r'supabase\.com/dashboard/project/([a-z]{20})')
_HOST_RE = re.compile(r'^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$')
_PROVIDER_RE = re.compile(r'^[a-z0-9_-]{1,40}$')

PASS, FAIL, CANNOT_TELL = 'pass', 'fail', 'cannot_tell'

_HTTP_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Supabase key validation (paste path + after an OAuth fill)
# ---------------------------------------------------------------------------
def normalize_supabase_url(raw: str) -> tuple[str, str, str]:
    """Return (base_url, project_ref, note).

    Accepts what people actually paste: the API URL with or without a trailing
    /rest/v1, a bare host, or the dashboard link. project_ref is '' when the URL
    is not a <ref>.supabase.co host (custom domain, self-hosted); the key/URL
    project comparison is then skipped rather than guessed.
    """
    s = (raw or '').strip()
    if not s:
        return '', '', ''
    m = _DASHBOARD_RE.search(s)
    if m:
        ref = m.group(1)
        return (f'https://{ref}.supabase.co', ref,
                'That was a dashboard link, so it was changed to the project API URL.')
    if '://' not in s:
        s = 'https://' + s
    p = urlparse(s)
    host = (p.hostname or '').lower()
    if not host:
        return '', '', ''
    base = f'{p.scheme or "https"}://{host}' + (f':{p.port}' if p.port else '')
    note = ''
    if p.path.strip('/'):
        note = 'The part after the domain was removed; the Project URL is just the address of the project.'
    hm = _SUPABASE_HOST_RE.match(host)
    return base, (hm.group(1) if hm else ''), note


def _jwt_claims(key: str) -> Optional[dict]:
    """Decode (NOT verify) a JWT's payload. Legacy Supabase keys are JWTs that
    carry the project `ref` and the `role` (anon / service_role)."""
    parts = (key or '').split('.')
    if len(parts) != 3:
        return None
    try:
        seg = parts[1] + '=' * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg.encode()).decode())
        return claims if isinstance(claims, dict) else None
    except Exception:
        return None


def _key_kind(key: str) -> tuple[str, Optional[dict]]:
    """('publishable'|'secret'|'anon'|'service_role'|'unknown', jwt_claims)"""
    k = (key or '').strip()
    if k.startswith('sb_publishable_'):
        return 'publishable', None
    if k.startswith('sb_secret_'):
        return 'secret', None
    claims = _jwt_claims(k)
    if claims:
        role = str(claims.get('role', ''))
        if role in ('anon', 'service_role'):
            return role, claims
    return 'unknown', claims


_FIELD_WANTS = {
    # field id -> (kinds accepted, plain name, probe path)
    'anon_key': ({'anon', 'publishable', 'unknown'}, 'anon / publishable key', '/auth/v1/settings'),
    'service_role_key': ({'service_role', 'secret', 'unknown'}, 'service role / secret key', '/rest/v1/'),
}


def _short(ref: str) -> str:
    return ref or 'unknown'


def _check_one_key(field: str, key: str, base_url: str, url_ref: str, http) -> dict:
    wants, plain, path = _FIELD_WANTS[field]
    kind, claims = _key_kind(key)
    out = {'field': field, 'kind': kind, 'verdict': CANNOT_TELL, 'message': '', 'status': None}

    # 1. Offline checks — definitive, no network needed.
    if kind not in wants:
        if field == 'anon_key':
            out.update(verdict=FAIL, message=(
                'The anon / publishable box has a secret key in it. Put the secret key in the '
                'service role box, and the anon / publishable key here.'))
        else:
            out.update(verdict=FAIL, message=(
                'The service role box has the public anon / publishable key in it. The service '
                'role key is the secret one, shown under "service_role" or "Secret keys".'))
        return out
    if claims:
        key_ref = str(claims.get('ref', ''))
        out['key_ref'] = key_ref
        if key_ref and url_ref and key_ref != url_ref:
            out.update(verdict=FAIL, message=(
                f'This {plain} belongs to Supabase project {key_ref}, but the Project URL points '
                f'to project {url_ref}. They have to come from the same project. If your app runs '
                f'on project {url_ref}, the keys must be copied from that project, which may be '
                f'owned by a different account (for example one a site builder created for you).'))
            return out
        exp = claims.get('exp')
        if isinstance(exp, (int, float)) and exp < time.time():
            out.update(verdict=FAIL, message=f'This {plain} has expired. Copy a current one from the project.')
            return out

    # 2. Ask the project itself.
    headers = {'apikey': key, 'Accept': 'application/json'}
    if claims:
        headers['Authorization'] = f'Bearer {key}'
    target = base_url.rstrip('/') + path
    try:
        resp = http.get(target, headers=headers, timeout=_HTTP_TIMEOUT)
    except requests.Timeout:
        out['message'] = f'Could not check the {plain}: the project did not answer in time.'
        return out
    except requests.RequestException:
        out['message'] = (f'Could not check the {plain}: nothing answered at {base_url}. '
                          'Check the Project URL.')
        return out

    out['status'] = resp.status_code
    body = (resp.text or '')[:400]
    if resp.status_code == 200:
        out.update(verdict=PASS, message=f'Supabase accepted the {plain}.')
    elif resp.status_code in (401, 403) and 'invalid api key' in body.lower():
        key_ref = out.get('key_ref', '')
        if key_ref and url_ref and key_ref == url_ref:
            msg = (f'Supabase rejected the {plain}. It is for the right project ({url_ref}) but is no '
                   'longer accepted: it was probably rotated, or legacy keys were turned off.')
        else:
            msg = (f'Supabase rejected the {plain} for the project at {base_url}. Either the key is '
                   'from a different project, or it was deleted or rotated.')
        out.update(verdict=FAIL, message=msg)
    elif resp.status_code >= 500:
        out['message'] = (f'Could not check the {plain}: the project answered {resp.status_code}. '
                          'It may be paused or restarting.')
    else:
        snippet = re.sub(r'\s+', ' ', body.replace(key, '***')).strip()[:160]
        out['message'] = (f'Could not tell whether the {plain} works: the project answered '
                          f'{resp.status_code}{" (" + snippet + ")" if snippet else ""}.')
    return out


def validate_supabase_fields(fields: dict, http=None) -> dict:
    """Validate a supabase credential's url + keys. Returns
    {verdict, message, checks[], url, ref, note}. verdict is pass / fail / cannot_tell.
    """
    http = http or requests
    fields = fields or {}
    base_url, url_ref, note = normalize_supabase_url(fields.get('url', ''))
    result = {'verdict': CANNOT_TELL, 'message': '', 'checks': [], 'url': base_url,
              'ref': url_ref, 'note': note, 'checked_at': time.time()}
    if not base_url:
        result['message'] = 'Add the Project URL (box 1) so the keys can be checked against it.'
        return result
    keys = [(f, (fields.get(f) or '').strip()) for f in ('anon_key', 'service_role_key')]
    keys = [(f, k) for f, k in keys if k]
    if not keys:
        result['message'] = 'No keys to check yet.'
        return result
    checks = [_check_one_key(f, k, base_url, url_ref, http) for f, k in keys]
    result['checks'] = checks
    fails = [c for c in checks if c['verdict'] == FAIL]
    unknown = [c for c in checks if c['verdict'] == CANNOT_TELL]
    if fails:
        result['verdict'] = FAIL
        result['message'] = fails[0]['message']
    elif unknown:
        result['verdict'] = CANNOT_TELL
        result['message'] = unknown[0]['message']
    else:
        result['verdict'] = PASS
        n = 'Both keys work' if len(checks) == 2 else 'The key works'
        result['message'] = f'{n} with project {_short(url_ref) if url_ref else base_url}.'
    return result


def fields_fingerprint(fields: dict) -> str:
    """Fingerprint of the values a verdict was computed for. A verdict whose
    fingerprint no longer matches the stored values is stale and must not be
    shown as the current state."""
    raw = json.dumps([(fields or {}).get(k, '') for k in ('url', 'anon_key', 'service_role_key')])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Supabase Management API (after "Connect Supabase")
# ---------------------------------------------------------------------------
class ConnectorError(Exception):
    def __init__(self, message: str, code: str = 'error', status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _mgmt_get(token: str, path: str, params: dict = None, http=None):
    http = http or requests
    try:
        resp = http.get(SUPABASE_API + path, params=params or None, timeout=15, headers={
            'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
    except requests.RequestException as exc:
        raise ConnectorError(f'Could not reach Supabase ({exc.__class__.__name__}). Try again.',
                             'unreachable', 502)
    if resp.status_code == 401:
        raise ConnectorError('Supabase no longer accepts this connection. Disconnect and connect Supabase again.',
                             'reconnect', 401)
    if resp.status_code == 403:
        raise ConnectorError('The Supabase app was not given permission for this. Disconnect, connect '
                             'again and approve access.', 'forbidden', 403)
    if resp.status_code != 200:
        raise ConnectorError(f'Supabase answered {resp.status_code} for {path}.', 'upstream', 502)
    try:
        return resp.json()
    except ValueError:
        raise ConnectorError(f'Supabase sent something that is not JSON for {path}.', 'upstream', 502)


def supabase_account_info(token: str, http=None) -> dict:
    """{'email': str, 'orgs': [{'slug','name'}]} — best effort, never raises."""
    info = {'email': '', 'orgs': []}
    try:
        prof = _mgmt_get(token, '/v1/profile', http=http)
        if isinstance(prof, dict):
            info['email'] = prof.get('primary_email', '') or prof.get('username', '') or ''
    except ConnectorError:
        pass  # /v1/profile carries no OAuth scope in the spec; it may be closed to app tokens
    try:
        orgs = _mgmt_get(token, '/v1/organizations', http=http)
        if isinstance(orgs, list):
            info['orgs'] = [{'slug': o.get('slug') or o.get('id', ''), 'name': o.get('name', '')}
                            for o in orgs if isinstance(o, dict)]
    except ConnectorError:
        pass
    return info


def _saved_supabase_ref(username: str) -> tuple[str, str]:
    from services.vault import get_credential_fields, get_catalog_credential
    fields = get_credential_fields(username, SUPABASE_CREDENTIAL_ID) or {}
    url = fields.get('url', '')
    if not url:
        entry = get_catalog_credential(SUPABASE_CREDENTIAL_ID) or {}
        for f in entry.get('fields', []) or []:
            if f.get('id') == 'url' and f.get('env_var'):
                url = os.environ.get(f['env_var'], '')
    base, ref, _ = normalize_supabase_url(url)
    return base, ref


def supabase_list_projects(username: str, http=None) -> dict:
    """Account, organizations and projects the connection can see, plus how the
    currently saved Project URL relates to them."""
    from services.vault import get_fresh_oauth_token, read_vault
    token = get_fresh_oauth_token(username, SUPABASE_CONNECTOR_ID)
    if not token:
        raise ConnectorError('Connect Supabase first.', 'not_connected', 409)
    info = supabase_account_info(token, http=http)
    projects = _mgmt_get(token, '/v1/projects', http=http)
    if not isinstance(projects, list):
        raise ConnectorError('Supabase sent an unexpected project list.', 'upstream', 502)
    org_names = {o['slug']: o['name'] for o in info['orgs']}
    out = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        ref = p.get('ref') or p.get('id') or ''
        if not _REF_RE.match(ref):
            continue
        org = p.get('organization_slug') or p.get('organization_id') or ''
        out.append({
            'ref': ref,
            'name': p.get('name', ref),
            'organization_slug': org,
            'organization_name': org_names.get(org, ''),
            'region': p.get('region', ''),
            'status': p.get('status', ''),
            'url': f'https://{ref}.supabase.co',
        })
    out.sort(key=lambda x: (x['organization_name'].lower(), x['name'].lower()))
    saved_url, saved_ref = _saved_supabase_ref(username)
    vault_entry = read_vault(username).get('credentials', {}).get(SUPABASE_CREDENTIAL_ID, {}) or {}
    selected = (vault_entry.get('oauth_project') or {}).get('ref', '')
    return {
        'ok': True,
        'account': info,
        'projects': out,
        'current': {
            'url': saved_url,
            'ref': saved_ref,
            'in_list': bool(saved_ref) and any(p['ref'] == saved_ref for p in out),
        },
        'selected_ref': selected,
    }


def _pick_key_pairs(keys: list) -> list[tuple[str, str, str]]:
    """Candidate (label, public_key, secret_key) pairs in preference order.
    Legacy JWT keys first: they work in every client and in the Authorization
    header; the newer publishable/secret keys are the fallback when a project
    has legacy keys turned off."""
    legacy = {k.get('name'): k.get('api_key') for k in keys
              if isinstance(k, dict) and (k.get('type') in ('legacy', None)) and k.get('api_key')}
    new_pub = [k.get('api_key') for k in keys if isinstance(k, dict) and k.get('type') == 'publishable' and k.get('api_key')]
    new_sec = [k.get('api_key') for k in keys if isinstance(k, dict) and k.get('type') == 'secret' and k.get('api_key')]
    pairs = []
    if legacy.get('anon') and legacy.get('service_role'):
        pairs.append(('legacy', legacy['anon'], legacy['service_role']))
    if new_pub and new_sec:
        pairs.append(('new', new_pub[0], new_sec[0]))
    return pairs


def supabase_select_project(username: str, ref: str, http=None) -> dict:
    """Fill the `supabase` credential from the picked project. The ref must be
    one the connection can see — never trust the browser's list."""
    from services.vault import get_fresh_oauth_token, set_credential
    ref = (ref or '').strip()
    if not _REF_RE.match(ref):
        raise ConnectorError('That is not a Supabase project reference.', 'bad_ref', 400)
    listing = supabase_list_projects(username, http=http)
    project = next((p for p in listing['projects'] if p['ref'] == ref), None)
    if not project:
        raise ConnectorError('That project is not in the Supabase organization you connected.', 'not_found', 404)
    token = get_fresh_oauth_token(username, SUPABASE_CONNECTOR_ID)
    if not token:
        raise ConnectorError('Connect Supabase first.', 'not_connected', 409)
    keys = _mgmt_get(token, f'/v1/projects/{ref}/api-keys', params={'reveal': 'true'}, http=http)
    if not isinstance(keys, list):
        raise ConnectorError('Supabase sent an unexpected key list.', 'upstream', 502)
    pairs = _pick_key_pairs(keys)
    if not pairs:
        raise ConnectorError('Supabase did not return usable keys for this project. The connection may '
                             'be missing the "Secrets: read" permission.', 'no_keys', 502)

    url = project['url']
    chosen = None
    verdict = None
    for label, pub, sec in pairs:
        v = validate_supabase_fields({'url': url, 'anon_key': pub, 'service_role_key': sec}, http=http)
        if v['verdict'] != FAIL:
            chosen, verdict = (label, pub, sec), v
            break
        verdict = v
    if not chosen:
        raise ConnectorError('Supabase handed over keys for this project, but the project itself rejected '
                             'them: ' + (verdict or {}).get('message', ''), 'keys_rejected', 502)

    fields = {'url': url, 'anon_key': chosen[1], 'service_role_key': chosen[2]}
    validation = validation_record(verdict, fields)
    to_restart = set_credential(
        username, SUPABASE_CREDENTIAL_ID, fields=fields, source='oauth',
        validation=validation,
        meta={'oauth_project': {'ref': ref, 'name': project['name'],
                                'organization': project['organization_name'] or project['organization_slug'],
                                'key_type': chosen[0], 'filled_at': time.time()}},
    )
    return {
        'ok': True,
        'project': project,
        'key_type': chosen[0],
        'verdict': verdict['verdict'],
        'message': verdict['message'],
        'restarting': sorted(to_restart),
    }


def validation_record(v: dict, fields: dict) -> dict:
    """What gets persisted next to the credential: the verdict, the plain-words
    message and the fingerprint of the values it was computed for. Never the keys."""
    return {
        'verdict': v.get('verdict', CANNOT_TELL),
        'message': v.get('message', ''),
        'checked_at': v.get('checked_at', time.time()),
        'fingerprint': fields_fingerprint(fields),
        'checks': [{'field': c.get('field'), 'verdict': c.get('verdict'), 'status': c.get('status'),
                    'message': c.get('message')} for c in v.get('checks', [])],
    }


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------
def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge). Verifier is 64 url-safe chars,
    inside RFC 7636's 43..128; challenge is 43 chars (GitHub requires that)."""
    import secrets
    verifier = secrets.token_urlsafe(48)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return verifier, challenge


# ---------------------------------------------------------------------------
# Single-callback relay
# ---------------------------------------------------------------------------
def relay_base_url() -> str:
    """OAUTH_RELAY_BASE_URL, e.g. https://oauth.jam-bot.com. Empty = relay off."""
    v = os.environ.get('OAUTH_RELAY_BASE_URL', '').strip().rstrip('/')
    if v and not v.startswith('https://') and not v.startswith('http://localhost') \
            and not v.startswith('http://127.0.0.1'):
        logger.warning('[connectors] OAUTH_RELAY_BASE_URL must be https — relay disabled')
        return ''
    return v


def relay_redirect_uri(provider: str) -> str:
    base = relay_base_url()
    return f'{base}/api/vault/oauth/relay/{provider}' if base else ''


def _relay_allowed(domain: str) -> bool:
    """OAUTH_RELAY_ALLOWED_DOMAINS: comma list. '.jam-bot.com' allows any
    subdomain of jam-bot.com; 'foo.example.com' allows that exact host.
    Empty list = allow nothing (fail closed)."""
    raw = os.environ.get('OAUTH_RELAY_ALLOWED_DOMAINS', '')
    rules = [r.strip().lower() for r in raw.split(',') if r.strip()]
    for r in rules:
        if r.startswith('.'):
            if domain.endswith(r) and len(domain) > len(r):
                return True
        elif domain == r:
            return True
    return False


def relay_target(provider: str, args) -> tuple[Optional[str], str]:
    """Where the relay sends the browser: (url, '') or (None, reason)."""
    if not _PROVIDER_RE.match(provider or ''):
        return None, 'Unknown provider.'
    try:
        state = json.loads(args.get('state', '') or '{}')
    except (json.JSONDecodeError, TypeError):
        state = {}
    domain = str(state.get('d', '') if isinstance(state, dict) else '').strip().lower()
    if not domain or not _HOST_RE.match(domain):
        return None, 'The sign-in came back without a valid return address. Start the connection again.'
    if not _relay_allowed(domain):
        return None, 'That return address is not one of ours. Start the connection again from your own admin page.'
    qs = {k: args.get(k) for k in ('code', 'state', 'error', 'error_description') if args.get(k)}
    return f'https://{domain}/api/vault/oauth/callback/{provider}?' + urlencode(qs), ''
