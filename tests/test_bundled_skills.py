"""Bundled agent skills (skills/) and their installers.

Guards the layout OpenClaw's skill loader depends on, keeps the public bundle free of
deployment-specific content, and exercises install-skills.js (when node is available)
and the canvas-shared seeding used by the threejs-html-surfaces skill.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.canvas_shared import seed_canvas_shared

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
MOUNT = "./skills:/root/.openclaw/skills/openvoiceui:ro"
MAX_SKILL_MD_BYTES = 256000  # OpenClaw default skills.limits.maxSkillFileBytes
MAX_DESCRIPTION_CHARS = 400  # descriptions share an ~18k-char prompt budget across all skills


def _skill_dirs():
    return sorted(p for p in SKILLS.iterdir() if p.is_dir() and not p.name.startswith("."))


def _frontmatter(text):
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    assert m, "missing YAML frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if kv:
            out[kv.group(1)] = kv.group(2).strip().strip("'\"")
    return out


def test_bundle_has_skills():
    assert SKILLS.is_dir()
    assert len(_skill_dirs()) >= 1


def test_bundle_root_is_a_group_not_a_skill():
    # OpenClaw treats a root that contains SKILL.md as ONE skill; the mounted
    # skills/openvoiceui/ folder must stay a grouping folder.
    assert not (SKILLS / "SKILL.md").exists()


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_skill_frontmatter_and_size(skill_dir):
    md = skill_dir / "SKILL.md"
    assert md.is_file(), f"{skill_dir.name} has no SKILL.md"
    assert md.stat().st_size <= MAX_SKILL_MD_BYTES
    fm = _frontmatter(md.read_text(encoding="utf-8"))
    assert fm.get("name") == skill_dir.name
    assert fm.get("description")
    assert len(fm["description"]) <= MAX_DESCRIPTION_CHARS


def test_no_symlinks_in_bundle():
    # OpenClaw skips skill paths whose real path escapes the skills root.
    links = [str(p.relative_to(SKILLS)) for p in SKILLS.rglob("*") if p.is_symlink()]
    assert links == []


# Content that belongs to one particular deployment, never to the public bundle.
_FORBIDDEN = [
    (re.compile(r"/mnt/"), "host volume path"),
    (re.compile(r"jam-bot\.com", re.I), "hosted-service domain"),
    (re.compile(r"\b[\w.-]+@mesh\b"), "agent-mesh URI"),
    (re.compile(r"openvoiceui:5001"), "multi-container hostname (stock installs use localhost)"),
    (re.compile(r"\buser_[A-Za-z0-9]{20,}\b"), "Clerk user id"),
    (re.compile(r"\b(sk-ant-|sk-or-|gsk_|xai-)[A-Za-z0-9_-]{16,}"), "API key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"localStorage\.setItem|sessionStorage\.setItem"), "browser storage write"),
]


def test_bundle_is_deployment_neutral():
    hits = []
    for path in SKILLS.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".ttf", ".otf", ".png", ".jpg", ".woff", ".woff2"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for rx, what in _FORBIDDEN:
            for m in rx.finditer(text):
                hits.append(f"{path.relative_to(ROOT)}: {what}: {m.group(0)!r}")
    assert hits == []


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.local.yml"])
def test_compose_mounts_skills_read_only(compose):
    text = (ROOT / compose).read_text(encoding="utf-8")
    openclaw_block = text.split("\n  openclaw:", 1)[1].split("\n  supertonic:", 1)[0] \
        if "\n  supertonic:" in text else text.split("\n  openclaw:", 1)[1]
    assert f"- {MOUNT}" in openclaw_block


# --- canvas-shared seeding ------------------------------------------------------------------

def test_seed_canvas_shared_copies_and_is_idempotent(tmp_path):
    src = tmp_path / "canvas-shared"
    src.mkdir()
    (src / "mod.js").write_text("export const v = 1;\n")
    pages = tmp_path / "canvas-pages"
    assert seed_canvas_shared(src, pages) == ["mod.js"]
    assert (pages / "_shared" / "mod.js").read_text() == "export const v = 1;\n"
    assert seed_canvas_shared(src, pages) == []  # unchanged -> nothing written


def test_seed_canvas_shared_backs_up_a_differing_copy(tmp_path):
    src = tmp_path / "canvas-shared"
    src.mkdir()
    (src / "mod.js").write_text("new\n")
    shared = tmp_path / "canvas-pages" / "_shared"
    shared.mkdir(parents=True)
    (shared / "mod.js").write_text("edited locally\n")
    assert seed_canvas_shared(src, tmp_path / "canvas-pages") == ["mod.js"]
    assert (shared / "mod.js").read_text() == "new\n"
    backups = list((shared / ".backups").iterdir())
    assert len(backups) == 1 and backups[0].read_text() == "edited locally\n"


def test_seed_canvas_shared_never_raises(tmp_path):
    src = tmp_path / "canvas-shared"
    src.mkdir()
    (src / "mod.js").write_text("x\n")
    blocker = tmp_path / "canvas-pages" / "_shared" / "mod.js"
    blocker.mkdir(parents=True)  # a directory where the file should go -> OSError, logged
    assert seed_canvas_shared(src, tmp_path / "canvas-pages") == []
    assert seed_canvas_shared(tmp_path / "missing", tmp_path / "canvas-pages") == []


def test_repo_ships_html_surfaces_module():
    assert (ROOT / "canvas-shared" / "html-surfaces.js").is_file()


# --- install-skills.js --------------------------------------------------------------

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def _run(script, *args, env=None):
    return subprocess.run([NODE, str(script), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=60)


def _fake_repo(tmp_path):
    """A throwaway copy of the installer next to a two-skill bundle."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(ROOT / "install-skills.js", repo / "install-skills.js")
    for name in ("alpha", "beta"):
        d = repo / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test {name}\n---\n# {name} v1\n")
    return repo, repo / "install-skills.js"


@needs_node
def test_installer_check_passes_on_real_bundle():
    r = _run(ROOT / "install-skills.js", "--check")
    assert r.returncode == 0, r.stderr
    for d in _skill_dirs():
        assert d.name in r.stdout


@needs_node
def test_installer_check_rejects_bad_bundle(tmp_path):
    repo, script = _fake_repo(tmp_path)
    (repo / "skills" / "beta" / "SKILL.md").write_text("---\nname: wrong\ndescription: x\n---\n")
    r = _run(script, "--check")
    assert r.returncode == 2
    assert "must equal the folder name" in r.stderr


@needs_node
def test_installer_default_target_follows_openclaw_state_dir(tmp_path):
    repo, script = _fake_repo(tmp_path)
    state = tmp_path / "state"
    r = _run(script, env={"OPENCLAW_STATE_DIR": str(state)})
    assert r.returncode == 0, r.stderr
    assert (state / "skills" / "openvoiceui" / "alpha" / "SKILL.md").is_file()


@needs_node
def test_installer_dry_run_writes_nothing(tmp_path):
    repo, script = _fake_repo(tmp_path)
    target = tmp_path / "target"
    r = _run(script, "--target", str(target), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert not target.exists()
    assert "+ alpha/SKILL.md" in r.stdout


@needs_node
def test_installer_updates_but_never_clobbers_or_deletes(tmp_path):
    repo, script = _fake_repo(tmp_path)
    target = tmp_path / "target"
    mine = target.parent / "my-own-skill" / "SKILL.md"   # a user skill next to the managed dir
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: my-own-skill\ndescription: mine\n---\n")

    assert _run(script, "--target", str(target)).returncode == 0
    manifest = json.loads((target / ".openvoiceui-skills.json").read_text())
    assert set(manifest["files"]) == {"alpha/SKILL.md", "beta/SKILL.md"}

    # New release: both skills change. The user edited beta locally; gamma is new; alpha retires.
    (repo / "skills" / "beta" / "SKILL.md").write_text("---\nname: beta\ndescription: t\n---\n# beta v2\n")
    (target / "beta" / "SKILL.md").write_text("---\nname: beta\ndescription: t\n---\n# my beta edits\n")
    shutil.rmtree(repo / "skills" / "alpha")
    (repo / "skills" / "gamma").mkdir()
    (repo / "skills" / "gamma" / "SKILL.md").write_text("---\nname: gamma\ndescription: t\n---\n")

    r = _run(script, "--target", str(target))
    assert r.returncode == 0, r.stderr
    assert "my beta edits" in (target / "beta" / "SKILL.md").read_text()      # kept
    assert "kept: edited locally" in r.stderr
    assert (target / "gamma" / "SKILL.md").is_file()                          # added
    assert (target / "alpha" / "SKILL.md").is_file()                          # never deleted
    assert "no longer bundled" in r.stderr
    assert mine.read_text().startswith("---\nname: my-own-skill")             # untouched

    r = _run(script, "--target", str(target), "--force")
    assert r.returncode == 0, r.stderr
    assert "beta v2" in (target / "beta" / "SKILL.md").read_text()            # replaced
    saved = list((target / "beta").glob("SKILL.md.local-*"))
    assert len(saved) == 1 and "my beta edits" in saved[0].read_text()        # local copy saved


@needs_node
def test_installer_updates_unedited_files(tmp_path):
    repo, script = _fake_repo(tmp_path)
    target = tmp_path / "target"
    assert _run(script, "--target", str(target)).returncode == 0
    (repo / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\ndescription: t\n---\n# alpha v2\n")
    r = _run(script, "--target", str(target))
    assert r.returncode == 0, r.stderr
    assert "alpha v2" in (target / "alpha" / "SKILL.md").read_text()
    assert "~ alpha/SKILL.md" in r.stdout
