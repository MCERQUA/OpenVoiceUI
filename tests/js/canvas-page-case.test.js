// canvas-page-case.test.js — [CANVAS:<id>] must open a mixed-case page under its real filename.
//
//   node tests/js/canvas-page-case.test.js
//   APP_SRC=/path/to/app.js node tests/js/canvas-page-case.test.js
//
// 2026-11-24: an agent wrote canvas-pages/yt-EFdA5duvOas-b.html (a YouTube id keeps its case) and
// tagged [CANVAS:yt-EFdA5duvOas-b]. CanvasMenu.findPageByName lowercased the query, so the exact-id
// lookup missed the manifest key "yt-EFdA5duvOas-b"; CanvasControl.showPageByName then fell back to
// a LOWERCASED filename: GET /pages/yt-efda5duvoas-b.html -> 404, black screen. 809 mixed-case page
// files existed across tenants.
//
// Runs the REAL CanvasMenu.findPageByName and CanvasControl.showPageByName (plus _directPageFilename
// when present) sliced out of src/app.js. On an app.js without the change the mixed-case cases FAIL
// on behaviour (the iframe gets the lowercased URL).

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
function method(name, required = true) {
    const m = new RegExp('\\n(\\s+)(async\\s+)?' + name + '\\([^)]*\\)\\s*\\{').exec(SRC);
    if (!m) { if (required) throw new Error('not found in app.js: ' + name); return ''; }
    const open = m.index + m[0].length - 1;
    return SRC.slice(m.index + 1, braceBlock(SRC, open)) + ',';
}

const FIND = method('findPageByName');
const BY_NAME = method('showPageByName');
const DIRECT = method('_directPageFilename', false);      // absent before the change

function world(pages) {
    const iframe = { src: '' };
    const shown = [];
    const ctx = {
        console: { log: () => {}, warn: () => {}, error: () => {} },
        Date: { now: () => 7 },
        localStorage: { setItem: () => {} },
        document: { getElementById: (id) => (id === 'canvas-iframe' ? iframe : null) },
    };
    vm.createContext(ctx);
    vm.runInContext(`
        const CanvasMenu = { manifest: ${pages ? JSON.stringify({ pages }) : 'null'},
            showPage(filename) { __shown.push(filename); iframe.src = '/pages/' + filename + '?t=7'; },
            ${FIND} };
        window = { CanvasMenu };
        const CanvasControl = { iframe, _lastMtime: 0, show() {}, ${DIRECT} ${BY_NAME} };
        this.CC = CanvasControl; this.CM = CanvasMenu;`,
        Object.assign(ctx, { iframe, __shown: shown }));
    return { cc: ctx.CC, cm: ctx.CM, iframe, shown };
}
const page = (id, extra = {}) => ({ filename: id + '.html', display_name: id.replace(/-/g, ' '), ...extra });

let failures = 0;
function test(name, fn) {
    try { fn(); console.log(`ok   ${name}`); }
    catch (e) { failures++; console.log(`FAIL ${name}\n     ${e.message}`); }
}

test('[CANVAS:yt-EFdA5duvOas-b] opens the mixed-case manifest page by its real filename', () => {
    const w = world({ 'yt-EFdA5duvOas-b': page('yt-EFdA5duvOas-b'), 'yt-EFdA5duvOas': page('yt-EFdA5duvOas') });
    w.cc.showPageByName('yt-EFdA5duvOas-b');
    assert.strictEqual(w.iframe.src, '/pages/yt-EFdA5duvOas-b.html?t=7');
});

test('a lowercased tag still finds a UNIQUE mixed-case page (case-insensitive, one match)', () => {
    const w = world({ 'yt-EFdA5duvOas-b': page('yt-EFdA5duvOas-b'), 'yt-EFdA5duvOas': page('yt-EFdA5duvOas') });
    w.cc.showPageByName('yt-efda5duvoas-b');
    assert.strictEqual(w.iframe.src, '/pages/yt-EFdA5duvOas-b.html?t=7');
});

test('two ids differing only by case: the exact one wins, a third casing is REFUSED (no guess)', () => {
    const w = world({ 'Foo-Bar': page('Foo-Bar'), 'foo-bar': page('foo-bar') });
    assert.strictEqual(w.cm.findPageByName('Foo-Bar').pageId, 'Foo-Bar');
    assert.strictEqual(w.cm.findPageByName('foo-bar').pageId, 'foo-bar');
    assert.strictEqual(w.cm.findPageByName('FOO-BAR'), null, 'ambiguous casing must not pick one');
});

test('page not in the manifest yet: direct load keeps the id case', () => {
    const w = world({ dashboard: page('dashboard') });
    w.cc.showPageByName('yt-EFdA5duvOas');
    assert.strictEqual(w.iframe.src, '/pages/yt-EFdA5duvOas.html?t=7');
});

test('manifest not loaded: direct load keeps the id case', () => {
    const w = world(null);
    w.cc.showPageByName('yt-EFdA5duvOas');
    assert.strictEqual(w.iframe.src, '/pages/yt-EFdA5duvOas.html?t=7');
});

test('no regression: lowercase id exact match, spoken phrase still slugs to lowercase', () => {
    const w = world({ 'marketing-plan': page('marketing-plan') });
    w.cc.showPageByName('marketing-plan');
    assert.strictEqual(w.iframe.src, '/pages/marketing-plan.html?t=7');
    const w2 = world(null);
    w2.cc.showPageByName('Marketing Plan');
    assert.strictEqual(w2.iframe.src, '/pages/marketing-plan.html?t=7');
});

test('no regression: a structured id never fuzzy-matches a short alias (job-2026-0001 vs "job")', () => {
    const w = world({ 'job-albums': page('job-albums', { voice_aliases: ['job'] }) });
    w.cc.showPageByName('job-2026-0001');
    assert.strictEqual(w.iframe.src, '/pages/job-2026-0001.html?t=7');
});

console.log(failures ? `\n${failures} FAILED` : '\nall passed');
process.exit(failures ? 1 : 0);
