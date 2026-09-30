"""A canvas page another uid wrote (mode 644) must still be refreshable.

Regression: ica's agent re-POSTed its 'Job Profits' page and got HTTP 500
('Canvas page creation failed') because the old file belonged to another uid
and the save overwrote it in place -> EACCES. A 0444 file reproduces that for
any non-root uid; the directory stays writable, as canvas-pages is in prod.
"""
import os
import stat

import pytest

from routes.canvas import _write_page_replacing

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason='root ignores file modes')


def _readonly_page(tmp_path):
    page = tmp_path / 'job-profits.html'
    page.write_text('<p>old</p>', encoding='utf-8')
    page.chmod(0o444)
    return page


def test_in_place_overwrite_fails_on_foreign_page(tmp_path):
    # Negative control: the old code path, on the same fixture, must fail.
    page = _readonly_page(tmp_path)
    with pytest.raises(PermissionError):
        page.write_text('<p>new</p>', encoding='utf-8')


def test_replace_write_succeeds_on_foreign_page(tmp_path):
    page = _readonly_page(tmp_path)
    _write_page_replacing(page, '<p>new</p>')
    assert page.read_text(encoding='utf-8') == '<p>new</p>'
    assert stat.S_IMODE(page.stat().st_mode) == 0o666
    assert [p.name for p in tmp_path.iterdir()] == ['job-profits.html']


def test_replace_write_creates_new_page(tmp_path):
    page = tmp_path / 'fresh.html'
    _write_page_replacing(page, '<p>hi</p>')
    assert page.read_text(encoding='utf-8') == '<p>hi</p>'


def test_failed_write_leaves_old_page_and_no_temp(tmp_path, monkeypatch):
    page = tmp_path / 'keep.html'
    page.write_text('<p>old</p>', encoding='utf-8')

    def boom(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        _write_page_replacing(page, '<p>new</p>')
    assert page.read_text(encoding='utf-8') == '<p>old</p>'
    assert [p.name for p in tmp_path.iterdir()] == ['keep.html']
