"""
Vault API — credential management, OAuth flows, sync, and testing.

Replaces the old /api/admin/ai-config endpoints with a dynamic,
plugin-aware credential system.
"""
import json
import logging
import os

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

vault_bp = Blueprint('vault', __name__)


def _get_username() -> str:
    """
    Determine the current client username.
    In multi-tenant JamBot, this comes from the DOMAIN env var (e.g., nick.jam-bot.com → nick).
    In single-user mode, defaults to 'default'.
    """
    domain = os.getenv('DOMAIN', '')
    if domain and '.jam-bot.com' in domain:
        return domain.split('.')[0]
    return os.getenv('VAULT_USERNAME', 'default')


# ---------------------------------------------------------------------------
# Credential CRUD
# ---------------------------------------------------------------------------

@vault_bp.route('/api/vault/credentials', methods=['GET'])
def list_credentials():
    """
    List all credentials with status — merged from platform catalog + installed plugins.
    Grouped by credential group, includes has_value/masked_value/consumers.
    """
    from services.vault import get_credentials_status, get_current_model_selection, get_available_models
    username = _get_username()
    creds = get_credentials_status(username)
    models = get_available_models(username)
    model_sel = get_current_model_selection(username)

    # Group credentials
    groups = {}
    for c in creds:
        g = c['group']
        groups.setdefault(g, []).append(c)

    # Stable group ordering
    group_order = ['LLM Providers', 'Voice', 'Services', 'Platform', 'Connections', 'Custom']
    ordered_groups = []
    for g in group_order:
        if g in groups:
            ordered_groups.append({'name': g, 'credentials': groups.pop(g)})
    # Remaining groups (from plugins, etc.)
    for g, clist in groups.items():
        ordered_groups.append({'name': g, 'credentials': clist})

    return jsonify({
        'groups': ordered_groups,
        'models': models,
        'model_selection': model_sel,
    })


@vault_bp.route('/api/vault/credentials/<cred_id>', methods=['PUT'])
def update_credential(cred_id):
    """
    Set/update a credential value.

    Body for single-key: {"value": "sk-..."}
    Body for multi-field: {"fields": {"login": "...", "password": "..."}}
    Body for model selection: {"primary": "mx/Model", "fallback": "zai/glm-5-turbo"}
    """
    from services.vault import (
        set_credential, get_catalog_credential, get_merged_catalog,
        set_model_selection, ensure_vault
    )
    username = _get_username()
    ensure_vault(username)
    data = request.get_json(silent=True) or {}

    # Handle model selection update
    if cred_id == '_model_selection':
        set_model_selection(
            username,
            primary=data.get('primary'),
            fallback=data.get('fallback'),
        )
        return jsonify({'ok': True, 'message': 'Model selection updated'})

    value = data.get('value')
    fields = data.get('fields')

    # Don't accept masked values as updates
    if value and value.startswith('***'):
        return jsonify({'ok': True, 'message': 'No change (masked value)'})
    if fields:
        fields = {k: v for k, v in fields.items() if not str(v).startswith('***')}
        if not fields:
            return jsonify({'ok': True, 'message': 'No change (masked values)'})

    to_restart = set_credential(username, cred_id, value=value, fields=fields)

    if to_restart:
        containers = sorted(to_restart)
        msg = (
            f'Saved. Agent will restart in ~6s to pick up the new value '
            f'(affects: {", ".join(containers)}).'
        )
        return jsonify({
            'ok': True,
            'message': msg,
            'restarting': containers,
            'eta_seconds': 15,
        })
    return jsonify({
        'ok': True,
        'message': f'Credential {cred_id} saved (no container restart needed)',
        'restarting': [],
        'eta_seconds': 0,
    })


@vault_bp.route('/api/vault/credentials', methods=['POST'])
def add_credential():
    """
    Add a custom credential not in any catalog.

    Body: {"name": "My API", "env_var": "MY_API_KEY", "value": "...", "group": "Custom"}
    """
    from services.vault import add_custom_credential, ensure_vault
    username = _get_username()
    ensure_vault(username)
    data = request.get_json(silent=True) or {}

    name = data.get('name', '').strip()
    env_var = data.get('env_var', '').strip()
    value = data.get('value', '').strip()
    group = data.get('group', 'Custom')

    if not name or not env_var:
        return jsonify({'error': 'name and env_var are required'}), 400

    cred_id = add_custom_credential(username, name, env_var, value, group)
    return jsonify({'ok': True, 'id': cred_id, 'message': f'Custom credential "{name}" added'})


@vault_bp.route('/api/vault/credentials/<cred_id>', methods=['DELETE'])
def remove_credential(cred_id):
    """Delete a custom credential. Only works for user-added custom credentials."""
    from services.vault import delete_credential, read_vault
    username = _get_username()
    vault = read_vault(username)
    entry = vault.get('credentials', {}).get(cred_id, {})

    if not entry.get('custom'):
        return jsonify({'error': 'Can only delete custom credentials'}), 400

    delete_credential(username, cred_id)
    return jsonify({'ok': True, 'message': f'Credential {cred_id} removed'})


# ---------------------------------------------------------------------------
# Credential testing
# ---------------------------------------------------------------------------

@vault_bp.route('/api/vault/credentials/<cred_id>/test', methods=['POST'])
def test_credential_endpoint(cred_id):
    """Test a credential by hitting the provider's test endpoint."""
    from services.vault import test_credential
    username = _get_username()
    result = test_credential(username, cred_id)
    return jsonify(result)


# ---------------------------------------------------------------------------
# OAuth flows
# ---------------------------------------------------------------------------

@vault_bp.route('/api/vault/oauth/<cred_id>/connect', methods=['GET'])
def oauth_connect(cred_id):
    """
    Start an OAuth flow. Returns the authorization URL to redirect the user to.
    """
    from services.vault import build_oauth_url
    username = _get_username()
    domain = os.getenv('DOMAIN', 'localhost')

    url = build_oauth_url(cred_id, username, domain)
    if not url:
        return jsonify({'error': 'OAuth not configured for this credential'}), 400

    return jsonify({'url': url})


@vault_bp.route('/api/vault/oauth/callback/<provider>', methods=['GET'])
def oauth_callback(provider):
    """
    OAuth callback — exchange auth code for tokens.
    This endpoint is hit by the OAuth provider after user consent.
    Returns HTML that closes the popup and notifies the parent window.
    """
    from services.vault import exchange_oauth_code

    code = request.args.get('code', '')
    state_raw = request.args.get('state', '{}')
    error = request.args.get('error', '')

    if error:
        return _oauth_callback_html(False, f'OAuth error: {error}')

    if not code:
        return _oauth_callback_html(False, 'No authorization code received')

    try:
        state = json.loads(state_raw)
    except json.JSONDecodeError:
        state = {}

    cred_id = state.get('cred_id', provider)
    username = state.get('username', '')

    if not username:
        return _oauth_callback_html(False, 'Missing username in state')

    result = exchange_oauth_code(cred_id, code, username)

    if result.get('ok'):
        return _oauth_callback_html(True, result.get('account', 'Connected'))
    else:
        return _oauth_callback_html(False, result.get('error', 'Unknown error'))


@vault_bp.route('/api/vault/oauth/<cred_id>/disconnect', methods=['POST'])
def oauth_disconnect(cred_id):
    """Disconnect an OAuth connection."""
    from services.vault import disconnect_oauth
    username = _get_username()
    disconnect_oauth(username, cred_id)
    return jsonify({'ok': True, 'message': f'{cred_id} disconnected'})


@vault_bp.route('/api/vault/oauth/<cred_id>/status', methods=['GET'])
def oauth_status(cred_id):
    """Get the status of an OAuth connection."""
    from services.vault import get_oauth_status
    username = _get_username()
    status = get_oauth_status(username, cred_id)
    return jsonify(status)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@vault_bp.route('/api/vault/sync', methods=['POST'])
def sync_all_endpoint():
    """Re-sync ALL credentials to ALL consumers."""
    from services.vault import sync_all
    username = _get_username()
    sync_all(username)
    return jsonify({'ok': True, 'message': 'All credentials synced'})


@vault_bp.route('/api/vault/sync/<cred_id>', methods=['POST'])
def sync_one_endpoint(cred_id):
    """Re-sync one credential to its consumers."""
    from services.vault import sync_credential
    username = _get_username()
    sync_credential(username, cred_id)
    return jsonify({'ok': True, 'message': f'{cred_id} synced'})


# ---------------------------------------------------------------------------
# Phase 1.5 Cycle C: Platform Setup
#
# These endpoints power the admin "Platform Setup" page where the operator
# of a JamBot instance configures OAuth provider credentials (Google,
# Facebook, etc.) once for all clients on the instance.
#
# The page is NOT role-gated — any admin can see it. The credentials live
# in /mnt/system/base/.platform-oauth.env (chmod 640 mike:openvoiceui).
# Saving via this endpoint writes the file and recreates the openvoiceui
# container so the new env vars are loaded.
# ---------------------------------------------------------------------------

# Provider definitions — stable identifiers that group catalog credentials.
# Each provider lists the env vars it needs and the catalog credentials it
# enables. This is the source of truth for the Platform Setup page.
_PLATFORM_PROVIDERS = [
    {
        'id': 'google',
        'name': 'Google',
        'description': 'Enables Google Business Profile, Google Analytics, Google Search Console, Gmail and other Google APIs.',
        'console_url': 'https://console.cloud.google.com/apis/credentials',
        'console_create_url': 'https://console.cloud.google.com/projectcreate',
        'docs_url': 'https://developers.google.com/identity/protocols/oauth2',
        'env_vars': {
            'client_id': 'GOOGLE_OAUTH_CLIENT_ID',
            'client_secret': 'GOOGLE_OAUTH_CLIENT_SECRET',
        },
        'enables_credentials': ['google_business', 'google_analytics', 'google_search_console'],
        'setup_steps': [
            'Create a Google Cloud project (free tier is fine).',
            'Enable the APIs you want clients to use: Business Profile API, Analytics Data API, Search Console API.',
            'Configure the OAuth consent screen (External, fill in app name + support email).',
            'Create OAuth 2.0 Client ID → Web application.',
            'Add the redirect URIs from this page to "Authorized redirect URIs".',
            'Copy the Client ID and Client Secret below and click Save.',
        ],
    },
    {
        'id': 'facebook',
        'name': 'Facebook / Meta',
        'description': 'Enables Facebook Pages and Instagram Business posting.',
        'console_url': 'https://developers.facebook.com/apps',
        'console_create_url': 'https://developers.facebook.com/apps/create/',
        'docs_url': 'https://developers.facebook.com/docs/facebook-login/',
        'env_vars': {
            'client_id': 'FACEBOOK_OAUTH_APP_ID',
            'client_secret': 'FACEBOOK_OAUTH_APP_SECRET',
        },
        'enables_credentials': ['facebook', 'instagram'],
        'setup_steps': [
            'Go to developers.facebook.com → Create App → Business type.',
            'Add Products: Facebook Login, Instagram Basic Display.',
            'Settings → Basic → copy App ID and App Secret.',
            'Facebook Login → Settings → paste redirect URIs from this page into "Valid OAuth Redirect URIs".',
            'Save the credentials below.',
        ],
    },
    {
        'id': 'intuit',
        'name': 'QuickBooks (Intuit)',
        'description': 'Enables QuickBooks Online — invoices, payments, customers, accounting.',
        'console_url': 'https://developer.intuit.com/app/developer/dashboard',
        'console_create_url': 'https://developer.intuit.com/app/developer/dashboard',
        'docs_url': 'https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization',
        'env_vars': {
            'client_id': 'INTUIT_OAUTH_CLIENT_ID',
            'client_secret': 'INTUIT_OAUTH_CLIENT_SECRET',
        },
        'enables_credentials': ['quickbooks'],
        'setup_steps': [
            'Sign in at developer.intuit.com and create a new App.',
            'Choose APIs your clients need (Accounting API at minimum).',
            'Keys & OAuth tab → copy production Client ID and Client Secret.',
            'Add the redirect URIs from this page to "Redirect URIs".',
            'Save the credentials below.',
        ],
    },
    {
        'id': 'linkedin',
        'name': 'LinkedIn',
        'description': 'Enables posting to LinkedIn pages and company updates.',
        'console_url': 'https://www.linkedin.com/developers/apps',
        'console_create_url': 'https://www.linkedin.com/developers/apps/new',
        'docs_url': 'https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow',
        'env_vars': {
            'client_id': 'LINKEDIN_OAUTH_CLIENT_ID',
            'client_secret': 'LINKEDIN_OAUTH_CLIENT_SECRET',
        },
        'enables_credentials': [],  # No catalog entry yet
        'setup_steps': [
            'Sign in to LinkedIn Developer Portal and create an app.',
            'Fill in company info, logo, and terms-of-service URL.',
            'Auth tab → copy Client ID and Client Secret.',
            'Add the redirect URIs from this page.',
            'Products tab → request Sign In with LinkedIn + Share on LinkedIn.',
        ],
    },
]


def _platform_oauth_env_path():
    """Return the path to .platform-oauth.env using the same resolution
    logic as services/vault.py."""
    from services.vault import _PLATFORM_OAUTH_ENV
    return _PLATFORM_OAUTH_ENV


def _read_platform_oauth_env() -> dict:
    """Parse .platform-oauth.env into a dict. Empty/missing → {}."""
    path = _platform_oauth_env_path()
    result = {}
    if not path.is_file():
        return result
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as exc:
        logger.error(f"Cannot read platform-oauth env: {exc}")
    return result


def _write_platform_oauth_env(updates: dict) -> bool:
    """Update .platform-oauth.env with the given key/value pairs.
    Preserves comments, blank lines, and ordering. Atomic write.
    Returns True on success.
    """
    from services.vault import _update_env_file
    path = _platform_oauth_env_path()
    if not path.is_file():
        # Create from template if missing
        try:
            template = path.parent / 'OpenVoiceUI' / '.platform-oauth.env.example'
            if template.is_file():
                path.write_text(template.read_text())
            else:
                # Minimal seed
                seed = '# Platform OAuth credentials — see .platform-oauth.env.example for full template\n'
                seed += '\n'.join(f'{k}=' for k in updates.keys()) + '\n'
                path.write_text(seed)
        except Exception as exc:
            logger.error(f"Cannot create platform-oauth env: {exc}")
            return False
    return _update_env_file(path, updates)


def _redirect_uris_for_provider(provider_id: str) -> list[str]:
    """Build the redirect URI list for a provider, one per live client.
    Reads /mnt/system/base/registry.json if available, falls back to
    listing /mnt/clients/<user> dirs."""
    import os
    from pathlib import Path
    registry_path = Path('/mnt/system/base/registry.json')
    domains = []
    if registry_path.is_file():
        try:
            with open(registry_path) as f:
                reg = json.load(f)
            for u in reg.get('users', []):
                d = u.get('domain', '').strip()
                if d:
                    domains.append(d)
        except Exception:
            pass
    if not domains:
        clients_dir = Path('/mnt/clients')
        if clients_dir.is_dir():
            for child in sorted(clients_dir.iterdir()):
                if child.is_dir():
                    domains.append(f'{child.name}.jam-bot.com')
    return [f'https://{d}/api/vault/oauth/callback/{provider_id}' for d in sorted(domains)]


@vault_bp.route('/api/vault/platform-setup', methods=['GET'])
def platform_setup_get():
    """Return the current Platform Setup state — list of OAuth providers
    with configuration status, env vars, and redirect URIs."""
    env = _read_platform_oauth_env()

    providers_out = []
    for p in _PLATFORM_PROVIDERS:
        cid_var = p['env_vars']['client_id']
        csec_var = p['env_vars']['client_secret']
        cid_val = env.get(cid_var, '').strip()
        csec_val = env.get(csec_var, '').strip()
        configured = bool(cid_val and csec_val)
        # Mask secret for display, leave client_id visible
        masked_secret = ''
        if csec_val:
            if len(csec_val) >= 12:
                masked_secret = csec_val[:4] + '...' + csec_val[-4:]
            else:
                masked_secret = '***'
        providers_out.append({
            'id': p['id'],
            'name': p['name'],
            'description': p['description'],
            'console_url': p['console_url'],
            'console_create_url': p.get('console_create_url', ''),
            'docs_url': p.get('docs_url', ''),
            'env_vars': p['env_vars'],
            'enables_credentials': p['enables_credentials'],
            'setup_steps': p['setup_steps'],
            'configured': configured,
            'client_id': cid_val,
            'client_secret_masked': masked_secret,
            'has_secret': bool(csec_val),
            'redirect_uris': _redirect_uris_for_provider(p['id']),
        })

    return jsonify({
        'providers': providers_out,
        'env_file': str(_platform_oauth_env_path()),
    })


@vault_bp.route('/api/vault/platform-setup/<provider_id>', methods=['PUT'])
def platform_setup_put(provider_id):
    """Update an OAuth provider's client_id and client_secret. Body:
    {"client_id": "...", "client_secret": "..."}.
    Empty string clears the value.

    No container recreate needed: .platform-oauth.env is only consumed by the
    openvoiceui Python process via os.environ. We update os.environ directly
    in the running process so the new values are live immediately. The file
    is also updated so the values persist across container restarts.
    """
    import os as _os
    p = next((x for x in _PLATFORM_PROVIDERS if x['id'] == provider_id), None)
    if not p:
        return jsonify({'error': f'Unknown provider {provider_id}'}), 400

    data = request.get_json(silent=True) or {}
    cid = data.get('client_id', '').strip()
    csec = data.get('client_secret', '').strip()

    # Reject masked values
    if csec.startswith('***') or '...' in csec:
        return jsonify({'ok': True, 'message': 'No change (masked secret)'})

    updates = {
        p['env_vars']['client_id']: cid,
        p['env_vars']['client_secret']: csec,
    }
    if not _write_platform_oauth_env(updates):
        return jsonify({'error': 'Failed to write platform-oauth env file'}), 500

    # Update os.environ in the running process so the new values are live
    # immediately. No container restart, no debounce, no rename collision.
    for k, v in updates.items():
        if v:
            _os.environ[k] = v
        else:
            _os.environ.pop(k, None)

    return jsonify({
        'ok': True,
        'message': f'{p["name"]} OAuth credentials saved and live.',
        'provider': provider_id,
        'restarting': [],
        'eta_seconds': 0,
    })


@vault_bp.route('/api/vault/platform-setup/<provider_id>', methods=['DELETE'])
def platform_setup_delete(provider_id):
    """Clear an OAuth provider's credentials."""
    import os as _os
    p = next((x for x in _PLATFORM_PROVIDERS if x['id'] == provider_id), None)
    if not p:
        return jsonify({'error': f'Unknown provider {provider_id}'}), 400
    updates = {
        p['env_vars']['client_id']: '',
        p['env_vars']['client_secret']: '',
    }
    if not _write_platform_oauth_env(updates):
        return jsonify({'error': 'Failed to write platform-oauth env file'}), 500
    # Live update — no restart needed
    for k in updates.keys():
        _os.environ.pop(k, None)
    return jsonify({'ok': True, 'message': f'{p["name"]} OAuth credentials cleared.'})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _oauth_callback_html(success: bool, message: str) -> str:
    """Return HTML that closes the popup and notifies the parent window."""
    status = 'success' if success else 'error'
    return f"""<!DOCTYPE html>
<html><head><title>OAuth {status}</title></head>
<body style="background:#1a1a2e;color:#e0e0e0;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h2>{'Connected' if success else 'Connection Failed'}</h2>
<p>{message}</p>
<p style="color:#888">This window will close automatically...</p>
</div>
<script>
if (window.opener) {{
    window.opener.postMessage({{type:'oauth_callback',status:'{status}',message:'{message}'}}, '*');
}}
setTimeout(function(){{ window.close(); }}, 2000);
</script>
</body></html>""", 200, {'Content-Type': 'text/html'}
