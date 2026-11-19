"""A default page that needs a newer server must carry an `openvoiceui-version` stamp.

WHY THIS EXISTS
===============
server.py seeds default-pages/ into each install's canvas-pages/ on boot, but it only copies a
page OVER an existing copy when the page carries an `<!-- openvoiceui-version: X -->` stamp in
its first 5 lines and X differs from the copy's. An unstamped page is seeded once, at install,
and never again.

PR #586 shipped the sliced upload for phone videos over Cloudflare's 100 MB edge cap: the server
routes (/api/upload/chunk/start + /api/upload/chunk) and a new bulk-image-uploader.html that
calls them. The page had no stamp, so every existing install kept the copy it was seeded with.
Measured 2026-11-19 across 25 installs: 21 served an October copy with no slicing at all (a
phone video over the edge cap dies at Cloudflare), and 2 served a pre-merge draft that sends a
client-made upload_id with no start step, which the merged server answers with
`400 start_required` on the first slice. The server side worked end to end the whole time
(99 MB through Cloudflare, sha256 identical); the page never arrived.

So: any default page that talks to the sliced-upload routes must carry a stamp the seeder can
read, and every stamp must sit where the seeder looks for it (server.py reads only the first
5 lines; a stamp further down is invisible to it).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = ROOT / 'default-pages'

# Mirrors server.py's _VERSION_RE and _extract_page_version(max_lines=5);
# test_reader_matches_server_py keeps the two in step.
VERSION_RE = re.compile(r'<!--\s*openvoiceui-version:\s*(\S+)\s*-->')
MAX_LINES = 5

# Pages whose behaviour depends on server routes added after many installs were seeded.
# A page that calls any of these must be re-seedable.
NEEDS_RESEED_MARKERS = ('/api/upload/chunk',)


def seeder_version(text: str) -> str | None:
    for line in text.splitlines()[:MAX_LINES]:
        m = VERSION_RE.search(line)
        if m:
            return m.group(1)
    return None


def stamp_problems(name: str, text: str) -> list[str]:
    problems = []
    has_stamp_anywhere = bool(VERSION_RE.search(text))
    if has_stamp_anywhere and seeder_version(text) is None:
        problems.append(f'{name}: openvoiceui-version stamp is below line {MAX_LINES}; '
                        'server.py never sees it, so the page is never re-seeded')
    if any(m in text for m in NEEDS_RESEED_MARKERS) and seeder_version(text) is None:
        problems.append(f'{name}: calls a sliced-upload route but has no openvoiceui-version '
                        f'stamp in its first {MAX_LINES} lines; existing installs keep their old copy')
    return problems


def _pages():
    pages = sorted(PAGES_DIR.glob('*.html'))
    assert pages, f'no default-pages/*.html under {PAGES_DIR} -- the checks below would be vacuous'
    return pages


def test_reader_matches_server_py():
    src = (ROOT / 'server.py').read_text(encoding='utf-8')
    assert VERSION_RE.pattern in src, 'server.py _VERSION_RE changed; update VERSION_RE here'
    assert re.search(r'def _extract_page_version\(path, max_lines=5\)', src), \
        'server.py _extract_page_version max_lines changed; update MAX_LINES here'


def test_every_default_page_is_reseedable_when_it_must_be():
    problems = [p for page in _pages() for p in stamp_problems(page.name, page.read_text(encoding='utf-8'))]
    assert not problems, '\n'.join(problems)


def test_uploader_page_is_stamped():
    text = (PAGES_DIR / 'bulk-image-uploader.html').read_text(encoding='utf-8')
    assert '/api/upload/chunk/start' in text
    assert seeder_version(text)


# ---- negative controls: the check must fire on the shapes it exists to catch ----

def test_check_fires_on_unstamped_chunk_page():
    page = "<!DOCTYPE html>\n<html>\n<script>xhr.open('POST','/api/upload/chunk/start')</script>\n</html>\n"
    assert stamp_problems('x.html', page), 'an unstamped sliced-upload page must be flagged'


def test_check_fires_on_stamp_below_the_seeder_window():
    page = '\n'.join(['<!DOCTYPE html>', '<html>', '<head>', '<meta charset="UTF-8">', '<title>t</title>',
                      '<!-- openvoiceui-version: 9 -->', '</head>', '</html>'])
    assert seeder_version(page) is None
    assert stamp_problems('x.html', page), 'a stamp on line 6 is invisible to server.py and must be flagged'


def test_check_passes_stamped_chunk_page_and_plain_unstamped_page():
    stamped = "<!DOCTYPE html>\n<!-- openvoiceui-version: 1 -->\n<script>'/api/upload/chunk'</script>\n"
    plain = "<!DOCTYPE html>\n<html><body>hello</body></html>\n"
    assert stamp_problems('a.html', stamped) == []
    assert stamp_problems('b.html', plain) == []
