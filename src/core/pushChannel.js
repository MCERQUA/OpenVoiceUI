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
// it" and the server still relays the result through the tenant's own agent.
//
// Nothing here touches browser storage. All dependencies are passed in so node can
// test it (tests/js/push-channel.test.js).

const MIN_DELAY_MS = 2000;
const MAX_DELAY_MS = 60000;

export class PushChannel {
    constructor({
        url,
        WebSocketImpl,
        onPush,
        beforeConnect = null,
        isVisible = () => true,
        setTimer = (fn, ms) => setTimeout(fn, ms),
        log = () => {},
    }) {
        this.url = url;
        this.WebSocketImpl = WebSocketImpl;
        this.onPush = onPush;
        this.beforeConnect = beforeConnect;
        this.isVisible = isVisible;
        this.setTimer = setTimer;
        this.log = log;
        this.ws = null;
        this.connected = false;
        this._connecting = false;
        this._stopped = true;
        this._delay = MIN_DELAY_MS;
        this._retryPending = false;
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
        if (ws) {
            try { ws.close(); } catch (e) { /* already closed */ }
        }
    }

    // Call on document visibilitychange.
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
            this._delay = MIN_DELAY_MS;
            this.log('push channel connected');
            this.reportVisibility();
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
            this._scheduleRetry();
        };
    }

    _scheduleRetry() {
        if (this._stopped || this._retryPending) return;
        const delay = this._delay;
        this._delay = Math.min(delay * 2, MAX_DELAY_MS);
        this._retryPending = true;
        this.log(`push channel reconnecting in ${delay / 1000}s`);
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
