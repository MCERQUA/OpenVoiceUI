// transcript-panel-input.test.js — the transcript/chat panel's text box (src/ui/AppShell.js + src/app.js + base.css)
//
//   node tests/js/transcript-panel-input.test.js
//
// 2026-11-01 (owner request): the chat panel opens by default so someone can just click and type, it grows with
// the conversation up to the top of the screen, and a message sends with BOTH Enter and the send icon.
//
// Why a FORM: Enter used to be an inline keydown handler and the icon a separate onclick. A phone keyboard's
// Send/Go key and an IME composition commit do not reliably reach a keydown with key==='Enter'; a form submit
// is the one path browsers give Enter, the Send key and a submit button alike, and it does not fire mid-
// composition. The trap that comes with it: every OTHER button inside a form submits it unless it is
// type="button" — the attachment ✕ would then send the message (measured in Chromium while building this).
//
// Static checks, no deps, so they run in CI next to the other tests/js files (tests/test_js_regressions.py).

const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..', '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

let failed = 0;
function check(name, ok, detail) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  -> ' + detail}`);
  if (!ok) failed++;
}

const shell = read('src/ui/AppShell.js');
const m = shell.match(/<div id="transcript-panel">([\s\S]*?)<!-- User Auth Section/);
check('transcript panel markup found', !!m, 'no <div id="transcript-panel"> block in AppShell.js');
const panel = m ? m[1] : '';

const form = panel.match(/<form\b[^>]*class="tp-input-bar"[^>]*>([\s\S]*?)<\/form>/);
check('input bar is a <form>', !!form, 'tp-input-bar is not a form');
const formTag = (panel.match(/<form\b[^>]*class="tp-input-bar"[^>]*>/) || [''])[0];
check('form submit calls sendText and prevents the page submit',
  /onsubmit="[^"]*preventDefault\(\)[^"]*TranscriptPanel\.sendText\(\)/.test(formTag), formTag);
const inner = form ? form[1] : '';
check('send icon is type="submit" with no second onclick send',
  /<button\s+type="submit"\s+class="tp-send-btn"/.test(inner) && !/tp-send-btn"[^>]*onclick=/.test(inner),
  (inner.match(/<button[^>]*tp-send-btn[^>]*>/) || ['(missing)'])[0]);
const otherButtons = (inner.match(/<button\b[^>]*>/g) || []).filter((b) => !/tp-send-btn/.test(b));
check('every other button in the form is type="button" (else it submits the message)',
  otherButtons.every((b) => /type="button"/.test(b)), otherButtons.filter((b) => !/type="button"/.test(b)).join(' '));
check('no inline Enter keydown handler (would double-send with the form submit)',
  !/onkeydown="[^"]*Enter/.test(inner), 'inline onkeydown Enter handler still present');
check('text input tells phone keyboards to show Send', /id="tp-text-input"[^>]*enterkeyhint="send"/.test(inner), 'no enterkeyhint="send"');

const app = read('src/app.js');
const initStart = app.indexOf("this.panel = document.getElementById('transcript-panel');");
const initEnd = app.indexOf("console.log('Transcript Panel initialized');", initStart);
check('TranscriptPanel.init found', initStart > 0 && initEnd > initStart, 'markers not found');
check('panel opens by default (init calls this.show())', /this\.show\(\);/.test(app.slice(initStart, initEnd)), 'no this.show() in init');

const css = read('src/styles/base.css');
const rule = (css.match(/#transcript-panel \{([\s\S]*?)\}/) || ['', ''])[1];
check('panel height is not capped at half the screen', !/max-height:\s*50vh/.test(rule), 'max-height: 50vh still set');
check('panel may grow to the top of the screen', /max-height:\s*calc\(100d?vh - 70px\)/.test(rule), rule.trim());

// Resize/move (same request): a fat top grip for height (mouse + touch), header drag detaches on desktop, and a
// dock button that puts it back. dock() must clear the bottom:auto that detaching set, or the docked panel jumps
// to the TOP of the screen (measured in Chromium while building this).
check('top grip for height drag exists', /class="tp-grip" id="tp-grip"/.test(panel), 'no #tp-grip');
check('dock button exists in the header', /class="tp-dock" id="tp-dock"[^>]*TranscriptPanel\.dock\(\)/.test(panel), 'no dock button');
const dockSrc = (app.match(/\n\s+dock\(\) \{([\s\S]*?)\n\s+\},/) || ['', ''])[1];
check("dock() resets 'bottom' as well as left/top/size", /'bottom'/.test(dockSrc) && /'left'/.test(dockSrc), dockSrc.slice(0, 200));
check('grip disables touch scrolling during the drag', /\.tp-grip \{[\s\S]*?touch-action:\s*none/.test(css), 'no touch-action:none on .tp-grip');

if (failed) { console.log(`\n${failed} check(s) FAILED`); process.exit(1); }
console.log('\nall transcript panel input checks passed');
