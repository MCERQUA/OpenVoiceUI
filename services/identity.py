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
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REGISTRY_PATH = os.getenv('IDENTITY_REGISTRY_PATH', '/app/data/identity-registry.json')

# The Office filing cabinet (built by host cron from transcripts + ledger + anomalies).
# Per-tenant path: <OFFICE_ROOT>/people/<slug>.md
# OPENCLAW workspace is mounted into openvoiceui at /app/runtime/workspace/Agent (RO)
# so the openvoiceui container can read these files at request time.
OFFICE_PEOPLE_DIR = os.getenv(
    'OFFICE_PEOPLE_DIR',
    '/app/runtime/workspace/Agent/office/people',
)
OFFICE_MATTERS_DIR = os.getenv(
    'OFFICE_MATTERS_DIR',
    '/app/runtime/workspace/Agent/office/matters',
)


def _slugify(s: str) -> str:
    s = re.sub(r'[^\w\s-]', '', s.lower())
    s = re.sub(r'\s+', '-', s).strip('-')
    return s[:60] or 'unknown'


def _load_office_briefing(name: str) -> Optional[str]:
    """Return a short briefing summary for `name` from the office filing cabinet.

    Pulls the person file and the top 2-3 highest-severity open matters.
    Returns None if no person file exists or office dir is missing.
    Fail-open: any error → None (CURRENT_USER tag still goes out without briefing).
    """
    try:
        slug = _slugify(name.split()[0])  # use first name only
        person_file = Path(OFFICE_PEOPLE_DIR) / f'{slug}.md'
        if not person_file.exists():
            return None

        text = person_file.read_text(errors='ignore')
        # Pull last_seen + total_events from frontmatter
        last_seen = None
        total_events = None
        for line in text.splitlines():
            if line.startswith('last_seen:'):
                last_seen = line.split(':', 1)[1].strip()
            elif line.startswith('total_events:'):
                total_events = line.split(':', 1)[1].strip()
            if last_seen and total_events:
                break

        # Pull commitments (first ~3 from the "Open follow-ups" section)
        commitments = []
        in_block = False
        for line in text.splitlines():
            if line.strip().startswith('## Open follow-ups'):
                in_block = True
                continue
            if in_block and line.startswith('## '):
                break
            if in_block and line.startswith('- ') and not line.startswith('- (none'):
                commitments.append(line[2:].strip()[:120])
                if len(commitments) >= 3:
                    break

        # Pull top 2 high/medium severity matters
        matters_top = []
        matters_dir = Path(OFFICE_MATTERS_DIR)
        if matters_dir.exists():
            scored = []
            for f in matters_dir.glob('*.md'):
                try:
                    mt = f.read_text(errors='ignore')
                    title = re.search(r'^title:\s*(.+)$', mt, re.M)
                    status = re.search(r'^status:\s*(\w+)$', mt, re.M)
                    sev = re.search(r'^severity:\s*(\w+)$', mt, re.M)
                    if not title:
                        continue
                    if status and status.group(1).lower() not in ('open', 'waiting'):
                        continue
                    sev_v = sev.group(1).lower() if sev else 'medium'
                    rank = 0 if sev_v == 'high' else 1
                    scored.append((rank, title.group(1).strip()[:120]))
                except Exception:
                    continue
            scored.sort()
            matters_top = [t for _, t in scored[:2]]

        # Build the briefing block
        parts = []
        if last_seen:
            parts.append(f'last spoke {last_seen}')
        if total_events and total_events != '0':
            parts.append(f'{total_events} prior contact events')
        meta = ', '.join(parts) if parts else 'first contact'

        out = [f'Office file present ({meta}).']
        if commitments:
            out.append('Open with them: ' + ' | '.join(commitments))
        else:
            out.append('No outstanding commitments to them.')
        if matters_top:
            out.append('Live matters at this company: ' + ' | '.join(matters_top))
        out.append('Lead with their name + 1 specific item. If they ask something not in this brief, say so honestly and log a follow-up — never guess.')

        return ' '.join(out)
    except Exception as e:
        logger.debug(f'office briefing load failed (non-fatal): {e}')
        return None


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
        tag = f'[CURRENT_USER: {body}]'
        # Append the office briefing for clients (the secretary's brief).
        # This is the difference between greeting them as "user" vs greeting
        # them with their open follow-ups + live matters at their company.
        briefing = _load_office_briefing(name)
        if briefing:
            tag += f'\n[OFFICE_BRIEFING: {briefing}]'
        return tag

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
