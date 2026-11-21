// profile-no-stale-restore.test.js — FE-5: a stale browser-cached profile must never be
// re-activated on the server at page load.
//
//   node tests/js/profile-no-stale-restore.test.js
//
// Runs ProviderManager-style QuickSettings init() from src/app.js (extracted, run in a vm
// with fake fetch/localStorage/document) with localStorage holding a stale
// active_profile_id and the server reporting a different active profile. Asserts: no POST
// /api/profiles/activate, the dropdown follows the server, and init never reads browser storage.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = process.env.APP_SRC || path.resolve(__dirname, '..', '..', 'src', 'app.js');
const src = fs.readFileSync(APP, 'utf8');
const start = src.indexOf("_DEFAULT_PROFILE: 'default',");
const initAt = src.indexOf('async init() {', start);
const end = src.indexOf('_updateDeleteBtn() {', initAt);
assert(start > 0 && initAt > start && end > initAt, 'could not locate init() in app.js');
const body = src.slice(initAt, end).trim().replace(/,\s*$/, '');

(async () => {
    const calls = [];
    const storageReads = [];
    const store = { active_profile_id: 'default' }; // stale
    const select = { innerHTML: '', appended: [], appendChild(o) { this.appended.push(o); }, value: '' };
    const ctx = {
        console,
        window: { _gatewayState: {}, applyProfile() {} },
        TranscriptPanel: {},
        localStorage: {
            getItem(k) { storageReads.push(k); return store[k] ?? null; },
            setItem(k, v) { store[k] = v; },
        },
        document: {
            getElementById: id => (id === 'voice-mode-select' ? select : null),
            createElement: () => ({}),
        },
        fetch: async (url, opts) => {
            calls.push({ url, method: (opts && opts.method) || 'GET' });
            if (url === '/api/profiles') {
                return { json: async () => ({ active: 'comms-brain', profiles: [{ id: 'default', name: 'Default' }, { id: 'comms-brain', name: 'Comms' }] }) };
            }
            return { json: async () => ({}) };
        },
    };
    ctx.QS = null;
    vm.createContext(ctx);
    vm.runInContext('QS = { _profiles: [], _DEFAULT_PROFILE: "default", _updateDeleteBtn() {}, ' + body + ' }', ctx);
    await ctx.QS.init();

    const activates = calls.filter(c => c.url === '/api/profiles/activate');
    assert.strictEqual(activates.length, 0, 'init() must not POST /api/profiles/activate (stale localStorage restore): ' + JSON.stringify(activates));
    assert.strictEqual(storageReads.length, 0, 'init() must not read browser storage: ' + storageReads);
    const sel = select.appended.find(o => o.selected);
    assert(sel && sel.value === 'comms-brain', 'dropdown must follow the server active profile');
    assert.strictEqual(store.active_profile_id, 'default', 'init() must not write browser storage');
    console.log('PASS profile-no-stale-restore');
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
