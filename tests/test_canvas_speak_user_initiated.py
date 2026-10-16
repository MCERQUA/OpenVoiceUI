"""The speak() helper injected into every canvas page must mark real button taps as
user-initiated (2026-10-16).

The shell (src/app.js, canvas-action 'speak') drops a speak without `userInitiated: true` when
it arrives within 15 s of a page load, and allows one per minute after that: that throttle is
for pages that announce themselves on open. The injected helper never set the flag, so a button
wired to speak() was throttled the same way, and the bundled skills had to tell authors to write
their own postMessage instead of using it.

The helper now sets the flag only while the frame has transient user activation (or, in a
browser without navigator.userActivation, during a trusted input event). Check 1 is plain
Python; check 2 runs the served helper under node and skips only when node is absent (CI has it).
"""
import json
import re
import shutil
import subprocess

import pytest

_HELPER_RE = re.compile(rb'<script id="canvas-nav-helpers">(.*?)</script>', re.S)


@pytest.fixture()
def helper(tmp_path, monkeypatch):
    import routes.canvas as canvas
    (tmp_path / 'game.html').write_text('<html><head></head><body><button>Go</button></body></html>')
    monkeypatch.setattr(canvas, 'CANVAS_PAGES_DIR', tmp_path)
    monkeypatch.setattr(canvas, 'CANVAS_REQUIRE_AUTH', False)
    from app import create_app
    app, _ = create_app(config_override={'TESTING': True})
    if 'canvas' not in app.blueprints:
        app.register_blueprint(canvas.canvas_bp)
    r = app.test_client().get('/pages/game.html')
    assert r.status_code == 200
    m = _HELPER_RE.search(r.data)
    assert m, 'canvas-nav-helpers script not injected'
    return m.group(1).decode()


def test_injected_speak_sets_user_initiated(helper):
    assert 'action:"speak"' in helper
    assert 'userInitiated' in helper, 'injected speak() never sets userInitiated'


_HARNESS = r"""
const vm = require('vm');
const helper = process.argv[2];
function run(activation, event) {
  const posted = [];
  const win = { parent: { postMessage: (m) => posted.push(m) }, event };
  const nav = activation === undefined ? {} : { userActivation: { isActive: activation } };
  const ctx = { window: win, navigator: nav };
  vm.createContext(ctx);
  vm.runInContext(helper, ctx);
  win.speak('I scored 10');
  return posted[0];
}
const out = {
  tapWithActivation: run(true),
  noActivation: run(false),
  noApiTrustedClick: run(undefined, { isTrusted: true, type: 'click' }),
  noApiUntrustedClick: run(undefined, { isTrusted: false, type: 'click' }),
  noApiNoEvent: run(undefined, undefined),
};
console.log(JSON.stringify(out));
"""


def test_injected_speak_behaviour_in_node(helper, tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('node not installed')
    harness = tmp_path / 'harness.js'
    harness.write_text(_HARNESS)
    proc = subprocess.run([node, str(harness), helper], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for case, msg in out.items():
        assert msg['type'] == 'canvas-action' and msg['action'] == 'speak' and msg['text'] == 'I scored 10'
    assert out['tapWithActivation']['userInitiated'] is True
    assert out['noApiTrustedClick']['userInitiated'] is True
    # No gesture: stays untagged, so the page-open throttle still applies.
    assert out['noActivation'].get('userInitiated') is not True
    assert out['noApiUntrustedClick'].get('userInitiated') is not True
    assert out['noApiNoEvent'].get('userInitiated') is not True
