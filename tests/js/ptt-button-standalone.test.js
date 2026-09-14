// ptt-button-standalone.test.js — push-to-talk with no call running (window.PTTButton in src/app.js)
//
//   node tests/js/ptt-button-standalone.test.js
//   APP_SRC=/path/to/app.js node tests/js/ptt-button-standalone.test.js   # another copy
//
// PTT must work any time, call or not, the same way the transcript text box does: hold, speak,
// release, and the words go to ClawdbotMode.sendMessage. Before this, a PTT press with no call
// changed the button colour and nothing else (measured on test-dev 2026-09-14: no Deepgram
// traffic until a call was started).
//
// Extracts the PTTButton object from app.js and drives it with a fake STT and ModeManager — no
// browser, no deps. The provider side (opening the mic for a no-call press) is covered by
// deepgram-streaming-ptt.test.js.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = process.env.APP_SRC || path.join(__dirname, '..', '..', 'src', 'app.js');

function load() {
  const src = fs.readFileSync(APP, 'utf8');
  const start = src.indexOf('window.PTTButton = {');
  const end = src.indexOf('window.PTTButton.init();', start);
  if (start < 0 || end < 0) throw new Error('PTTButton block not found in ' + APP);

  const timers = [];
  const origResult = () => {};
  const origListenFinal = () => {};
  const stt = {
    calls: [], isListening: false, _pttHolding: false, _micMuted: false,
    onResult: origResult, onListenFinal: origListenFinal,
    pttActivate() { this.calls.push('pttActivate'); this._pttHolding = true; this._micMuted = false; },
    pttRelease() { this.calls.push('pttRelease'); this._pttHolding = false; },
    pttMute() { this.calls.push('pttMute'); this._micMuted = true; },
    pttUnmute() { this.calls.push('pttUnmute'); this._micMuted = false; },
    stop() { this.calls.push('stop'); this.isListening = false; this._micMuted = false; },
    resetProcessing() { this.calls.push('resetProcessing'); },
  };
  const clawdbotMode = { stt, _voiceActive: false, sent: [], sendMessage(t) { this.sent.push(t); } };
  const button = {
    classList: { add() {}, remove() {}, toggle() {} }, offsetHeight: 0,
    addEventListener() {}, setPointerCapture() {}, hasPointerCapture() { return true; },
  };
  const sandbox = {
    window: {},
    document: { getElementById: () => button },
    ModeManager: { clawdbotMode },
    console: { log() {}, warn() {}, error() {} },
    fetch: () => Promise.resolve(),
    convPath: (p) => '/api/conversation/' + p,
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: (id) => { if (id) timers[id - 1] = null; },
  };
  vm.createContext(sandbox);
  vm.runInContext(src.slice(start, end), sandbox, { filename: APP });
  const ptt = sandbox.window.PTTButton;
  const runTimers = () => { for (let i = 0; i < timers.length; i++) { const fn = timers[i]; timers[i] = null; if (fn) fn(); } };
  return { ptt, stt, cm: clawdbotMode, sandbox, runTimers, origResult, origListenFinal };
}

const cases = [];
const test = (name, fn) => cases.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test('no call, PTT mode on: the press sends its words and gives the mic back', () => {
  const { ptt, stt, cm, origResult, origListenFinal } = load();
  ptt._setPTT(true);
  ptt._activateMic();
  assert(stt.onResult !== origResult, 'the press did not take over the STT result callback');
  ptt._releaseMic();
  stt.onResult('turn on the porch light');   // what the provider does once the transcript is in
  assert(cm.sent.join('|') === 'turn on the porch light', `sent ${JSON.stringify(cm.sent)}`);
  assert(stt.onResult === origResult && stt.onListenFinal === origListenFinal, 'callbacks were not restored');
  assert(stt.calls.includes('stop'), 'the mic was not released');
  assert(stt.calls[stt.calls.length - 1] === 'pttMute' && stt._micMuted, 'PTT mode was not re-applied after releasing the mic');
});

test('call running: PTT leaves the call\'s callbacks alone', () => {
  const { ptt, stt, cm, origResult } = load();
  cm._voiceActive = true;
  ptt._setPTT(true);
  ptt._activateMic();
  assert(stt.onResult === origResult, 'the press took over the call\'s STT callback');
  ptt._releaseMic();
  assert(!stt.calls.includes('stop') && cm.sent.length === 0, `stop=${stt.calls.includes('stop')} sent=${JSON.stringify(cm.sent)}`);
});

test('no call, nothing said: the mic is still given back', () => {
  const { ptt, stt, cm, runTimers, origResult } = load();
  ptt._setPTT(true);
  ptt._activateMic();
  ptt._releaseMic();
  runTimers();
  assert(stt.onResult === origResult, 'callbacks were not restored');
  assert(stt.calls.includes('stop') && cm.sent.length === 0, `stop=${stt.calls.includes('stop')} sent=${JSON.stringify(cm.sent)}`);
});

test('Listen mode: PTT does not take the mic from it', () => {
  const { ptt, stt, sandbox, origResult } = load();
  sandbox.window.ModeSelector = { currentMode: 'listen' };
  ptt._setPTT(true);
  ptt._activateMic();
  assert(stt.onResult === origResult, 'the press took over Listen mode\'s callback');
});

test('hotkey press with PTT mode off and no call: sends, and does not switch PTT mode on', () => {
  const { ptt, stt, cm } = load();
  ptt._activateMic();                          // PTTHotkey._press
  ptt._releaseMic();
  stt.onResult('what time is it');
  assert(cm.sent.join('|') === 'what time is it', `sent ${JSON.stringify(cm.sent)}`);
  assert(stt.calls.includes('stop') && !stt._micMuted, `stop=${stt.calls.includes('stop')} micMuted=${stt._micMuted}`);
});

test('quick re-press with no call: both utterances are sent', () => {
  const { ptt, stt, cm, origResult } = load();
  ptt._setPTT(true);
  ptt._activateMic();
  ptt._releaseMic();
  ptt._activateMic();                          // pressed again before the first transcript arrived
  stt.onResult('first');
  assert(stt.onResult !== origResult && !stt.calls.includes('stop'), 'the first result ended the second press');
  ptt._releaseMic();
  stt.onResult('second');
  assert(cm.sent.join('|') === 'first|second', `sent ${JSON.stringify(cm.sent)}`);
  assert(stt.calls.includes('stop'), 'the mic was not released after the second press');
});

let failed = 0;
for (const c of cases) {
  try { c.fn(); console.log(`PASS  ${c.name}`); }
  catch (e) { failed++; console.log(`FAIL  ${c.name}\n      ${e.message}`); }
}
console.log(`\n${cases.length - failed}/${cases.length} passed  (${APP})`);
process.exit(failed ? 1 : 0);
