// upload-body-ios.test.js — uploads must not go out empty on Safari/iOS 26.5+
//
//   node tests/js/upload-body-ios.test.js
//   SW_SRC=/path/to/sw.js APP_SRC=/path/to/app.js PAGES_DIR=/path/to/default-pages \
//     node tests/js/upload-body-ios.test.js                                  # another copy
//
// Measured on tenant-i 2026-09-30: 19 POST /api/upload from an iPhone (Safari 26.6.2) reached Flask
// as multipart/form-data with content_length=0, every one a 400 "No file provided". Two causes
// documented upstream, both covered here:
//
//   1. static/sw.js re-issued EVERY request through respondWith(fetch(event.request)), and a
//      multipart POST re-fetched that way loses its body on Safari 26.5+ (WebKit bug 319396,
//      duplicate of 319985). Non-GET requests must not be intercepted at all.
//   2. A disk-backed File from the picker can go out as Content-Length: 0 even without an
//      intercepting worker (WebKit bug 319985). TranscriptPanel.uploadFile must send bytes the
//      page has read (file.arrayBuffer()), never the live File. The bulk path had no snapshot.
//   3. The default pages that upload a picked file to /api/upload need the same fix as 2:
//      bulk-image-uploader, website-setup, grid-creator, desktop (wallpaper). image-splitter is
//      not here on purpose: it uploads canvas-encoded blobs, never the picker's File.
//   4. bulk-image-uploader goes one step further (2026-11-14): even a Blob of read bytes inside
//      FormData went out EMPTY from iPhone WebKit, so it builds the multipart body itself as one
//      Uint8Array. Its check parses that body instead of a FormData. Files over CHUNK_OVER go up
//      as slices to /api/upload/chunk (Cloudflare refuses any request over ~100 MB), after ONE
//      /api/upload/chunk/start that carries the page's upload grant and returns the signed id
//      every slice must carry (review of PR #586: no anonymous writes, size fixed at start).
//
// No browser, no deps: sw.js runs in a vm with a fake `self`; the TranscriptPanel object is cut
// out of app.js, and each page's upload functions are cut out of its HTML, then driven with a
// fake fetch / XHR and a FormData that records what was appended.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..', '..');
const SW = process.env.SW_SRC || path.join(ROOT, 'static', 'sw.js');
const APP = process.env.APP_SRC || path.join(ROOT, 'src', 'app.js');
const PAGES = process.env.PAGES_DIR || path.join(ROOT, 'default-pages');

let failed = 0;
async function check(name, fn) {
  try { await fn(); console.log('PASS ' + name); }
  catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + (e && e.message)); }
}

// ---------- service worker ----------
function loadSW() {
  const listeners = {};
  const fetched = [];
  const self = {
    addEventListener(type, fn) { listeners[type] = fn; },
    skipWaiting() {},
    clients: { claim() { return Promise.resolve(); } },
  };
  const sandbox = {
    self,
    console: { log() {}, warn() {}, error() {} },
    fetch(req) { fetched.push(req); return Promise.resolve({ ok: true }); },
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(SW, 'utf8'), sandbox, { filename: SW });
  return { listeners, fetched };
}

function fireFetch(sw, method) {
  const request = { method, url: 'https://tenant-i.jam-bot.com/api/upload' };
  const event = { request, responded: 0, respondWith() { this.responded++; } };
  sw.listeners.fetch(event);
  return event;
}

// ---------- TranscriptPanel.uploadFile ----------
function loadPanel() {
  const src = fs.readFileSync(APP, 'utf8');
  const start = src.indexOf('window.TranscriptPanel = {');
  const end = src.indexOf('window.ActionConsole = {', start);
  if (start < 0 || end < 0) throw new Error('TranscriptPanel block not found in ' + APP);

  const posts = [];
  class RecordingFormData {
    constructor() { this.entries = []; }
    append(name, value, filename) { this.entries.push({ name, value, filename }); }
  }
  const sandbox = {
    window: { CONFIG: { serverUrl: '' } },
    Blob,
    FormData: RecordingFormData,
    console: { log() {}, warn() {}, error() {} },
    fetch(url, opts) {
      posts.push({ url, opts });
      return Promise.resolve({ ok: true, json: async () => ({ path: 'uploads/x.jpg', type: 'image' }) });
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(src.slice(start, end), sandbox, { filename: APP });
  return { panel: sandbox.window.TranscriptPanel, posts };
}

// A stand-in for the picker's disk-backed File: the page can read its bytes, and the test can
// tell whether the object itself (rather than a copy of its bytes) was handed to FormData.
function pickerFile(bytes, name, type) {
  const reads = { n: 0 };
  return {
    file: {
      name, type, size: bytes.length,
      arrayBuffer: async () => { reads.n++; return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length); },
    },
    reads,
  };
}

async function sentBytes(entry) {
  if (entry.bytes) return entry.bytes;
  return Buffer.from(await entry.value.arrayBuffer());
}

// Parse a multipart/form-data body the page built by hand (a Uint8Array): returns the parts as
// { name, filename, bytes } in order.
function parseMultipart(u8) {
  const buf = Buffer.from(u8);
  const firstNl = buf.indexOf('\r\n');
  const delim = buf.slice(0, firstNl);           // --boundary
  const parts = [];
  let pos = 0;
  for (;;) {
    const start = buf.indexOf(delim, pos);
    if (start < 0) break;
    const after = start + delim.length;
    if (buf.slice(after, after + 2).toString() === '--') break;
    const hdrEnd = buf.indexOf('\r\n\r\n', after);
    const next = buf.indexOf(Buffer.concat([Buffer.from('\r\n'), delim]), hdrEnd);
    const head = buf.slice(after + 2, hdrEnd).toString();
    const name = (/name="([^"]*)"/.exec(head) || [])[1];
    const filename = (/filename="([^"]*)"/.exec(head) || [])[1];
    parts.push({ name, filename, bytes: buf.slice(hdrEnd + 4, next) });
    pos = next + 2;
  }
  return parts;
}

// The 'file' entry of one recorded POST, whichever way the page built the body.
function fileEntry(post) {
  if (ArrayBuffer.isView(post.body)) {   // built inside the vm: another realm, so no instanceof
    const part = parseMultipart(post.body).find((e) => e.name === 'file');
    return part && { name: 'file', filename: part.filename, bytes: part.bytes, value: post.body, raw: true };
  }
  return post.body.entries.find((e) => e.name === 'file');
}

// ---------- default pages ----------
// Cut one named function out of a page's inline script: brace matching that skips strings,
// template literals (including ${...}), and comments. The upload functions hold no regex
// literals, which is the one thing this does not parse.
function extractFunction(src, name) {
  const m = new RegExp('(async\\s+)?function\\s+' + name + '\\s*\\(').exec(src);
  if (!m) return null;
  const open = src.indexOf('{', src.indexOf(')', m.index));
  const stack = [{ tmpl: false, depth: 0 }];
  for (let j = open; j < src.length; j++) {
    const c = src[j];
    const top = stack[stack.length - 1];
    if (top.tmpl) {
      if (c === '\\') { j++; continue; }
      if (c === '`') { stack.pop(); continue; }
      if (c === '$' && src[j + 1] === '{') { stack.push({ tmpl: false, depth: 1 }); j++; }
      continue;
    }
    if (c === '/' && src[j + 1] === '/') { j = src.indexOf('\n', j); if (j < 0) break; continue; }
    if (c === '/' && src[j + 1] === '*') { j = src.indexOf('*/', j + 2) + 1; continue; }
    if (c === '"' || c === "'") {
      for (j++; j < src.length && src[j] !== c; j++) { if (src[j] === '\\') j++; }
      continue;
    }
    if (c === '`') { stack.push({ tmpl: true }); continue; }
    if (c === '{') top.depth++;
    else if (c === '}' && --top.depth === 0) {
      if (stack.length === 1) return src.slice(m.index, j + 1);
      stack.pop();
    }
  }
  throw new Error('unbalanced function ' + name);
}

function pageSandbox(file, names, prelude, stubs) {
  const src = fs.readFileSync(path.join(PAGES, file), 'utf8');
  const fns = names.map((n) => {
    // multipartBody holds a regex literal (which extractFunction does not parse): it is a
    // top-level function, so it ends at the first line that is just "}".
    if (n === 'multipartBody') {
      const at = src.indexOf('function multipartBody(');
      if (at < 0) throw new Error(n + ' not found in ' + file);
      return src.slice(at, src.indexOf('\n}\n', at) + 2);
    }
    const f = extractFunction(src, n);
    if (!f && n !== 'readIntoMemory') throw new Error(n + ' not found in ' + file);
    return f;
  }).filter(Boolean);
  const posts = [];
  class RecordingFormData {
    constructor() { this.entries = []; }
    append(name, value, filename) { this.entries.push({ name, value, filename }); }
  }
  const sandbox = Object.assign({
    window: {}, Blob, FormData: RecordingFormData, setTimeout, TextEncoder, crypto: globalThis.crypto,
    console: { log() {}, warn() {}, error() {} },
    fetch(url, opts) {
      posts.push({ url, body: opts && opts.body });
      return Promise.resolve({ ok: true, json: async () => ({ url: '/uploads/x.jpg', filename: 'x.jpg' }) });
    },
  }, stubs(posts));
  vm.createContext(sandbox);
  vm.runInContext(prelude + '\n' + fns.join('\n'), sandbox, { filename: file });
  return { sandbox, posts };
}

const flush = () => new Promise((r) => setTimeout(r, 5));

// Each page: how to start an upload of one picked file, and where its errors land.
const PAGE_CASES = [
  {
    page: 'bulk-image-uploader.html',
    make() {
      const errors = [];
      class FakeXHR {
        constructor() { this.upload = {}; }
        open() {} setRequestHeader() {} abort() {}
        send(fd) { this._posts.push({ url: '/api/upload', body: fd }); }
      }
      const { sandbox, posts } = pageSandbox('bulk-image-uploader.html', ['readIntoMemory', 'multipartBody', 'upload'],
        'var ups=[], activeUploads=1, files=[], CHUNK_OVER=90*1024*1024, CHUNK_SIZE=8*1024*1024;', (posts) => {
          FakeXHR.prototype._posts = posts;
          return {
            XMLHttpRequest: FakeXHR,
            setQI: (id, pct, st, msg) => { if (st === 'err') errors.push(String(msg)); },
            drainQueue() {}, enqueue() {}, renderThrottled() {}, updateStorThrottled() {},
            ext: () => 'jpeg', cat: () => 'img',
          };
        });
      return { posts, errors, send: (f) => { sandbox.upload(f, 7, 0); } };
    },
  },
  {
    page: 'website-setup.html (single)',
    make() {
      const errors = [];
      const { sandbox, posts } = pageSandbox('website-setup.html',
        ['readIntoMemory', 'authUpload', 'handleUpload'],
        'var formData={images:{logo:{},hero:{},team:{},gallery:[]},branding:{}};', () => ({
          _authHeaders: () => ({}), showUploadPreview() {}, scheduleSave() {}, renderGallery() {},
          toast: (m) => { if (/fail|error/i.test(m)) errors.push(String(m)); },
        }));
      return { posts, errors, send: (f) => sandbox.handleUpload('logo', f) };
    },
  },
  {
    page: 'website-setup.html (gallery bulk)',
    make() {
      const errors = [];
      const { sandbox, posts } = pageSandbox('website-setup.html',
        ['readIntoMemory', 'authUpload', 'handleBulkUpload'],
        'var formData={images:{logo:{},hero:{},team:{},gallery:[]},branding:{}};', () => ({
          _authHeaders: () => ({}), scheduleSave() {}, renderGallery() {},
          toast: (m) => { if (/fail|error/i.test(m)) errors.push(String(m)); },
        }));
      return { posts, errors, send: (f) => sandbox.handleBulkUpload([f]) };
    },
  },
  {
    page: 'grid-creator.html',
    make() {
      const els = {};
      const $ = (id) => (els[id] = els[id] || { textContent: '', value: 'C:\\fakepath\\x' });
      const { sandbox, posts } = pageSandbox('grid-creator.html', ['readIntoMemory', 'uploadRefs'], '',
        () => ({ $, addRef() {}, renderRefs() { $('refHint').textContent = ''; } }));
      const errors = { get length() { return $('refHint').textContent ? 1 : 0; },
                       join() { return $('refHint').textContent; } };
      return { posts, errors, send: (f) => sandbox.uploadRefs([f]) };
    },
  },
  {
    page: 'desktop.html (wallpaper, re-encode fell back to the picked File)',
    make() {
      const errors = [];
      const { sandbox, posts } = pageSandbox('desktop.html', ['readIntoMemory', 'uploadWallpaper'], '', () => ({
        optimizeWallpaperImage: async (f) => f,
        applyWallpaper() {}, saveState() {}, buildWallpaperSection() {},
        document: { querySelectorAll: () => [] },
        showConfirmModal: (m) => errors.push(String(m)),
      }));
      return { posts, errors, send: (f) => sandbox.uploadWallpaper(f) };
    },
  },
];

async function pageChecks() {
  for (const pc of PAGE_CASES) {
    await check(`page ${pc.page}: a picked File is sent as read bytes, original filename kept`, async () => {
      const h = pc.make();
      const bytes = Buffer.from('jpeg-bytes-IMG_1640');
      const { file, reads } = pickerFile(bytes, 'IMG_1640.jpeg', 'image/jpeg');
      await h.send(file); await flush();
      assert.strictEqual(h.posts.length, 1, 'expected one POST, got ' + h.posts.length);
      const entry = fileEntry(h.posts[0]);
      assert.ok(entry, 'no file field appended');
      assert.notStrictEqual(entry.value, file, 'the live File handle was appended');
      if (!entry.raw) assert.ok(entry.value instanceof Blob, 'appended value is not a Blob');
      assert.strictEqual(reads.n, 1, 'file bytes were not read in the page');
      assert.ok((await sentBytes(entry)).equals(bytes), 'sent bytes differ from the file');
      assert.strictEqual(entry.filename, 'IMG_1640.jpeg', 'original filename not kept');
    });

    await check(`page ${pc.page}: an unreadable file is refused with "could not be read", no POST`, async () => {
      const h = pc.make();
      const file = { name: 'IMG_9.jpeg', type: 'image/jpeg', size: 10,
                     arrayBuffer: async () => { throw new Error('NotReadableError'); } };
      await h.send(file); await flush();
      assert.strictEqual(h.posts.length, 0, 'an unreadable file was still POSTed');
      assert.ok(h.errors.length && /could not be read/.test(h.errors.join(' ')),
        'no "could not be read" error shown: ' + h.errors.join(' | '));
    });

    await check(`page ${pc.page}: a zero-byte read is refused with "came back empty", no POST`, async () => {
      const h = pc.make();
      const { file } = pickerFile(Buffer.alloc(0), 'IMG_0.jpeg', 'image/jpeg');
      await h.send(file); await flush();
      assert.strictEqual(h.posts.length, 0, 'an empty file was still POSTed');
      assert.ok(h.errors.length && /came back empty/.test(h.errors.join(' ')),
        'no "came back empty" error shown: ' + h.errors.join(' | '));
    });
  }
}

// bulk-image-uploader: a file over CHUNK_OVER is STARTED once (with the page's upload grant),
// then goes up as ordered slices to /api/upload/chunk carrying the server-issued id, and is only
// "Done" when the server answers with the finished file.
const SIGNED_ID = 'c1.0123456789abcdef01234567.250.9999999999.' + 'ab'.repeat(32);
async function chunkedCheck() {
  await check('page bulk-image-uploader.html: a file over CHUNK_OVER is started with the grant, then sent as ordered slices carrying the signed id', async () => {
    const posts = [], starts = [], states = [];
    const total = 250;
    const bytes = Buffer.from(Array.from({ length: total }, (_, i) => i % 251));
    class FakeXHR {
      constructor() { this.upload = {}; this._hdr = {}; }
      open(m, url) { this._url = url; } setRequestHeader(k, v) { this._hdr[k] = v; } abort() {}
      send(body) {
        if (this._url === '/api/upload/chunk/start') {
          assert.strictEqual(posts.length, 0, 'a slice went out before the upload was started');
          starts.push({ body: JSON.parse(body), hdr: this._hdr });
          this.status = 200;
          this.responseText = JSON.stringify({ ok: true, upload_id: SIGNED_ID, size: total });
          setTimeout(() => this.onload(), 0);
          return;
        }
        const parts = parseMultipart(body);
        const f = Object.fromEntries(parts.filter((p) => p.name !== 'file').map((p) => [p.name, p.bytes.toString()]));
        const piece = parts.find((p) => p.name === 'file').bytes;
        posts.push({ url: this._url, offset: Number(f.offset), size: Number(f.size), uid: f.upload_id, piece });
        const held = Number(f.offset) + piece.length;
        this.status = 200;
        this.responseText = JSON.stringify(held >= total
          ? { url: '/uploads/IMG_1__abc.mov', original_name: 'IMG_1.MOV', file_size: total }
          : { ok: true, received: held, size: total });
        setTimeout(() => this.onload(), 0);
      }
    }
    const { sandbox } = pageSandbox('bulk-image-uploader.html', ['readIntoMemory', 'multipartBody', 'uploadChunked', 'upload'],
      'var ups=[], activeUploads=1, files=[], CHUNK_OVER=100, CHUNK_SIZE=40;'
      + 'window._canvasUploadGrant="g1.9999999999.0123456789abcdef.' + 'cd'.repeat(32) + '";', () => ({
        XMLHttpRequest: FakeXHR,
        setQI: (id, pct, st, msg) => states.push(st + (msg ? ':' + msg : '')),
        drainQueue() {}, enqueue() {}, renderThrottled() {}, updateStorThrottled() {},
        ext: () => 'mov', cat: () => 'vid',
      }));
    const file = { name: 'IMG_1.MOV', type: 'video/quicktime', size: total,
                   slice: (a, b) => ({ arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset + a, bytes.byteOffset + b) }) };
    sandbox.upload(file, 3, 0);
    for (let i = 0; i < 50 && !states.includes('ok'); i++) await flush();
    assert.ok(posts.length === 7, 'expected 7 slices of <=40 bytes, got ' + posts.length);
    assert.ok(posts.every((p) => p.url === '/api/upload/chunk'), 'a slice went somewhere other than /api/upload/chunk');
    assert.strictEqual(starts.length, 1, 'expected exactly one start, got ' + starts.length);
    assert.deepStrictEqual([starts[0].body.name, starts[0].body.size], ['IMG_1.MOV', total], 'start did not declare name/size');
    assert.ok(/^g1\./.test(starts[0].body.grant), 'start did not carry the page upload grant');
    assert.ok(posts.every((p) => p.uid === SIGNED_ID), 'a slice did not carry the server-issued upload_id');
    assert.deepStrictEqual(posts.map((p) => p.offset), [0, 40, 80, 120, 160, 200, 240]);
    assert.ok(Buffer.concat(posts.map((p) => p.piece)).equals(bytes), 'slices do not reassemble to the file');
    assert.ok(states.includes('ok'), 'never reported Done: ' + states.join(','));
  });
}

(async () => {
  await check('sw: a POST is not intercepted (no respondWith, no re-fetch)', () => {
    const sw = loadSW();
    const ev = fireFetch(sw, 'POST');
    assert.strictEqual(ev.responded, 0, 'respondWith was called for POST');
    assert.strictEqual(sw.fetched.length, 0, 'POST was re-fetched by the worker');
  });

  await check('sw: PUT / DELETE are not intercepted either', () => {
    const sw = loadSW();
    for (const m of ['PUT', 'DELETE', 'PATCH']) {
      assert.strictEqual(fireFetch(sw, m).responded, 0, 'respondWith was called for ' + m);
    }
  });

  await check('sw: a GET still passes through the worker', () => {
    const sw = loadSW();
    const ev = fireFetch(sw, 'GET');
    assert.strictEqual(ev.responded, 1, 'respondWith was not called for GET');
    assert.strictEqual(sw.fetched.length, 1, 'GET was not passed to fetch()');
    assert.strictEqual(sw.fetched[0], ev.request, 'GET was not fetched as the original request');
  });

  await check('upload: a bulk file (no stage-time snapshot) is sent as read bytes, not the live File', async () => {
    const { panel, posts } = loadPanel();
    const bytes = Buffer.from('jpeg-bytes-IMG_1640');
    const { file, reads } = pickerFile(bytes, 'IMG_1640.jpeg', 'image/jpeg');
    await panel.uploadFile(file);
    assert.strictEqual(posts.length, 1, 'expected one POST');
    const entry = posts[0].opts.body.entries.find((e) => e.name === 'file');
    assert.ok(entry, 'no file field appended');
    assert.notStrictEqual(entry.value, file, 'the live File handle was appended');
    assert.ok(entry.value instanceof Blob, 'appended value is not a Blob');
    assert.strictEqual(reads.n, 1, 'file bytes were not read in the page');
    assert.ok((await sentBytes(entry)).equals(bytes), 'sent bytes differ from the file');
    assert.strictEqual(entry.filename, 'IMG_1640.jpeg');
  });

  await check('upload: the bulk path routes every file through the in-memory copy', async () => {
    const { panel, posts } = loadPanel();
    const picks = [1, 2, 3].map((i) => pickerFile(Buffer.from('photo-' + i), `IMG_164${i}.jpeg`, 'image/jpeg'));
    const results = await panel.uploadBulkFiles(picks.map((p) => p.file));
    assert.strictEqual(results.filter((r) => r.error).length, 0, JSON.stringify(results));
    assert.strictEqual(posts.length, 3);
    posts.forEach((p, i) => {
      const entry = p.opts.body.entries.find((e) => e.name === 'file');
      assert.notStrictEqual(entry.value, picks[i].file, `file ${i} sent as the live handle`);
    });
  });

  await check('upload: a stage-time snapshot is used as-is (no second read)', async () => {
    const { panel, posts } = loadPanel();
    const { file, reads } = pickerFile(Buffer.from('live'), 'IMG_1.jpeg', 'image/jpeg');
    const snap = new Blob([Buffer.from('snapshot')], { type: 'image/jpeg' });
    await panel.uploadFile(file, { file, name: 'IMG_1.jpeg', blob: snap });
    const entry = posts[0].opts.body.entries.find((e) => e.name === 'file');
    assert.strictEqual(entry.value, snap, 'stage-time snapshot was not the body');
    assert.strictEqual(reads.n, 0, 'file was read again despite a snapshot');
  });

  await check('upload: an unreadable file fails loudly before any POST', async () => {
    const { panel, posts } = loadPanel();
    const file = { name: 'IMG_9.jpeg', type: 'image/jpeg', size: 10, arrayBuffer: async () => { throw new Error('NotReadableError'); } };
    await assert.rejects(panel.uploadFile(file), /could not be read/);
    assert.strictEqual(posts.length, 0, 'an unreadable file was still POSTed');
  });

  await pageChecks();
  await chunkedCheck();

  if (failed) { console.log(`\n${failed} failed`); process.exit(1); }
  console.log('\nall passed');
})();
