"""
/api/identity/whoami — returns the resolved identity for the currently
logged-in Clerk user. Used by:
  - frontend UI elements that want to show "Logged in as Mike (admin)"
  - debug verification ("did the right clerk_id make it to the server?")
  - the agent itself, if it ever wants to introspect its own current user
"""
import os

from flask import Blueprint, g, jsonify

from services.identity import whoami_payload

identity_bp = Blueprint('identity', __name__)


@identity_bp.route('/api/identity/whoami', methods=['GET'])
def whoami():
    clerk_user_id = getattr(g, 'clerk_user_id', None)
    tenant = os.getenv('JAMBOT_TENANT') or os.getenv('TENANT_NAME') or None
    return jsonify(whoami_payload(clerk_user_id, tenant))
