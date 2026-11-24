// proactive-canvas-url.test.js — a PUSHED [CANVAS_URL:...] must open in the canvas, like a reply's.
//
//   node tests/js/proactive-canvas-url.test.js
//   APP_SRC=/path/to/app.js node tests/js/proactive-canvas-url.test.js
//
// 2026-11-24: the owner texts an agent "play X on the TV"; the agent answers with
// `voice-say --push "... [CANVAS_URL:https://www.youtube.com/embed/<id>?autoplay=1]"`. The server
// enqueued the frame to the open app ("Proactive push: enqueued to 1/1 clients"), but the browser's
// push handler (_handleProactiveMessage) acted only on CANVAS / CANVAS_CLOSE / CANVAS_MENU and
// canvas-action tags; CANVAS_URL was stripped from the text and never opened. The reply path did
// open it. Fix: one CANVAS_URL handler (openCanvasUrlTag) used by the streamed reply, the
// non-streamed voice/greeting reply and the push.
//
// Runs the REAL _handleProactiveMessage, resolveCanvasUrl and (when present) openCanvasUrlTag sliced
// out of src/app.js against a fake DOM. On an app.js without the change the push cases FAIL on
// behaviour (the iframe never loads), not on a missing symbol.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = process.env.APP_SRC || path.join(__dirname, '..', '..', 'src', 'app.js');
const SRC = fs.readFileSync(APP, 'utf8');

function braceBlock(src, from) {
    let depth = 0;
    for (let i = from; i < src.length; i++) {
        const c = src[i];
        if (c === '{') depth++;
        else if (c === '}') { depth--; if (depth === 0) return i + 1; }
        else if (c === '"' || c === "'" || c === '`') {
            const q = c;
            for (i++; i < src.length && src[i] !== q; i++) if (src[i] === '\\') i++;
        } else if (c === '/' && src[i + 1] === '/' && src[i - 1] !== '\\') {
            while (i < src.length && src[i] !== '\n') i++;
        }
    }
    throw new Error('unbalanced block at ' + from);
}
function slice(marker, required = true) {
    const at = SRC.indexOf(marker);
    if (at < 0) { if (required) throw new Error('not found in app.js: ' + marker); return ''; }
    const open = SRC.indexOf(') {', at) + 2;      // the body brace, not a destructured param's
    return SRC.slice(at, braceBlock(SRC, open));
}

const RESOLVE = slice('function resolveCanvasUrl(url) {');
const HELPER = slice('function openCanvasUrlTag(', false);           // absent before the change
const PUSH = slice('async _handleProactiveMessage(data) {');

function world({ devsiteMap = {} } = {}) {
    const iframe = { src: '' };
    const log = { console: [], chip: [], shown: [], played: [], show: 0, showPage: [], hide: 0, menu: 0, fetch: [] };
    const ctx = {
        console: { log: () => {}, warn: (...a) => log.console.push(a.join(' ')), error: () => {} },
        window: { AGENT_CONFIG: { devsiteMap }, CanvasMenu: { loadManifest: async () => {} } },
        document: { getElementById: (id) => (id === 'canvas-iframe' ? iframe : null) },
        ActionConsole: { addEntry: (k, m) => log.console.push(m) },
        AgentActivityChip: { handleTag: (t, d) => log.chip.push([t, d]) },
        CanvasControl: {
            show: () => { log.show++; },
            showPage: (p) => { log.showPage.push(p); },
            hide: () => { log.hide++; },
            showMenu: () => { log.menu++; return true; },
        },
        fetch: async (u) => { log.fetch.push(u); return { ok: true }; },
    };
    vm.createContext(ctx);
    vm.runInContext(`${RESOLVE}\n${HELPER}\nclass M {
        constructor() { this.config = { serverUrl: '' }; this.callbacks = { onMessage() {} };
                        this.isPlaying = false; this.audioQueue = []; }
        displayMessage(role, t) { __log.shown.push(t); }
        playAudio(a) { __log.played.push(a); this.isPlaying = true; }
        _pushWakeGuard() { return { pause: () => false }; }
        ${PUSH}
    }\nthis.M = M;`, Object.assign(ctx, { __log: log }));
    return { m: new ctx.M(), iframe, log };
}

const YT = 'https://www.youtube.com/embed/jNQXAC9IVRw?autoplay=1';
let failures = 0;
async function test(name, fn) {
    try { await fn(); console.log(`ok   ${name}`); }
    catch (e) { failures++; console.log(`FAIL ${name}\n     ${e.message}`); }
}

(async () => {
    await test('pushed CANVAS_URL with NO audio (speak_unprompted=false) opens the canvas', async () => {
        const w = world();
        await w.m._handleProactiveMessage({ text: `Here you go [CANVAS_URL:${YT}]`, audio: null });
        assert.strictEqual(w.iframe.src, YT, 'canvas iframe did not load the pushed URL');
        assert.strictEqual(w.log.show, 1, 'CanvasControl.show() not called');
        assert.deepStrictEqual(w.log.shown, ['Here you go'], 'tag must be stripped from the shown text');
        assert.deepStrictEqual(w.log.played, [], 'no audio was sent, none must play');
    });

    await test('pushed CANVAS_URL with audio opens the canvas AND plays the audio', async () => {
        const w = world();
        await w.m._handleProactiveMessage({ text: `Playing it now [CANVAS_URL:${YT}]`, audio: 'QUJD' });
        assert.strictEqual(w.iframe.src, YT);
        assert.deepStrictEqual(w.log.played, ['QUJD']);
        assert.ok(w.log.chip.some(([t, d]) => t === 'canvas_url' && d === YT), 'activity chip not told');
    });

    await test('pushed tag-only CANVAS_URL (no words) still opens the canvas', async () => {
        const w = world();
        await w.m._handleProactiveMessage({ text: `[CANVAS_URL:${YT}]`, audio: null });
        assert.strictEqual(w.iframe.src, YT);
        assert.deepStrictEqual(w.log.shown, []);
    });

    await test('pushed private/internal URL is still BLOCKED (resolveCanvasUrl check kept)', async () => {
        const w = world();
        await w.m._handleProactiveMessage({ text: '[CANVAS_URL:http://10.0.0.5:8080/admin]', audio: null });
        assert.strictEqual(w.iframe.src, '', 'private URL must never load');
        assert.strictEqual(w.log.show, 0);
        assert.ok(w.log.console.some((l) => /blocked private URL/i.test(l)), 'block not reported');
    });

    await test('pushed dev-domain URL is rewritten to the same-origin proxy', async () => {
        const w = world({ devsiteMap: { 'dev-x.jam-bot.com': 'https://x.jam-bot.com/devsite-x/' } });
        await w.m._handleProactiveMessage({ text: '[CANVAS_URL:https://dev-x.jam-bot.com/about]', audio: null });
        assert.strictEqual(w.iframe.src, 'https://x.jam-bot.com/devsite-x/about');
    });

    await test('pushed [CANVAS:page] still opens the page and does not touch the URL iframe path', async () => {
        const w = world();
        await w.m._handleProactiveMessage({ text: 'Done [CANVAS:report]', audio: null });
        assert.deepStrictEqual(w.log.showPage, ['report']);
        assert.strictEqual(w.iframe.src, '');
    });

    await test('ONE CANVAS_URL handler: the tag regex appears once in app.js (no duplicated handler)', () => {
        const n = SRC.split('/\\[CANVAS_URL:([^\\]]+)\\]/i').length - 1;
        assert.strictEqual(n, 1, `CANVAS_URL dispatch regex appears ${n}x in app.js; expected 1 (openCanvasUrlTag)`);
        const uses = (SRC.match(/openCanvasUrlTag\(/g) || []).length;
        assert.ok(uses >= 4, `openCanvasUrlTag defined + used by stream, voice and push paths (found ${uses})`);
    });

    console.log(failures ? `\n${failures} FAILED` : '\nall passed');
    process.exit(failures ? 1 : 0);
})();
