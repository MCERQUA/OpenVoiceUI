"""Browser-provider regression tests in tests/js/ that must run in CI, not only by hand.

tests/js/deepgram-streaming-ptt.test.js covers push-to-talk in DeepgramStreamingSTT. Switching
PTT mode on before a call left the whole call with no mic stream: every hold opened a Deepgram
socket, sent zero audio, and nothing reached the agent (measured 2026-09-14 in Chromium against
live Deepgram). A node test that only runs by hand would not catch that coming back, so pytest
runs it.

tests/js/ptt-button-standalone.test.js covers PTT with no call running: it must send the
words the same way the transcript text box does, then give the mic back. Before, such a press
only changed the button colour.

Skips only when node is absent, and the skip is visible in pytest output. CI (ubuntu-latest)
always has node.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).resolve().parent / "js"


@pytest.mark.parametrize("name", ["deepgram-streaming-ptt.test.js", "ptt-button-standalone.test.js"])
def test_js_regression_suite(name: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    proc = subprocess.run(
        [node, str(JS_TESTS / name)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
