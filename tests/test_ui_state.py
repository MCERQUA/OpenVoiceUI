"""
Tests for services/ui_state.py — the UI_STATE.md workspace mirror (issue #94).

The module is deliberately Flask-free, so these tests run without the app.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import services.ui_state as ui_state  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_hash_cache():
    """Each test starts with a clean last-written-hash cache."""
    with ui_state._last_hash_lock:
        ui_state._last_hash = None
    yield
    with ui_state._last_hash_lock:
        ui_state._last_hash = None


def test_build_doc_contains_sections_and_content():
    doc = ui_state.build_ui_state_doc(
        '[Canvas pages: home, dashboard] [OPENVOICEUI SYSTEM INSTRUCTIONS: ...]',
        ['Canvas OPEN: dashboard', 'Music PLAYING: Test Track'],
    )
    assert '# UI STATE' in doc
    assert '## Current state' in doc
    assert '- Canvas OPEN: dashboard' in doc
    assert '- Music PLAYING: Test Track' in doc
    assert '## Standing instructions + catalogs' in doc
    assert '[Canvas pages: home, dashboard]' in doc


def test_build_doc_no_dynamic_lines():
    doc = ui_state.build_ui_state_doc('[static]', [])
    assert '(no UI state reported this turn)' in doc


def test_write_creates_file_atomically(tmp_path):
    wrote = ui_state.write_ui_state(tmp_path, '[static blob]', ['Canvas CLOSED'])
    assert wrote is True
    target = tmp_path / ui_state.UI_STATE_FILENAME
    assert target.is_file()
    text = target.read_text(encoding='utf-8')
    assert '[static blob]' in text
    assert '- Canvas CLOSED' in text
    # No tmp droppings left behind
    leftovers = [p for p in tmp_path.iterdir() if p.name != ui_state.UI_STATE_FILENAME]
    assert leftovers == []


def test_write_skips_unchanged_content(tmp_path):
    assert ui_state.write_ui_state(tmp_path, '[static]', ['Canvas CLOSED']) is True
    target = tmp_path / ui_state.UI_STATE_FILENAME
    mtime1 = target.stat().st_mtime_ns
    # Identical content (timestamp line excluded from the hash) → skip
    assert ui_state.write_ui_state(tmp_path, '[static]', ['Canvas CLOSED']) is False
    assert target.stat().st_mtime_ns == mtime1


def test_write_rewrites_on_dynamic_change(tmp_path):
    assert ui_state.write_ui_state(tmp_path, '[static]', ['Canvas CLOSED']) is True
    assert ui_state.write_ui_state(tmp_path, '[static]', ['Canvas OPEN: x']) is True
    text = (tmp_path / ui_state.UI_STATE_FILENAME).read_text(encoding='utf-8')
    assert '- Canvas OPEN: x' in text
    assert '- Canvas CLOSED' not in text


def test_write_rewrites_on_static_change(tmp_path):
    assert ui_state.write_ui_state(tmp_path, '[pages: a]', []) is True
    assert ui_state.write_ui_state(tmp_path, '[pages: a, b]', []) is True
    text = (tmp_path / ui_state.UI_STATE_FILENAME).read_text(encoding='utf-8')
    assert '[pages: a, b]' in text


def test_write_fail_open_on_bad_dir(tmp_path):
    """A write failure returns False and raises nothing (fail-open contract)."""
    blocker = tmp_path / 'not-a-dir'
    blocker.write_text('file, not dir')
    assert ui_state.write_ui_state(blocker, '[static]', []) is False


def test_creates_missing_uploads_dir(tmp_path):
    nested = tmp_path / 'uploads'
    assert ui_state.write_ui_state(nested, '[static]', []) is True
    assert (nested / ui_state.UI_STATE_FILENAME).is_file()
