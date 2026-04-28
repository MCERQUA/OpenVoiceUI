/**
 * airadio-bridge.js — maps [AIRADIO_*] voice tags → /api/airadio/* endpoints.
 *
 * Exposes window.airadioDispatch(verb, data). app.js calls this for each
 * unique [AIRADIO_VERB:data] tag it finds in agent replies. The bridge is
 * async but fire-and-forget from app.js's perspective (never throws; UI
 * must keep running if AI-Radio is down).
 *
 * When an endpoint returns data worth surfacing to the agent on the next
 * turn (search results, library counts, friend-inbox, queue reasons, error
 * codes) this module pushes a short summary into the ActionConsole so the
 * transcript picks it up.
 *
 * Backend: routes/airadio_bridge.py (Flask blueprint).
 */

// --------------------------------------------------------------------------
// Low-level helpers
// --------------------------------------------------------------------------

function _post(url, body) {
    return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    })
    .then(r => r.json().catch(() => null).then(j => ({ status: r.status, body: j })))
    .catch(e => {
        console.warn('[airadio] POST', url, 'failed:', e?.message || e);
        return { status: 0, body: null };
    });
}

function _get(url, params) {
    let full = url;
    if (params && typeof params === 'object') {
        const qs = Object.entries(params)
            .filter(([, v]) => v !== undefined && v !== null && v !== '')
            .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
            .join('&');
        if (qs) full = `${url}?${qs}`;
    }
    return fetch(full, { method: 'GET' })
        .then(r => r.json().catch(() => null).then(j => ({ status: r.status, body: j })))
        .catch(e => {
            console.warn('[airadio] GET', full, 'failed:', e?.message || e);
            return { status: 0, body: null };
        });
}

function _splitPipe(data) {
    if (!data) return [];
    return data.split('|').map(s => s.trim());
}

// Push a status line into the transcript/action console so the agent
// receives the result on its next turn.
function _log(level, text) {
    try {
        // ActionConsole is attached to window by app.js
        const Con = (typeof window !== 'undefined' && window.ActionConsole) || null;
        if (Con && typeof Con.addEntry === 'function') {
            Con.addEntry(level || 'system', `AI-Radio: ${text}`);
            return;
        }
    } catch (_) { /* ignore */ }
    // Fallback — console only
    console.log('[airadio]', text);
}

function _logResult(verb, res, onOk) {
    const ok = res && res.body && res.body.ok !== false && res.status >= 200 && res.status < 400;
    if (!ok) {
        const err = (res && res.body && res.body.error) || null;
        const code = (err && err.code) || `HTTP_${res?.status || 0}`;
        const msg = (err && err.message) || 'request failed';
        _log('error', `${verb} → ${code}: ${msg}`);
        return false;
    }
    try { if (typeof onOk === 'function') onOk(res.body); } catch (_) { /* ignore */ }
    // Surface public-mode note once per dispatch so the agent can tell the user
    // "to upload your own songs, you'll need to connect an AI-Radio account."
    // The bridge tags mode="public" when it fell back to /public/* due to no key.
    if (res?.body?.mode === 'public' && !_publicModeNoticed) {
        _publicModeNoticed = true;
        _log('system', 'running in public mode — push/vote/friend/save require an AI-Radio account (generate aia_sk_* at ai-radio.jam-bot.com/settings)');
    }
    return true;
}

// Only log the public-mode notice once per page load, not on every call.
let _publicModeNoticed = false;

// --------------------------------------------------------------------------
// Per-tag handlers — each returns the fetch promise so callers can await
// if needed, but app.js uses fire-and-forget.
// --------------------------------------------------------------------------

function _handlePushSong(d) {
    return _post('/api/airadio/push-song', { filename: d, title: d })
        .then(r => _logResult('PUSH_SONG', r, body => {
            _log('system', `pushed "${d}" (id: ${body.songId || body.data?.songId || '—'})`);
        }));
}

function _handlePushPlaylist(d) {
    return _post('/api/airadio/push-playlist', { name: d })
        .then(r => _logResult('PUSH_PLAYLIST', r, body => {
            const pushed = body.pushed_count ?? (body.pushed?.length || 0);
            const failed = body.failed_count ?? (body.failed?.length || 0);
            _log('system', `playlist "${d}" → ${pushed} pushed, ${failed} failed`);
        }));
}

function _handlePlay(kind, query, extra) {
    const body = { type: kind === 'playlist' ? 'playlist' : 'song', query };
    if (extra && extra.id) body.id = extra.id;
    return _post('/api/airadio/play', body)
        .then(r => _logResult(`PLAY_${kind.toUpperCase()}`, r, b => {
            const t = b.title || b.data?.title || query;
            const by = b.artist ? ` by ${b.artist}` : '';
            _log('system', `playing ${kind} "${t}"${by}`);
            // If the response carried a streamUrl, hand it to the music player
            const url = b.url || b.data?.url;
            if (url) _playRemote(url, { title: t, artist: b.artist || b.data?.artist });
        }));
}

function _handlePlayFromCatalog(d) {
    return _post('/api/airadio/play', { type: 'song', query: d })
        .then(r => _logResult('PLAY_FROM_CATALOG', r, b => {
            const t = b.title || d;
            _log('system', `playing "${t}" from catalog`);
            const url = b.url || b.data?.url;
            if (url) _playRemote(url, { title: t, artist: b.artist || b.data?.artist });
        }));
}

function _handlePlayFriendSong(d) {
    const [friend, song] = _splitPipe(d);
    return _post('/api/airadio/play', { type: 'song', query: song, friend })
        .then(r => _logResult('PLAY_FRIEND_SONG', r, b => {
            _log('system', `playing ${friend}'s "${song}"`);
        }));
}

function _handleSetImage(target, d) {
    // SET_SONG_COVER / SET_PLAYLIST_COVER carry pipe-separated <target>|<path>;
    // SET_AVATAR / SET_BANNER carry just the path.
    let target_id = '', path = d;
    if (target === 'song_cover' || target === 'playlist_cover') {
        const parts = _splitPipe(d);
        target_id = parts[0] || '';
        path = parts[1] || '';
    }
    return _post('/api/airadio/set-image', { target, target_id, path })
        .then(r => _logResult(`SET_${target.toUpperCase()}`, r, () => {
            _log('system', `updated ${target}${target_id ? ` for ${target_id}` : ''}`);
        }));
}

function _handleSendToFriend(d) {
    const parts = _splitPipe(d);
    const song = parts[0] || '';
    const friend = parts[1] || '';
    const note = parts[2] || '';
    return _post('/api/airadio/send-to-friend', {
        song_title: song,
        receiver_handle: friend,
        note,
    })
    .then(r => _logResult('SEND_TO_FRIEND', r, () => {
        _log('system', `sent "${song}" to ${friend}`);
    }));
}

function _handleReplyToSend(d) {
    const [sendId, song, note] = _splitPipe(d);
    return _post('/api/airadio/reply-to-send', {
        send_id: sendId,
        song_title: song,
        note: note || '',
    })
    .then(r => _logResult('REPLY_TO_SEND', r, () => {
        _log('system', `replied with "${song}"`);
    }));
}

function _handleVote(d) {
    const [song, direction] = _splitPipe(d);
    const dir = (direction || '').toLowerCase();
    const value = dir === 'up' ? 1 : dir === 'down' ? -1 : 0;
    return _post('/api/airadio/vote', { song_title: song, value })
        .then(r => _logResult('VOTE', r, () => {
            _log('system', `vote ${dir} on "${song}"`);
        }));
}

function _handleFriend(action, d) {
    return _post(`/api/airadio/friend-${action}`, { handle: d })
        .then(r => _logResult(`FRIEND_${action.toUpperCase()}`, r, () => {
            _log('system', `friend ${action}: ${d}`);
        }));
}

function _handleCatalogSearch(d) {
    return _get('/api/airadio/catalog', { q: d, limit: 10 })
        .then(r => _logResult('CATALOG_SEARCH', r, body => {
            const items = (body && body.data && body.data.items) || body.items || [];
            if (!items.length) {
                _log('system', `catalog search "${d}" — no matches`);
                return;
            }
            const top = items.slice(0, 3)
                .map(it => `${it.title}${it.artist ? ` — @${it.artist}` : ''}`)
                .join('; ');
            _log('system', `catalog "${d}" (${items.length}): ${top}`);
        }));
}

function _handleCheckInLibrary(d) {
    return _get('/api/airadio/library/search', { q: d, limit: 5 })
        .then(r => _logResult('CHECK_IN_LIBRARY', r, body => {
            if (body.exists) {
                const top = (body.matches || []).slice(0, 3)
                    .map(m => m.title).filter(Boolean).join(', ');
                _log('system',
                    `"${d}" ALREADY in library${body.exact ? ' (exact match)' : ''}` +
                    (top ? ` → ${top}` : ''));
            } else {
                _log('system', `"${d}" NOT in library — safe to push`);
            }
        }));
}

function _handleSaveToLibrary(d) {
    // Accept either an id or a title — try as id first, fall back to title.
    const isId = /^[a-z0-9_-]{8,}$/i.test(d) && !/\s/.test(d);
    const body = isId ? { song_id: d } : { song_id: d };
    return _post('/api/airadio/library/save', body)
        .then(r => _logResult('SAVE_TO_LIBRARY', r, () => {
            _log('system', `saved "${d}" to library`);
        }));
}

function _handlePlaylistCreate(d) {
    const [name, description] = _splitPipe(d);
    return _post('/api/airadio/playlist', { name, description: description || '' })
        .then(r => _logResult('PLAYLIST_CREATE', r, body => {
            const id = body.data?.id || body.data?.playlistId || body.id || '';
            _log('system', `playlist "${name}" created${id ? ` (id: ${id})` : ''}`);
        }));
}

function _handlePlaylistRead(d) {
    return _get(`/api/airadio/playlist/${encodeURIComponent(d)}`, null)
        .then(r => _logResult('PLAYLIST_READ', r, body => {
            const songs = body.data?.songs || body.songs || [];
            _log('system', `playlist "${d}" has ${songs.length} song(s)`);
        }));
}

function _handleInbox() {
    return _get('/api/airadio/inbox', null)
        .then(r => _logResult('INBOX', r, body => {
            const items = body.data?.items || body.items || [];
            const unread = items.filter(x => !x.readAt && !x.read).length;
            _log('system', `inbox: ${items.length} sends (${unread} unread)`);
        }));
}

function _handleLibrary() {
    return _get('/api/airadio/library', null)
        .then(r => _logResult('LIBRARY', r, body => {
            const data = body.data || {};
            const songs = data.songs || data.items || [];
            const playlists = data.playlists || [];
            _log('system', `library: ${songs.length} songs, ${playlists.length} playlists`);
        }));
}

function _handleSetUserKey(d) {
    return _post('/api/airadio/set-user-key', { key: d })
        .then(r => _logResult('SET_USER_KEY', r, body => {
            _log('system',
                body.restartRequired
                    ? 'user key saved — RESTART workspace to activate'
                    : 'user key saved'
            );
        }));
}

// --------------------------------------------------------------------------
// Queue handlers — all return { items, reason } with signed streamUrls.
// We log the reason (agent should speak it verbatim) and enqueue items
// into the music player if available.
// --------------------------------------------------------------------------

function _playRemote(url, metadata) {
    try {
        const mp = window.musicPlayer;
        if (!mp) return false;
        if (typeof mp.playRemote === 'function') {
            mp.playRemote(url, metadata || {});
            return true;
        }
        if (typeof mp.playAudioUrl === 'function') {
            mp.playAudioUrl(url, metadata || {});
            return true;
        }
        // Fallback: audio element attached to document so it doesn't GC
        let el = window.__airadio_audio;
        if (!el) {
            el = document.createElement('audio');
            el.id = '__airadio_audio';
            el.preload = 'auto';
            el.controls = false;
            el.style.display = 'none';
            document.body.appendChild(el);
            window.__airadio_audio = el;
        }
        el.src = url;
        el.play().catch(e => console.warn('[airadio] audio play rejected:', e?.message));
        return true;
    } catch (e) {
        console.warn('[airadio] playRemote failed:', e?.message || e);
        return false;
    }
}

function _queueItems(items, reason) {
    if (!Array.isArray(items) || !items.length) return;
    try {
        const mp = window.musicPlayer;
        if (mp && typeof mp.queueAiRadio === 'function') {
            mp.queueAiRadio(items, reason || '');
            return;
        }
        if (mp && typeof mp.queueRemoteTracks === 'function') {
            mp.queueRemoteTracks(items);
            return;
        }
        // No queue interface — start the first, let the player manage the rest manually.
        const first = items[0];
        if (first && first.streamUrl) {
            _playRemote(first.streamUrl, { title: first.title, artist: first.artist });
        }
    } catch (e) {
        console.warn('[airadio] queueItems failed:', e?.message || e);
    }
}

function _handleQueue(endpoint, params, data) {
    return _get(`/api/airadio/queue/${endpoint}`, params || null)
        .then(r => _logResult(`QUEUE_${endpoint.toUpperCase()}`, r, body => {
            const payload = body.data || body;
            const items = payload.items || [];
            const reason = payload.reason || '';
            _log('system', `${items.length} song(s) queued${reason ? ` — ${reason}` : ''}${data ? ` [${data}]` : ''}`);
            _queueItems(items, reason);
        }));
}

function _handleQueueGeneric(d) {
    return _get('/api/airadio/queue', { q: d, limit: 20 })
        .then(r => _logResult('QUEUE', r, body => {
            const payload = body.data || body;
            const items = payload.items || [];
            const reason = payload.reason || '';
            _log('system', `${items.length} song(s) queued${reason ? ` — ${reason}` : ''}`);
            _queueItems(items, reason);
        }));
}

// --------------------------------------------------------------------------
// Dispatch table
// --------------------------------------------------------------------------

function _dispatch(verb, data) {
    const v = (verb || '').toUpperCase();
    const d = (data || '').toString();
    try {
        switch (v) {
            // Push (local → AI-Radio)
            case 'PUSH_SONG':           return _handlePushSong(d);
            case 'PUSH_PLAYLIST':       return _handlePushPlaylist(d);

            // Pull (AI-Radio → OVU player)
            case 'PLAY_SONG':           return _handlePlay('song', d);
            case 'PLAY_PLAYLIST':       return _handlePlay('playlist', d);
            case 'PLAY_FROM_CATALOG':   return _handlePlayFromCatalog(d);
            case 'PLAY_FRIEND_SONG':    return _handlePlayFriendSong(d);

            // Images
            case 'SET_AVATAR':          return _handleSetImage('avatar', d);
            case 'SET_BANNER':          return _handleSetImage('banner', d);
            case 'SET_SONG_COVER':      return _handleSetImage('song_cover', d);
            case 'SET_PLAYLIST_COVER':  return _handleSetImage('playlist_cover', d);

            // Social
            case 'SEND_TO_FRIEND':      return _handleSendToFriend(d);
            case 'REPLY_TO_SEND':       return _handleReplyToSend(d);
            case 'VOTE':                return _handleVote(d);
            case 'FRIEND_REQUEST':      return _handleFriend('request', d);
            case 'FRIEND_ACCEPT':       return _handleFriend('accept', d);
            case 'FRIEND_DECLINE':      return _handleFriend('decline', d);

            // Catalog / library
            case 'CATALOG_SEARCH':      return _handleCatalogSearch(d);
            case 'CHECK_IN_LIBRARY':    return _handleCheckInLibrary(d);
            case 'SAVE_TO_LIBRARY':     return _handleSaveToLibrary(d);
            case 'LIBRARY':             return _handleLibrary();
            case 'INBOX':               return _handleInbox();

            // Playlists
            case 'PLAYLIST_CREATE':     return _handlePlaylistCreate(d);
            case 'PLAYLIST_READ':       return _handlePlaylistRead(d);

            // Config
            case 'SET_USER_KEY':        return _handleSetUserKey(d);

            // Queue / radio
            case 'QUEUE_NEW':           return _handleQueue('new', null, d);
            case 'QUEUE_TRENDING':      return _handleQueue('trending', null, d);
            case 'QUEUE_TOP':           return _handleQueue('top', d ? { window: d } : null, d);
            case 'QUEUE_RANDOM':        return _handleQueue('random', null, d);
            case 'QUEUE_FRIENDS':       return _handleQueue('friends', null, d);
            case 'QUEUE_FOLLOWING':     return _handleQueue('following', null, d);
            case 'QUEUE_ME':            return _handleQueue('me', null, d);
            case 'QUEUE_LIKED':         return _handleQueue('liked', null, d);
            case 'QUEUE_UNHEARD':       return _handleQueue('unheard', null, d);
            case 'QUEUE_GENRE':         return _handleQueue(`genre/${encodeURIComponent(d)}`, null, d);
            case 'QUEUE_MOOD':          return _handleQueue(`mood/${encodeURIComponent(d)}`, null, d);
            case 'QUEUE_ARTIST':        return _handleQueue(`artist/${encodeURIComponent(d.replace(/^@/, ''))}`, null, d);
            case 'QUEUE_SIMILAR':       return _handleQueue(`similar/${encodeURIComponent(d)}`, null, d);
            case 'QUEUE':               return _handleQueueGeneric(d);

            default:
                console.warn('[airadio] unknown verb:', v, d);
                _log('warning', `unknown tag AIRADIO_${v}`);
                return null;
        }
    } catch (e) {
        console.warn('[airadio] dispatch error:', e?.message || e);
        _log('error', `dispatch error: ${e?.message || e}`);
        return null;
    }
}

export function connectAiradio() {
    if (window.airadioDispatch) return;
    window.airadioDispatch = _dispatch;
    console.log('[airadio] bridge ready');
}
