/**
 * tool-bridge.js — Voice Agent Tool Bus, client half.
 *
 * Spec: docs/jambot/voice-agent-tool-bus.md
 * Framework: future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md
 *
 * Three jobs:
 *
 *   1. Fetch the capability-filtered tool catalog so an adapter can hand
 *      `realtimeTools()` to a realtime model in session.update.
 *   2. Execute a tool call. Client-side tools become AgentEvents on the
 *      EventBridge (the same events ClawdBotAdapter emits, so every existing
 *      shell bridge handles them unchanged). Server-side tools go to
 *      POST /api/tools/invoke.
 *   3. Listen on the /api/tools/events SSE stream and turn a finished async job
 *      into AgentActions.FORCE_MESSAGE — which every adapter already implements
 *      — so the live voice agent SPEAKS the conclusion.
 *
 * Job 3 is the whole point. A voice agent that dispatches a four-minute build
 * must not block; it says "on it", keeps talking, and gets interrupted with the
 * answer when it lands.
 *
 * Usage (from an adapter's init):
 *   import { toolBridge } from '../shell/tool-bridge.js';
 *   await toolBridge.init(bridge, { profileId: 'xai-realtime', sessionId });
 *   const tools = toolBridge.realtimeTools();
 *   ...
 *   const result = await toolBridge.invoke(name, args);
 */

import { AgentEvents, AgentActions } from '../core/EventBridge.js';

// ─────────────────────────────────────────────────────────────────────────────
// Client-side tool handlers
// ─────────────────────────────────────────────────────────────────────────────
// Each returns the object handed back to the model as the function result.
// Keep the returns terse and factual — the model reads them out loud.
//
// These deliberately emit the SAME AgentEvents that ClawdBotAdapter emits from
// its [MARKER] parser, so canvas-bridge / music-bridge / sounds-bridge need no
// changes and both agent classes drive identical UI behaviour.

const CLIENT_HANDLERS = {
    open_canvas_page: (bridge, p) => {
        const page = (p.page || '').trim();
        if (!page) return { ok: false, error: 'No page name given.' };
        bridge.emit(AgentEvents.CANVAS_CMD, { action: 'present', url: page });
        return { ok: true, result: `Displaying "${page}".` };
    },

    open_canvas_menu: (bridge) => {
        bridge.emit(AgentEvents.CANVAS_CMD, { action: 'menu' });
        return { ok: true, result: 'Page picker opened.' };
    },

    screenshot_canvas: (bridge) => {
        bridge.emit(AgentEvents.CANVAS_CMD, { action: 'screenshot' });
        return { ok: true, result: 'Screenshot requested.' };
    },

    play_music: (bridge, p) => {
        const track = (p.track || '').trim() || null;
        bridge.emit(AgentEvents.MUSIC_PLAY, { action: 'play', track });
        return { ok: true, result: track ? `Playing "${track}".` : 'Music playing.' };
    },

    stop_music: (bridge) => {
        bridge.emit(AgentEvents.MUSIC_PLAY, { action: 'stop' });
        return { ok: true, result: 'Music stopped.' };
    },

    next_track: (bridge) => {
        bridge.emit(AgentEvents.MUSIC_PLAY, { action: 'skip' });
        return { ok: true, result: 'Skipped.' };
    },

    play_spotify: (bridge, p) => {
        const track = (p.track || '').trim();
        if (!track) return { ok: false, error: 'No track given.' };
        bridge.emit(AgentEvents.MUSIC_PLAY, {
            action: 'play', track, artist: (p.artist || '').trim(), source: 'spotify',
        });
        return { ok: true, result: `Playing "${track}" on Spotify.` };
    },

    play_sound: (bridge, p) => {
        const sound = (p.sound || '').trim();
        if (!sound) return { ok: false, error: 'No sound given.' };
        bridge.emit(AgentEvents.PLAY_SOUND, { sound, type: 'dj' });
        return { ok: true, result: `Played ${sound}.` };
    },

    register_face: (bridge, p) => {
        const name = (p.name || '').trim();
        if (!name) return { ok: false, error: 'No name given.' };
        bridge.emit(AgentEvents.TOOL_CALLED, { name: 'register_face', params: { name }, result: null });
        return { ok: true, result: `Registering the visible face as ${name}.` };
    },

    sleep: (bridge) => {
        bridge.emit(AgentActions.END_SESSION, {});
        return { ok: true, result: 'Going to sleep.' };
    },
};


// ─────────────────────────────────────────────────────────────────────────────
// ToolBridge
// ─────────────────────────────────────────────────────────────────────────────

class ToolBridge {
    constructor() {
        this._bridge = null;
        this._profileId = null;
        this._sessionId = null;
        this._catalog = null;
        this._eventSource = null;
        this._destroyed = false;
        this._reconnectTimer = null;
        this._reconnectDelay = 1000;
        // Highest job updated_at we have already surfaced. Handed back on
        // reconnect as ?since= so a completion that lands during the gap is
        // replayed rather than lost.
        this._watermark = 0;
        this._pendingJobs = new Map();   // job_id -> tool name, for nicer phrasing
    }

    // -- lifecycle ----------------------------------------------------------

    async init(bridge, { profileId, sessionId } = {}) {
        this._bridge = bridge;
        this._profileId = profileId || null;
        this._sessionId = sessionId || null;
        this._destroyed = false;

        await this._loadCatalog();
        this._openEventStream();

        const n = this._catalog?.realtime_tools?.length || 0;
        console.log(`[ToolBridge] Ready — ${n} tool(s) granted to profile "${this._profileId}"`);
    }

    destroy() {
        this._destroyed = true;
        clearTimeout(this._reconnectTimer);
        this._closeEventStream();
        this._catalog = null;
        this._pendingJobs.clear();
    }

    // -- catalog ------------------------------------------------------------

    async _loadCatalog() {
        try {
            const qs = this._profileId ? `?profile=${encodeURIComponent(this._profileId)}` : '';
            const res = await fetch(`/api/tools/catalog${qs}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this._catalog = await res.json();
        } catch (err) {
            // Fail CLOSED. An empty catalog means the model is told it has no
            // tools — strictly better than guessing a default set it may not be
            // authorised for.
            console.error('[ToolBridge] Catalog load failed — no tools will be offered:', err);
            this._catalog = { realtime_tools: [], routing: {}, markers: [] };
        }
    }

    /** tools[] array for a realtime session.update. */
    realtimeTools() {
        return this._catalog?.realtime_tools || [];
    }

    /** Routing metadata for one tool, or null if it isn't granted. */
    routing(name) {
        return this._catalog?.routing?.[name] || null;
    }

    hasTools() {
        return this.realtimeTools().length > 0;
    }

    // -- invocation ---------------------------------------------------------

    /**
     * Execute a tool call.
     * @param {string} name
     * @param {object|string} args - parsed object, or the raw JSON string a
     *   realtime API sends in function_call_arguments.
     * @returns {Promise<object>} result object to hand back to the model
     */
    async invoke(name, args) {
        let params = args;
        if (typeof args === 'string') {
            try {
                params = args.trim() ? JSON.parse(args) : {};
            } catch (err) {
                return { ok: false, error: `Could not parse arguments: ${err.message}` };
            }
        }
        params = params || {};

        const route = this.routing(name);
        if (!route) {
            // Either hallucinated, or real but not granted. Same answer either
            // way — do not leak that a gated tool exists.
            console.warn(`[ToolBridge] Rejected ungranted tool: ${name}`);
            return { ok: false, error: `Tool "${name}" is not available.` };
        }

        this._bridge?.emit(AgentEvents.TOOL_CALLED, { name, params, result: null });

        if (route.execution === 'client') {
            const handler = CLIENT_HANDLERS[name];
            if (!handler) {
                console.error(`[ToolBridge] Catalog lists client tool "${name}" with no handler`);
                return { ok: false, error: `Tool "${name}" is not wired up.` };
            }
            try {
                return handler(this._bridge, params);
            } catch (err) {
                console.error(`[ToolBridge] Client handler "${name}" threw:`, err);
                return { ok: false, error: `That failed: ${err.message}` };
            }
        }

        return this._invokeServer(name, params);
    }

    async _invokeServer(name, params) {
        try {
            const res = await fetch('/api/tools/invoke', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tool: name,
                    params,
                    profile: this._profileId,
                    session_id: this._sessionId,
                }),
            });
            const data = await res.json().catch(() => ({}));

            if (!res.ok) {
                return { ok: false, error: data.error || `Server returned ${res.status}.` };
            }

            if (data.status === 'dispatched') {
                this._pendingJobs.set(data.job_id, name);
                return {
                    ok: true,
                    status: 'dispatched',
                    job_id: data.job_id,
                    result: data.message,
                };
            }

            return { ok: true, result: data.result };

        } catch (err) {
            console.error(`[ToolBridge] Server tool "${name}" failed:`, err);
            return { ok: false, error: `Could not reach the server: ${err.message}` };
        }
    }

    // -- server → agent push -------------------------------------------------

    _openEventStream() {
        if (this._destroyed) return;
        this._closeEventStream();

        const params = new URLSearchParams();
        if (this._sessionId) params.set('session_id', this._sessionId);
        if (this._watermark) params.set('since', String(this._watermark));

        const url = `/api/tools/events?${params.toString()}`;

        try {
            this._eventSource = new EventSource(url);
        } catch (err) {
            console.error('[ToolBridge] Could not open event stream:', err);
            this._scheduleReconnect();
            return;
        }

        this._eventSource.onopen = () => {
            this._reconnectDelay = 1000;
            console.log('[ToolBridge] Job event stream connected');
        };

        this._eventSource.onmessage = (evt) => this._onServerEvent(evt);

        this._eventSource.onerror = () => {
            // EventSource retries on its own, but we close and reopen so the
            // ?since= watermark advances — its native retry would replay from
            // the original URL and re-announce jobs the agent already spoke.
            if (this._destroyed) return;
            console.warn('[ToolBridge] Job event stream dropped — reconnecting');
            this._closeEventStream();
            this._scheduleReconnect();
        };
    }

    _scheduleReconnect() {
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => this._openEventStream(), this._reconnectDelay);
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, 30000);
    }

    _closeEventStream() {
        if (this._eventSource) {
            try { this._eventSource.close(); } catch (_) {}
            this._eventSource = null;
        }
    }

    _onServerEvent(evt) {
        let msg;
        try {
            msg = JSON.parse(evt.data);
        } catch (_) {
            return;
        }

        if (msg.at && msg.at > this._watermark) this._watermark = msg.at;

        switch (msg.type) {
            case 'connected':
                break;

            case 'reconnect':
                this._closeEventStream();
                this._openEventStream();
                break;

            case 'job.done':
            case 'job.failed':
                this._announceJob(msg);
                break;

            default:
                break;
        }
    }

    /**
     * Interrupt the live conversation with a finished job.
     *
     * FORCE_MESSAGE (not CONTEXT_UPDATE) is deliberate: the agent PROMISED the
     * user an answer, so it must speak — a silent context inject would leave the
     * promise unfulfilled from the user's point of view. Every adapter
     * implements FORCE_MESSAGE (xai-realtime.js:195, hume-evi.js:119,
     * elevenlabs-*.js, ClawdBotAdapter.js:87).
     */
    _announceJob(msg) {
        const tool = msg.tool || this._pendingJobs.get(msg.job_id) || 'task';
        this._pendingJobs.delete(msg.job_id);

        this._bridge?.emit(AgentEvents.TOOL_CALLED, {
            name: tool,
            params: { job_id: msg.job_id },
            result: msg.summary,
        });

        const artifacts = (msg.artifacts || []).length
            ? ` Files touched: ${msg.artifacts.join(', ')}.`
            : '';

        const text = msg.ok
            ? `[TOOL RESULT] The "${tool}" job you started has finished. ` +
              `Result: ${msg.summary}${artifacts} ` +
              `Tell the user this now, in one short spoken sentence.`
            : `[TOOL RESULT] The "${tool}" job you started FAILED. ` +
              `Reason: ${msg.summary} ` +
              `Tell the user briefly and offer to try again.`;

        console.log(`[ToolBridge] Job ${msg.job_id} ${msg.ok ? 'done' : 'failed'} — announcing`);
        this._bridge?.emit(AgentActions.FORCE_MESSAGE, { text });
    }
}

// Singleton — one bus per page, like every other shell bridge.
export const toolBridge = new ToolBridge();
export { ToolBridge, CLIENT_HANDLERS };
