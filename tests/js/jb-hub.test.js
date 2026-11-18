// jb-hub.test.js — the SDK's Hub overview call (/jb/v1/jb.js): jb.hub.summary().
//
//   node tests/js/jb-hub.test.js
//
// jb.js runs in a vm with a fake location + fetch so the request it would send can be inspected.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SDK = fs.readFileSync(path.join(__dirname, '..', '..', 'jb', 'v1', 'jb.js'), 'utf8');

function loadSdk(pathname, calls) {
    const ctx = { location: { pathname }, console };
    ctx.window = ctx;
    ctx.fetch = (url, opts) => {
        calls.push({ url, opts });
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{"hub":1,"sections":{}}') });
    };
    vm.createContext(ctx);
    vm.runInContext(SDK, ctx);
    return ctx.jb;
}

let failed = 0;
async function test(name, fn) {
    try { await fn(); console.log('ok  ', name); } catch (e) { failed++; console.log('FAIL', name, '\n', e); }
}

(async () => {
    await test('jb.hub.summary() is one GET of /api/biz/v1/hub/summary with no tenant anywhere', async () => {
        const calls = [];
        const jb = loadSdk('/pages/dashboard.html', calls);
        const out = await jb.hub.summary();
        assert.strictEqual(out.hub, 1);
        assert.strictEqual(calls.length, 1);
        assert.strictEqual(calls[0].url, '/api/biz/v1/hub/summary');
        assert.strictEqual(calls[0].opts.method, 'GET');
        assert.ok(!/tenant/i.test(calls[0].url + JSON.stringify(calls[0].opts.headers || {})));
    });
    await test('on a sandbox page it reads that sandbox (header), never a tenant', async () => {
        const calls = [];
        const jb = loadSdk('/pages/sandbox/foamology/overview.html', calls);
        await jb.hub.summary();
        assert.strictEqual(calls[0].opts.headers['X-Biz-Sandbox'], 'foamology');
    });
    if (failed) { console.log(`${failed} failed`); process.exit(1); }
    console.log('all passed');
})();
