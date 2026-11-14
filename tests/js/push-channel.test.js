// push-channel.test.js — the app must open the proactive-push socket at start, in
// every voice mode, and keep it open.
//
//   node tests/js/push-channel.test.js
//
// 2026-11-14: no browser on any tenant had ever connected to the push channel. It
// rode only on /ws/clawdbot, opened only by ModeManager.switchMode(), and init()
// skipped switchMode() because the default transport ('supertonic') was already the
// current one. Every background-job result logged "no connected browsers".
//
// Part 1 checks app.js wiring (init() starts a PushChannel on /ws/push, outside
// switchMode). Part 2 runs src/core/pushChannel.js with a fake WebSocket and timers.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..');
const APP = process.env.APP_SRC || path.join(ROOT, 'src', 'app.js');
const MOD = process.env.PUSH_CHANNEL_SRC || path.join(ROOT, 'src', 'core', 'pushChannel.js');

let failures = 0;
function test(name, fn) {
    return Promise.resolve().then(fn).then(
        () => console.log(`ok   ${name}`),
        (e) => { failures++; console.log(`FAIL ${name}\n     ${e.message}`); });
}

// ---- Part 1: app.js wiring --------------------------------------------------
function initBody(src) {
    const start = src.indexOf('async function init()');
    assert.ok(start >= 0, 'app.js has no init()');
    return src.slice(start, start + 20000);
}

function switchModeBody(src) {
    const start = src.indexOf('async switchMode(mode)');
    assert.ok(start >= 0, 'app.js has no ModeManager.switchMode()');
    const end = src.indexOf('getCurrentMode()', start);
    return src.slice(start, end);
}

// ---- Part 2: the module ------------------------------------------------------
function loadModule() {
    assert.ok(fs.existsSync(MOD), `missing ${path.relative(ROOT, MOD)}`);
    const src = fs.readFileSync(MOD, 'utf8').replace(/^export\s+/gm, '');
    const ctx = { JSON, Math, console, setTimeout };
    vm.createContext(ctx);
    vm.runInContext(src + '\nthis.PushChannel = PushChannel; this.makeProactiveDeduper = makeProactiveDeduper;'
        + '\nif (typeof PushWakeGuard !== "undefined") this.PushWakeGuard = PushWakeGuard;', ctx);
    return ctx;
}

class FakeWS {
    constructor(url) { this.url = url; this.sent = []; this.closed = false; FakeWS.all.push(this); }
    send(s) { this.sent.push(JSON.parse(s)); }
    close() { this.closed = true; }
    open() { this.onopen && this.onopen(); }
    msg(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }); }
    drop() { this.onclose && this.onclose(); }
}
FakeWS.all = [];

const flush = () => new Promise((r) => setImmediate(r));

function harness(m, extra = {}) {
    FakeWS.all = [];
    const timers = [];
    const pushes = [];
    const order = [];
    const repeats = [];
    const clock = { t: 0 };
    let visible = true;
    const ch = new m.PushChannel({
        url: 'wss://tenant.example.com/ws/push',
        WebSocketImpl: function (url) { order.push('ws'); return new FakeWS(url); },
        onPush: (d) => pushes.push(d),
        beforeConnect: async () => { order.push('cookie'); },
        isVisible: () => visible,
        setTimer: (fn, ms) => timers.push({ fn, ms }),
        setRepeat: (fn, ms) => {
            const r = { fn, ms, cancelled: false };
            repeats.push(r);
            return () => { r.cancelled = true; };
        },
        now: () => clock.t,
        random: () => 1,  // no jitter unless a test asks for it
        ...extra,
    });
    return { ch, timers, pushes, order, repeats, clock, setVisible: (v) => { visible = v; } };
}

(async () => {
    const appSrc = fs.readFileSync(APP, 'utf8');

    await test('init() starts a PushChannel on /ws/push', () => {
        const body = initBody(appSrc);
        assert.match(body, /new PushChannel\(/, 'init() never creates the push channel');
        assert.match(body, /\/ws\/push/, 'push channel must use /ws/push');
        assert.match(body, /pushChannel\.start\(\)/, 'init() never starts the push channel');
        assert.match(body, /visibilitychange/, 'tab visibility must be reported');
    });

    await test('the push channel does not depend on a voice-mode switch', () => {
        assert.doesNotMatch(switchModeBody(appSrc), /PushChannel|pushChannel/);
        assert.match(appSrc, /import \{[^}]*PushChannel[^}]*\} from '\.\/core\/pushChannel\.js'/);
    });

    await test('a frame on both sockets is handled once', () => {
        assert.match(appSrc, /case 'proactive_message':[\s\S]{0,300}isNewProactivePush\(data\.id\)/);
    });

    // Review item 5: push audio now plays OUTSIDE a call. It must take the same
    // path as normal TTS, whose onSpeaking mutes the shared STT and raises
    // _ttsPlaying (STT results are dropped while it is up, then for the 2.5 s echo
    // cooldown), so the agent cannot hear itself. Nothing here may create or
    // destroy a SpeechRecognition. (The passive wake-word detector has no pause
    // hook at all; normal TTS only ever plays after a call start has stopped it.)
    await test('push audio plays through the normal TTS path that mutes STT', () => {
        const body = initBody(appSrc);
        assert.match(body, /onPush:\s*\(data\)\s*=>\s*ModeManager\.clawdbotMode\?\.handleMessage\(data\)/,
            'pushes must reach ClawdbotMode.handleMessage');
        const h = appSrc.indexOf('async _handleProactiveMessage(data)');
        assert.ok(h >= 0, 'no _handleProactiveMessage');
        const handler = appSrc.slice(h, appSrc.indexOf('async sendMessage(', h));
        assert.match(handler, /this\.playAudio\(data\.audio/, 'push audio must use ClawdbotMode.playAudio');
        assert.doesNotMatch(handler, /new Audio\(|SpeechRecognition|\.recognition\b/,
            'push audio must not bypass the TTS queue or touch recognition');
        const pn = appSrc.indexOf('async playNextAudio()', appSrc.indexOf('playAudio(base64Audio, format = \'wav\')'));
        assert.match(appSrc.slice(pn, pn + 4000), /this\.callbacks\.onSpeaking\(\)/,
            'ClawdbotMode playback must fire onSpeaking');
        const cb = appSrc.indexOf('this.clawdbotMode.setCallbacks({');
        const speaking = appSrc.slice(cb, appSrc.indexOf('onListening:', cb));
        assert.match(speaking, /stt\.mute\(\)/, 'onSpeaking must mute the shared STT');
        assert.match(speaking, /_ttsPlaying = true/, 'onSpeaking must raise _ttsPlaying');
    });

    // Review follow-up: outside a call the wake-word detector is the only listener
    // while push audio plays. The app pauses it with the EXISTING instance's
    // .stop()/.start() (the same thing a call start does) and restores it from
    // try/finally paths, so a failed play cannot leave wake word off.
    await test('push audio pauses wake word and every end path restores it', () => {
        const h = appSrc.indexOf('async _handleProactiveMessage(data)');
        const handler = appSrc.slice(h, appSrc.indexOf('async sendMessage(', h));
        assert.match(handler, /_pushWakeGuard\(\)\.pause\(\)[\s\S]*try \{[\s\S]*this\.playAudio\(data\.audio[\s\S]*\} finally \{[\s\S]*_pushWake\.resume\(\)/,
            'push playback must pause wake word and restore it in a finally');
        const g = handler.slice(handler.indexOf('_pushWakeGuard() {'));
        assert.match(g, /new PushWakeGuard\(/);
        assert.match(g, /getDetector:\s*\(\)\s*=>\s*window\.wakeDetector/, 'must reuse the existing detector');
        assert.match(g, /inCall:[^\n]*_voiceActive/);
        assert.match(g, /pttActive:[^\n]*pttMode/);
        const pn = appSrc.indexOf('async playNextAudio()', appSrc.indexOf("playAudio(base64Audio, format = 'wav')"));
        assert.match(appSrc.slice(pn, pn + 5000), /Truly done[\s\S]{0,300}try \{[\s\S]{0,200}onListening\(\)[\s\S]{0,200}\} finally \{[\s\S]{0,240}_pushWake\?\.resume\(\)/,
            'the drained queue must restore wake word even if onListening throws');
        const sa = appSrc.indexOf('stopAudio() {', pn);
        assert.match(appSrc.slice(sa, appSrc.indexOf('base64ToBlob(', sa)), /_pushWake\?\.resume\(\)/,
            'stopAudio must restore wake word (HTMLAudio never fires onended after pause)');
        const sv = appSrc.indexOf('// Stop wake word detector before starting STT');
        assert.match(appSrc.slice(sv, sv + 600), /_pushWake\?\.takeOver\(\)\)\s*\{[\s\S]{0,120}restartWakeAfter = true/,
            'a call started during push audio must take over the restart');
        const tw = appSrc.indexOf('window.toggleWakeWord = async function()');
        assert.match(appSrc.slice(tw, tw + 700), /_pushWake\?\.takeOver\(\)[\s\S]{0,80}wakeDetector\.toggle\(\)/,
            "the user's own toggle must win over the pause");
        // A Hume EVI call (or a VoiceConversation call) is a call too: without this,
        // a Hume call started during push audio got wake word restarted mid-call.
        const inCallLine = g.slice(g.indexOf('inCall:'), g.indexOf('pttActive:'));
        assert.match(inCallLine, /humeAdapter\?\.isConnected/, 'an open Hume call must count as in-call');
        assert.match(inCallLine, /humeAdapter\?\.isConnecting/, 'a connecting Hume call must count as in-call');
        assert.match(inCallLine, /_voiceConversation\?\.isConnected/);
        for (const marker of ['// Pause wake word detector during Hume conversation',
                              '// Pause wake word detector during conversation']) {
            const at = appSrc.indexOf(marker);
            assert.ok(at >= 0, `missing: ${marker}`);
            assert.match(appSrc.slice(at, at + 600), /_pushWake\?\.takeOver\(\)\)\s*\{[\s\S]{0,120}restartWakeAfter = true/,
                `${marker}: a call started during push audio must take over the restart`);
        }
        assert.strictEqual((appSrc.match(/new (?:\w*)WakeWordDetector\(/g) || []).length, 4,
            'no new wake-word detector may be constructed');
        assert.doesNotMatch(appSrc, /new\s+(?:window\.)?(?:webkit)?SpeechRecognition\(/);
    });

    await test('no browser storage in the push channel', () => {
        assert.ok(fs.existsSync(MOD), `missing ${path.relative(ROOT, MOD)}`);
        assert.doesNotMatch(fs.readFileSync(MOD, 'utf8'), /localStorage|sessionStorage|indexedDB/);
    });

    let m;
    try { m = loadModule(); } catch (e) {
        failures++; console.log(`FAIL load src/core/pushChannel.js\n     ${e.message}`);
    }
    if (m) {
        await test('start() refreshes the cookie, then connects to /ws/push', async () => {
            const h = harness(m);
            h.ch.start();
            await flush();
            assert.deepStrictEqual(h.order, ['cookie', 'ws']);
            assert.strictEqual(FakeWS.all[0].url, 'wss://tenant.example.com/ws/push');
        });

        await test('on open it reports visibility; visibility changes are reported', async () => {
            const h = harness(m);
            h.ch.start(); await flush();
            const ws = FakeWS.all[0];
            ws.open();
            assert.deepStrictEqual(ws.sent, [{ type: 'visibility', visible: true }]);
            h.setVisible(false);
            h.ch.reportVisibility();
            assert.deepStrictEqual(ws.sent[1], { type: 'visibility', visible: false });
        });

        await test('only proactive_message frames reach onPush', async () => {
            const h = harness(m);
            h.ch.start(); await flush();
            const ws = FakeWS.all[0];
            ws.open();
            ws.msg({ type: 'push_ready' });
            ws.msg({ type: 'keepalive' });
            ws.msg({ type: 'proactive_message', text: 'Done.', id: 'a1' });
            ws.onmessage({ data: 'not json' });
            assert.strictEqual(h.pushes.length, 1);
            assert.strictEqual(h.pushes[0].text, 'Done.');
        });

        await test('a dropped socket reconnects with capped backoff, reset on success', async () => {
            const h = harness(m);
            h.ch.start(); await flush();
            const delays = [];
            for (let i = 0; i < 7; i++) {
                FakeWS.all[FakeWS.all.length - 1].drop();
                const t = h.timers.shift();
                assert.ok(t, `no reconnect scheduled after drop ${i + 1}`);
                delays.push(t.ms);
                t.fn(); await flush();
            }
            assert.deepStrictEqual(delays, [2000, 4000, 8000, 16000, 32000, 60000, 60000]);
            assert.strictEqual(FakeWS.all.length, 8, 'one new socket per retry');
            FakeWS.all[7].open();
            h.clock.t += 30000;
            FakeWS.all[7].drop();
            assert.strictEqual(h.timers.shift().ms, 2000, 'backoff resets after a connection held 30 s');
        });

        // Review fix 4: onopen used to reset the backoff, so a server that accepts
        // and drops at once (restart loop, expired session) got hit every 2 s.
        await test('a connection that drops before 30 s does not reset the backoff', async () => {
            const h = harness(m);
            h.ch.start(); await flush();
            const delays = [];
            for (let i = 0; i < 5; i++) {
                const ws = FakeWS.all[FakeWS.all.length - 1];
                ws.open();
                h.clock.t += 29999;
                ws.drop();
                const t = h.timers.shift();
                delays.push(t.ms);
                t.fn(); await flush();
            }
            assert.deepStrictEqual(delays, [2000, 4000, 8000, 16000, 32000]);
        });

        await test('reconnect delays carry jitter and never pass the 60 s cap', async () => {
            let r = 0;
            const h = harness(m, { random: () => r });
            h.ch.start(); await flush();
            FakeWS.all[0].drop();
            assert.strictEqual(h.timers.shift().ms, 1000, 'random=0 waits half the base delay');
            r = 0.5;
            h.ch._retryPending = false;
            h.ch._scheduleRetry();
            assert.strictEqual(h.timers.shift().ms, 3000, 'random=0.5 lands between base/2 and base');
            for (let i = 0; i < 10; i++) {
                r = Math.random();
                h.ch._retryPending = false;
                h.ch._scheduleRetry();
                const ms = h.timers.shift().ms;
                assert.ok(ms <= 60000, `delay ${ms} is over the cap`);
                assert.ok(ms >= 1000, `delay ${ms} is under the floor`);
            }
            r = 1;
            h.ch._retryPending = false;
            h.ch._scheduleRetry();
            assert.strictEqual(h.timers.shift().ms, 60000);
        });

        // Review fix 2: the server ignores a visibility report older than ~60 s, so
        // the tab must keep reporting while it is connected.
        await test('visibility is re-reported every 20 s while connected, and stops on close', async () => {
            const h = harness(m);
            h.ch.start(); await flush();
            const ws = FakeWS.all[0];
            ws.open();
            assert.strictEqual(h.repeats.length, 1, 'no repeating visibility report');
            assert.strictEqual(h.repeats[0].ms, 20000);
            h.repeats[0].fn();
            h.setVisible(false);
            h.repeats[0].fn();
            assert.deepStrictEqual(ws.sent, [
                { type: 'visibility', visible: true },
                { type: 'visibility', visible: true },
                { type: 'visibility', visible: false },
            ]);
            ws.drop();
            assert.ok(h.repeats[0].cancelled, 'the report must stop when the socket closes');
            h.timers.shift().fn(); await flush();
            FakeWS.all[1].open();
            assert.strictEqual(h.repeats.length, 2);
            assert.ok(!h.repeats[1].cancelled);
            h.ch.stop();
            assert.ok(h.repeats[1].cancelled, 'stop() must end the report');
        });

        await test('the cookie is refreshed before every reconnect', async () => {
            const h = harness(m);
            h.ch.start(); await flush();
            FakeWS.all[0].drop();
            h.timers.shift().fn(); await flush();
            assert.deepStrictEqual(h.order, ['cookie', 'ws', 'cookie', 'ws']);
        });

        await test('a failing cookie refresh does not stop the connect', async () => {
            const h = harness(m, { beforeConnect: async () => { throw new Error('signed out'); } });
            h.ch.start(); await flush();
            assert.strictEqual(FakeWS.all.length, 1);
        });

        await test('stop() closes and never reconnects', async () => {
            const h = harness(m);
            h.ch.start(); await flush();
            const ws = FakeWS.all[0];
            ws.open();
            h.ch.stop();
            assert.ok(ws.closed);
            ws.drop();
            assert.strictEqual(h.timers.length, 0);
        });

        await test('start() twice opens one socket', async () => {
            const h = harness(m);
            h.ch.start(); h.ch.start(); await flush();
            assert.strictEqual(FakeWS.all.length, 1);
        });


        // ---- PushWakeGuard -------------------------------------------------
        class FakeDetector {
            constructor(listening = true) { this.isListening = listening; this.stops = 0; this.starts = 0; this.fail = false; }
            isSupported() { return true; }
            stop() { this.stops++; this.isListening = false; }
            start() { this.starts++; if (this.fail) throw new Error('start failed'); this.isListening = true; return Promise.resolve(true); }
        }
        function guard(over = {}) {
            const st = { wd: new FakeDetector(), call: false, ptt: false, ptoTakeover: 0, timers: [], cleared: [] };
            const g = new m.PushWakeGuard({
                getDetector: () => st.wd,
                inCall: () => st.call,
                pttActive: () => st.ptt,
                onPttTakeover: () => { st.ptoTakeover++; },
                setTimer: (fn, ms) => { st.timers.push({ fn, ms }); return st.timers.length; },
                clearTimer: (id) => st.cleared.push(id),
                ...over,
            });
            return { g, st };
        }

        await test('wake guard: stops a listening detector outside a call, restarts it after', () => {
            assert.ok(m.PushWakeGuard, 'pushChannel.js exports no PushWakeGuard');
            const { g, st } = guard();
            assert.strictEqual(g.pause(), true);
            assert.strictEqual(st.wd.stops, 1);
            assert.strictEqual(st.wd.isListening, false);
            assert.strictEqual(g.pause(), true, 'a second push while paused');
            assert.strictEqual(st.wd.stops, 1, 'stop() only once');
            g.resume();
            assert.strictEqual(st.wd.starts, 1);
            g.resume(); g.resume();
            assert.strictEqual(st.wd.starts, 1, 'resume is idempotent');
        });

        await test('wake guard: leaves the detector alone in a call or when it was off', () => {
            const a = guard(); a.st.call = true;
            assert.strictEqual(a.g.pause(), false);
            assert.strictEqual(a.st.wd.stops, 0);
            const b = guard(); b.st.wd.isListening = false;
            assert.strictEqual(b.g.pause(), false);
            b.g.resume();
            assert.strictEqual(b.st.wd.starts, 0, 'never starts a detector it did not stop');
        });

        await test('wake guard: no restart when a call started, PTT came on, or the user toggled', () => {
            const a = guard(); a.g.pause(); a.st.call = true; a.g.resume();
            assert.strictEqual(a.st.wd.starts, 0, 'a call owns the detector now');
            const b = guard(); b.g.pause(); b.st.ptt = true; b.g.resume();
            assert.strictEqual(b.st.wd.starts, 0, 'PTT keeps wake word off');
            assert.strictEqual(b.st.ptoTakeover, 1, 'PTT-off must be told to restore it');
            const c = guard(); c.g.pause();
            assert.strictEqual(c.g.takeOver(), true);
            c.g.resume();
            assert.strictEqual(c.st.wd.starts, 0, "the user's toggle wins");
            const d = guard(); d.g.pause(); d.st.wd.isListening = true; d.g.resume();
            assert.strictEqual(d.st.wd.starts, 0, 'already listening again');
        });

        await test('wake guard: a Hume call started during push audio blocks the restart', () => {
            // Mirrors the app's inCall: ClawdBotMode flags OR an open/connecting Hume call.
            const hume = { isConnected: false, isConnecting: false };
            const { g, st } = guard({ inCall: () => hume.isConnected || hume.isConnecting });
            assert.strictEqual(g.pause(), true);
            hume.isConnecting = true;
            g.resume();
            assert.strictEqual(st.wd.starts, 0, 'no restart into a connecting Hume call');
            const b = guard({ inCall: () => hume.isConnected });
            hume.isConnecting = false; hume.isConnected = true;
            assert.strictEqual(b.g.pause(), false, 'a Hume call already owns the detector');
        });

        await test('wake guard: a failing start() does not throw, and a hard cap restores it', () => {
            const a = guard(); a.g.pause(); a.st.wd.fail = true;
            a.g.resume();
            assert.strictEqual(a.st.wd.starts, 1);
            const b = guard({ maxPauseMs: 1234 });
            b.g.pause();
            assert.strictEqual(b.st.timers.length, 1);
            assert.strictEqual(b.st.timers[0].ms, 1234);
            b.st.timers[0].fn();
            assert.strictEqual(b.st.wd.starts, 1, 'the cap restarts it if playback never reported its end');
            const c = guard(); c.g.pause(); c.g.resume();
            assert.deepStrictEqual(c.st.cleared, [1], 'a normal resume clears the cap');
        });

        await test('deduper: same id once, no id always, bounded', () => {
            const isNew = m.makeProactiveDeduper(3);
            assert.strictEqual(isNew('x'), true);
            assert.strictEqual(isNew('x'), false);
            assert.strictEqual(isNew(undefined), true);
            assert.strictEqual(isNew(undefined), true);
            isNew('a'); isNew('b'); isNew('c');
            assert.strictEqual(isNew('x'), true, 'oldest id falls out of the window');
        });
    }

    if (failures) { console.log(`\n${failures} failing`); process.exit(1); }
    console.log('\nall push-channel tests passed');
})();
