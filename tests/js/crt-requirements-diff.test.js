// crt-requirements-diff.test.js — the staff requirements page renders EVERY key of EVERY item, then and now
// (rules 1.6.0, host ruling M), and names only an APPROVED set as "the version in use" (ruling D).
//
//   node tests/js/crt-requirements-diff.test.js
//
// The page's own renderDiff / val source is lifted out of the shipped HTML and run in a vm with a tiny fake DOM.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PAGE = fs.readFileSync(path.join(__dirname, '..', '..', 'default-schemas', 'packs', 'risk-transfer', 'pages',
    'cca-crt-requirements.html'), 'utf8');

function lift(name) {
    const start = PAGE.indexOf('  function ' + name + '(');
    assert.ok(start >= 0, 'page has no function ' + name);
    // the function ends at the first line that is exactly "  }" after its start
    const end = PAGE.indexOf('\n  }\n', start);
    return PAGE.slice(start, end + 4);
}

class Node {
    constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.textContent = ''; }
    appendChild(c) { this.kids.push(c); return c; }
    removeChild(c) { this.kids = this.kids.filter((k) => k !== c); }
    get firstChild() { return this.kids[0]; }
    setAttribute(k, v) { this.attrs[k] = v; }
    addEventListener() {}
    text() { return [this.textContent].concat(this.kids.map((k) => (typeof k === 'string' ? k : k.text()))).join(' '); }
    rows() {        // every <tr> under this node, as arrays of cell texts
        let out = this.tag === 'tr' ? [this.kids.map((k) => k.text().replace(/\s+/g, ' ').trim())] : [];
        this.kids.forEach((k) => { if (typeof k !== 'string') out = out.concat(k.rows()); });
        return out;
    }
}

function render(d) {
    const ctx = {
        document: { createElement: (t) => new Node(t), createTextNode: (s) => { const n = new Node('#text'); n.textContent = s; return n; } },
    };
    vm.createContext(ctx);
    vm.runInContext(`
      function el(tag, attrs, kids) {
        var n = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) { if (k === 'text') n.textContent = attrs[k]; else n.setAttribute(k, attrs[k]); });
        [].concat(kids || []).forEach(function (c) { if (c !== null && c !== undefined) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        return n;
      }
      var U = {el: el, clear: function (n) { n.kids = []; return n; }, chip: function (s, t) { return el('span', {text: '[' + (t || s) + ']'}); }};
      ${lift('val')}
      ${lift('renderDiff')}
      this.renderDiff = renderDiff;`, ctx);
    const box = new Node('div');
    ctx.renderDiff(box, d);
    return box;
}

let failed = 0;
function test(name, fn) {
    try { fn(); console.log('ok  ', name); } catch (e) { failed++; console.log('FAIL', name, '\n', e); }
}

const ROWS = (pairs) => pairs.map(([key, was, now]) => ({ key, was, now, changed: JSON.stringify(was) !== JSON.stringify(now) }));

test('every key of a changed item is a row with its was and now (a key swap is never a bare chip)', () => {
    const d = { against: { kind: 'set', approved: true, set_version: 1, name: 'MEP' }, unapproved_between: [],
        items: [{ id: 'gl_occ', change: 'changed', changed_fields: ['limit'],
            rows: ROWS([['kind', 'limit', 'limit'], ['limit', 'each_occurrence', 'general_aggregate'], ['min', 1000000, null],
                ['minimum_applied', 1000000, 1000000], ['floor', 1000000, 1000000]]) }],
        form_functions: {}, name: {}, trade_category: {}, notes: {}, floor_violations: [] };
    const rows = render(d).rows().filter((r) => r.length === 4 && r[0] !== 'Key' && r[0] !== 'Form');
    const by = Object.fromEntries(rows.map((r) => [r[0], r]));
    assert.deepStrictEqual(Object.keys(by).sort(), ['floor', 'kind', 'limit', 'min', 'minimum applied']);
    assert.ok(by.limit[1] === 'each_occurrence' && by.limit[2] === 'general_aggregate' && /changed/.test(by.limit[3]), by.limit);
    assert.ok(by.min[1] === '1000000' && by.min[2] === '-' && /changed/.test(by.min[3]), by.min);
    assert.ok(/same/.test(by['minimum applied'][3]), by['minimum applied']);
});

test('added, removed and same items show every key too', () => {
    const d = { against: { kind: 'set', approved: true, set_version: 2 }, unapproved_between: [],
        items: [{ id: 'a', change: 'added', changed_fields: [], rows: ROWS([['kind', null, 'endorsement'], ['forms', null, ['CG 20 37']]]) },
            { id: 'b', change: 'removed', changed_fields: [], rows: ROWS([['kind', 'endorsement', null], ['function', 'ai_completed', null]]) },
            { id: 'c', change: 'same', changed_fields: [], rows: ROWS([['kind', 'ai', 'ai'], ['line', 'gl', 'gl']]) }],
        form_functions: {}, name: {}, trade_category: {}, notes: {}, floor_violations: [] };
    const text = render(d).text();
    ['CG 20 37', 'ai_completed', 'function', 'line'].forEach((s) => assert.ok(text.includes(s), s));
    assert.strictEqual(render(d).rows().filter((r) => r.length === 4 && r[0] !== 'Key' && r[0] !== 'Form').length, 6);
});

test('"the version in use" names only an approved set; drafts in between are said', () => {
    const base = { unapproved_between: [{ set_version: 2 }], items: [], form_functions: {}, name: {}, trade_category: {}, notes: {}, floor_violations: [] };
    const ok = render(Object.assign({ against: { kind: 'set', approved: true, set_version: 1, name: 'MEP' } }, base)).text();
    assert.ok(ok.includes('approved version in use: MEP v1') && ok.includes('1 unapproved draft(s) (v2)'), ok);
    const bad = render(Object.assign({ against: { kind: 'set', set_version: 2 } }, base)).text();
    assert.ok(!bad.includes('version in use') && bad.includes('Not approved'), bad);
    const tpl = render(Object.assign({ against: { kind: 'template', template_key: 'mep', name: 'MEP trades' } }, base)).text();
    assert.ok(tpl.includes('the MEP trades template') && !tpl.includes('version in use'), tpl);
});

test('required is shown as the verdict reads it: only false is suggested, null is never a bare dash (rules 1.7.0 A)', () => {
    const item = (now) => ({ id: 'ai_completed', change: 'changed', changed_fields: ['required'],
        was: { required: true }, now: { required: now },
        rows: ROWS([['required', true, now], ['required_applied', true, now !== false]]) });
    const cell = (now) => {
        const d = { against: { kind: 'set', approved: true, set_version: 1 }, unapproved_between: [], items: [item(now)],
            form_functions: {}, name: {}, trade_category: {}, notes: {}, floor_violations: [] };
        return render(d).rows().find((r) => r[0] === 'required');
    };
    assert.strictEqual(cell(null)[2], 'required (default)');
    assert.strictEqual(cell(0)[2], 'required (0 reads as required)');
    assert.strictEqual(cell('')[2], 'required ( reads as required)');
    assert.strictEqual(cell(false)[2], 'false (suggested)');
    assert.strictEqual(cell(true)[1], 'true');
    assert.ok(![null, 0, ''].some((v) => cell(v)[2] === '-'));
});

if (failed) { console.log(failed + ' failed'); process.exit(1); }
console.log('all passed');
