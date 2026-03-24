"""
routes/chatgpt_import.py — ChatGPT Conversation Import Blueprint

Endpoints:
  POST /api/import/chatgpt              — Upload and parse a ChatGPT export ZIP
  GET  /api/import/chatgpt/conversations — List all imported conversations
  GET  /api/import/chatgpt/conversation/<id> — Get a single conversation markdown
  GET  /api/import/chatgpt/status        — Get import status/stats
"""

import json
import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

chatgpt_import_bp = Blueprint('chatgpt_import', __name__)

# ---------------------------------------------------------------------------
# Paths — imports dir lives alongside uploads in the runtime directory
# ---------------------------------------------------------------------------

from services.paths import UPLOADS_DIR, WORKSPACE_DIR

# Imported conversations go to workspace/memory/chatgpt-import/
# This makes them accessible to the OpenClaw agent for reference
IMPORT_DIR = WORKSPACE_DIR / 'memory' / 'chatgpt-import'

_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB — exports can be large


@chatgpt_import_bp.route('/api/import/chatgpt', methods=['POST'])
def import_chatgpt():
    """Accept a ChatGPT export ZIP, parse it, store conversations."""
    from services.chatgpt_parser import parse_chatgpt_export

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = Path(f.filename).suffix.lower()
    if ext != '.zip':
        return jsonify({'error': 'Only ZIP files are accepted. Export your data from ChatGPT Settings > Data Controls > Export.'}), 415

    # Size check
    f.stream.seek(0, 2)
    file_size = f.stream.tell()
    f.stream.seek(0)
    if file_size > _MAX_UPLOAD_BYTES:
        return jsonify({'error': 'File too large (500 MB max)'}), 413

    # Save ZIP to uploads temporarily
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    zip_name = f"chatgpt-export-{uuid.uuid4().hex[:8]}.zip"
    zip_path = UPLOADS_DIR / zip_name
    f.save(str(zip_path))

    # Parse and write conversations
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    result = parse_chatgpt_export(zip_path, IMPORT_DIR)

    return jsonify({
        'status': 'complete',
        'total': result['total'],
        'imported': result['imported'],
        'skipped': result['skipped'],
        'errors': result['errors'][:20],  # Cap error messages
        'conversations': result['conversations'],
    })


@chatgpt_import_bp.route('/api/import/chatgpt/conversations', methods=['GET'])
def list_imported_conversations():
    """List all imported ChatGPT conversations from the index."""
    index_path = IMPORT_DIR / 'index.json'
    if not index_path.exists():
        return jsonify({'conversations': [], 'total': 0})

    try:
        index = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return jsonify({'conversations': [], 'total': 0})

    conversations = index.get('conversations', [])

    # Support search query
    q = request.args.get('q', '').strip().lower()
    if q:
        conversations = [
            c for c in conversations
            if q in c.get('title', '').lower()
            or q in ' '.join(c.get('topics', []))
        ]

    # Support topic filter
    topic = request.args.get('topic', '').strip().lower()
    if topic:
        conversations = [
            c for c in conversations
            if topic in [t.lower() for t in c.get('topics', [])]
        ]

    return jsonify({
        'conversations': conversations,
        'total': len(conversations),
        'imported_at': index.get('imported_at'),
    })


@chatgpt_import_bp.route('/api/import/chatgpt/conversation/<path:filename>', methods=['GET'])
def get_imported_conversation(filename):
    """Get the markdown content of a single imported conversation."""
    # Prevent path traversal
    safe_name = Path(filename).name
    file_path = IMPORT_DIR / safe_name

    if not file_path.exists() or not file_path.suffix == '.md':
        return jsonify({'error': 'Conversation not found'}), 404

    try:
        content = file_path.read_text(encoding='utf-8')
    except OSError:
        return jsonify({'error': 'Could not read conversation file'}), 500

    return jsonify({
        'filename': safe_name,
        'content': content,
    })


@chatgpt_import_bp.route('/api/import/chatgpt/status', methods=['GET'])
def import_status():
    """Get current import status and stats."""
    index_path = IMPORT_DIR / 'index.json'
    if not index_path.exists():
        return jsonify({
            'has_import': False,
            'total_conversations': 0,
            'imported_at': None,
        })

    try:
        index = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return jsonify({'has_import': False, 'total_conversations': 0, 'imported_at': None})

    conversations = index.get('conversations', [])

    # Aggregate topic counts
    topic_counts = {}
    total_words = 0
    total_messages = 0
    for c in conversations:
        for t in c.get('topics', []):
            topic_counts[t] = topic_counts.get(t, 0) + 1
        total_words += c.get('word_count', 0)
        total_messages += c.get('message_count', 0)

    return jsonify({
        'has_import': True,
        'total_conversations': len(conversations),
        'imported_at': index.get('imported_at'),
        'total_words': total_words,
        'total_messages': total_messages,
        'topics': dict(sorted(topic_counts.items(), key=lambda x: -x[1])),
    })
