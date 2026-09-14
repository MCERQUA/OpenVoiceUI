// deepgram-streaming-ptt.test.js — push-to-talk regression tests for src/providers/DeepgramStreamingSTT.js
//
//   node tests/js/deepgram-streaming-ptt.test.js
//   STT_SRC=/path/to/DeepgramStreamingSTT.js node tests/js/deepgram-streaming-ptt.test.js   # another copy
//
// Evaluates the provider in node with a fake WebSocket, mic stream and AudioContext — no browser,
// no deps. The onResult gate below is ClawdbotMode's (app.js: `if (this.stt._micMuted) return;`),
// because that gate is where PTT transcripts used to be dropped.
//
// Each case is a failure measured in real Chromium against live Deepgram (2026-09-14):
//   - PTT mode switched on before the call: start() refused, no mic stream, every hold sent zero audio
//   - a final that only exists after CloseStream raced a fixed 300 ms timer and was dropped
//   - a hotkey press during hands-free listening left the mic muted for the rest of the call
//   - a pause mid-hold (UtteranceEnd) sent a partial transcript while the button was still down
//   - a quick re-press closed the flushing socket early and lost the first utterance
//   - with no call running, a PTT press opened no mic at all (PTT must work call or not)
//
// The WebSpeech fallback stub THROWS. Any error inside the provider (a missing sandbox global, a
// bad socket call) makes it fall back to WebSpeech silently, and every PTT call would then be
// testing the stub instead of the provider.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = process.env.STT_SRC
  || path.join(__dirname, '..', '..', 'src', 'providers', 'DeepgramStreamingSTT.js');

let unhandled = null;
process.on('unhandledRejection', (e) => { unhandled = e; });

class FakeWebSocket {
  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    this.readyState = FakeWebSocket.CONNECTING;
    this.binaryBytes = 0;
    this.textSent = [];
    this._listeners = { close: [] };
    FakeWebSocket.instances.push(this);
  }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  send(d) {
    if (this.readyState !== FakeWebSocket.OPEN) throw new Error('send on non-open socket');
    if (typeof d === 'string') this.textSent.push(JSON.parse(d).type);
    else this.binaryBytes += d.byteLength;
  }
  close() {
    if (this.readyState >= FakeWebSocket.CLOSING) return;
    this.readyState = FakeWebSocket.CLOSING;
    setTimeout(() => this.serverClose(1005), 0);
  }
  // --- test controls: what Deepgram does ---
  open() { this.readyState = FakeWebSocket.OPEN; if (this.onopen) this.onopen({}); }
  final(transcript) {
    this.onmessage({ data: JSON.stringify({
      type: 'Results', is_final: true, speech_final: true,
      channel: { alternatives: [{ transcript }] },
    }) });
  }
  utteranceEnd() { this.onmessage({ data: JSON.stringify({ type: 'UtteranceEnd' }) }); }
  serverClose(code = 1000) {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    const ev = { code };
    if (this.onclose) this.onclose(ev);           // handler attribute was registered first
    for (const fn of this._listeners.close.splice(0)) fn(ev);
  }
}
Object.assign(FakeWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3, instances: [] });

function load() {
  const code = fs.readFileSync(SRC, 'utf8')
    .replace(/^import .*$/m, '')
    .replace(/^export \{[^}]*\};?\s*$/m, '')
    + '\nglobalThis.DeepgramStreamingSTT = DeepgramStreamingSTT;';
  const stats = { getUserMedia: 0, tokenFetches: 0 };
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    setTimeout, clearTimeout, setInterval, clearInterval, URLSearchParams,
    WebSocket: FakeWebSocket,
    window: { location: { origin: 'http://test' } },
    navigator: { mediaDevices: { getUserMedia: async () => {
      stats.getUserMedia++;
      const stream = { active: true, getTracks: () => [{ stop() { stream.active = false; } }] };
      return stream;
    } } },
    fetch: async (url) => {
      if (String(url).includes('deepgram/token')) stats.tokenFetches++;
      return { ok: true, json: async () => ({ token: 'test-token' }) };
    },
    AudioContext: class {
      constructor() { this.state = 'running'; this.destination = {}; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
    },
    WebSpeechSTT: class {
      constructor() { throw new Error('provider fell back to WebSpeech — an error inside it was swallowed'); }
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: SRC });
  FakeWebSocket.instances = [];
  const stt = new sandbox.DeepgramStreamingSTT({ serverUrl: 'http://test' });
  stt.accumulationDelayMs = 30;
  const results = [];
  stt.onResult = (t) => { if (stt._micMuted) return; results.push(t); };
  return { stt, results, stats };
}

const tick = (ms = 0) => new Promise(r => setTimeout(r, ms));
const latest = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
const totalBinary = () => FakeWebSocket.instances.reduce((n, w) => n + w.binaryBytes, 0);
// Push one 4096-sample frame through the provider's real ScriptProcessor handler.
function pumpAudio(stt) {
  const node = stt._processorNode;
  if (node && node.onaudioprocess) node.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(4096).fill(0.1) } });
}
async function startCall(stt) {
  const p = stt.start();
  await tick(); await tick();
  const ws = latest();
  if (ws && ws.readyState === FakeWebSocket.CONNECTING) ws.open();
  const ok = await p;
  if (!ok || stt._usingFallback) throw new Error(`start() did not bring up Deepgram (ok=${ok}, fallback=${stt._usingFallback})`);
  return ok;
}
async function openNewSocket(before) {
  for (let i = 0; i < 10 && FakeWebSocket.instances.length === before; i++) await tick();
  const ws = latest();
  if (FakeWebSocket.instances.length > before && ws.readyState === FakeWebSocket.CONNECTING) ws.open();
  await tick();
  return FakeWebSocket.instances.length > before ? ws : null;
}

const cases = [];
const test = (name, fn) => cases.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test('PTT mode on before the call: a hold streams audio and delivers the transcript', async () => {
  const { stt, results } = load();
  stt.pttMute();                        // PTT button tapped on before starting the call
  await startCall(stt);
  pumpAudio(stt);
  assert(totalBinary() === 0, 'audio streamed while idle in PTT mode');
  stt.pttActivate();
  await openNewSocket(FakeWebSocket.instances.length);
  pumpAudio(stt);
  assert(totalBinary() > 0, 'no audio reached Deepgram during the hold');
  latest().final('turn on the lights');
  stt.pttRelease();
  latest().serverClose(1000);
  await tick();
  assert(results.join('|') === 'turn on the lights', `delivered ${JSON.stringify(results)}`);
  const sent = totalBinary();
  pumpAudio(stt);
  assert(totalBinary() === sent, 'mic left open after the release in PTT mode');
});

test('a final that arrives only after CloseStream is delivered', async () => {
  const { stt, results } = load();
  await startCall(stt);
  stt.pttMute();
  stt.pttActivate();
  const ws = latest();
  stt.pttRelease();
  assert(ws.textSent.includes('CloseStream'), 'CloseStream was not sent on release');
  await tick(400);                      // longer than the old fixed 300 ms wait
  ws.final('short press');
  ws.serverClose(1000);
  await tick();
  assert(results.join('|') === 'short press', `delivered ${JSON.stringify(results)}`);
});

test('a hotkey press during hands-free listening returns to hands-free', async () => {
  const { stt, results } = load();
  await startCall(stt);
  stt.pttActivate();                    // PTTHotkey._press with PTT mode off
  latest().final('hello there');
  const before = FakeWebSocket.instances.length;
  stt.pttRelease();
  latest().serverClose(1000);
  await tick();
  assert(results.join('|') === 'hello there', `delivered ${JSON.stringify(results)}`);
  stt.resume();                         // the app resumes STT after the reply
  await openNewSocket(before);
  const sent = totalBinary();
  pumpAudio(stt);
  assert(!stt._micMuted && totalBinary() > sent, 'mic stayed muted after the hotkey press');
});

test('a pause mid-hold does not send while the button is still down', async () => {
  const { stt, results } = load();
  await startCall(stt);
  stt.pttMute();
  stt.pttActivate();
  const ws = latest();
  ws.final('first part');
  ws.utteranceEnd();
  await tick(stt.accumulationDelayMs + 60);
  assert(results.length === 0, `sent mid-hold: ${JSON.stringify(results)}`);
  ws.final('second part');
  stt.pttRelease();
  ws.serverClose(1000);
  await tick();
  assert(results.join('|') === 'first part second part', `delivered ${JSON.stringify(results)}`);
});

test('a quick re-press keeps the first utterance', async () => {
  const { stt, results } = load();
  await startCall(stt);
  stt.pttMute();
  stt.pttActivate();
  const ws1 = latest();
  stt.pttRelease();                     // CloseStream on ws1; its final is still on the way
  const before = FakeWebSocket.instances.length;
  stt.pttActivate();                    // pressed again straight away
  await tick();
  if (ws1.readyState === FakeWebSocket.OPEN) { ws1.final('first'); ws1.serverClose(1000); }
  const ws2 = await openNewSocket(before);
  assert(ws2, 'the re-press did not open a fresh socket');
  ws2.final('second');
  stt.pttRelease();
  ws2.serverClose(1000);
  await tick();
  const all = results.join(' ');
  assert(all.includes('first') && all.includes('second'), `delivered ${JSON.stringify(results)}`);
});

test('PTT with no call running opens the mic for that press and delivers the transcript', async () => {
  const { stt, results, stats } = load();
  stt.pttMute();                        // PTT mode on, no call started
  stt.pttActivate();
  await openNewSocket(0);
  assert(stats.getUserMedia === 1, `getUserMedia called ${stats.getUserMedia} time(s)`);
  pumpAudio(stt);
  assert(totalBinary() > 0, 'no audio reached Deepgram during a no-call hold');
  latest().final('what is on my calendar');
  stt.pttRelease();
  latest().serverClose(1000);
  await tick(20);
  assert(results.join('|') === 'what is on my calendar', `delivered ${JSON.stringify(results)}`);
  assert(!stt.isListening && FakeWebSocket.instances.length === 1,
    `after a no-call press: listening=${stt.isListening}, sockets opened=${FakeWebSocket.instances.length}`);
});

test('control: hands-free listening still delivers on end of utterance', async () => {
  const { stt, results } = load();
  await startCall(stt);
  latest().final('hands free works');
  latest().utteranceEnd();
  await tick(stt.accumulationDelayMs + 60);
  assert(results.join('|') === 'hands free works', `delivered ${JSON.stringify(results)}`);
});

(async () => {
  let failed = 0;
  for (const c of cases) {
    unhandled = null;
    try {
      await c.fn();
      await tick(10);
      if (unhandled) throw new Error(`unhandled rejection inside the provider: ${unhandled.message}`);
      console.log(`PASS  ${c.name}`);
    } catch (e) {
      failed++;
      console.log(`FAIL  ${c.name}\n      ${e.message}`);
    }
  }
  console.log(`\n${cases.length - failed}/${cases.length} passed  (${SRC})`);
  process.exit(failed ? 1 : 0);
})();
