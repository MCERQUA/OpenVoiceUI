"""
Onboarding state blueprint.

GET  /api/onboarding/state  → read onboarding state from runtime dir
POST /api/onboarding/state  → write onboarding state to runtime dir

State file lives at RUNTIME_DIR/onboarding-state.json (bind-mounted volume
in JamBot — persists across containers and devices). Falls back to localStorage
on the frontend when these endpoints return 404 or fail (safe for non-JamBot).
"""

import json
import logging
from flask import Blueprint, jsonify, request
from services.paths import RUNTIME_DIR

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint('onboarding', __name__)

STATE_FILE = RUNTIME_DIR / 'onboarding-state.json'


@onboarding_bp.route('/api/onboarding/state', methods=['GET'])
def get_state():
    try:
        if STATE_FILE.exists():
            return jsonify(json.loads(STATE_FILE.read_text()))
        return jsonify(None)
    except Exception as e:
        logger.error('onboarding get_state error: %s', e)
        return jsonify({'error': str(e)}), 500


@onboarding_bp.route('/api/onboarding/state', methods=['POST'])
def save_state():
    try:
        data = request.get_json(silent=True) or {}
        STATE_FILE.write_text(json.dumps(data))
        return jsonify({'ok': True})
    except Exception as e:
        logger.error('onboarding save_state error: %s', e)
        return jsonify({'error': str(e)}), 500
