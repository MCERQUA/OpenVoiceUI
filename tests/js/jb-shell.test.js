// jb-shell.test.js — the company-system menu model (/jb/v1/shell.js) and the SDK's sandbox header.
//
//   node tests/js/jb-shell.test.js
//
// No browser, no deps: each file runs in a vm. shell.js with no `document` only defines
// jbShell.buildModel (the pure part); jb.js runs with a fake location + fetch so the
// request it would send can be inspected.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..');
const SHELL = fs.readFileSync(path.join(ROOT, 'jb', 'v1', 'shell.js'), 'utf8');
const SDK = fs.readFileSync(path.join(ROOT, 'jb', 'v1', 'jb.js'), 'utf8');

function loadShell() {
    const ctx = {};
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(SHELL, ctx);
    return ctx.jbShell;
}

const payload = {
    sections: [
        { id: 'jobs', label: 'Jobs', icon: 'clipboard', pages: [
            { id: 'crew-log', file: 'crew-log.html', label: 'Crew log', icon: 'clipboard', verb: null, shell: 'field', tab: true }] },
        { id: 'home', label: 'Home', icon: 'home', pages: [
            { id: 'overview', file: 'overview.html', label: 'Overview', icon: 'home', verb: null, shell: 'office', tab: false }] },
        { id: 'sales', label: 'Sales', icon: 'document', pages: [
            { id: 'quotes', file: 'quotes.html', label: 'Quotes', icon: 'document', verb: 'New quote',
              verb_href: 'quotes.html#new', shell: 'office', tab: true }] },
    ],
    tabs: ['overview', 'quotes', 'crew-log', 'ghost-page'],
    more: false, company: { name: 'Example Co', logo: null }, theme: { brand: '#0f766e' },
    icons: { home: '<path/>' }, base: '/pages/', count: 3,
};

const queue = [];
function test(name, fn) { queue.push([name, fn]); }

test('sections come out in the fixed taxonomy order', () => {
    const m = loadShell().buildModel(payload, 'quotes');
    assert.deepStrictEqual(Array.from(m.sections, s => s.id), ['home', 'sales', 'jobs']);
});

test('current page, eyebrow, title and verb', () => {
    const m = loadShell().buildModel(payload, 'quotes');
    assert.strictEqual(m.title, 'Quotes');
    assert.strictEqual(m.eyebrow, 'Sales');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(m.verb)), { label: 'New quote', file: 'quotes.html#new' });
    assert.strictEqual(m.home.id, 'overview');
});

test('tabs drop ids that are not in the menu and never exceed five', () => {
    const m = loadShell().buildModel(payload, 'quotes');
    assert.deepStrictEqual(Array.from(m.tabs, p => p.id), ['overview', 'quotes', 'crew-log']);
    const many = Object.assign({}, payload, { tabs: ['overview', 'quotes', 'crew-log', 'overview', 'quotes', 'crew-log', 'quotes'] });
    assert.ok(loadShell().buildModel(many, null).tabs.length <= 5);
});

test('an unregistered page gets no title or verb, and an empty payload is safe', () => {
    const m = loadShell().buildModel(payload, 'not-registered');
    assert.strictEqual(m.current, null);
    assert.strictEqual(m.verb, null);
    const e = loadShell().buildModel({}, 'x');
    assert.strictEqual(e.sections.length, 0);
    assert.strictEqual(e.base, '/pages/');
});

function loadSdk(pathname, calls) {
    const ctx = { location: { pathname }, console };
    ctx.window = ctx;
    ctx.fetch = (url, opts) => {
        calls.push({ url, opts });
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{}') });
    };
    vm.createContext(ctx);
    vm.runInContext(SDK, ctx);
    return ctx.jb;
}

test('jb.js sends X-Biz-Sandbox only on /pages/sandbox/<name>/ pages', async () => {
    const calls = [];
    const sbx = loadSdk('/pages/sandbox/one/quotes.html', calls);
    assert.strictEqual(sbx.sandbox, 'one');
    const prod = loadSdk('/pages/quotes.html', calls);
    assert.strictEqual(prod.sandbox, null);
    await sbx.health();
    await prod.health();
    assert.strictEqual(calls[0].opts.headers['X-Biz-Sandbox'], 'one');
    assert.strictEqual(calls[1].opts.headers['X-Biz-Sandbox'], undefined);
    const odd = loadSdk('/pages/sandbox/../quotes.html', calls);
    assert.strictEqual(odd.sandbox, null);
});

test('jb.pages.update refuses without a version', async () => {
    const jb = loadSdk('/pages/x.html', []);
    await assert.rejects(jb.pages.update('x', { label: 'y' }), e => e.code === 'if_match_required');
});

(async () => {
    for (const [name, fn] of queue) {
        await fn();
        console.log('ok', name);
    }
    console.log(`jb-shell: ${queue.length} tests passed`);
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
