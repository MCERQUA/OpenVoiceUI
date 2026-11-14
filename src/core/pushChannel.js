// pushChannel.js — the browser's always-on proactive-push socket (/ws/push).
//
// When a background job finishes after the voice turn that started it, the server
// pushes the result to every open app (server.py push_proactive_message). That push
// used to ride only on /ws/clawdbot, which the app opens only when the user SWITCHES
// the voice transport to supertonic. Supertonic is already the default, so the switch
// never ran and no browser ever connected: every push fleet-wide logged "no connected
// browsers" and results showed up only at the next voice turn (2026-11-14).
//
// This socket is opened once at app start, whatever the voice mode, and comes back by
// itself after server restarts and network drops. It tells the server whether the tab
// is visible, so a tab left open in the background does not count as "the owner saw
// it" and the server still relays the result through the tenant's own agent. It
// re-reports every VISIBILITY_REPORT_MS while connected: the server ignores a report
// older than about 60 s, so a laptop that went to sleep with the tab in front stops
// counting even while its TCP socket still looks open.
//
// Reconnects back off exponentially with jitter, capped at MAX_DELAY_MS. The backoff
// resets only after a connection has stayed up STABLE_MS: a server that accepts and
// then drops at once (a restart loop, an expired session) must not be hammered every
// MIN_DELAY_MS by every open tab at the same moment.
//
// Nothing here touches browser storage. All dependencies are passed in so node can
// test it (tests/js/push-channel.test.js).

const MIN_DELAY_MS = 2000;
const MAX_DELAY_MS = 60000;
const STABLE_MS = 30000;
const VISIBILITY_REPORT_MS = 20000;

export class PushChannel {
    constructor({
        url,
        WebSocketImpl,
        onPush,
        beforeConnect = null,
        isVisible = () => true,
        setTimer = (fn, ms) => setTimeout(fn, ms),
        // Returns a cancel function. Separate from setTimer so the repeating
        // visibility report never competes with a reconnect.
        setRepeat = (fn, ms) => { const id = setInterval(fn, ms); return () => clearInterval(id); },
        now = () => Date.now(),
        random = () => Math.random(),
        log = () => {},
    }) {
        this.url = url;
        this.WebSocketImpl = WebSocketImpl;
        this.onPush = onPush;
        this.beforeConnect = beforeConnect;
        this.isVisible = isVisible;
        this.setTimer = setTimer;
        this.setRepeat = setRepeat;
        this.now = now;
        this.random = random;
        this.log = log;
        this.ws = null;
        this.connected = false;
        this._connecting = false;
        this._stopped = true;
        this._delay = MIN_DELAY_MS;
        this._retryPending = false;
        this._openedAt = null;
        this._cancelReport = null;
    }

    start() {
        if (!this._stopped) return;
        this._stopped = false;
        this._connect();
    }

    stop() {
        this._stopped = true;
        const ws = this.ws;
        this.ws = null;
        this.connected = false;
        this._stopReporting();
        if (ws) {
            try { ws.close(); } catch (e) { /* already closed */ }
        }
    }

    // Call on document visibilitychange; also runs every VISIBILITY_REPORT_MS.
    reportVisibility() {
        if (!this.connected || !this.ws) return;
        try {
            this.ws.send(JSON.stringify({ type: 'visibility', visible: !!this.isVisible() }));
        } catch (e) { /* socket closing; onclose reconnects */ }
    }

    async _connect() {
        if (this._stopped || this._connecting || this.ws) return;
        this._connecting = true;
        // Refresh the session cookie first: a WebSocket cannot send a Bearer header,
        // so the cookie is the only credential, and after a sleep it may hold an
        // expired token.
        if (this.beforeConnect) {
            try { await this.beforeConnect(); } catch (e) { /* connect anyway */ }
        }
        if (this._stopped) { this._connecting = false; return; }
        let ws;
        try {
            ws = new this.WebSocketImpl(this.url);
        } catch (e) {
            this._connecting = false;
            this._scheduleRetry();
            return;
        }
        this.ws = ws;
        this._connecting = false;
        ws.onopen = () => {
            if (this.ws !== ws) return;
            this.connected = true;
            this._openedAt = this.now();
            this.log('push channel connected');
            this.reportVisibility();
            this._stopReporting();
            this._cancelReport = this.setRepeat(() => this.reportVisibility(), VISIBILITY_REPORT_MS);
        };
        ws.onmessage = (event) => {
            if (this.ws !== ws) return;
            let data;
            try { data = JSON.parse(event.data); } catch (e) { return; }
            if (data && data.type === 'proactive_message') this.onPush(data);
        };
        ws.onerror = () => { /* onclose follows */ };
        ws.onclose = () => {
            if (this.ws !== ws) return;
            this.ws = null;
            this.connected = false;
            this._stopReporting();
            // Only a connection that held for STABLE_MS earns a fresh, short backoff.
            if (this._openedAt !== null && this.now() - this._openedAt >= STABLE_MS) {
                this._delay = MIN_DELAY_MS;
            }
            this._openedAt = null;
            this._scheduleRetry();
        };
    }

    _stopReporting() {
        const cancel = this._cancelReport;
        this._cancelReport = null;
        if (cancel) {
            try { cancel(); } catch (e) { /* already cancelled */ }
        }
    }

    _scheduleRetry() {
        if (this._stopped || this._retryPending) return;
        const base = this._delay;
        this._delay = Math.min(base * 2, MAX_DELAY_MS);
        // Equal jitter: somewhere in [base/2, base], so tabs dropped together by one
        // server restart do not all come back in the same instant. Never above the cap.
        const delay = Math.min(MAX_DELAY_MS, Math.round(base / 2 + this.random() * (base / 2)));
        this._retryPending = true;
        this.log(`push channel reconnecting in ${(delay / 1000).toFixed(1)}s`);
        this.setTimer(() => {
            this._retryPending = false;
            this._connect();
        }, delay);
    }
}

// One push frame can reach a tab twice (on /ws/push and on /ws/clawdbot when that
// socket is also open). Returns true the first time an id is seen. Frames with no
// id (older servers) are always new. Memory only, bounded.
export function makeProactiveDeduper(max = 100) {
    const seen = [];
    return function isNew(id) {
        if (!id) return true;
        if (seen.includes(id)) return false;
        seen.push(id);
        if (seen.length > max) seen.shift();
        return true;
    };
}

// Wake word during push audio outside a call. A push can speak while no call is
// open, and the passive WakeWordDetector is then the only thing listening: it could
// hear the agent say its own wake phrase. Normal TTS never meets this, because a call
// start stops the detector first. So for push audio we do the same thing the call
// start does, with the existing instance only (.stop() now, .start() after) — never
// destroying or recreating a SpeechRecognition (jambot-voice-flow). Its abort/restart
// loop while listening is normal and is left alone.
//
// resume() restarts the detector ONLY if this guard stopped it and it is still
// wanted: no call has started (a call takes the pause over via takeOver()), push-to-
// talk is not on (PTT keeps the detector off, so the pause is handed to PTT to
// restore), the user did not toggle wake word meanwhile (takeOver()), and it is not
// already listening. A hard cap restarts it even if playback never reported its end.
export class PushWakeGuard {
    constructor({
        getDetector,
        inCall = () => false,
        pttActive = () => false,
        onPttTakeover = () => {},
        setTimer = (fn, ms) => setTimeout(fn, ms),
        clearTimer = (id) => clearTimeout(id),
        maxPauseMs = 5 * 60 * 1000,
        log = () => {},
    }) {
        this.getDetector = getDetector;
        this.inCall = inCall;
        this.pttActive = pttActive;
        this.onPttTakeover = onPttTakeover;
        this.setTimer = setTimer;
        this.clearTimer = clearTimer;
        this.maxPauseMs = maxPauseMs;
        this.log = log;
        this.paused = false;
        this._cap = null;
    }

    // Before push audio plays. True when the detector is (now) paused for it.
    pause() {
        if (this.paused) return true;
        if (this.inCall()) return false;
        const wd = this.getDetector();
        if (!wd || !wd.isListening) return false;
        wd.stop();
        this.paused = true;
        this.log('wake word paused for push audio');
        this._cap = this.setTimer(() => { this._cap = null; this.resume(); }, this.maxPauseMs);
        return true;
    }

    // A call or the user has taken over the detector. Returns whether it was paused.
    takeOver() {
        const was = this.paused;
        this._clear();
        return was;
    }

    // After push audio ended, failed or was stopped. Safe to call any number of times.
    resume() {
        if (!this.paused) return;
        this._clear();
        if (this.inCall()) return;
        if (this.pttActive()) { this.onPttTakeover(); return; }
        const wd = this.getDetector();
        if (!wd || wd.isListening || (wd.isSupported && !wd.isSupported())) return;
        try {
            const r = wd.start();
            if (r && typeof r.catch === 'function') r.catch(() => {});
            this.log('wake word restored after push audio');
        } catch (e) { /* already started */ }
    }

    _clear() {
        this.paused = false;
        if (this._cap !== null) {
            try { this.clearTimer(this._cap); } catch (e) { /* gone */ }
            this._cap = null;
        }
    }
}
