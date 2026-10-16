// safe-redirect.test.js — the post-login ?redirect= target must stay on this origin.
//
//   node tests/js/safe-redirect.test.js
//
// The login shell sends a browser back to /?redirect=<path> after it refreshes a stale
// session (canvas pages and /uploads/ both bounce through it). The old check was
// redirectTo.startsWith('/'), which accepts protocol-relative targets. Attack cases below
// come from how the WHATWG URL parser treats input, not from the fix: '//host' and
// '/\host' are protocol-relative, and tab/CR/LF are deleted before parsing.
//
// No browser, no deps: the module is read, its `export` keyword dropped, and run in a vm
// with Node's own URL (the same parser browsers implement).

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..', '..');
const MOD = process.env.SAFE_REDIRECT_SRC || path.join(ROOT, 'src', 'core', 'safeRedirect.js');
const APP = process.env.APP_SRC || path.join(ROOT, 'src', 'app.js');

function load(src) {
    const ctx = { URL };
    vm.createContext(ctx);
    vm.runInContext(src.replace(/^export\s+/m, '') + '\nthis.safeRedirectTarget = safeRedirectTarget;', ctx);
    return ctx.safeRedirectTarget;
}

const ORIGIN = 'https://tenant.example.com';
const safe = load(fs.readFileSync(MOD, 'utf8'));
let n = 0;

// Positive controls: same-origin paths come back (normalised by the URL parser).
for (const [raw, want] of [
    ['/pages/report.html', '/pages/report.html'],
    ['/uploads/reports/list.xlsx', '/uploads/reports/list.xlsx'],
    ['/uploads/a b.png', '/uploads/a%20b.png'],
    ['/?tab=1#top', '/?tab=1#top'],
    ['/%2F%2Fstill-a-path', '/%2F%2Fstill-a-path'],
]) {
    assert.strictEqual(safe(raw, ORIGIN), want, `should keep ${JSON.stringify(raw)}`);
    n++;
}

// Negative controls: anything that leaves the origin, or is not a path at all.
const ATTACKS = [
    '//evil.example/x',
    '/\\evil.example',
    '/\t/evil.example',
    '/\n/evil.example',
    '/\r\n/evil.example',
    '\\\\evil.example',
    'https://evil.example/',
    'javascript:alert(1)',
    ' /\t/evil.example',
    'pages/report.html',
    '',
    null,
    undefined,
    42,
];
for (const raw of ATTACKS) {
    assert.strictEqual(safe(raw, ORIGIN), null, `should refuse ${JSON.stringify(raw)}`);
    n++;
}

// Mutant control: the predicate this replaced lets the first attacks through, so these
// cases would have caught it.
const old = (raw) => (raw && typeof raw === 'string' && raw.startsWith('/')) ? raw : null;
const leaked = ATTACKS.filter((raw) => {
    const t = old(raw);
    if (!t) return false;
    try { return new URL(t, ORIGIN).origin !== ORIGIN; } catch (e) { return false; }
});
assert.ok(leaked.length >= 4, `old startsWith('/') check should leak >=4 attacks, leaked ${leaked.length}`);
n++;

// Wiring: app.js imports the guard and no longer redirects on a bare startsWith('/').
const app = fs.readFileSync(APP, 'utf8');
assert.ok(/import\s*\{\s*safeRedirectTarget\s*\}\s*from\s*'\.\/core\/safeRedirect\.js'/.test(app),
    'app.js must import safeRedirectTarget');
assert.ok(/safeRedirectTarget\(\s*new URLSearchParams\(window\.location\.search\)\.get\('redirect'\)/.test(app),
    'app.js must pass ?redirect= through safeRedirectTarget');
assert.ok(!/redirectTo\s*&&\s*redirectTo\.startsWith\('\/'\)/.test(app),
    "app.js must not redirect on a bare startsWith('/')");
n++;

console.log(`safe-redirect: ${n} checks passed`);
