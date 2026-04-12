"""
Credential Vault Service — unified key management and OAuth token storage.

Single source of truth for ALL API keys, OAuth tokens, and service credentials.
Replaces the fragmented .platform-keys.env / .openclaw-keys.env / admin ai-config system.

Architecture:
  - Platform catalog (platform-credentials.json) defines known credential types
  - Installed plugins add credentials via plugin.json "credentials" array
  - Per-client vault (vault/credentials.json) stores actual values
  - Sync engine pushes credentials to consumers (openclaw.json, plugin .env files, etc.)
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PLATFORM_CATALOG_PATH = Path('/mnt/system/base/platform-credentials.json')
_PLATFORM_OAUTH_PATH = Path('/mnt/system/base/platform-oauth.json')
_CLIENTS_DIR = Path('/mnt/clients')
_PLUGINS_DIR = Path('/app/plugins')
_OPENCLAW_CONFIG_PATH = Path('/app/runtime/openclaw.json')

# Inside container, vault is mounted at /app/runtime/vault
# which maps to /mnt/clients/<user>/vault on the host
_RUNTIME_VAULT_DIR = Path('/app/runtime/vault')

# Fallback paths for local / dev mode
if not _CLIENTS_DIR.exists():
    _CLIENTS_DIR = Path(os.getenv('VAULT_CLIENTS_DIR', '/tmp/vault-clients'))
if not _PLATFORM_CATALOG_PATH.exists():
    _alt_path = os.getenv('VAULT_CATALOG_PATH', '').strip()
    if _alt_path:
        _alt = Path(_alt_path)
        if _alt.is_file():
            _PLATFORM_CATALOG_PATH = _alt


# ---------------------------------------------------------------------------
# JSONC parser — string-aware so URLs and other // inside strings are preserved
# ---------------------------------------------------------------------------
def _parse_jsonc(text: str) -> dict:
    """Parse JSONC (JSON with // and /* */ comments and trailing commas).

    Walks character-by-character so comment markers inside string literals
    (e.g., "postgresql://..." or "https://...") are left intact.
    """
    out = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        # Not in a string
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
        elif c == '/' and i + 1 < n and text[i + 1] == '/':
            # Line comment — skip to newline
            while i < n and text[i] != '\n':
                i += 1
        elif c == '/' and i + 1 < n and text[i + 1] == '*':
            # Block comment — skip to closing */
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2  # skip the */
        else:
            out.append(c)
            i += 1
    stripped = ''.join(out)
    # Strip trailing commas before } or ]
    stripped = re.sub(r',(\s*[}\]])', r'\1', stripped)
    return json.loads(stripped)


def _safe_exists(path: Path) -> bool:
    """Path.exists() that returns False on PermissionError instead of raising.
    Needed because stat() requires directory search permission on every
    ancestor, which fails when openvoiceui container tries to stat paths
    under /mnt/clients/<user>/openclaw/ (chmod 700 mike:mike).
    """
    try:
        return path.is_file()
    except (PermissionError, OSError):
        return False


def _read_json(path: Path) -> dict:
    """Read a JSON or JSONC file. Tries plain JSON first for speed/safety,
    falls back to JSONC only if plain JSON fails (e.g., openclaw.json with
    comments). Vault credential files are pure JSON so they take the fast path.
    Returns {} on any error (missing file, permission denied, parse error).
    """
    if not _safe_exists(path):
        return {}
    try:
        text = path.read_text()
        if not text.strip():
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return _parse_jsonc(text)
    except (PermissionError, OSError) as exc:
        logger.warning(f"Cannot read {path}: {exc}")
        return {}
    except Exception as exc:
        logger.error(f"Failed to read {path}: {exc}")
        return {}


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 6 and last 4 chars."""
    if not key or len(key) < 12:
        return '***' if key else ''
    return key[:6] + '...' + key[-4:]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Vault paths per client
# ---------------------------------------------------------------------------
def _vault_dir(username: str) -> Path:
    """
    Get vault directory for a client.
    Inside container: /app/runtime/vault (mounted from /mnt/clients/<user>/vault)
    On host: /mnt/clients/<user>/vault
    """
    if _RUNTIME_VAULT_DIR.exists():
        return _RUNTIME_VAULT_DIR
    return _CLIENTS_DIR / username / 'vault'


def _vault_creds_path(username: str) -> Path:
    return _vault_dir(username) / 'credentials.json'


def _vault_oauth_dir(username: str) -> Path:
    return _vault_dir(username) / 'oauth'


def _vault_oauth_path(username: str, provider: str) -> Path:
    return _vault_oauth_dir(username) / f'{provider}.json'


# ---------------------------------------------------------------------------
# Platform catalog
# ---------------------------------------------------------------------------
_catalog_cache: Optional[dict] = None
_catalog_mtime: float = 0


def get_platform_catalog() -> list[dict]:
    """Load platform credential catalog (cached, reloads on file change)."""
    global _catalog_cache, _catalog_mtime
    try:
        mtime = _PLATFORM_CATALOG_PATH.stat().st_mtime
    except OSError:
        return []
    if _catalog_cache is not None and mtime == _catalog_mtime:
        return _catalog_cache
    data = _read_json(_PLATFORM_CATALOG_PATH)
    _catalog_cache = data.get('credentials', [])
    _catalog_mtime = mtime
    return _catalog_cache


def get_catalog_credential(cred_id: str) -> Optional[dict]:
    """Get a single credential definition from the platform catalog."""
    for c in get_platform_catalog():
        if c['id'] == cred_id:
            return c
    return None


# ---------------------------------------------------------------------------
# Plugin credentials — merge installed plugins' declared credentials
# ---------------------------------------------------------------------------
def get_plugin_credentials() -> list[dict]:
    """Collect credentials declared by installed plugins."""
    result = []
    if not _PLUGINS_DIR.exists():
        return result
    for plugin_dir in _PLUGINS_DIR.iterdir():
        if not plugin_dir.is_dir():
            continue
        manifest_path = plugin_dir / 'plugin.json'
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            continue
        plugin_id = manifest.get('id', plugin_dir.name)
        for cred in manifest.get('credentials', []):
            cred_copy = dict(cred)
            cred_copy['_plugin_id'] = plugin_id
            cred_copy['_plugin_name'] = manifest.get('name', plugin_id)
            result.append(cred_copy)
    return result


# ---------------------------------------------------------------------------
# Merged credential catalog — platform + plugins
# ---------------------------------------------------------------------------
def get_merged_catalog() -> list[dict]:
    """
    Merge platform catalog with plugin credentials.
    Plugin credentials with "extend": true add consumers to existing entries.
    Plugin credentials without extend add new entries.
    """
    platform = [dict(c) for c in get_platform_catalog()]
    by_id = {c['id']: c for c in platform}

    for pcred in get_plugin_credentials():
        cred_id = pcred['id']
        if pcred.get('extend') and cred_id in by_id:
            # Merge consumers into existing platform credential
            existing = by_id[cred_id]
            for consumer_name, consumer_cfg in pcred.get('consumers', {}).items():
                existing.setdefault('consumers', {})[consumer_name] = consumer_cfg
            # Track which plugin added this consumer
            existing.setdefault('_extended_by', []).append(pcred.get('_plugin_name', ''))
        elif cred_id not in by_id:
            # New credential from plugin
            by_id[cred_id] = pcred
            platform.append(pcred)
        else:
            # Same ID, not extend — merge consumers only
            existing = by_id[cred_id]
            for consumer_name, consumer_cfg in pcred.get('consumers', {}).items():
                existing.setdefault('consumers', {})[consumer_name] = consumer_cfg

    return platform


# ---------------------------------------------------------------------------
# Per-client vault CRUD
# ---------------------------------------------------------------------------
def ensure_vault(username: str):
    """Create vault directory structure for a client."""
    vdir = _vault_dir(username)
    vdir.mkdir(parents=True, exist_ok=True)
    _vault_oauth_dir(username).mkdir(parents=True, exist_ok=True)
    creds_path = _vault_creds_path(username)
    if not creds_path.exists():
        _write_json(creds_path, {
            'version': 1,
            'updated': _now_iso(),
            'credentials': {},
        })
    # Permissions: owner read/write only
    try:
        os.chmod(str(vdir), 0o700)
        os.chmod(str(creds_path), 0o600)
    except OSError:
        pass


def read_vault(username: str) -> dict:
    """Read a client's credential vault."""
    return _read_json(_vault_creds_path(username))


def _write_vault(username: str, vault: dict):
    """Write a client's credential vault."""
    vault['updated'] = _now_iso()
    _write_json(_vault_creds_path(username), vault)


def get_credential_value(username: str, cred_id: str) -> Optional[str]:
    """Get the raw value of a credential from the vault."""
    vault = read_vault(username)
    entry = vault.get('credentials', {}).get(cred_id)
    if not entry:
        return None
    return entry.get('value', '')


def get_credential_fields(username: str, cred_id: str) -> Optional[dict]:
    """Get multi-field credential values (for basic_auth, multi_field types)."""
    vault = read_vault(username)
    entry = vault.get('credentials', {}).get(cred_id)
    if not entry:
        return None
    return entry.get('fields', {})


def set_credential(username: str, cred_id: str, value: str = None,
                   fields: dict = None, source: str = 'user'):
    """
    Set a credential value in the vault, then sync to consumers.

    Args:
        username: Client username
        cred_id: Credential ID from catalog
        value: For single-key credentials
        fields: For multi-field credentials (e.g. {"login": "x", "password": "y"})
        source: "platform", "user", or "plugin"
    """
    vault = read_vault(username)
    creds = vault.setdefault('credentials', {})

    entry = creds.get(cred_id, {})
    entry['updated'] = _now_iso()
    entry['source'] = source

    if value is not None:
        entry['value'] = value
    if fields is not None:
        entry['fields'] = fields

    creds[cred_id] = entry
    _write_vault(username, vault)

    # Sync to consumers
    sync_credential(username, cred_id)


def delete_credential(username: str, cred_id: str):
    """Remove a credential from the vault (custom credentials only)."""
    vault = read_vault(username)
    creds = vault.get('credentials', {})
    if cred_id in creds:
        del creds[cred_id]
        _write_vault(username, vault)


def add_custom_credential(username: str, name: str, env_var: str, value: str,
                          group: str = 'Custom'):
    """Add a user-defined custom credential not in any catalog."""
    vault = read_vault(username)
    creds = vault.setdefault('credentials', {})

    # Generate ID from env_var
    cred_id = f"custom_{env_var.lower()}"

    creds[cred_id] = {
        'value': value,
        'source': 'user',
        'updated': _now_iso(),
        'custom': True,
        'name': name,
        'env_var': env_var,
        'group': group,
    }
    _write_vault(username, vault)
    return cred_id


# ---------------------------------------------------------------------------
# Credential status — merge catalog + vault into displayable list
# ---------------------------------------------------------------------------
def get_credentials_status(username: str) -> list[dict]:
    """
    Get full credential status for a client — merged catalog with vault state.
    Returns list suitable for admin UI rendering.
    """
    catalog = get_merged_catalog()
    vault = read_vault(username)
    vault_creds = vault.get('credentials', {})

    result = []
    seen_ids = set()

    for entry in catalog:
        cred_id = entry['id']
        seen_ids.add(cred_id)
        vault_entry = vault_creds.get(cred_id, {})

        cred_type = entry.get('type', 'api_key')
        has_value = False
        masked_value = ''

        if cred_type == 'oauth2':
            # Check for OAuth token file
            oauth_provider = entry.get('oauth', {}).get('provider', cred_id)
            token_path = _vault_oauth_path(username, cred_id)
            if _safe_exists(token_path):
                token_data = _read_json(token_path)
                has_value = bool(token_data.get('refresh_token'))
                masked_value = token_data.get('connected_account', 'Connected')
        elif cred_type in ('multi_field', 'basic_auth'):
            fields = vault_entry.get('fields', {})
            field_defs = entry.get('fields', [])
            has_value = all(fields.get(f['id']) for f in field_defs)
            if has_value:
                # Mask the first field as representative
                first_field = field_defs[0]['id'] if field_defs else ''
                masked_value = _mask_key(fields.get(first_field, ''))
        else:
            raw_val = vault_entry.get('value', '')
            # Also check env var as fallback
            if not raw_val and entry.get('env_var'):
                raw_val = os.environ.get(entry['env_var'], '')
            has_value = bool(raw_val)
            masked_value = _mask_key(raw_val)

        # Build consumer list
        consumers = []
        for cname, ccfg in entry.get('consumers', {}).items():
            consumers.append(cname)

        item = {
            'id': cred_id,
            'name': entry.get('name', cred_id),
            'description': entry.get('description', ''),
            'type': cred_type,
            'group': entry.get('group', 'Other'),
            'has_value': has_value,
            'masked_value': masked_value,
            'consumers': consumers,
            'source': vault_entry.get('source', 'platform' if entry.get('platform_default') else 'none'),
            'env_var': entry.get('env_var', ''),
            'docs_url': entry.get('docs_url', ''),
            'has_test': bool(entry.get('test')),
            'fields': entry.get('fields'),  # For multi-field rendering
        }

        if cred_type == 'oauth2':
            item['oauth'] = entry.get('oauth', {})

        # Plugin source info
        if entry.get('_plugin_id'):
            item['plugin_id'] = entry['_plugin_id']
            item['plugin_name'] = entry['_plugin_name']

        result.append(item)

    # Add custom vault credentials not in catalog
    for cred_id, vault_entry in vault_creds.items():
        if cred_id in seen_ids:
            continue
        if not vault_entry.get('custom'):
            continue
        result.append({
            'id': cred_id,
            'name': vault_entry.get('name', cred_id),
            'description': '',
            'type': 'api_key',
            'group': vault_entry.get('group', 'Custom'),
            'has_value': bool(vault_entry.get('value')),
            'masked_value': _mask_key(vault_entry.get('value', '')),
            'consumers': [],
            'source': 'user',
            'env_var': vault_entry.get('env_var', ''),
            'custom': True,
            'has_test': False,
        })

    return result


# ---------------------------------------------------------------------------
# Sync engine — push credentials to consumers
# ---------------------------------------------------------------------------
def sync_credential(username: str, cred_id: str):
    """Push a credential to all its declared consumers."""
    catalog = get_merged_catalog()
    cat_entry = None
    for c in catalog:
        if c['id'] == cred_id:
            cat_entry = c
            break

    if not cat_entry:
        logger.debug(f"No catalog entry for {cred_id}, skipping sync")
        return

    vault = read_vault(username)
    vault_entry = vault.get('credentials', {}).get(cred_id, {})
    raw_value = vault_entry.get('value', '')
    raw_fields = vault_entry.get('fields', {})

    for consumer_name, consumer_cfg in cat_entry.get('consumers', {}).items():
        consumer_type = consumer_cfg.get('type', 'env_var')
        try:
            if consumer_type == 'openclaw_provider':
                _sync_to_openclaw(username, cred_id, raw_value, consumer_cfg)
            elif consumer_type == 'env_file':
                plugin_id = consumer_name  # consumer name = plugin dir name
                _sync_to_env_file(username, cred_id, raw_value, consumer_cfg, plugin_id)
            elif consumer_type == 'env_var':
                _sync_to_env_var(username, cred_id, raw_value, raw_fields, cat_entry)
            elif consumer_type == 'oauth_token':
                pass  # OAuth tokens are read directly, no push needed
            else:
                logger.warning(f"Unknown consumer type {consumer_type} for {cred_id}")
        except Exception as exc:
            logger.error(f"Sync {cred_id} → {consumer_name} failed: {exc}")


def sync_all(username: str):
    """Re-sync ALL credentials for a client."""
    vault = read_vault(username)
    for cred_id in vault.get('credentials', {}):
        sync_credential(username, cred_id)


def _sync_to_openclaw(username: str, cred_id: str, key_value: str, consumer_cfg: dict):
    """Write/update a provider block in the client's openclaw.json.

    Guarded against PermissionError — openvoiceui container (UID 1001) cannot
    write openclaw.json (chmod 600, owned UID 1000). Phase 2 (Vault v2)
    eliminates this cross-container write entirely; openclaw will read keys
    from a read-only mounted vault at startup.
    """
    if not key_value:
        return

    config_path = _CLIENTS_DIR / username / 'openclaw' / 'openclaw.json'
    if not config_path.exists():
        # Fall back to runtime path (inside container)
        config_path = _OPENCLAW_CONFIG_PATH
    if not config_path.exists():
        logger.warning(f"No openclaw.json found for {username}")
        return

    try:
        config = _parse_jsonc(config_path.read_text())
    except (PermissionError, OSError) as exc:
        logger.warning(
            f"Cannot read {config_path} for sync ({exc}); credential stored "
            f"in vault only. Openclaw will continue using its existing key "
            f"until Phase 2 migration or manual restart."
        )
        return
    except Exception as exc:
        logger.error(f"Failed to parse {config_path}: {exc}")
        return

    providers = config.setdefault('models', {'mode': 'merge'}).setdefault('providers', {})
    provider_id = consumer_cfg['provider_id']

    if provider_id not in providers:
        # Create new provider block
        providers[provider_id] = {
            'baseUrl': consumer_cfg['base_url'],
            'api': consumer_cfg['api_format'],
            'apiKey': key_value,
            'models': consumer_cfg.get('models', []),
        }
    else:
        # Update existing key only
        providers[provider_id]['apiKey'] = key_value

    try:
        _write_json(config_path, config)
        logger.info(f"Synced {cred_id} → openclaw provider '{provider_id}' for {username}")
    except (PermissionError, OSError) as exc:
        logger.warning(
            f"Cannot write {config_path} ({exc}); credential stored in vault "
            f"only. Phase 2 will fix this cross-container write."
        )


def _sync_to_env_file(username: str, cred_id: str, key_value: str,
                      consumer_cfg: dict, plugin_id: str):
    """Write/update key in a plugin's env file."""
    if not key_value:
        return

    plugin_dir = _CLIENTS_DIR / username / plugin_id
    if not plugin_dir.exists():
        logger.debug(f"Plugin dir {plugin_dir} not found, skipping env_file sync")
        return

    env_path = plugin_dir / '.env'
    env_var = consumer_cfg.get('env_var', '')
    if not env_var:
        return

    # Parse existing env file
    existing = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                existing[k.strip()] = v.strip()

    existing[env_var] = key_value

    # Also set companion vars
    for k, v in consumer_cfg.get('also_set', {}).items():
        existing[k] = v

    # Write back
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text('\n'.join(lines) + '\n')
    logger.info(f"Synced {cred_id} → env_file {env_path}")


def _sync_to_env_var(username: str, cred_id: str, raw_value: str,
                     raw_fields: dict, cat_entry: dict):
    """
    Sync to the client's .openclaw.env file (per-client env overrides).
    These get picked up by Docker containers on restart.
    """
    env_path = _CLIENTS_DIR / username / 'compose' / '.openclaw.env'
    if not env_path.parent.exists():
        return

    existing = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                existing[k.strip()] = v.strip()

    cred_type = cat_entry.get('type', 'api_key')
    if cred_type in ('multi_field', 'basic_auth'):
        fields = cat_entry.get('fields', [])
        for fdef in fields:
            fval = raw_fields.get(fdef['id'], '')
            if fval:
                existing[fdef['env_var']] = fval
    else:
        env_var = cat_entry.get('env_var', '')
        if env_var and raw_value:
            existing[env_var] = raw_value

    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text('\n'.join(lines) + '\n')


# ---------------------------------------------------------------------------
# Credential testing
# ---------------------------------------------------------------------------
def test_credential(username: str, cred_id: str) -> dict:
    """
    Test a credential by hitting the provider's test endpoint.
    Returns: {"ok": bool, "status_code": int, "message": str, "latency_ms": int}
    """
    cat_entry = get_catalog_credential(cred_id)
    if not cat_entry:
        # Check merged catalog (includes plugin creds)
        for c in get_merged_catalog():
            if c['id'] == cred_id:
                cat_entry = c
                break
    if not cat_entry or not cat_entry.get('test'):
        return {'ok': False, 'message': 'No test configuration for this credential'}

    test_cfg = cat_entry['test']
    vault = read_vault(username)
    vault_entry = vault.get('credentials', {}).get(cred_id, {})

    # Get the key value
    cred_type = cat_entry.get('type', 'api_key')
    key_value = vault_entry.get('value', '')
    if not key_value and cat_entry.get('env_var'):
        key_value = os.environ.get(cat_entry['env_var'], '')

    if not key_value and cred_type not in ('basic_auth', 'multi_field'):
        return {'ok': False, 'message': 'No key configured'}

    # Build request
    url = test_cfg['url']
    method = test_cfg.get('method', 'GET').upper()
    headers = {}
    auth = None

    auth_type = test_cfg.get('auth_type', '')
    if auth_type == 'basic':
        fields = vault_entry.get('fields', {})
        field_defs = cat_entry.get('fields', [])
        if len(field_defs) >= 2:
            login = fields.get(field_defs[0]['id'], '')
            password = fields.get(field_defs[1]['id'], '')
            if not login or not password:
                return {'ok': False, 'message': 'Missing login or password'}
            auth = (login, password)
    else:
        auth_header = test_cfg.get('auth_header', 'Authorization')
        auth_prefix = test_cfg.get('auth_prefix', '')
        headers[auth_header] = f"{auth_prefix}{key_value}"

    start = time.time()
    try:
        kwargs = {'headers': headers, 'timeout': 10}
        if auth:
            kwargs['auth'] = auth
        if method == 'POST' and test_cfg.get('test_body'):
            kwargs['json'] = test_cfg['test_body']
            if 'Content-Type' not in headers:
                headers['Content-Type'] = 'application/json'

        resp = requests.request(method, url, **kwargs)
        latency = int((time.time() - start) * 1000)

        expect = test_cfg.get('expect_status', [200])
        if resp.status_code in expect or resp.status_code == 200:
            return {
                'ok': True,
                'status_code': resp.status_code,
                'message': 'Connected',
                'latency_ms': latency,
            }
        elif resp.status_code == 401:
            return {
                'ok': False,
                'status_code': 401,
                'message': 'Invalid API key',
                'latency_ms': latency,
            }
        elif resp.status_code == 429:
            return {
                'ok': True,  # Key is valid, just rate limited
                'status_code': 429,
                'message': 'Valid key (rate limited)',
                'latency_ms': latency,
            }
        else:
            return {
                'ok': False,
                'status_code': resp.status_code,
                'message': f'Unexpected response: {resp.status_code}',
                'latency_ms': latency,
            }
    except requests.Timeout:
        return {'ok': False, 'message': 'Connection timed out', 'latency_ms': 10000}
    except requests.ConnectionError as e:
        return {'ok': False, 'message': f'Connection failed: {e}'}
    except Exception as e:
        return {'ok': False, 'message': str(e)}


# ---------------------------------------------------------------------------
# OAuth token management
# ---------------------------------------------------------------------------
def get_oauth_apps() -> dict:
    """Read platform OAuth app credentials (client_id/secret per provider)."""
    return _read_json(_PLATFORM_OAUTH_PATH)


def set_oauth_app(provider: str, client_id: str, client_secret: str, redirect_uri: str):
    """Register/update an OAuth app."""
    apps = get_oauth_apps()
    apps[provider] = {
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
    }
    _write_json(_PLATFORM_OAUTH_PATH, apps)


def build_oauth_url(cred_id: str, username: str, domain: str) -> Optional[str]:
    """
    Build the OAuth authorization URL for a credential.
    Returns the URL the user should be redirected to.
    """
    cat_entry = get_catalog_credential(cred_id)
    if not cat_entry or cat_entry.get('type') != 'oauth2':
        return None

    oauth_cfg = cat_entry.get('oauth', {})
    provider = oauth_cfg.get('provider', '')
    apps = get_oauth_apps()
    app_creds = apps.get(provider)
    if not app_creds:
        logger.error(f"No OAuth app registered for provider '{provider}'")
        return None

    import urllib.parse
    params = {
        'client_id': app_creds['client_id'],
        'redirect_uri': app_creds['redirect_uri'],
        'response_type': 'code',
        'scope': ' '.join(oauth_cfg.get('scopes', [])),
        'state': json.dumps({'cred_id': cred_id, 'username': username}),
    }
    # Extra params (e.g. access_type=offline for Google)
    params.update(oauth_cfg.get('extra_params', {}))

    return oauth_cfg['auth_url'] + '?' + urllib.parse.urlencode(params)


def exchange_oauth_code(cred_id: str, code: str, username: str) -> dict:
    """
    Exchange an authorization code for tokens and store them.
    Returns: {"ok": bool, "account": str, "error": str}
    """
    cat_entry = get_catalog_credential(cred_id)
    if not cat_entry or cat_entry.get('type') != 'oauth2':
        return {'ok': False, 'error': 'Not an OAuth credential'}

    oauth_cfg = cat_entry.get('oauth', {})
    provider = oauth_cfg.get('provider', '')
    apps = get_oauth_apps()
    app_creds = apps.get(provider)
    if not app_creds:
        return {'ok': False, 'error': f'No OAuth app for provider {provider}'}

    # Exchange code for tokens
    try:
        resp = requests.post(oauth_cfg['token_url'], data={
            'code': code,
            'client_id': app_creds['client_id'],
            'client_secret': app_creds['client_secret'],
            'redirect_uri': app_creds['redirect_uri'],
            'grant_type': 'authorization_code',
        }, timeout=15)

        if resp.status_code != 200:
            return {'ok': False, 'error': f'Token exchange failed: {resp.status_code} {resp.text[:200]}'}

        token_data = resp.json()
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}

    # Calculate expiry
    expires_in = token_data.get('expires_in', 3600)
    expires_at = datetime.now(timezone.utc).timestamp() + expires_in

    # Try to get connected account info
    account_email = _get_oauth_account_info(provider, token_data.get('access_token', ''))

    # Store token
    token_file = {
        'access_token': token_data.get('access_token', ''),
        'refresh_token': token_data.get('refresh_token', ''),
        'token_type': token_data.get('token_type', 'Bearer'),
        'expires_at': datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        'scopes': oauth_cfg.get('scopes', []),
        'connected_account': account_email,
        'connected_at': _now_iso(),
    }

    ensure_vault(username)
    _write_json(_vault_oauth_path(username, cred_id), token_file)

    # Also mark in vault credentials
    vault = read_vault(username)
    creds = vault.setdefault('credentials', {})
    creds[cred_id] = {
        'type': 'oauth2',
        'source': 'user',
        'connected_account': account_email,
        'token_file': f'oauth/{cred_id}.json',
        'updated': _now_iso(),
    }
    _write_vault(username, vault)

    return {'ok': True, 'account': account_email}


def _get_oauth_account_info(provider: str, access_token: str) -> str:
    """Get the email/account name for the connected OAuth account."""
    if not access_token:
        return ''
    try:
        if provider == 'google':
            resp = requests.get(
                'https://www.googleapis.com/oauth2/v2/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=5,
            )
            if resp.ok:
                return resp.json().get('email', '')
        elif provider == 'facebook':
            resp = requests.get(
                'https://graph.facebook.com/me?fields=name,email',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=5,
            )
            if resp.ok:
                data = resp.json()
                return data.get('email', data.get('name', ''))
    except Exception:
        pass
    return ''


def get_fresh_oauth_token(username: str, cred_id: str) -> Optional[str]:
    """
    Get a fresh OAuth access token, auto-refreshing if expired.
    Returns the access_token string or None.
    """
    token_path = _vault_oauth_path(username, cred_id)
    if not _safe_exists(token_path):
        return None

    token_data = _read_json(token_path)
    if not token_data.get('access_token'):
        return None

    # Check if expired
    expires_at = token_data.get('expires_at', '')
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            if exp_dt.timestamp() < time.time() - 60:  # 60s buffer
                # Refresh needed
                refreshed = _refresh_oauth_token(cred_id, token_data['refresh_token'])
                if refreshed:
                    token_data.update(refreshed)
                    _write_json(token_path, token_data)
                else:
                    return None
        except (ValueError, KeyError):
            pass

    return token_data.get('access_token')


def _refresh_oauth_token(cred_id: str, refresh_token: str) -> Optional[dict]:
    """Refresh an OAuth token."""
    cat_entry = get_catalog_credential(cred_id)
    if not cat_entry:
        return None

    oauth_cfg = cat_entry.get('oauth', {})
    provider = oauth_cfg.get('provider', '')
    apps = get_oauth_apps()
    app_creds = apps.get(provider)
    if not app_creds:
        return None

    try:
        resp = requests.post(oauth_cfg['token_url'], data={
            'client_id': app_creds['client_id'],
            'client_secret': app_creds['client_secret'],
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }, timeout=15)

        if resp.status_code != 200:
            logger.error(f"OAuth refresh failed for {cred_id}: {resp.status_code}")
            return None

        data = resp.json()
        expires_in = data.get('expires_in', 3600)
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        return {
            'access_token': data['access_token'],
            'expires_at': datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            # Some providers return new refresh_token
            'refresh_token': data.get('refresh_token', refresh_token),
        }
    except Exception as exc:
        logger.error(f"OAuth refresh error for {cred_id}: {exc}")
        return None


def disconnect_oauth(username: str, cred_id: str):
    """Disconnect an OAuth connection — remove tokens."""
    token_path = _vault_oauth_path(username, cred_id)
    if _safe_exists(token_path):
        # Rename to .revoked instead of deleting
        revoked_path = token_path.with_suffix('.revoked')
        token_path.rename(revoked_path)

    vault = read_vault(username)
    creds = vault.get('credentials', {})
    if cred_id in creds:
        del creds[cred_id]
        _write_vault(username, vault)


def get_oauth_status(username: str, cred_id: str) -> dict:
    """Get the status of an OAuth connection."""
    token_path = _vault_oauth_path(username, cred_id)
    if not _safe_exists(token_path):
        return {'connected': False}

    token_data = _read_json(token_path)
    if not token_data.get('refresh_token'):
        return {'connected': False}

    return {
        'connected': True,
        'account': token_data.get('connected_account', ''),
        'connected_at': token_data.get('connected_at', ''),
        'scopes': token_data.get('scopes', []),
    }


# ---------------------------------------------------------------------------
# Model selection helpers — for the model dropdown in connections UI
# ---------------------------------------------------------------------------
def get_available_models(username: str) -> list[dict]:
    """
    Get all models available to a client based on their connected LLM providers.
    Returns list of {"id": "mx/ModelName", "name": "...", "provider": "...", ...}
    """
    catalog = get_merged_catalog()
    vault = read_vault(username)
    vault_creds = vault.get('credentials', {})

    models = []
    for entry in catalog:
        cred_id = entry['id']
        if entry.get('group') != 'LLM Providers':
            continue

        # Check if key exists
        vault_entry = vault_creds.get(cred_id, {})
        has_key = bool(vault_entry.get('value'))
        if not has_key and entry.get('env_var'):
            has_key = bool(os.environ.get(entry['env_var'], ''))

        if not has_key:
            continue

        # Get models from openclaw consumer config
        oc_cfg = entry.get('consumers', {}).get('openclaw', {})
        if oc_cfg.get('type') != 'openclaw_provider':
            continue

        provider_id = oc_cfg['provider_id']
        for model in oc_cfg.get('models', []):
            models.append({
                'id': f"{provider_id}/{model['id']}",
                'name': model.get('name', model['id']),
                'provider': entry['name'],
                'provider_id': provider_id,
                'contextWindow': model.get('contextWindow', 0),
            })

    return models


def get_current_model_selection(username: str) -> dict:
    """Get current primary/fallback model selection from openclaw.json.

    openclaw.json is owned by UID 1000 (openclaw's node user) with chmod 600.
    The openvoiceui Flask container runs as UID 1001 so reads will fail with
    PermissionError. We catch that and return empty defaults so the
    Connections page still loads; Phase 2 (Vault v2) moves model selection
    into the vault itself and eliminates this cross-container read.
    """
    config_path = _CLIENTS_DIR / username / 'openclaw' / 'openclaw.json'
    if not _safe_exists(config_path):
        config_path = _OPENCLAW_CONFIG_PATH
    if not _safe_exists(config_path):
        return {'primary': '', 'fallback': ''}

    try:
        config = _parse_jsonc(config_path.read_text())
    except (PermissionError, OSError) as exc:
        logger.warning(
            f"Cannot read {config_path} for model selection ({exc}); "
            f"returning empty defaults. This is expected when openvoiceui "
            f"and openclaw containers run as different UIDs."
        )
        return {'primary': '', 'fallback': ''}
    except Exception as exc:
        logger.error(f"Failed to parse {config_path}: {exc}")
        return {'primary': '', 'fallback': ''}

    defaults = config.get('agents', {}).get('defaults', {})
    model_cfg = defaults.get('model', {})
    fallbacks = model_cfg.get('fallbacks', [])

    return {
        'primary': model_cfg.get('primary', ''),
        'fallback': fallbacks[0] if fallbacks else '',
    }


def set_model_selection(username: str, primary: str = None, fallback: str = None):
    """Update primary/fallback model selection in openclaw.json.

    Guarded the same way as get_current_model_selection — Phase 1 avoids
    crashing when the openvoiceui container can't write openclaw.json.
    Phase 2 (Vault v2) moves this into the vault itself.
    """
    config_path = _CLIENTS_DIR / username / 'openclaw' / 'openclaw.json'
    if not _safe_exists(config_path):
        config_path = _OPENCLAW_CONFIG_PATH
    if not _safe_exists(config_path):
        return

    try:
        config = _parse_jsonc(config_path.read_text())
    except (PermissionError, OSError) as exc:
        logger.warning(
            f"Cannot read {config_path} to set model selection ({exc}); "
            f"skipping. Phase 2 will store model selection in the vault."
        )
        return
    except Exception as exc:
        logger.error(f"Failed to parse {config_path}: {exc}")
        return

    defaults = config.setdefault('agents', {}).setdefault('defaults', {})
    model_cfg = defaults.setdefault('model', {})

    if primary:
        model_cfg['primary'] = primary
        defaults.setdefault('models', {})[primary] = {}
        defaults.setdefault('subagents', {})['model'] = primary

    if fallback is not None:
        model_cfg['fallbacks'] = [fallback] if fallback else []
        if fallback:
            defaults.setdefault('models', {})[fallback] = {}

    try:
        _write_json(config_path, config)
    except (PermissionError, OSError) as exc:
        logger.warning(
            f"Cannot write {config_path} ({exc}); model selection update "
            f"skipped. Phase 2 will fix this properly."
        )
