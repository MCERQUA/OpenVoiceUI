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
