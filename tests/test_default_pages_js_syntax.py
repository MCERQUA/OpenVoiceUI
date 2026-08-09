"""Every default-page's inline JavaScript must PARSE before it can ship to a tenant.

WHY THIS EXISTS
===============
PR #439 (6adb26f, 2026-08-01) added 32 curated icons to desktop.html's ICONS map using
unquoted hyphenated object keys:

    ai-image-creator:`<svg …>`,

A hyphen is not legal in an unquoted JS object key, so the ENTIRE inline <script> failed to
parse -- `SyntaxError: Unexpected token '-'`. Nothing in the page ran, not even the hardcoded
SYSTEM_ITEMS (My Computer, Documents, Settings, Recycle Bin), so the desktop rendered
completely blank rather than degrading to fewer icons. That total-failure mode is what makes
this class worth a gate: a bad icon entry does not cost you one icon, it costs the whole page.

It shipped to every tenant provisioned after 2026-08-01 (ttt on 08-02, gcu on 08-07) and both
desktops were dead -- gcu's for hours, ttt's for six days with nobody noticing.

The other 26 tenants escaped only because #439 forgot to bump desktop.html's
`openvoiceui-version` stamp, so jambot-deploy-verifier.sh (cron */30) never healed the page
onto them. Had the stamp been bumped, that cron would have docker-cp'd the broken page across
the whole fleet inside 30 minutes. The thing that hid the bug was also what was about to
amplify it -- so "it only affected one client" was luck, not containment.

TWO CHECKS, DELIBERATELY
------------------------
1. test_no_unquoted_hyphenated_object_keys -- pure Python, ZERO dependencies, so it can never
   silently no-op. This catches the exact #439 shape on any machine.
2. test_inline_js_parses -- the general check, via `node --check`. Catches every syntax error,
   not just this one. Skips only when node is genuinely absent, and the skip is visible in
   pytest output; CI (ubuntu-latest) always has node, and tests/js already relies on it.

Check 1 exists precisely so that check 2 being skipped cannot leave the known class unguarded.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGES_DIR = Path(__file__).resolve().parent.parent / "default-pages"

# Inline <script> only -- a block with src= is an external file, not our text to parse.
_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)

# An object key that is a hyphenated identifier, unquoted, at line start + indentation,
# immediately followed by a value. Anchored so nothing inside an SVG body, a URL, a CSS
# block or a template literal can match.
_BAD_KEY_RE = re.compile(
    r"^(?P<indent>[ \t]{2,})(?P<key>[A-Za-z_$][\w$]*(?:-[\w$]+)+)\s*:\s*(?P<val>[`'\"{\[])",
    re.M,
)


def _pages() -> list[Path]:
    return sorted(PAGES_DIR.glob("*.html"))


def _inline_js(html: str) -> str:
    return "\n;\n".join(_SCRIPT_RE.findall(html))


def test_pages_dir_is_populated():
    """Guard the guard: if the glob silently returns nothing, both checks below become
    vacuous passes over an empty set -- a test suite reporting green about nothing."""
    pages = _pages()
    assert pages, f"no default-pages/*.html found under {PAGES_DIR} -- the checks below would be vacuous"
    assert len(pages) >= 10, f"only {len(pages)} default pages found; expected the full set"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_no_unquoted_hyphenated_object_keys(page: Path):
    """The exact #439 defect. Dependency-free so it always runs."""
    js = _inline_js(page.read_text(errors="replace"))
    if not js.strip():
        pytest.skip(f"{page.name} has no inline script")
    bad = [(m.group("key"), js[: m.start()].count("\n") + 1) for m in _BAD_KEY_RE.finditer(js)]
    assert not bad, (
        f"{page.name}: {len(bad)} unquoted hyphenated object key(s) -- a hyphen is not legal in "
        f"an unquoted JS key and kills the WHOLE inline script, blanking the page. "
        f"Quote them (e.g. 'ai-image-creator':). Offenders: "
        + ", ".join(f"{k} (js line {ln})" for k, ln in bad[:8])
    )


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_inline_js_parses(page: Path, tmp_path: Path):
    """General syntax check on the concatenated inline script."""
    node = shutil.which("node")
    if not node:
        pytest.skip(
            "node not on PATH -- general JS syntax check skipped. The #439 class is still "
            "covered by test_no_unquoted_hyphenated_object_keys, which needs no node."
        )
    js = _inline_js(page.read_text(errors="replace"))
    if not js.strip():
        pytest.skip(f"{page.name} has no inline script")
    f = tmp_path / f"{page.stem}.js"
    f.write_text(js)
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        f"{page.name}: inline JavaScript does not parse, so NOTHING on the page will run.\n"
        f"{(r.stderr or r.stdout).strip()[:1200]}"
    )
