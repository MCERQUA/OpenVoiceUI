"""Controls for the two watchers: tools/biz/isolation-probe.py and crt-verdict-watch.py.

Each must fire on known-bad input and stay quiet on known-good input (BABY rule), and neither
may change anything. The watcher is checked against stored reviews made by the real engine
(positive) and against forged "Meets" reviews written straight into the store (negative).
"""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (H, NAMED, STAFF_1, TODAY, _ends, dirs, h, world)  # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'tools' / 'biz' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def keep_env():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


# ── isolation probe ─────────────────────────────────────────────────────────
def test_probe_positive_control_passes(keep_env):
    probe_mod = _load('isolation-probe')
    probe, verdict = probe_mod.self_test('positive')
    bad = [c for c in probe.checks if c['verdict'] != 'PASS']
    assert verdict == 'PASS', bad
    names = ' | '.join(c['check'] for c in probe.checks)
    for must in ('foam key -> josh data: read crt_policy', 'josh key -> foam data: file meta',
                 'key naming the other sandbox', 'anonymous -> josh files', 'josh agent key: crt/confirm',
                 'josh agent key: crt_review create', 'josh agent key: set status APPROVED'):
        assert must in names, must
    assert len(probe.checks) >= 50


def test_probe_negative_control_planted_foreign_row_fails(keep_env):
    probe_mod = _load('isolation-probe')
    probe, verdict = probe_mod.self_test('negative')
    assert verdict == 'FAIL'
    failed = [c['check'] for c in probe.checks if c['verdict'] == 'FAIL']
    assert 'foam key -> josh data: read crt_policy' in failed
    assert 'foam key -> josh data: list crt_policy' in failed


def test_probe_refuses_to_write_into_a_business_store():
    probe_mod = _load('isolation-probe')

    class FakeT:
        def __init__(self):
            self.calls = []

        def call(self, method, path, headers=None, body=None, file=None):
            self.calls.append((method, path))
            return 200, {'tenant': 'acme', 'mode': 'read-write'}           # no 'sandbox' in health
    t = FakeT()
    p = probe_mod.Probe(t, [{'name': 'a', 'key': 'k1'}, {'name': 'b', 'key': 'k2'}])
    assert p.run() == 'CANNOT-TELL'
    assert all(m == 'GET' for m, _ in t.calls)


def test_probe_cannot_tell_on_server_errors():
    probe_mod = _load('isolation-probe')

    class Down:
        def call(self, *a, **k):
            return None, {'error': 'URLError'}
    assert probe_mod.Probe(Down(), [{'name': 'a', 'key': 'k'}, {'name': 'b', 'key': 'k'}]).run() == 'CANNOT-TELL'


# ── verdict watcher ─────────────────────────────────────────────────────────
def _db(dirs_):
    return str(dirs_['a'] / 'sandboxes' / 'josh')


def _watch(dirs_):
    return _load('crt-verdict-watch').watch(_db(dirs_))


def _forge_meets(h_, w, confirmation_ids=None):
    """Write a 'meets' review straight into the store, the way a bug or a forger would."""
    store = h_.app.extensions['biz']['sandbox:josh']
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return store.create('crt_review', {
        'crt_assignment_id': w['asg']['id'], 'state': 'meets', 'label': 'Meets requirements (documents reviewed)',
        'variance_codes': [], 'requirement_set_id': w['set']['id'], 'requirement_set_version': 1,
        'confirmation_ids': confirmation_ids or [], 'reviewed_by': 'clerk:' + STAFF_1, 'reviewed_at': now},
        by='clerk:' + STAFF_1, via_pack=True)


def test_watch_positive_engine_review_is_backed(h, dirs):
    w = world(h)
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    assert rv['review']['data']['state'] == 'meets'
    db = Path(_db(dirs)) / 'business.db'
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    verdict, rows, summary = _watch(dirs)
    assert verdict == 'PASS', rows
    assert [r['watch'] for r in rows] == ['PASS']
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before          # it changed nothing


def test_watch_positive_blanket_and_after_completion(h, dirs):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=[], scheduled_projects=[], form='CG 20 33'))
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    w2 = world(h, project={'substantial_completion': '2025-09-01'}, party_name='Closeout Electric Inc')
    h.ok(h.post(f'/crt/assignment/{w2["asg"]["id"]}/review'), 201)
    verdict, rows, _ = _watch(dirs)
    assert verdict == 'PASS' and len(rows) == 2, rows


def test_watch_fails_a_forged_meets_on_a_wrong_party_endorsement(h, dirs):
    w = world(h, endorsements=_ends(scheduled_names=['Zenith General Contractors Inc', 'Mesa Health District']))
    assert h.state(w['asg']['id'])['state'] == 'variance'          # the engine knows
    ev = h.state(w['asg']['id'])
    _forge_meets(h, w, ev['confirmation_ids'])
    verdict, rows, summary = _watch(dirs)
    assert verdict == 'FAIL' and rows[0]['watch'] == 'FAIL'
    assert any(r.startswith('R4 no person-confirmed ai_ongoing') for r in rows[0]['reasons'])


def test_watch_fails_a_forged_meets_with_nothing_confirmed(h, dirs):
    w = world(h, confirm_all=False)
    _forge_meets(h, w)
    verdict, rows, _ = _watch(dirs)
    assert verdict == 'FAIL'
    assert any(r.startswith('R3') for r in rows[0]['reasons']) and any(r.startswith('R4') for r in rows[0]['reasons'])


def test_watch_fails_a_meets_that_read_an_agent_confirmation(h, dirs):
    w = world(h)
    store = h.app.extensions['biz']['sandbox:josh']
    fake = store.create('crt_confirmation', {
        'record_collection': 'crt_policy', 'record_id': w['pol']['gl']['id'], 'record_version': w['pol']['gl']['version'],
        'fields': ['line'], 'confirmed_by': 'agent:test-dev-sbx-josh'}, by='agent:test-dev-sbx-josh', via_pack=True)
    _forge_meets(h, w, [fake['id']])
    verdict, rows, _ = _watch(dirs)
    assert verdict == 'FAIL' and any(r.startswith('R2') for r in rows[0]['reasons'])


def test_watch_fails_when_the_endorsement_file_no_longer_matches(h, dirs):
    w = world(h)
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    fid = w['files']['e_ongoing']['id']
    (Path(_db(dirs)) / 'files' / fid).write_bytes(b'%PDF-1.4\n% swapped after review\n')
    verdict, rows, _ = _watch(dirs)
    assert verdict == 'FAIL' and any('ai_ongoing' in r for r in rows[0]['reasons'])


def test_watch_cannot_tell_when_an_input_changed_after_the_review(h, dirs):
    w = world(h)
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    gl = h.ok(h.get(f'/crt_policy/{w["pol"]["gl"]["id"]}'))
    h.ok(h.patch(f'/crt_policy/{gl["id"]}', {'data': {'notes': 'renewal expected'}}, gl['version']))
    verdict, rows, _ = _watch(dirs)
    assert verdict == 'CANNOT-TELL' and rows[0]['watch'] == 'CANNOT-TELL'


def test_watch_cannot_tell_with_nothing_to_check_or_no_store(h, dirs, tmp_path):
    world(h)                                     # no review recorded
    assert _watch(dirs)[0] == 'CANNOT-TELL'
    assert _load('crt-verdict-watch').watch(str(tmp_path / 'nope'))[0] == 'CANNOT-TELL'


def test_watch_ignores_non_meets_reviews(h, dirs):
    w = world(h, endorsements=_ends(scheduled_projects=['Tempe Warehouse Retrofit']))
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    assert rv['review']['data']['state'] == 'variance'
    verdict, rows, _ = _watch(dirs)
    assert verdict == 'CANNOT-TELL' and rows == []          # nothing claimed Meets


def test_watch_is_an_independent_instrument():
    src = (ROOT / 'tools' / 'biz' / 'crt-verdict-watch.py').read_text()
    assert 'from services' not in src and 'import services' not in src and 'from routes' not in src
    assert 'mode=ro' in src
    for word in ('requests.', 'urllib', 'smtplib', 'mesh-send', 'inkbox', '.execute(\'INSERT', 'UPDATE records'):
        assert word not in src, word


def test_watch_cli_exit_codes(h, dirs):
    w = world(h)
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    mod = _load('crt-verdict-watch')
    assert mod.main(['--db-dir', _db(dirs)]) == 0
    _forge_meets(h, world(h, confirm_all=False, party_name='Forged Sub LLC'))
    assert mod.main(['--db-dir', _db(dirs), '--json']) == 1
