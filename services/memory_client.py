"""
Memory Client for OpenVoiceUI

Direct access to clawdbot's memory system:
- SQLite FTS5 full-text search
- Discord session search
- Ambient transcript retrieval
- Combined context compilation

This gives the voice agent the same memory access as the Discord agent.
"""

import os
import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Paths to clawdbot data (configure via env vars or leave empty if not using memory features)
_home = Path.home()
MEMORY_DB = Path(os.getenv('CLAWDBOT_MEMORY_DB', str(_home / '.clawdbot/memory/main.sqlite')))
SESSIONS_DIR = Path(os.getenv('CLAWDBOT_SESSIONS_DIR', str(_home / '.clawdbot/agents/main/sessions/')))
VOICE_EVENTS = Path('/tmp/openvoiceui-events.jsonl')

# Ambient transcripts directory
AMBIENT_DIR = Path(__file__).parent / "ambient_transcripts"


class MemoryClient:
    """Direct access to clawdbot memory and sessions."""

    def __init__(self):
        self.memory_db = str(MEMORY_DB)
        self.sessions_dir = SESSIONS_DIR

    def search_memory(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Search memory chunks using FTS5.

        Args:
            query: Search query (FTS5 syntax supported)
            limit: Max results to return

        Returns:
            List of {text, source, score} dicts
        """
        if not MEMORY_DB.exists():
            logger.warning(f"Memory DB not found: {MEMORY_DB}")
            return []

        try:
            conn = sqlite3.connect(self.memory_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Clean query for FTS5 (remove special chars that break it)
            clean_query = ''.join(c for c in query if c.isalnum() or c.isspace())
            if not clean_query:
                return []

            # FTS5 search - the table has all columns, no join needed
            cursor.execute("""
                SELECT text, path, bm25(chunks_fts) as score
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                ORDER BY bm25(chunks_fts)
                LIMIT ?
            """, (clean_query, limit))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'text': row['text'][:500],  # Truncate long chunks
                    'source': row['path'].split('/')[-1] if row['path'] else 'memory',
                    'score': row['score']
                })

            conn.close()
            logger.info(f"Memory search for '{query}': {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"Memory search error: {e}")
            return []

    def search_sessions(self, query: str, limit: int = 3) -> List[Dict]:
        """
        Search Discord session files for relevant conversations.

        Args:
            query: Search terms
            limit: Max results

        Returns:
            List of {content, role, session} dicts
        """
        if not self.sessions_dir.exists():
            logger.warning(f"Sessions dir not found: {self.sessions_dir}")
            return []

        results = []
        query_lower = query.lower()

        # Get most recent sessions first
        session_files = sorted(
            self.sessions_dir.glob('*.jsonl'),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )[:20]  # Only search last 20 sessions

        for session_file in session_files:
            try:
                with open(session_file, 'r') as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get('type') != 'message':
                                continue

                            msg = entry.get('message', {})
                            content = msg.get('content', '')
                            role = msg.get('role', 'unknown')

                            if query_lower in content.lower():
                                results.append({
                                    'content': content[:400],
                                    'role': role,
                                    'session': session_file.stem[:20]
                                })

                                if len(results) >= limit:
                                    return results

                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.debug(f"Error reading session {session_file}: {e}")
                continue

        logger.info(f"Session search for '{query}': {len(results)} results")
        return results

    def get_recent_ambient(self, user_id: str, minutes: int = 30, max_chars: int = 1500) -> List[Dict]:
        """
        Get recent ambient transcripts for a user.

        These are background audio recordings that were transcribed while the
        user was not actively conversing with the agent.

        Args:
            user_id: Clerk user ID
            minutes: How many minutes back to look (default: 30)
            max_chars: Maximum total characters to return

        Returns:
            List of {transcript, timestamp, has_wake_word} dicts
        """
        if not user_id:
            return []

        user_dir = AMBIENT_DIR / user_id
        if not user_dir.exists():
            return []

        threshold = datetime.now() - timedelta(minutes=minutes)
        entries = []
        total_chars = 0

        # Check today's file and yesterday's
        for days_ago in [0, 1]:
            date = datetime.now() - timedelta(days=days_ago)
            date_str = date.strftime('%Y-%m-%d')
            transcript_file = user_dir / f"{date_str}.jsonl"

            if not transcript_file.exists():
                continue

            try:
                with open(transcript_file, 'r') as f:
                    for line in reversed(list(f)):  # Start from newest
                        try:
                            entry = json.loads(line)

                            # Parse timestamp
                            ts_str = entry.get('timestamp', '')
                            try:
                                # Handle various ISO formats
                                entry_time = datetime.fromisoformat(
                                    ts_str.replace('Z', '+00:00').replace('+00:00', '')
                                )
                                if entry_time < threshold:
                                    continue
                            except ValueError:
                                pass  # Include if we can't parse timestamp

                            text = entry.get('transcript', '')
                            if total_chars + len(text) > max_chars:
                                break

                            entries.append({
                                'transcript': text,
                                'timestamp': entry.get('timestamp', ''),
                                'has_wake_word': entry.get('has_wake_word', False),
                                'duration_seconds': entry.get('duration_seconds', 0)
                            })
                            total_chars += len(text)

                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.debug(f"Error reading ambient transcripts: {e}")

        # Reverse to chronological order
        entries.reverse()
        logger.info(f"Retrieved {len(entries)} ambient transcripts for user {user_id}")
        return entries

    def search_voice_transcripts(self, query: str, limit: int = 3) -> List[Dict]:
        """
        Search past voice conversations from events file.

        Args:
            query: Search terms
            limit: Max results

        Returns:
            List of {content, role, time} dicts
        """
        if not VOICE_EVENTS.exists():
            return []

        results = []
        query_lower = query.lower()

        try:
            with open(VOICE_EVENTS, 'r') as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if event.get('type') != 'conversation':
                            continue

                        message = event.get('message', '')
                        if query_lower in message.lower():
                            results.append({
                                'content': message[:300],
                                'role': event.get('role', 'unknown'),
                                'time': event.get('timestamp', 'unknown')
                            })

                            if len(results) >= limit:
                                break
                    except:
                        continue
        except Exception as e:
            logger.error(f"Voice transcript search error: {e}")

        return results

    def get_full_context(self, user_message: str, user_id: str = None) -> Dict:
        """
        Get combined context from all sources.

        Args:
            user_message: What user just said
            user_id: Optional Clerk user ID for ambient transcripts

        Returns:
            Dict with memory, sessions, transcripts, and ambient context
        """
        # Extract key terms from message
        key_terms = self._extract_keywords(user_message)
        search_query = ' '.join(key_terms[:5])

        context = {
            'query': search_query,
            'memory': self.search_memory(search_query, limit=5),
            'sessions': self.search_sessions(search_query, limit=3),
            'voice_transcripts': self.search_voice_transcripts(search_query, limit=2),
            'generated_at': datetime.now().isoformat()
        }

        # Add ambient transcripts if user_id provided
        if user_id:
            context['ambient'] = self.get_recent_ambient(user_id, minutes=30, max_chars=1000)

        return context

    def format_context_for_prompt(self, context: Dict, max_tokens: int = 1500) -> str:
        """
        Format context for injection into system prompt.

        Args:
            context: Context dict from get_full_context()
            max_tokens: Approximate max tokens (chars / 4)

        Returns:
            Formatted string for prompt injection
        """
        parts = ["\n--- RELEVANT CONTEXT ---"]

        char_limit = max_tokens * 4  # Rough chars estimate
        current_chars = 0

        # Add memory results
        if context.get('memory'):
            parts.append("\nFrom memory:")
            for item in context['memory'][:3]:
                text = f"\n- [{item['source']}] {item['text'][:200]}"
                if current_chars + len(text) > char_limit:
                    break
                parts.append(text)
                current_chars += len(text)

        # Add session results
        if context.get('sessions'):
            parts.append("\nFrom Discord conversations:")
            for item in context['sessions'][:2]:
                text = f"\n- {item['role']}: {item['content'][:150]}"
                if current_chars + len(text) > char_limit:
                    break
                parts.append(text)
                current_chars += len(text)

        # Add voice transcripts
        if context.get('voice_transcripts'):
            parts.append("\nFrom past voice calls:")
            for item in context['voice_transcripts'][:2]:
                text = f"\n- {item['role']}: {item['content'][:100]}"
                if current_chars + len(text) > char_limit:
                    break
                parts.append(text)
                current_chars += len(text)

        # Add ambient transcripts (background audio context)
        # This is special context about what was heard around the user
        if context.get('ambient'):
            ambient_intro = """
--- SURROUNDING AUDIO CONTEXT ---
You have ears that can hear the sounds and audio around you when the user has this feature enabled.
This is what you recently heard in the background. Determine if this requires a response or is just
background noise (TV, music, other people talking, etc). Use this to understand the user's context
and make relevant comments when they wake you up. Be playful and observant!
"""
            parts.append(ambient_intro)

            for item in context['ambient'][:5]:
                # Format timestamp for readability
                ts = item.get('timestamp', '')
                if 'T' in ts:
                    ts = ts.split('T')[1][:8]  # Just the time HH:MM:SS

                text = f"\n[{ts}] \"{item.get('transcript', '')[:200]}\""
                if item.get('has_wake_word'):
                    text += " [WAKE WORD DETECTED]"

                if current_chars + len(text) > char_limit:
                    break
                parts.append(text)
                current_chars += len(text)

            parts.append("\n--- END SURROUNDING AUDIO ---")

        parts.append("\n--- END CONTEXT ---")

        return '\n'.join(parts) if current_chars > 0 else ""

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords from text."""
        # Stop words to ignore
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'must', 'can',
            'what', 'which', 'who', 'whom', 'when', 'where', 'why', 'how',
            'this', 'that', 'these', 'those', 'i', 'me', 'my', 'we', 'our',
            'you', 'your', 'he', 'him', 'his', 'she', 'her', 'it', 'its',
            'they', 'them', 'their', 'and', 'or', 'but', 'if', 'then',
            'else', 'so', 'for', 'with', 'about', 'into', 'to', 'from',
            'up', 'down', 'in', 'out', 'on', 'off', 'over', 'under',
            'again', 'further', 'once', 'here', 'there', 'all', 'each',
            'few', 'more', 'most', 'other', 'some', 'such', 'only', 'just'
        }

        # Extract words
        words = []
        for word in text.lower().split():
            # Clean word
            clean = ''.join(c for c in word if c.isalnum())
            # Keep if long enough and not stop word
            if len(clean) > 3 and clean not in stop_words:
                words.append(clean)

        # Return unique words, preserving order
        seen = set()
        keywords = []
        for w in words:
            if w not in seen:
                seen.add(w)
                keywords.append(w)

        return keywords[:10]


# Singleton instance
_client = None

def get_memory_client() -> MemoryClient:
    """Get or create memory client instance."""
    global _client
    if _client is None:
        _client = MemoryClient()
    return _client


# Convenience functions for direct import
def search_memory(query: str, limit: int = 5) -> List[Dict]:
    return get_memory_client().search_memory(query, limit)

def search_sessions(query: str, limit: int = 3) -> List[Dict]:
    return get_memory_client().search_sessions(query, limit)

def get_full_context(user_message: str, user_id: str = None) -> Dict:
    return get_memory_client().get_full_context(user_message, user_id=user_id)

def format_context_for_prompt(context: Dict, max_tokens: int = 1500) -> str:
    return get_memory_client().format_context_for_prompt(context, max_tokens)


if __name__ == '__main__':
    # Test the client
    logging.basicConfig(level=logging.INFO)

    client = get_memory_client()

    # Test memory search
    print("Testing memory search...")
    results = client.search_memory("steve call project")
    for r in results:
        print(f"  [{r['source']}] {r['text'][:100]}...")

    # Test session search
    print("\nTesting session search...")
    results = client.search_sessions("discord bot")
    for r in results:
        print(f"  {r['role']}: {r['content'][:100]}...")

    # Test full context
    print("\nTesting full context...")
    context = client.get_full_context("what did we talk about yesterday")
    print(client.format_context_for_prompt(context))
