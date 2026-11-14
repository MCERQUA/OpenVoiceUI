// hold-speech-while-thinking.test.js — words spoken while the agent is THINKING are not lost
//
//   node tests/js/hold-speech-while-thinking.test.js
//   APP_SRC=/path/to/app.js STT_SRC=/path/to/WebSpeechSTT.js node tests/js/hold-speech-while-thinking.test.js
//
// Measured 2026-11-14 on a comms-brain tenant (replies take 15-30 s to start): everything the
// owner said while a reply was in flight vanished. Cause: WebSpeechSTT sets isProcessing=true when
// it hands a transcript to ClawdbotMode, and its onresult handler returns early while isProcessing
// is set; nothing clears it until the reply's TTS has played. The server never saw those words.
//
// Profile opt-in conversation.hold_speech_while_thinking=true: the mic stays open while the agent
// thinks; what is heard is HELD and sent as the next turn once the reply is completely over
// (stream closed, nothing playing). Nothing is sent mid-turn. TTS still mutes the mic (echo).
//
// Runs the REAL WebSpeechSTT (fake SpeechRecognition) wired to ClawdbotMode's real stt.onResult
// handler, _holdSpeechEnabled/_flushHeldSpeech and ModeManager's real onListening callback, all
// sliced out of src/app.js. On an app.js without the change the methods are simply absent and the
// first case FAILS on behaviour (the words are never sent), which is the regression this guards.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = process.env.APP_SRC || path.join(__dirname, '..', '..', 'src', 'app.js');
const STT = process.env.STT_SRC || path.join(__dirname, '..', '..', 'src', 'providers', 'WebSpeechSTT.js');

// ---- slice helpers --------------------------------------------------------------------------
function braceBlock(src, from) {
  // from = index of the opening '{'; returns index just past its matching '}'
  let depth = 0;
  for (let i = from; i < src.length; i++) {
    const c = src[i];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return i + 1; }
    else if (c === '"' || c === "'" || c === '`') {
      const q = c;
      for (i++; i < src.length && src[i] !== q; i++) if (src[i] === '\\') i++;
    } else if (c === '/' && src[i + 1] === '/') {
      while (i < src.length && src[i] !== '\n') i++;
    }
  }
  throw new Error('unbalanced block at ' + from);
}
function sliceArrow(src, marker, after = 0) {
  const at = src.indexOf(marker, after);
  if (at < 0) throw new Error('not found in app.js: ' + marker);
  const open = src.indexOf('{', at + marker.length - 1);
  return src.slice(open, braceBlock(src, open));     // "{ ...body... }"
}
function sliceMethod(src, name) {
  const re = new RegExp('\\n\\s+' + name + '\\(\\)\\s*\\{');
  const m = re.exec(src);
  if (!m) return null;                                // absent on an app.js without the change
  const open = m.index + m[0].length - 1;
  return src.slice(open, braceBlock(src, open));
}

const APP_SRC = fs.readFileSync(APP, 'utf8');
const ON_RESULT = sliceArrow(APP_SRC, 'this.stt.onResult = (transcript) => {');
const HOLD_ENABLED = sliceMethod(APP_SRC, '_holdSpeechEnabled');
const FLUSH = sliceMethod(APP_SRC, '_flushHeldSpeech');
const CB_AT = APP_SRC.indexOf('this.clawdbotMode.setCallbacks({');
if (CB_AT < 0) throw new Error('ModeManager clawdbotMode.setCallbacks not found');
const ON_LISTENING = sliceArrow(APP_SRC, 'onListening: () => {', CB_AT);

// ---- world ----------------------------------------------------------------------------------
function world({ hold }) {
  const timers = [];
  let now = 1_000_000;
  const setTimeout_ = (fn, ms = 0) => { timers.push({ fn, at: now + ms }); return timers.length; };
  const clearTimeout_ = (id) => { if (id && timers[id - 1]) timers[id - 1].fn = null; };
  const advance = (ms) => {
    const until = now + ms;
    for (;;) {
      let next = null;
      for (const t of timers) if (t.fn && t.at <= until && (!next || t.at < next.at)) next = t;
      if (!next) break;
      now = next.at; const fn = next.fn; next.fn = null; fn();
    }
    now = until;
  };

  class FakeRecognition {
    constructor() { this.running = false; FakeRecognition.last = this; }
    start() { if (this.running) throw new Error('already started'); this.running = true; }
    stop() { this.running = false; if (this.onend) this.onend(); }
    abort() { this.running = false; if (this.onend) this.onend(); }
    say(text) {   // one Chrome final result
      if (!this.running) return;   // a stopped engine hears nothing
      const r = [{ transcript: text }]; r.isFinal = true;
      this.onresult({ resultIndex: 0, results: [r] });
    }
  }

  const console_ = { log() {}, warn() {}, error() {} };
  const sandbox = {
    console: console_, setTimeout: setTimeout_, clearTimeout: clearTimeout_,
    Date: { now: () => now },
    navigator: { userAgent: 'node', mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
    fetch: () => Promise.resolve(),
  };
  sandbox.window = { SpeechRecognition: FakeRecognition, _activeProfileData: { conversation: { hold_speech_while_thinking: hold } } };
  vm.createContext(sandbox);
  const sttCode = fs.readFileSync(STT, 'utf8').replace(/^export \{[^}]*\};?\s*$/m, '') + '\nglobalThis.WebSpeechSTT = WebSpeechSTT;';
  vm.runInContext(sttCode, sandbox, { filename: STT });

  const stt = new sandbox.WebSpeechSTT();
  stt.silenceDelayMs = 1500;

  // ClawdbotMode stand-in carrying the REAL handler + methods from app.js
  const cm = {
    stt, sent: [], _voiceActive: true, _ttsPlaying: false, _fetchAbortController: null,
    _ttsEchoCooldownUntil: 0, isPlaying: false, audioQueue: [], _heldSpeech: [],
    callbacks: {},
    sendMessage(text) { this.sent.push(text); this._fetchAbortController = { open: true }; },
  };
  const ActionConsole = { addEntry() {} };
  const stub = new Proxy({}, { get: () => () => {} });
  const doc = { getElementById: () => ({ style: {}, classList: { add() {}, remove() {} } }) };
  Object.assign(sandbox, { cm, ActionConsole, StatusModule: stub, FaceModule: stub, WaveformModule: stub,
    MusicModule: stub, AgentActivityChip: stub, document: doc, ModeManager: { clawdbotMode: cm } });
  if (HOLD_ENABLED) cm._holdSpeechEnabled = vm.runInContext(`(function () ${HOLD_ENABLED})`, sandbox);
  else cm._holdSpeechEnabled = () => false;
  if (FLUSH) cm._flushHeldSpeech = vm.runInContext(`(function () ${FLUSH})`, sandbox);
  stt.onResult = vm.runInContext(`(function (transcript) ${ON_RESULT})`, sandbox).bind(cm);
  // ModeManager's onListening uses `this.clawdbotMode`
  const mm = { clawdbotMode: cm };
  const onListening = vm.runInContext(`(function () ${ON_LISTENING})`, sandbox).bind(mm);
  cm.callbacks.onListening = onListening;

  return {
    stt, cm, advance,
    async startCall() { await stt.start(); },
    rec: () => FakeRecognition.last,
    // the reply's TTS starts (ModeManager onSpeaking does exactly this to the STT)
    ttsStarts() { cm._ttsPlaying = true; stt.mute(); },
    // the reply is over: stream closed, audio drained, onListening fires
    replyEnds() { cm._fetchAbortController = null; cm.isPlaying = false; onListening(); advance(400); },
  };
}

const cases = [];
const test = (name, fn) => cases.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test('opt-in: words said while the agent thinks are sent once the reply is over', async () => {
  const w = world({ hold: true });
  await w.startCall();
  w.rec().say('use the first design'); w.advance(1600);
  assert(w.cm.sent.join('|') === 'use the first design', `first turn: ${JSON.stringify(w.cm.sent)}`);
  // thinking: the reply has not started speaking yet
  w.advance(5000);
  w.rec().say('and make the header blue'); w.advance(1600);
  assert(w.cm.sent.length === 1, `sent mid-turn: ${JSON.stringify(w.cm.sent)}`);
  w.ttsStarts(); w.advance(3000);
  w.replyEnds();
  assert(w.cm.sent.join('|') === 'use the first design|and make the header blue',
    `the words spoken during thinking were never sent: ${JSON.stringify(w.cm.sent)}`);
});

test('opt-in: two utterances during one thinking turn go as one message', async () => {
  const w = world({ hold: true });
  await w.startCall();
  w.rec().say('check the domain'); w.advance(1600);
  w.rec().say('the arizona one'); w.advance(1600);
  w.advance(2000);
  w.rec().say('not the dot net'); w.advance(1600);
  w.ttsStarts(); w.replyEnds();
  assert(w.cm.sent.join('|') === 'check the domain|the arizona one not the dot net', JSON.stringify(w.cm.sent));
});

test('opt-in: still held when the mic comes back mid-turn (interim status line); sent after', async () => {
  const w = world({ hold: true });
  await w.startCall();
  w.rec().say('build the page'); w.advance(1600);
  w.rec().say('with the logo'); w.advance(1600);
  // an interim spoken status plays and ends while the stream is still open
  w.ttsStarts(); w.cm.callbacks.onListening(); w.advance(400);
  assert(w.cm.sent.length === 1, `sent while the turn was still open: ${JSON.stringify(w.cm.sent)}`);
  w.ttsStarts(); w.replyEnds();
  assert(w.cm.sent.join('|') === 'build the page|with the logo', JSON.stringify(w.cm.sent));
});

test('opt-in: speech during the reply\'s own TTS is still ignored (echo guard intact)', async () => {
  const w = world({ hold: true });
  await w.startCall();
  w.rec().say('what is on my calendar'); w.advance(1600);
  w.ttsStarts();
  w.rec().say('you have two meetings today'); w.advance(1600);   // the agent's own voice
  w.replyEnds();
  assert(w.cm.sent.join('|') === 'what is on my calendar', JSON.stringify(w.cm.sent));
});

test('opt-in: nothing held is sent after hang-up', async () => {
  const w = world({ hold: true });
  await w.startCall();
  w.rec().say('call the supplier'); w.advance(1600);
  w.rec().say('actually wait'); w.advance(1600);
  w.cm._voiceActive = false;   // hang-up while one utterance is held
  w.replyEnds();
  assert(w.cm.sent.join('|') === 'call the supplier', JSON.stringify(w.cm.sent));
});

test('control, flag off: today\'s behaviour is unchanged (speech while thinking is dropped)', async () => {
  const w = world({ hold: false });
  await w.startCall();
  w.rec().say('use the first design'); w.advance(1600);
  w.rec().say('and make the header blue'); w.advance(1600);
  w.ttsStarts(); w.replyEnds();
  assert(w.cm.sent.join('|') === 'use the first design', JSON.stringify(w.cm.sent));
  assert(w.stt.isProcessing === false, 'mic was not given back after the reply');
});

(async () => {
  let failed = 0;
  for (const c of cases) {
    try { await c.fn(); console.log('ok   ' + c.name); }
    catch (e) { failed++; console.log('FAIL ' + c.name + '\n     ' + e.message); }
  }
  console.log(`${cases.length - failed}/${cases.length} passed`);
  process.exit(failed ? 1 : 0);
})();
