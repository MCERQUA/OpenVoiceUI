// jb-pack.test.js — the SDK's pack routes and private file store calls (/jb/v1/jb.js).
//
//   node tests/js/jb-pack.test.js
//
// jb.js runs in a vm with a fake location + fetch so the requests it would send can be inspected.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SDK = fs.readFileSync(path.join(__dirname, '..', '..', 'jb', 'v1', 'jb.js'), 'utf8');

let lastCtx = null;
function loadSdk(pathname, calls, surface, documentStub) {
    const ctx = { location: { pathname }, console };
    if (documentStub) ctx.document = documentStub;
    if (surface) ctx.__JB_SURFACE__ = surface;      // what a platform staff page's server-rendered boot script sets
    ctx.window = ctx;
    ctx.FormData = class { constructor() { this.parts = []; } append(k, v, n) { this.parts.push([k, v, n]); } };
    ctx.fetch = (url, opts) => {
        calls.push({ url, opts });
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{"id":"x"}') });
    };
    vm.createContext(ctx);
    vm.runInContext(SDK, ctx);
    lastCtx = ctx;
    return ctx.jb;
}

let failed = 0;
async function test(name, fn) {
    try { await fn(); console.log('ok  ', name); } catch (e) { failed++; console.log('FAIL', name, '\n', e); }
}

(async () => {
    await test('jb.pack(prefix) calls /api/biz/v1/<prefix>/<path> with the sandbox header', async () => {
        const calls = [];
        const jb = loadSdk('/pages/sandbox/josh/cca-crt-book.html', calls);
        await jb.pack('crt').get('book', { days: 30 });
        await jb.pack('crt').post('confirm', { record_id: 'r' });
        assert.strictEqual(calls[0].url, '/api/biz/v1/crt/book?days=30');
        assert.strictEqual(calls[0].opts.headers['X-Biz-Sandbox'], 'josh');
        assert.strictEqual(calls[1].opts.method, 'POST');
        assert.strictEqual(JSON.parse(calls[1].opts.body).record_id, 'r');
    });
    await test('jb.pack refuses odd prefixes and path traversal', async () => {
        const jb = loadSdk('/pages/x.html', []);
        assert.throws(() => jb.pack('../x'));
        assert.throws(() => jb.pack('crt').get('../../export'));
        assert.throws(() => jb.pack('crt').get('a?b=1'));
    });
    await test('jb.files.upload posts multipart (no JSON content type) and the source as JSON text', async () => {
        const calls = [];
        const jb = loadSdk('/pages/sandbox/josh/cca-crt-intake.html', calls);
        await jb.files.upload({ name: 'coi.pdf' }, { source: { channel: 'email' } });
        assert.strictEqual(calls[0].url, '/api/biz/v1/files');
        assert.strictEqual(calls[0].opts.headers['Content-Type'], undefined);
        assert.strictEqual(calls[0].opts.headers['X-Biz-Sandbox'], 'josh');
        const parts = calls[0].opts.body.parts;
        assert.strictEqual(parts[0][0], 'file');
        assert.deepStrictEqual(JSON.parse(parts[1][1]), { channel: 'email' });
    });
    await test('jb.files.idOf accepts only biz-file:<sha256>', async () => {
        const jb = loadSdk('/pages/x.html', []);
        const sha = 'a'.repeat(64);
        assert.strictEqual(jb.files.idOf('biz-file:' + sha), sha);
        assert.strictEqual(jb.files.idOf('/uploads/crt/x.pdf'), null);
        assert.strictEqual(jb.files.idOf('biz-file:' + sha + '/../x'), null);
    });
    await test('on a platform staff page every call carries the per-render token (host ruling H4), and the global is gone', async () => {
        const calls = [];
        const jb = loadSdk('/api/biz/v1/crt/staff/cca-crt-intake.html', calls,
                           { token: 'v1.1.abc.def', sandbox: 'josh', base: '/api/biz/v1/crt/staff/' });
        await jb.pack('crt').post('confirm', { record_id: 'r' });
        await jb.list('crt_policy');
        await jb.files.upload({ name: 'coi.pdf' });
        for (const c of calls) {
            assert.strictEqual(c.opts.headers['X-JB-Staff-Surface'], 'v1.1.abc.def');
            assert.strictEqual(c.opts.headers['X-Biz-Sandbox'], 'josh');
        }
        assert.strictEqual(jb.staffBase, '/api/biz/v1/crt/staff/');
        assert.strictEqual('__JB_SURFACE__' in lastCtx, false);   // nothing later on the page can read it
    });
    await test('rules 1.4.0 (review M2): the boot script node that held the token is taken out of the DOM', async () => {
        const removed = [];
        const boot = { id: 'jb-surface-boot' };
        boot.parentNode = { removeChild: (n) => { removed.push(n); } };
        const doc = { getElementById: (id) => (id === 'jb-surface-boot' ? boot : null) };
        const jb = loadSdk('/api/biz/v1/crt/staff/cca-crt-intake.html', [],
                           { token: 'v2.1.abc.def', sandbox: 'josh', base: '/api/biz/v1/crt/staff/' }, doc);
        assert.strictEqual(removed.length, 1);
        assert.strictEqual(removed[0], boot);
        assert.strictEqual(jb.staffBase, '/api/biz/v1/crt/staff/');
    });
    await test('a platform staff page shows a Sign out control that ends the staff session', async () => {
        const calls = [], appended = [];
        const listeners = {};
        const doc = {
            readyState: 'complete',
            getElementById: () => null,
            createElement: (tag) => ({ tag, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; },
                addEventListener(e, fn) { listeners[e] = fn; } }),
            body: { appendChild: (n) => appended.push(n) },
        };
        const jb = loadSdk('/api/biz/v1/crt/staff/cca-crt-book.html', calls,
            { token: 'v2.t', sandbox: null, base: '/api/biz/v1/crt/staff/' }, doc);
        let went = null;
        lastCtx.location.replace = (u) => { went = u; };
        assert.strictEqual(appended.length, 1);
        assert.strictEqual(appended[0].id, 'jb-staff-signout');
        assert.strictEqual(appended[0].textContent, 'Sign out');
        listeners.click();
        await new Promise((r) => setTimeout(r, 0));
        assert.strictEqual(calls[0].url, '/api/biz/v1/crt/staff/signout');
        assert.strictEqual(calls[0].opts.method, 'POST');
        assert.strictEqual(went, '/api/biz/v1/crt/staff/signin');
        assert.strictEqual(typeof jb.staffSignOut, 'function');
    });
    await test('a canvas page (no platform boot) never sends a surface token', async () => {
        const calls = [];
        const jb = loadSdk('/pages/sandbox/josh/agent-intake.html', calls);
        await jb.pack('crt').post('confirm', { record_id: 'r' });
        assert.strictEqual(calls[0].opts.headers['X-JB-Staff-Surface'], undefined);
        assert.strictEqual(jb.staffBase, null);
    });
    if (failed) { console.log(failed + ' failed'); process.exit(1); }
    console.log('all passed');
})();
