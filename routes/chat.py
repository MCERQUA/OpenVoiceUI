"""
routes/chat.py — Lightweight text completion for canvas pages.

POST /api/chat  { message: "..." }  →  { response: "..." }

Uses Groq (fast, free) for simple text rewriting / enhancement.
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

    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'GROQ_API_KEY not configured'}), 500

    try:
        r = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [{'role': 'user', 'content': message}],
                'max_tokens': 1024,
                'temperature': 0.7,
            },
            timeout=30,
        )
        r.raise_for_status()
        text = r.json()['choices'][0]['message']['content'].strip()
        return jsonify({'response': text})
    except Exception as exc:
        logger.exception('chat completion error')
        return jsonify({'error': str(exc)}), 500
