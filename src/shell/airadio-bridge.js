/**
 * airadio-bridge.js — Route [AIRADIO_*] voice tags to /api/airadio/* endpoints.
 *
 * VoiceSession emits a generic `cmd:airadio` event with `{ verb, data }`
 * whenever it parses an [AIRADIO_<VERB>(?::data)?] tag in the stream.
 * This module is the only place that knows how each verb maps to an
 * HTTP endpoint + payload shape. Flask bridge does the AI-Radio auth.
 *
 * All calls are same-origin (cookies ride along), so the Clerk session
 * authenticates the request on the server side. The Flask bridge then
 * resolves the per-user aia_sk_ (future: identity map) or falls back
 * to the container's AIRADIO_USER_KEY / AIRADIO_AGENT_KEY env.
 *
 * Subscribes ONCE at module load — safe across mode switches because
 * the orchestrator only clears bridge handlers, not eventBus handlers.
 */

import { eventBus } from '../core/EventBus.js';

const LOG_PREFIX = '[airadio]';

/** Fire-and-forget POST helper — logs, doesn't throw. */
async function _post(endpoint, body = {}) {
    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        let payload = null;
        try { payload = await res.json(); } catch { /* non-JSON */ }
        if (!res.ok || (payload && payload.ok === false)) {
            console.warn(`${LOG_PREFIX} ${endpoint} failed:`, res.status, payload);
            return { ok: false, status: res.status, payload };
        }
        console.log(`${LOG_PREFIX} ${endpoint} ok`, payload);
        return { ok: true, status: res.status, payload };
    } catch (err) {
        console.error(`${LOG_PREFIX} ${endpoint} network error:`, err);
        return { ok: false, error: String(err) };
    }
}

/** GET helper (for queue/inbox/library/resolve endpoints). */
async function _get(endpoint) {
    try {
        const res = await fetch(endpoint, { credentials: 'same-origin' });
        let payload = null;
        try { payload = await res.json(); } catch { /* non-JSON */ }
        if (!res.ok || (payload && payload.ok === false)) {
            console.warn(`${LOG_PREFIX} ${endpoint} failed:`, res.status, payload);
            return { ok: false, status: res.status, payload };
        }
        console.log(`${LOG_PREFIX} ${endpoint} ok`);
        return { ok: true, status: res.status, payload };
    } catch (err) {
        console.error(`${LOG_PREFIX} ${endpoint} network error:`, err);
        return { ok: false, error: String(err) };
    }
}

/**
 * Parse `a|b|c` style payloads into parts, trimming each.
 */
function _pipeParts(raw, count) {
    if (!raw) return [];
    const parts = String(raw).split('|').map(s => s.trim());
    while (parts.length < count) parts.push('');
    return parts;
}

/**
 * Dispatch one AIRADIO verb+data to the right endpoint.
 * Returns the fetch result (async) — callers don't await.
 */
function _dispatch({ verb, data }) {
    const V = (verb || '').toUpperCase();

    switch (V) {
        // ── PUSH (local → AI-Radio) ──────────────────────────────────────────
        case 'PUSH_SONG':
            // Data is the song title (or filename). Server resolves.
            return _post('/api/airadio/push-song', { title: data, filename: data });

        case 'PUSH_PLAYLIST':
            return _post('/api/airadio/push-playlist', { name: data });

        // ── CATALOG (platform-wide browse) ───────────────────────────────────
        case 'CATALOG_SEARCH':
            return _get(`/api/airadio/catalog?q=${encodeURIComponent(data || '')}`);

        case 'PLAY_FROM_CATALOG':
            // data = song title or id
            return _post('/api/airadio/play', { source: 'catalog', query: data });

        case 'SAVE_TO_LIBRARY':
            return _post('/api/airadio/library/save', { query: data });

        // ── PLAYLISTS ────────────────────────────────────────────────────────
        case 'PLAYLIST_CREATE': {
            const [name, description] = _pipeParts(data, 2);
            return _post('/api/airadio/playlist', { name, description });
        }

        case 'PLAYLIST_READ':
            return _get(`/api/airadio/playlist/${encodeURIComponent(data)}`);

        // ── PLAY (on AI-Radio side) ──────────────────────────────────────────
        case 'PLAY_SONG':
            return _post('/api/airadio/play', { source: 'mine', query: data });

        case 'PLAY_PLAYLIST':
            return _post('/api/airadio/play', { source: 'playlist', query: data });

        case 'PLAY_FRIEND_SONG': {
            const [handle, title] = _pipeParts(data, 2);
            return _post('/api/airadio/play', { source: 'friend', handle, query: title });
        }

        // ── IMAGES / PROFILE ─────────────────────────────────────────────────
        case 'SET_AVATAR':
            return _post('/api/airadio/set-image', { target: 'avatar', path: data });

        case 'SET_BANNER':
            return _post('/api/airadio/set-image', { target: 'banner', path: data });

        case 'SET_SONG_COVER': {
            const [title, path] = _pipeParts(data, 2);
            return _post('/api/airadio/set-image', { target: 'song', title, path });
        }

        case 'SET_PLAYLIST_COVER': {
            const [name, path] = _pipeParts(data, 2);
            return _post('/api/airadio/set-image', { target: 'playlist', name, path });
        }

        // ── FRIENDS ──────────────────────────────────────────────────────────
        case 'FRIEND_REQUEST':
            return _post('/api/airadio/friend-request', { handle: data });

        case 'FRIEND_ACCEPT':
            return _post('/api/airadio/friend-accept', { handle: data });

        case 'FRIEND_DECLINE':
            return _post('/api/airadio/friend-decline', { handle: data });

        case 'SEND_TO_FRIEND': {
            const [song, handle, note] = _pipeParts(data, 3);
            return _post('/api/airadio/send-to-friend', { song, handle, note });
        }

        case 'REPLY_TO_SEND': {
            const [sendId, song, note] = _pipeParts(data, 3);
            return _post('/api/airadio/send-to-friend', { reply_to: sendId, song, note });
        }

        // ── VOTE ─────────────────────────────────────────────────────────────
        case 'VOTE': {
            const [song, vote] = _pipeParts(data, 2);
            return _post('/api/airadio/vote', { song, vote: (vote || 'up').toLowerCase() });
        }

        // ── QUEUE (GET endpoints) ────────────────────────────────────────────
        case 'QUEUE_NEW':       return _get('/api/airadio/queue/new');
        case 'QUEUE_TRENDING':  return _get('/api/airadio/queue/trending');
        case 'QUEUE_TOP':
            // data may be "day" | "week" | "all" — empty = default
            return _get(`/api/airadio/queue/top${data ? `?window=${encodeURIComponent(data)}` : ''}`);
        case 'QUEUE_RANDOM':    return _get('/api/airadio/queue/random');
        case 'QUEUE_FRIENDS':   return _get('/api/airadio/queue/friends');
        case 'QUEUE_FOLLOWING': return _get('/api/airadio/queue/following');
        case 'QUEUE_ME':        return _get('/api/airadio/queue/me');
        case 'QUEUE_LIKED':     return _get('/api/airadio/queue/liked');
        case 'QUEUE_UNHEARD':   return _get('/api/airadio/queue/unheard');

        case 'QUEUE_GENRE':
            return _get(`/api/airadio/queue/genre/${encodeURIComponent(data)}`);
        case 'QUEUE_MOOD':
            return _get(`/api/airadio/queue/mood/${encodeURIComponent(data)}`);
        case 'QUEUE_ARTIST':
            return _get(`/api/airadio/queue/artist/${encodeURIComponent((data || '').replace(/^@/, ''))}`);
        case 'QUEUE_SIMILAR':
            return _get(`/api/airadio/queue/similar/${encodeURIComponent(data)}`);

        case 'QUEUE':
            // Free-form natural-language queue — server interprets.
            return _post('/api/airadio/interpret', { query: data });

        default:
            console.warn(`${LOG_PREFIX} unhandled verb: ${V} (data=${data})`);
            return Promise.resolve({ ok: false, error: 'UNHANDLED_VERB' });
    }
}

let _wired = false;

/**
 * Install the cmd:airadio listener + expose a window-level dispatcher.
 * Idempotent. Called once during app bootstrap.
 *
 * `window.airadioDispatch(verb, data)` is the imperative entry point used
 * by app.js's inline action-tag parsers (which don't import eventBus).
 */
export function connectAiradio() {
    if (_wired) return;
    _wired = true;

    const handler = ({ verb, data }) => {
        if (!verb) return;
        console.log(`${LOG_PREFIX} dispatch verb=${verb} data=${data || ''}`);
        // Fire and forget — agent already spoke a confirmation by the time
        // we get here. Surface failures in the console for debugging.
        _dispatch({ verb, data }).catch(err => {
            console.error(`${LOG_PREFIX} dispatch error`, err);
        });
    };

    eventBus.on('cmd:airadio', handler);
    window.airadioDispatch = (verb, data) => handler({ verb, data });

    console.log(`${LOG_PREFIX} wired — cmd:airadio listener + window.airadioDispatch`);
}

export default { connectAiradio };
