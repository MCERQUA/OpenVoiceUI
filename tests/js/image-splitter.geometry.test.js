// image-splitter.geometry.test.js — geometry regression tests for default-pages/image-splitter.html
//
//   node tests/js/image-splitter.geometry.test.js
//
// NOT collected by pytest (the repo suite is Python); run it by hand or from CI after any
// change to the splitter's band math, drag clamping, output sizing or selection helpers.
// It stubs just enough DOM to evaluate the page script in node — no browser, no deps.
//
// It exists because the band rewrite shipped a real bug: deriving each band's `end` from its
// already-rounded `start` accumulated rounding error into the gutters, so a 4x4 grid on a
// 1000px image asked for 5px gutters and got 5,6,5. Caught here before it reached a tenant.

// Harness for the pure geometry in image-splitter.html: band derivation, drag clamping,
// output sizing. Stubs just enough DOM that the page script can be evaluated in node.
const fs = require('fs');
const path = require('path');

global.VALUES = {
  cols: '4', rows: '4', trim: '6', gutter: '5', inset: '1', prefix: 'post-',
  format: 'png', quality: '92', sizePreset: 'original', fit: 'cover',
  customW: '1080', customH: '1080',
};

function fakeEl(id) {
  return {
    id,
    get value() { return VALUES[id]; },
    set value(v) { VALUES[id] = String(v); },
    checked: id === 'tighten' ? false : true,   // tighten OFF: we are testing raw geometry
    style: {}, dataset: {}, textContent: '',
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    addEventListener(){}, appendChild(){}, setPointerCapture(){}, releasePointerCapture(){},
    getContext: () => ctx2d(),
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000 }),
    querySelector: () => null, querySelectorAll: () => [],
    click(){},
  };
}
function ctx2d() {
  return new Proxy({}, { get: (t, k) => {
    if (k === 'getImageData') return () => ({ data: new Uint8ClampedArray(4) });
    if (k === 'canvas') return { width: 0, height: 0 };
    return () => {};
  }});
}
const els = {};
global.document = {
  getElementById: id => (els[id] ||= fakeEl(id)),
  createElement: () => fakeEl('tmp'),
  addEventListener(){}, querySelector: () => null, querySelectorAll: () => [],
};
global.window = {};
global.FileReader = class {};
global.Image = class {};
global.XMLHttpRequest = class {};
global.FormData = class {};
global.URL = { createObjectURL: () => '', revokeObjectURL(){} };

const html = fs.readFileSync(path.join(__dirname, '../../default-pages/image-splitter.html'), 'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/g).pop().replace(/^<script>|<\/script>$/g, '');
// Evaluate in global scope so the page's `function` declarations become callable here.
// eval runs at the bottom, after TESTS is defined


const TESTS = "// \u2500\u2500 test fixture: a 1000x1000 source image \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\nsourceImg = { naturalWidth: 1000, naturalHeight: 1000 };\n\nlet pass = 0, fail = 0;\nconst eq = (name, got, want) => {\n  const ok = JSON.stringify(got) === JSON.stringify(want);\n  ok ? pass++ : fail++;\n  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `\\n        got  ${JSON.stringify(got)}\\n        want ${JSON.stringify(want)}`}`);\n};\nconst ok = (name, cond, detail = '') => {\n  cond ? pass++ : fail++;\n  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ' + detail}`);\n};\n\n// 1. Manual bands tile the image without overlap and respect trim/gutter\nmode = 'manual'; customBands = null;\nlet b = getBands();\neq('manual 4x4 \u2192 4 col bands', b.cols.length, 4);\neq('first col starts at trim', b.cols[0].start, 6);\nok('no col band overlaps the next',\n   b.cols.every((c, i) => i === 0 || c.start > b.cols[i-1].end),\n   JSON.stringify(b.cols));\nok('gutter between bands == 5',\n   b.cols.slice(1).every((c, i) => c.start - b.cols[i].end - 1 === 5),\n   JSON.stringify(b.cols));\nok('last col ends within image', b.cols[3].end <= 999, `end=${b.cols[3].end}`);\nok('right trim == left trim (last band ends at W-1-trim)', b.cols[3].end === 999 - 6, `end=${b.cols[3].end}`);\nok('cell widths differ by at most 1px',\n   (() => { const w = b.cols.map(c => c.end - c.start + 1); return Math.max(...w) - Math.min(...w) <= 1; })(),\n   JSON.stringify(b.cols.map(c => c.end - c.start + 1)));\n\n// 2. Cell rects: 16 cells, inset applied on all four edges\nlet rects = getCellRects();\neq('4x4 \u2192 16 rects', rects.length, 16);\neq('rect #1 inset by 1px', [rects[0].x, rects[0].y], [7, 7]);\nok('row-major numbering (#2 is right of #1)', rects[1].x > rects[0].x && rects[1].y === rects[0].y);\nok('#5 is the second row', rects[4].y > rects[0].y && rects[4].x === rects[0].x);\n\n// 3. Auto mode uses detected bands verbatim\ndetected = { cols: [{start:0,end:99},{start:110,end:209}], rows: [{start:0,end:99}] };\nmode = 'auto'; customBands = null;\neq('auto \u2192 2 cells', getCellRects().length, 2);\neq('auto keeps detected band start', getBands().cols[1].start, 110);\n\n// 4. Drag clamping \u2014 the safety property that stops bands crossing\nmode = 'manual'; customBands = null; detected = null;\nensureCustomBands();\nconst before = JSON.parse(JSON.stringify(customBands.cols));\n// Drag col-1's start way past its own end \u2192 must stop MIN_BAND short of the end\ndrag = { axis: 'cols', bandIdx: 1, edge: 'start' };\napplyDrag({ x: 99999, y: 0, s: { sx: 1, sy: 1 } });\nok('start cannot pass its own end',\n   customBands.cols[1].start === customBands.cols[1].end - 4,\n   `start=${customBands.cols[1].start} end=${customBands.cols[1].end}`);\n// Drag it far negative \u2192 must stop at the previous band's end + 1\napplyDrag({ x: -99999, y: 0, s: { sx: 1, sy: 1 } });\nok('start cannot pass the previous band',\n   customBands.cols[1].start === before[0].end + 1,\n   `start=${customBands.cols[1].start} prevEnd=${before[0].end}`);\n// Drag the LAST band's end past the image edge \u2192 clamps to W-1\ndrag = { axis: 'cols', bandIdx: 3, edge: 'end' };\napplyDrag({ x: 99999, y: 0, s: { sx: 1, sy: 1 } });\neq('last end clamps to image width-1', customBands.cols[3].end, 999);\n// Drag a row end above its own start \u2192 clamps to start + MIN_BAND\ndrag = { axis: 'rows', bandIdx: 0, edge: 'end' };\napplyDrag({ x: 0, y: -500, s: { sx: 1, sy: 1 } });\nok('end cannot pass its own start',\n   customBands.rows[0].end === customBands.rows[0].start + 4,\n   `start=${customBands.rows[0].start} end=${customBands.rows[0].end}`);\nok('dragging switched mode to custom', mode === 'custom', `mode=${mode}`);\n\n// 5. Output sizing\nconst r = { x: 0, y: 0, w: 200, h: 300, idx: 1 };\nVALUES.sizePreset = 'original';\neq('original size = rect size', getOutputSize(r), [200, 300]);\nVALUES.sizePreset = '1080x1350';\neq('preset parsed', getOutputSize(r), [1080, 1350]);\nVALUES.sizePreset = 'custom'; VALUES.customW = '640'; VALUES.customH = '480';\neq('custom size', getOutputSize(r), [640, 480]);\nVALUES.customW = '99999';\neq('custom width clamps to 8000', getOutputSize(r)[0], 8000);\nVALUES.sizePreset = 'original';\n\n// 6. Filenames follow the chosen format\nlastCellCount = 16;\nVALUES.format = 'jpeg';\neq('jpeg filename ext', cellFilename({ idx: 3 }, 2), 'post-03.jpg');\nVALUES.format = 'webp';\neq('webp filename ext', cellFilename({ idx: 12 }, 2), 'post-12.webp');\nVALUES.format = 'png';\neq('png filename ext', cellFilename({ idx: 1 }, 2), 'post-01.png');\n\n// 7. Selection helpers\nlastCellCount = -1;\nsyncSelection(16);\neq('new grid selects all', selected.size, 16);\nselected.delete(3); selected.delete(7);\neq('selectedRects filters', selectedRects(Array.from({length:16},(_,i)=>({idx:i+1}))).length, 14);\ninvertSelection();\neq('invert flips the set', [...selected].sort((a,b)=>a-b), [3, 7]);\nselectAll(true);\neq('select all restores 16', selected.size, 16);\nselectAll(false);\neq('select none empties', selected.size, 0);\nsyncSelection(16);\neq('same count keeps empty selection (no silent re-select)', selected.size, 0);\nsyncSelection(9);\neq('changed count re-selects all', selected.size, 9);\n\nconsole.log(`\\n${pass} passed, ${fail} failed`);\nprocess.exit(fail ? 1 : 0);\n";

(0, eval)(js + '\n' + TESTS);
