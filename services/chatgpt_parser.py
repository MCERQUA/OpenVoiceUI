"""
ChatGPT Export Parser — Extracts conversations from OpenAI's data export ZIP.

Handles the tree-structured conversations.json format where messages are stored
as a directed graph (parent/children fields) rather than a flat list. Walks from
root to current_node to reconstruct the linear conversation the user actually saw.

Output: per-conversation markdown files + a metadata index JSON.
"""

import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_chatgpt_export(zip_path: Path, output_dir: Path) -> dict:
    """
    Parse a ChatGPT data export ZIP and write individual conversation files.

    Args:
        zip_path: Path to the uploaded ZIP file
        output_dir: Directory to write parsed conversations into

    Returns:
        dict with keys: total, imported, skipped, errors, conversations (list of metadata)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    conversations_json = _extract_conversations_json(zip_path)
    if conversations_json is None:
        return {
            'total': 0, 'imported': 0, 'skipped': 0,
            'errors': ['No conversations.json found in ZIP'],
            'conversations': [],
        }

    try:
        conversations = json.loads(conversations_json)
    except json.JSONDecodeError as e:
        return {
            'total': 0, 'imported': 0, 'skipped': 0,
            'errors': [f'Invalid JSON in conversations.json: {e}'],
            'conversations': [],
        }

    if not isinstance(conversations, list):
        return {
            'total': 0, 'imported': 0, 'skipped': 0,
            'errors': ['conversations.json is not a JSON array'],
            'conversations': [],
        }

    result = {
        'total': len(conversations),
        'imported': 0,
        'skipped': 0,
        'errors': [],
        'conversations': [],
    }

    for conv in conversations:
        try:
            meta = _process_conversation(conv, output_dir)
            if meta:
                result['conversations'].append(meta)
                result['imported'] += 1
            else:
                result['skipped'] += 1
        except Exception as e:
            result['errors'].append(f"Error processing '{conv.get('title', 'unknown')}': {e}")
            result['skipped'] += 1

    # Write the index file
    index_path = output_dir / 'index.json'
    index_data = {
        'source': 'chatgpt-export',
        'imported_at': datetime.now(timezone.utc).isoformat(),
        'total_conversations': result['total'],
        'imported_conversations': result['imported'],
        'conversations': result['conversations'],
    }
    index_path.write_text(json.dumps(index_data, indent=2, default=str))

    logger.info(
        "ChatGPT import complete: %d/%d conversations imported, %d skipped, %d errors",
        result['imported'], result['total'], result['skipped'], len(result['errors']),
    )

    return result


def _extract_conversations_json(zip_path: Path) -> str | None:
    """Extract conversations.json from the ZIP, searching common locations."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if name.endswith('conversations.json'):
                    return zf.read(name).decode('utf-8')
    except zipfile.BadZipFile:
        logger.error("Not a valid ZIP file: %s", zip_path)
    except Exception as e:
        logger.error("Error reading ZIP %s: %s", zip_path, e)
    return None


def _process_conversation(conv: dict, output_dir: Path) -> dict | None:
    """
    Process a single conversation dict. Walks the message tree to reconstruct
    the linear conversation path, writes a markdown file, returns metadata.
    """
    title = conv.get('title', '').strip() or 'Untitled'
    mapping = conv.get('mapping', {})
    current_node = conv.get('current_node')
    create_time = conv.get('create_time')
    update_time = conv.get('update_time')
    conv_id = conv.get('conversation_id', conv.get('id', ''))

    if not mapping:
        return None

    # Walk the tree from current_node back to root, then reverse
    messages = _walk_conversation_tree(mapping, current_node)

    if not messages:
        return None

    # Filter to user/assistant messages with actual content
    meaningful = [m for m in messages if m['role'] in ('user', 'assistant') and m['text'].strip()]

    if not meaningful:
        return None

    # Build markdown
    md_lines = []
    md_lines.append(f"# {title}")
    md_lines.append("")

    if create_time:
        dt = _ts_to_str(create_time)
        md_lines.append(f"**Date:** {dt}")
        md_lines.append("")

    word_count = 0
    for msg in meaningful:
        role_label = "User" if msg['role'] == 'user' else "Assistant"
        md_lines.append(f"## {role_label}")
        md_lines.append("")
        md_lines.append(msg['text'])
        md_lines.append("")
        word_count += len(msg['text'].split())

    markdown = "\n".join(md_lines)

    # Generate safe filename
    slug = _slugify(title)
    date_prefix = ""
    if create_time:
        try:
            dt = datetime.fromtimestamp(create_time, tz=timezone.utc)
            date_prefix = dt.strftime("%Y%m%d-")
        except (OSError, ValueError):
            pass

    filename = f"{date_prefix}{slug}.md"
    # Avoid collisions
    dest = output_dir / filename
    counter = 1
    while dest.exists():
        filename = f"{date_prefix}{slug}-{counter}.md"
        dest = output_dir / filename
        counter += 1

    dest.write_text(markdown, encoding='utf-8')

    # Detect topics from content
    topics = _detect_topics(title, meaningful)

    return {
        'id': conv_id,
        'title': title,
        'filename': filename,
        'message_count': len(meaningful),
        'word_count': word_count,
        'created': _ts_to_str(create_time) if create_time else None,
        'updated': _ts_to_str(update_time) if update_time else None,
        'topics': topics,
        'user_messages': len([m for m in meaningful if m['role'] == 'user']),
        'assistant_messages': len([m for m in meaningful if m['role'] == 'assistant']),
    }


def _walk_conversation_tree(mapping: dict, current_node: str | None) -> list[dict]:
    """
    Walk from current_node back to root via parent pointers, then reverse
    to get chronological order. Returns list of {role, text, timestamp} dicts.
    """
    if not current_node or current_node not in mapping:
        # Fallback: try to find a leaf node
        current_node = _find_leaf_node(mapping)
        if not current_node:
            return []

    # Walk backwards from current_node to root
    path = []
    node_id = current_node
    visited = set()

    while node_id and node_id in mapping and node_id not in visited:
        visited.add(node_id)
        node_data = mapping[node_id]
        msg = node_data.get('message')

        if msg:
            role = msg.get('author', {}).get('role', 'unknown')
            content = msg.get('content', {})
            text = _extract_message_text(content)
            timestamp = msg.get('create_time')

            if text:
                path.append({
                    'role': role,
                    'text': text,
                    'timestamp': timestamp,
                })

        node_id = node_data.get('parent')

    path.reverse()
    return path


def _find_leaf_node(mapping: dict) -> str | None:
    """Find a leaf node (no children) to use as conversation endpoint."""
    all_ids = set(mapping.keys())
    parents = set()
    for node_data in mapping.values():
        parent = node_data.get('parent')
        if parent:
            parents.add(parent)

    leaves = all_ids - parents
    if not leaves:
        # Everything is a parent — just pick the last one by timestamp
        return _find_latest_by_time(mapping)

    # Among leaves, pick the one with the latest timestamp
    best = None
    best_time = 0
    for leaf_id in leaves:
        node = mapping[leaf_id]
        msg = node.get('message', {})
        ts = msg.get('create_time', 0) if msg else 0
        if ts and ts > best_time:
            best_time = ts
            best = leaf_id

    return best or (leaves.pop() if leaves else None)


def _find_latest_by_time(mapping: dict) -> str | None:
    """Fallback: find the node with the latest create_time."""
    best = None
    best_time = 0
    for node_id, node_data in mapping.items():
        msg = node_data.get('message', {})
        ts = msg.get('create_time', 0) if msg else 0
        if ts and ts > best_time:
            best_time = ts
            best = node_id
    return best


def _extract_message_text(content: dict) -> str:
    """Extract readable text from a ChatGPT message content object."""
    if not content:
        return ""

    content_type = content.get('content_type', 'text')
    parts = content.get('parts', [])

    if content_type in ('text', 'multimodal_text'):
        text_parts = []
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                # Image asset pointers, file references, etc.
                alt = part.get('alt_text', '')
                if alt:
                    text_parts.append(f"[Image: {alt}]")
                elif part.get('content_type') == 'image_asset_pointer':
                    text_parts.append("[Image]")
        return "\n".join(text_parts)

    elif content_type == 'code':
        text = content.get('text', '')
        lang = content.get('language', '')
        if text:
            return f"```{lang}\n{text}\n```"

    elif content_type == 'execution_output':
        text = content.get('text', '')
        if text:
            return f"**Output:**\n```\n{text}\n```"

    elif content_type in ('tether_browsing_display', 'tether_quote'):
        result = content.get('result', '') or content.get('text', '')
        title = content.get('title', '')
        url = content.get('url', '')
        pieces = []
        if title:
            pieces.append(f"**{title}**")
        if url:
            pieces.append(f"Source: {url}")
        if result:
            pieces.append(result)
        return "\n".join(pieces)

    # Fallback: try to get any text from parts
    if parts:
        return "\n".join(str(p) for p in parts if isinstance(p, str))

    return ""


def _detect_topics(title: str, messages: list[dict]) -> list[str]:
    """Simple keyword-based topic detection."""
    all_text = (title + " " + " ".join(m['text'] for m in messages[:6])).lower()
    topics = []

    topic_keywords = {
        'website': ['website', 'web design', 'landing page', 'homepage', 'html', 'css'],
        'seo': ['seo', 'search engine', 'keywords', 'ranking', 'backlink', 'meta tag'],
        'marketing': ['marketing', 'campaign', 'ads', 'advertising', 'social media', 'brand'],
        'business': ['business', 'revenue', 'profit', 'pricing', 'invoice', 'client'],
        'coding': ['code', 'python', 'javascript', 'function', 'api', 'debug', 'programming'],
        'design': ['design', 'logo', 'color', 'font', 'layout', 'ui', 'ux'],
        'content': ['blog', 'article', 'writing', 'copy', 'content', 'post'],
        'email': ['email', 'newsletter', 'outreach', 'subject line'],
        'sales': ['sales', 'closing', 'objection', 'proposal', 'lead', 'prospect'],
        'operations': ['schedule', 'workflow', 'process', 'automation', 'system'],
        'legal': ['contract', 'legal', 'compliance', 'license', 'terms'],
        'finance': ['accounting', 'tax', 'budget', 'expense', 'payroll'],
        'hiring': ['hire', 'employee', 'interview', 'job posting', 'recruiting'],
        'ai': ['ai', 'chatgpt', 'prompt', 'machine learning', 'model', 'openai'],
    }

    for topic, keywords in topic_keywords.items():
        if any(kw in all_text for kw in keywords):
            topics.append(topic)

    return topics[:5]  # Cap at 5 topics


def _slugify(text: str) -> str:
    """Convert text to a safe filename slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')
    return text[:60] or 'untitled'


def _ts_to_str(ts) -> str:
    """Convert a Unix timestamp to ISO string."""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, ValueError, TypeError):
        return ""
