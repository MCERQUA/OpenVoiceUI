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
// The page declares its state with `let`, which indirect eval scopes to the eval itself —
// so the assertions must be evaluated in the SAME call to see `sourceImg`, `mode`, `bands`.
// They live in a sibling file purely so both stay readable.
const TESTS = fs.readFileSync(path.join(__dirname, 'image-splitter.geometry.cases.js'), 'utf8');



(0, eval)(js + '\n' + TESTS);
