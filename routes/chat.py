"""
routes/chat.py — Lightweight text completion for canvas pages.

POST /api/chat  { message: "..." }  →  { response: "..." }

Uses Z.AI (glm-5-turbo) for text rewriting / enhancement.
NEVER Groq — Groq is TTS only, never for LLM.
"""

import logging
import os

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/api/chat', methods=['POST'])
def chat_complete():
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'No message provided'}), 400

    api_key = os.environ.get('ZAI_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'ZAI_API_KEY not configured'}), 500

    try:
        r = requests.post(
            'https://api.z.ai/api/anthropic/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'glm-5-turbo',
                'messages': [{'role': 'user', 'content': message}],
                'max_tokens': 1024,
            },
            timeout=30,
        )
        r.raise_for_status()
        text = r.json()['content'][0]['text'].strip()
        return jsonify({'response': text})
    except Exception as exc:
        logger.exception('chat completion error')
        return jsonify({'error': str(exc)}), 500
