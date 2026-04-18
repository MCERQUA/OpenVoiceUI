"""
Identity resolution — maps Clerk user_id to a human identity that the
conversation route injects as [CURRENT_USER: ...] context on every turn.

The agent uses this to know whether it's talking to:
  - admin (Mike, the platform developer — peer collaborator mode)
  - client (account owner — service mode)
  - dev (developer with multi-tenant access)
  - guest (unknown clerk_id — treat as visitor)

Registry is a JSON file mounted at /app/data/identity-registry.json.
File is re-read on every request (cheap, ~few KB) so onboarding a new user
is one JSON edit, no container restart.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

REGISTRY_PATH = os.getenv('IDENTITY_REGISTRY_PATH', '/app/data/identity-registry.json')


def _load_registry() -> dict:
    """Return the registry dict, or empty dict if file is missing/invalid (fail-open)."""
    try:
        with open(REGISTRY_PATH, 'r') as f:
            data = json.load(f)
            return data.get('users', {}) if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f'identity registry load failed: {e}')
        return {}


def resolve(clerk_user_id: Optional[str]) -> Optional[dict]:
    """Look up a clerk user_id in the registry. Returns None if unknown or no id."""
    if not clerk_user_id:
        return None
    return _load_registry().get(clerk_user_id)


def get_current_user_tag(clerk_user_id: Optional[str], tenant: Optional[str] = None) -> Optional[str]:
    """
    Build the [CURRENT_USER: ...] context tag that gets prepended to gateway
    messages. Returns None when there is no useful identity to report (the
    caller should skip injection in that case).

    Three shapes:
      1. Known admin/dev    → emphasizes peer-collaborator framing
      2. Known client       → emphasizes service framing
      3. Unknown clerk_id   → guest framing with the raw id (so logs show it)
    """
    if not clerk_user_id:
        return None

    identity = resolve(clerk_user_id)
    tenant = (tenant or '').strip() or None

    if not identity:
        if tenant:
            return (
                f'[CURRENT_USER: Unknown user (clerk_id: {clerk_user_id}) on tenant {tenant}. '
                f'Treat as a guest until they identify themselves.]'
            )
        return f'[CURRENT_USER: Unknown user (clerk_id: {clerk_user_id}). Treat as a guest.]'

    name = identity.get('name', 'Unknown')
    role = identity.get('role', 'guest')
    title = identity.get('title', '')
    notes = identity.get('notes', '')
    user_tenant = identity.get('tenant', '')

    title_part = f' ({role}/{title})' if title else f' ({role})'

    if role in ('admin', 'dev'):
        if tenant and user_tenant and tenant != user_tenant:
            location = f' currently logged into the {tenant} tenant (not their own).'
        elif tenant:
            location = f' currently logged into the {tenant} tenant.'
        else:
            location = ''
        body = f'{name}{title_part} —{location} {notes}'.strip()
        return f'[CURRENT_USER: {body}]'

    if role == 'client':
        if tenant and user_tenant and tenant != user_tenant:
            location = f' (logged into the {tenant} tenant, not their own {user_tenant}).'
        elif tenant:
            location = f' (logged into their own {tenant} tenant).'
        else:
            location = ''
        body = f'{name}{title_part}{location} {notes}'.strip()
        return f'[CURRENT_USER: {body}]'

    body = f'{name}{title_part}. {notes}'.strip()
    return f'[CURRENT_USER: {body}]'


def whoami_payload(clerk_user_id: Optional[str], tenant: Optional[str] = None) -> dict:
    """Return a JSON-serializable dict describing the current identity, for /api/identity/whoami."""
    identity = resolve(clerk_user_id) or {}
    return {
        'clerk_user_id': clerk_user_id,
        'tenant': tenant,
        'known': bool(identity),
        'name': identity.get('name'),
        'role': identity.get('role'),
        'title': identity.get('title'),
        'notes': identity.get('notes'),
        'home_tenant': identity.get('tenant'),
        'tag': get_current_user_tag(clerk_user_id, tenant),
    }
