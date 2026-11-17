"""Review 5 of PR #597, follow-ups: the bighead plugin route module (MEDIUM: an agent-key
write-anywhere through /api/bighead/create-agent) and the gateway-plugin hole in the
guard-scope population.

The reviewer's probes (tests/test_zz_review5_*.py, byte-identical) assert the attacks fail
on the module the repo ships; these pin the legitimate behaviour, cover the plugin's
fallback path (an OVU image without services/safe_open: the rollout copies the plugin
before the image roll, so tenants run the fallback first), and keep both controls:
the tenant copy (sha256 9183ce5a...) is still vulnerable and still flagged by the guard.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
BIGHEAD = ROOT / 'plugins' / 'bhb-animated-characters' / 'routes' / 'bighead.py'
BIGHEAD_ORIG = ROOT / 'tests' / 'review5_fixtures' / 'tenant_bighead_9183ce5a.tenant-copy.txt'


def _load(monkeypatch, runtime, without_safe_open, path=BIGHEAD):
    name = f'r5_bighead_{int(without_safe_open)}_{path.name.replace(".", "_")}'
    spec = importlib.util.spec_from_file_location(
        name, str(path), loader=importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'RUNTIME_DIR', runtime)
    if without_safe_open:   # an older OVU image: the plugin's own fd-based fallback
        monkeypatch.setattr(mod, '_Anchored', None)
    app = Flask(__name__)
    app.register_blueprint(mod.bighead_bp)
    return mod, app.test_client()


@pytest.fixture(params=[False, True], ids=['safe_open', 'fallback'])
def bh(request, monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    (runtime / 'workspace').mkdir(parents=True)
    _, c = _load(monkeypatch, runtime, request.param)
    return c, runtime, tmp_path


def _tree(p):
    return sorted(str(x.relative_to(p)) for x in p.rglob('*'))


# ── legitimate use is unchanged ────────────────────────────────────────────────────────

def test_create_agent_writes_the_three_files(bh):
    c, runtime, _ = bh
    r = c.post('/api/bighead/create-agent', json={
        'id': 'kyle-valhalla', 'name': 'Kyle', 'personality': 'chill', 'backstory': 'BHB',
        'memories': 'the mint', 'archetype': 'chill', 'traits': {'HEAD': 'Big', 'EYES': 'None'}})
    assert r.status_code == 201, r.get_json()
    d = runtime / 'workspace' / 'characters' / 'kyle-valhalla'
    assert r.get_json()['agent_dir'] == str(d)
    soul = (d / 'SOUL.md').read_text()
    assert soul.startswith('# Kyle\n') and '## Backstory\nBHB' in soul and 'HEAD: Big' in soul
    assert 'EYES' not in soul
    assert (d / 'memory' / 'core-memories.md').read_text() == "# Kyle's Core Memories\n\nthe mint\n"
    cfg = json.loads((d / 'character.json').read_text())
    assert cfg['id'] == 'kyle-valhalla' and cfg['traits'] == {'HEAD': 'Big', 'EYES': 'None'}
    # a second create overwrites in place
    r = c.post('/api/bighead/create-agent', json={'id': 'kyle-valhalla', 'name': 'Kyle2'})
    assert r.status_code == 201
    assert (d / 'SOUL.md').read_text().startswith('# Kyle2\n')


def test_characters_and_active_round_trip(bh):
    c, runtime, _ = bh
    assert c.get('/api/bighead/characters').get_json() == []
    assert c.get('/api/bighead/active').get_json() is None
    r = c.post('/api/bighead/characters', json={'BODY': 'b', 'id': 'sir-x', 'name': 'Sir'})
    assert r.status_code == 201
    r = c.post('/api/bighead/characters', json={'BODY': 'b'})       # server-made id
    assert r.status_code == 201 and r.get_json()['id'].startswith('bighead-')
    assert [x['id'] for x in c.get('/api/bighead/characters').get_json()][0] == 'sir-x'
    assert c.put('/api/bighead/characters/sir-x', json={'name': 'Sir Y'}).get_json()['name'] == 'Sir Y'
    assert c.delete('/api/bighead/characters/sir-x').status_code == 204
    assert c.delete('/api/bighead/characters/sir-x').status_code == 404
    assert c.put('/api/bighead/active', json={'id': 'a'}).get_json() == {'ok': True}
    assert c.get('/api/bighead/active').get_json() == {'id': 'a'}
    assert json.loads((runtime / 'bighead' / 'active.json').read_text()) == {'id': 'a'}


# ── the attack classes ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', ['/abs/dir', '../../uploads/x', '..', 'a/b', '', 'x' * 65,
                                 'a\x00b', 'a b', 7, None])
def test_create_agent_refuses_a_non_slug_id(bh, bad):
    c, runtime, tmp_path = bh
    before = _tree(tmp_path)
    r = c.post('/api/bighead/create-agent', json={'id': bad, 'name': 'x', 'personality': 'P'})
    assert r.status_code == 400
    assert _tree(tmp_path) == before


def test_create_character_refuses_a_non_slug_id(bh):
    c, runtime, _ = bh
    for bad in ('../../x', '/abs', 'a/b', 5):
        assert c.post('/api/bighead/characters', json={'BODY': 'b', 'id': bad}).status_code == 400
    assert c.get('/api/bighead/characters').get_json() == []


def test_create_agent_does_not_follow_a_planted_characters_dir(bh):
    """workspace/characters -> outside: refused, nothing written outside."""
    c, runtime, tmp_path = bh
    outside = tmp_path / 'app'
    outside.mkdir()
    (runtime / 'workspace' / 'characters').symlink_to(outside)
    r = c.post('/api/bighead/create-agent', json={'id': 'k', 'name': 'x', 'memories': 'm'})
    assert r.status_code == 409
    assert list(outside.iterdir()) == []


def test_create_agent_does_not_follow_a_planted_agent_dir_or_file(bh):
    c, runtime, tmp_path = bh
    outside = tmp_path / 'app'
    outside.mkdir()
    chars = runtime / 'workspace' / 'characters'
    chars.mkdir()
    (chars / 'k').symlink_to(outside)                     # the agent dir itself
    assert c.post('/api/bighead/create-agent', json={'id': 'k', 'name': 'x'}).status_code == 409
    assert list(outside.iterdir()) == []
    victim = tmp_path / 'victim.md'
    victim.write_text('ORIGINAL')
    (chars / 'j').mkdir()
    (chars / 'j' / 'SOUL.md').symlink_to(victim)          # a file name inside it
    assert c.post('/api/bighead/create-agent', json={'id': 'j', 'name': 'x'}).status_code == 409
    assert victim.read_text() == 'ORIGINAL'


def test_workspace_root_that_is_a_symlink_is_refused(monkeypatch, tmp_path):
    for without in (False, True):
        runtime = tmp_path / f'rt{int(without)}'
        runtime.mkdir()
        outside = tmp_path / f'out{int(without)}'
        outside.mkdir()
        (runtime / 'workspace').symlink_to(outside)
        _, c = _load(monkeypatch, runtime, without)
        r = c.post('/api/bighead/create-agent', json={'id': 'k', 'name': 'x'})
        assert r.status_code == 409
        assert list(outside.iterdir()) == []


def test_state_files_are_not_followed(bh):
    c, runtime, tmp_path = bh
    secret = tmp_path / 'secret.json'
    secret.write_text('[{"id": "LEAKED"}]')
    (runtime / 'bighead').mkdir()
    (runtime / 'bighead' / 'characters.json').symlink_to(secret)
    (runtime / 'bighead' / 'active.json').symlink_to(secret)
    assert c.get('/api/bighead/characters').get_json() == []
    assert c.get('/api/bighead/active').get_json() is None
    assert c.post('/api/bighead/characters', json={'BODY': 'b', 'id': 'a'}).status_code == 409
    assert c.put('/api/bighead/active', json={'id': 'a'}).status_code == 409
    assert secret.read_text() == '[{"id": "LEAKED"}]'


def test_bighead_dir_symlink_is_refused(bh):
    c, runtime, tmp_path = bh
    outside = tmp_path / 'app-runtime-db'
    outside.mkdir()
    (runtime / 'bighead').symlink_to(outside)
    assert c.put('/api/bighead/active', json={'id': 'a'}).status_code == 409
    assert list(outside.iterdir()) == []


def test_fifo_at_a_state_name_does_not_hang(bh):
    c, runtime, _ = bh
    (runtime / 'bighead').mkdir()
    fifo = runtime / 'bighead' / 'characters.json'
    os.mkfifo(fifo)
    # a live reader on the FIFO: a write open then succeeds, so only S_ISREG refuses it
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert c.get('/api/bighead/characters').get_json() == []
        assert c.post('/api/bighead/characters', json={'BODY': 'b', 'id': 'a'}).status_code == 409
        assert os.read(reader, 16) == b''
    finally:
        os.close(reader)


# ── controls ───────────────────────────────────────────────────────────────────────────

def test_bighead_orig_tenant_copy_is_the_vulnerable_one(monkeypatch, tmp_path):
    """Positive control: the byte copy of the module live on 5 tenants still writes outside."""
    assert hashlib.sha256(BIGHEAD_ORIG.read_bytes()).hexdigest().startswith('9183ce5a')
    runtime = tmp_path / 'runtime'
    (runtime / 'workspace').mkdir(parents=True)
    target = tmp_path / 'app' / 'routes'
    target.mkdir(parents=True)
    _, c = _load(monkeypatch, runtime, False, path=BIGHEAD_ORIG)
    c.post('/api/bighead/create-agent', json={'id': str(target), 'name': 'x', 'personality': 'P'})
    assert sorted(p.name for p in target.iterdir()) == ['SOUL.md', 'character.json', 'memory']


def test_guard_flags_the_bighead_tenant_copy(tmp_path):
    """Negative control: the guard flags the pre-fix module's three by-name writes."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_io_scan import Scanner
    rel = 'plugins/bhb-animated-characters/routes/bighead.py'
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_text(BIGHEAD_ORIG.read_text())
    found = {(r, q, s) for r, q, s, _ in Scanner(tmp_path).findings()}
    assert (rel, 'create_agent', '.write_text') in found


def test_probe_fixture_is_the_repo_plugin():
    fx = ROOT / 'tests' / 'review5_fixtures' / 'tenant_bighead_9183ce5a.py.txt'
    assert fx.is_symlink() and fx.resolve() == BIGHEAD.resolve(), (
        'the review-5 probe must exercise the plugin file the repo ships and the rollout copies')


def test_gateway_scope_check_fires_on_an_unscanned_gateway(tmp_path):
    """Negative control for the gateway loader: a `provides: gateway` manifest whose
    gateway.py is not in the tree is reported; once the code is in, it is not."""
    spec = importlib.util.spec_from_file_location(
        'r5_guard', str(ROOT / 'tests' / 'test_agent_writable_io_guard.py'))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    (tmp_path / 'plugins' / 'gw').mkdir(parents=True)
    (tmp_path / 'plugins' / 'gw' / 'plugin.json').write_text(json.dumps({'provides': 'gateway'}))
    assert 'gw/gateway.py' in g._unscanned_plugin_modules(tmp_path)
    (tmp_path / 'plugins' / 'gw' / 'gateway.py').write_text('x = 1\n')
    assert 'gw/gateway.py' not in g._unscanned_plugin_modules(tmp_path)
