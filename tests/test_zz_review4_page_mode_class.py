"""Review 4 of PR #597 (non-author): B1 at the CLASS level, not just _write_page_replacing.

Host ruling (review 3, B1): a server-written canvas page must stay editable IN PLACE by the
tenant agent (uid 1000, no group shared with the server's uid 1001; openclaw's write/edit
tools do fs.writeFile = open(O_WRONLY|O_TRUNC)). 47d88bf fixed the page WRITE path (0666).

The page RESTORE path (POST /api/canvas/versions/<page>/<ts>/restore ->
services/canvas_versioning.restore_version) rewrites the same page through
Anchored.write_bytes(), whose write_beneath() fchmod()s the EXISTING file to the default
0644 on every write. On main the restore was Path.write_bytes() (O_TRUNC, the mode is kept),
so a 0666 page stayed 0666. Asserts the SAFE outcome: a FAIL means the page is re-locked.
"""
import os
import stat


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_version_restore_keeps_page_editable_by_agent(tmp_path, monkeypatch):
    import routes.canvas as canvas
    import services.canvas_versioning as cv
    monkeypatch.setattr(canvas, 'CANVAS_PAGES_DIR', tmp_path)
    monkeypatch.setattr(cv, 'CANVAS_PAGES_DIR', tmp_path)
    page = tmp_path / 'p.html'
    canvas._write_page_replacing(page, '<html>v2</html>')       # the fixed writer: 0666
    assert _mode(page) & 0o002, 'precondition: the PR writes pages 0666'
    vdir = tmp_path / '.versions'
    vdir.mkdir()
    (vdir / 'p.1700000000.html').write_bytes(b'<html>v1</html>')
    assert cv.restore_version('p', 1700000000)
    assert page.read_bytes() == b'<html>v1</html>'
    assert _mode(page) & 0o002, (
        f'restored page is {oct(_mode(page))}: the agent (uid 1000) can no longer edit it in '
        'place after a version restore (B1 class, services/canvas_versioning.py:252)')


def test_anchored_in_place_write_keeps_the_existing_mode(tmp_path):
    """Root cause: Path.write_text keeps an existing file's mode; Anchored.write_text (the
    drop-in the PR swapped in at ~40 sites) silently chmods it to `mode` (default 0644)."""
    from services.safe_open import Anchored
    f = tmp_path / 'shared.json'
    f.write_text('{}')
    os.chmod(f, 0o666)
    Anchored(tmp_path, 'shared.json').write_text('{"a": 1}')
    assert _mode(f) == 0o666, (f'Anchored.write_text re-moded an existing shared file to '
                               f'{oct(_mode(f))} (Path.write_text keeps 0o666)')
