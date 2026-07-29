// Assertions for image-splitter.geometry.test.js — evaluated inside the page script's scope
// by that harness, not runnable on its own.

// ── test fixture: a 1000x1000 source image ─────────────────────────────────
sourceImg = { naturalWidth: 1000, naturalHeight: 1000 };

let pass = 0, fail = 0;
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  ok ? pass++ : fail++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `\n        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`}`);
};
const ok = (name, cond, detail = '') => {
  cond ? pass++ : fail++;
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ' + detail}`);
};

// 1. Manual bands tile the image without overlap and respect trim/gutter
mode = 'manual'; customBands = null;
let b = getBands();
eq('manual 4x4 → 4 col bands', b.cols.length, 4);
eq('first col starts at trim', b.cols[0].start, 6);
ok('no col band overlaps the next',
   b.cols.every((c, i) => i === 0 || c.start > b.cols[i-1].end),
   JSON.stringify(b.cols));
ok('gutter between bands == 5',
   b.cols.slice(1).every((c, i) => c.start - b.cols[i].end - 1 === 5),
   JSON.stringify(b.cols));
ok('last col ends within image', b.cols[3].end <= 999, `end=${b.cols[3].end}`);
ok('right trim == left trim (last band ends at W-1-trim)', b.cols[3].end === 999 - 6, `end=${b.cols[3].end}`);
ok('cell widths differ by at most 1px',
   (() => { const w = b.cols.map(c => c.end - c.start + 1); return Math.max(...w) - Math.min(...w) <= 1; })(),
   JSON.stringify(b.cols.map(c => c.end - c.start + 1)));

// 2. Cell rects: 16 cells, inset applied on all four edges
let rects = getCellRects();
eq('4x4 → 16 rects', rects.length, 16);
eq('rect #1 inset by 1px', [rects[0].x, rects[0].y], [7, 7]);
ok('row-major numbering (#2 is right of #1)', rects[1].x > rects[0].x && rects[1].y === rects[0].y);
ok('#5 is the second row', rects[4].y > rects[0].y && rects[4].x === rects[0].x);

// 3. Auto mode uses detected bands verbatim
detected = { cols: [{start:0,end:99},{start:110,end:209}], rows: [{start:0,end:99}] };
mode = 'auto'; customBands = null;
eq('auto → 2 cells', getCellRects().length, 2);
eq('auto keeps detected band start', getBands().cols[1].start, 110);

// 4. Drag clamping — the safety property that stops bands crossing
mode = 'manual'; customBands = null; detected = null;
ensureCustomBands();
const before = JSON.parse(JSON.stringify(customBands.cols));
// Drag col-1's start way past its own end → must stop MIN_BAND short of the end
drag = { axis: 'cols', bandIdx: 1, edge: 'start' };
applyDrag({ x: 99999, y: 0, s: { sx: 1, sy: 1 } });
ok('start cannot pass its own end',
   customBands.cols[1].start === customBands.cols[1].end - 4,
   `start=${customBands.cols[1].start} end=${customBands.cols[1].end}`);
// Drag it far negative → must stop at the previous band's end + 1
applyDrag({ x: -99999, y: 0, s: { sx: 1, sy: 1 } });
ok('start cannot pass the previous band',
   customBands.cols[1].start === before[0].end + 1,
   `start=${customBands.cols[1].start} prevEnd=${before[0].end}`);
// Drag the LAST band's end past the image edge → clamps to W-1
drag = { axis: 'cols', bandIdx: 3, edge: 'end' };
applyDrag({ x: 99999, y: 0, s: { sx: 1, sy: 1 } });
eq('last end clamps to image width-1', customBands.cols[3].end, 999);
// Drag a row end above its own start → clamps to start + MIN_BAND
drag = { axis: 'rows', bandIdx: 0, edge: 'end' };
applyDrag({ x: 0, y: -500, s: { sx: 1, sy: 1 } });
ok('end cannot pass its own start',
   customBands.rows[0].end === customBands.rows[0].start + 4,
   `start=${customBands.rows[0].start} end=${customBands.rows[0].end}`);
ok('dragging switched mode to custom', mode === 'custom', `mode=${mode}`);

// 5. Output sizing
const r = { x: 0, y: 0, w: 200, h: 300, idx: 1 };
VALUES.sizePreset = 'original';
eq('original size = rect size', getOutputSize(r), [200, 300]);
VALUES.sizePreset = '1080x1350';
eq('preset parsed', getOutputSize(r), [1080, 1350]);
VALUES.sizePreset = 'custom'; VALUES.customW = '640'; VALUES.customH = '480';
eq('custom size', getOutputSize(r), [640, 480]);
VALUES.customW = '99999';
eq('custom width clamps to 8000', getOutputSize(r)[0], 8000);
VALUES.sizePreset = 'original';

// 6. Filenames follow the chosen format
lastCellCount = 16;
VALUES.format = 'jpeg';
eq('jpeg filename ext', cellFilename({ idx: 3 }, 2), 'post-03.jpg');
VALUES.format = 'webp';
eq('webp filename ext', cellFilename({ idx: 12 }, 2), 'post-12.webp');
VALUES.format = 'png';
eq('png filename ext', cellFilename({ idx: 1 }, 2), 'post-01.png');

// 7. Selection helpers
lastCellCount = -1;
syncSelection(16);
eq('new grid selects all', selected.size, 16);
selected.delete(3); selected.delete(7);
eq('selectedRects filters', selectedRects(Array.from({length:16},(_,i)=>({idx:i+1}))).length, 14);
invertSelection();
eq('invert flips the set', [...selected].sort((a,b)=>a-b), [3, 7]);
selectAll(true);
eq('select all restores 16', selected.size, 16);
selectAll(false);
eq('select none empties', selected.size, 0);
syncSelection(16);
eq('same count keeps empty selection (no silent re-select)', selected.size, 0);
syncSelection(9);
eq('changed count re-selects all', selected.size, 9);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
