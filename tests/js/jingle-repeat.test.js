// jingle-repeat.test.js — the [JINGLE_GENERATE:brand|style|gender|instrumental|repeat] tag's
// 5th field must reach POST /api/suno (window.sunoModule.generateJingle in src/app.js).
//
//   node tests/js/jingle-repeat.test.js
//   APP_SRC=/path/to/app.js node tests/js/jingle-repeat.test.js   # another copy
//
// The tag parser read `repeat` and passed it as a 5th argument, but generateJingle took four
// parameters and never sent it, so every jingle used the server default. Extracts the method
// from app.js and drives it with a fake fetch: no browser, no deps.

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const APP = process.env.APP_SRC || path.join(__dirname, '..', '..', 'src', 'app.js');

function load() {
  const src = fs.readFileSync(APP, 'utf8');
  const start = src.indexOf('async generateJingle(');
  const end = src.indexOf('async generate(prompt)', start);
  if (start < 0 || end < 0) throw new Error('generateJingle not found in ' + APP);
  const method = src.slice(start, end).trim().replace(/,\s*$/, '');
  const sent = [];
  const sandbox = {
    CONFIG: { serverUrl: '' },
    ActionConsole: { addEntry() {} },
    console: { log() {}, warn() {}, error() {} },
    setTimeout: () => 0,
    fetch: async (url, opts) => {
      sent.push({ url, body: JSON.parse(opts.body) });
      return { json: async () => ({ action: 'generating', job_id: 'j' + sent.length }) };
    },
  };
  vm.createContext(sandbox);
  const mod = vm.runInContext(`({
    _recentPrompts: new Map(), _DEDUP_WINDOW_MS: 60000,
    _showStatus() {}, _hideStatus() {}, _startPolling() {}, _announceFailure() {},
    ${method}
  })`, sandbox, { filename: APP });
  return { mod, sent };
}

(async () => {
  const { mod, sent } = load();

  await mod.generateJingle('Acme', 'country', 'm', false, 1);
  assert.strictEqual(sent.length, 1, 'jingle request not sent');
  assert.strictEqual(sent[0].url, '/api/suno');
  assert.strictEqual(sent[0].body.action, 'jingle');
  assert.strictEqual(sent[0].body.repeat, 1, 'repeat from the tag did not reach the request: ' + JSON.stringify(sent[0].body));

  // A different repeat for the same brand is a different jingle, not a duplicate.
  await mod.generateJingle('Acme', 'country', 'm', false, 3);
  assert.strictEqual(sent.length, 2, 'repeat=3 was dropped as a duplicate of repeat=1');
  assert.strictEqual(sent[1].body.repeat, 3);

  // The same request again within the window is still de-duplicated.
  await mod.generateJingle('Acme', 'country', 'm', false, 3);
  assert.strictEqual(sent.length, 2, 'identical jingle within the dedup window was sent twice');

  // No repeat given: the field is omitted so the server default applies.
  await mod.generateJingle('Other Co', '', 'f', false);
  assert.strictEqual(sent.length, 3);
  assert.ok(!('repeat' in sent[2].body), 'repeat should be omitted when not given');

  console.log('jingle-repeat: OK');
})().catch((e) => { console.error(e.stack || e); process.exit(1); });
