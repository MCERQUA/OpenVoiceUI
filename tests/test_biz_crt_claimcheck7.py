"""Rules 1.7.0 regression set: gate-4 re-check #7 (docs/jambot/contractors-choice-claimcheck7-20261117.md). Each case is
written to FAIL on df962ce (rules 1.6.0, before PR #597 was merged in) and pass here; positive controls say so in their
name; guards that already held on df962ce (by another rule) say `already_held` in theirs. The verbatim re-checker probes are tests/test_claimcheck7_probes.py. Fake data only.

A  `required` is read ONE way everywhere (engine, approval page, watcher): only false is "suggested". null / 0 / "" /
   anything else reads as required; a new set stores it as true (the revise route says so in its warnings); a staff
   edit that sends a non-boolean is refused; a stored non-boolean breaks the floor (Can't tell; watcher RA); the diff
   shows `required_applied` beside the stored value.
B  every waivable code is raised by paper (document + file hash), COMPLETED_OPS_LAPSED included (bound to the term's
   certificate): a renewal raises it again, and a waiver bound to no paper covers nothing (SCOPE_CHANGED is the one
   event-bound code). The watcher accepts the honest same-term lapse waiver (RB) and nothing else.
C  an ISO number is read however it is written: with its edition after it ('CG 20 10 04 13'), an 'ISO' prefix, or
   inside other text. One reading (form_key / iso_numbers_in) for the endorsement, the item's forms, the set's table,
   the diff's forms_read_as row and the watcher (RC7); a number whose printed edition disagrees with the edition
   field reads Can't tell.
D  the dev-page routes are no-follow beneath the canvas mount (PR #597 services/safe_open): a planted link is refused,
   never followed, read or copied.
"""
import ast
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, END_FIELDS, M, SBX, STAFF_1, TODAY, _ends, codes, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed, _staff_confirm_wording
from tests.test_claimcheck7_probes import _lapsed_world, _renew, _waive

ROOT = Path(__file__).resolve().parents[1]


def _store(h):
    return h.app.extensions['biz']['sandbox:josh']


def _set(h, sid):
    h.staff()
    return h.ok(h.get(f'/crt_requirement_set/{sid}'))


def _no_completed():
    e = _ends()
    e.pop('ai_completed')
    return e


def _items_with(x, iid, change):
    return [dict(it, **change) if it['id'] == iid else it for it in x['data']['items']]


def _db(dirs):
    return dirs['a'] / 'sandboxes' / 'josh' / 'business.db'


def _raw_patch(dirs, rid, fn):
    """Change a stored record's data around the API (a set or waiver written before rules 1.7.0, or by a bug): the
    version is untouched, so every approval and confirmation still names it."""
    conn = sqlite3.connect(str(_db(dirs)))
    try:
        data = json.loads(conn.execute('SELECT data FROM records WHERE id = ?', (rid,)).fetchone()[0])
        fn(data)
        conn.execute('UPDATE records SET data = ? WHERE id = ?', (json.dumps(data, separators=(',', ':')), rid))
        conn.commit()
    finally:
        conn.close()


def _forge(h, w, state='meets'):
    from datetime import datetime, timezone
    asg = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return _store(h).create('crt_review', {
        'crt_assignment_id': w['asg']['id'], 'state': state, 'label': 'Meets requirements (documents reviewed)',
        'variance_codes': [], 'requirement_set_id': asg['data']['requirement_set_id'],
        'requirement_set_version': asg['data']['requirement_set_version'], 'confirmation_ids': [],
        'reviewed_by': 'clerk:' + STAFF_1, 'reviewed_at': now}, by='clerk:' + STAFF_1, via_pack=True)


def _review(h, w):
    h.staff()
    return h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']


def _reasons(rows):
    return [r for row in rows for r in row['reasons']]


# ═════════════════════════ A: `required` is read one way everywhere ═════════════════════════
@pytest.mark.parametrize('value', [None, 0, '', 'no', 1], ids=['null', 'zero', 'empty', 'string', 'one'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_a_a_non_boolean_required_is_stored_as_required_and_said(h, value, who):
    w = world(h, endorsements=_no_completed(), party_name=f'A Store {value!r} {who} Inc')
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(x, 'ai_completed', {'required': value})})
    out = h.ok(r, 201)
    it = next(i for i in out['record']['data']['items'] if i['id'] == 'ai_completed')
    assert it['required'] is True, it
    assert any(wn.get('item') == 'ai_completed' and 'read and stored as required' in wn.get('warning', '')
               for wn in out['warnings']), out['warnings']


@pytest.mark.parametrize('value', [None, 0, ''], ids=['null', 'zero', 'empty'])
def test_a_a_generic_create_stores_a_non_boolean_required_as_true(h, value):
    w = world(h, party_name=f'A Generic {value!r} Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    rec = h.ok(h.post('/crt_requirement_set', {'data': {
        'name': 'generic copy', 'trade_category': x['data']['trade_category'],
        'items': _items_with(x, 'ai_completed', {'required': value}),
        'form_functions': x['data']['form_functions']}}), 201)
    it = next(i for i in rec['data']['items'] if i['id'] == 'ai_completed')
    assert it['required'] is True, it


def test_a_a_staff_edit_with_a_non_boolean_required_is_refused(h):
    """An edit cannot restate the stored value (the store keeps what check_write saw), so it refuses instead."""
    w = world(h, party_name='A Edit Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    draft = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {}), 201)['record']
    for value in (None, 0, ''):
        r = h.patch(f'/crt_requirement_set/{draft["id"]}',
                    {'data': {'items': _items_with(draft, 'ai_completed', {'required': value})}}, draft['version'])
        assert r.status_code == 422 and r.get_json()['code'] == 'required_not_boolean', (value, r.get_json())
    after = _set(h, draft['id'])
    assert all(isinstance(i.get('required', True), bool) for i in after['data']['items']), after


@pytest.mark.parametrize('value', [None, 0, ''], ids=['null', 'zero', 'empty'])
def test_a_a_stored_non_boolean_required_reads_cant_tell_never_meets(h, value):
    """A set stored around the API (before 1.7.0, or by a bug): the engine never reads it as "not required"."""
    w = world(h, endorsements=_no_completed(), party_name=f'A Stored {value!r} Inc')
    x = _set(h, w['set']['id'])
    from services.biz.packs import risk_transfer as rt
    data = dict(x['data'], items=_items_with(x, 'ai_completed', {'required': value}))
    ev = rt.evaluate_inputs(dict(rt.gather(_store(h), w['asg']['id']), set=dict(x, data=data)), TODAY)
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev
    assert 'MISSING:ai_completed' in codes(ev), ev                  # and the item itself is still required
    assert any('required is' in n for n in ev['notes']), ev['notes']


def test_a_the_diff_shows_what_the_verdict_applies_for_required(h, dirs):
    w = world(h, endorsements=_no_completed(), party_name='A Diff Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    draft = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {}), 201)['record']
    _raw_patch(dirs, draft['id'], lambda d: d.update(items=[dict(i, required=None) if i['id'] == 'ai_completed'
                                                             else i for i in d['items']]))
    d = h.ok(h.get(f'/crt/requirement-sets/{draft["id"]}/diff'))
    row = {r['key']: r for r in next(i for i in d['items'] if i['id'] == 'ai_completed')['rows']}
    assert row['required_applied']['was'] is True and row['required_applied']['now'] is True, row
    assert any('required is' in v for v in d['floor_violations']), d['floor_violations']
    gl = {r['key']: r for r in next(i for i in d['items'] if i['id'] == 'gl_occ')['rows']}
    assert gl['required_applied']['now'] is True                    # a limit item is always checked


def test_a_the_page_renders_a_null_required_as_required_never_a_dash():
    page = (ROOT / 'default-schemas/packs/risk-transfer/pages/cca-crt-requirements.html').read_text(encoding='utf-8')
    assert "if (key !== 'required') return val(v);" in page and "'required (default)'" in page
    assert "it.required === false ? 'Suggested: '" in page            # the item list: only false is suggested


def test_a_positive_control_required_false_is_suggested_everywhere(h):
    w = world(h, endorsements=_no_completed(), party_name='A False Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise',
                    {'items': _items_with(x, 'ai_completed', {'required': False})}), 201)['record']
    d = h.ok(h.get(f'/crt/requirement-sets/{a["id"]}/diff'))
    row = {r['key']: r for r in next(i for i in d['items'] if i['id'] == 'ai_completed')['rows']}
    assert row['required']['now'] is False and row['required_applied']['now'] is False, row


def test_watch_a_a_stored_non_boolean_required_meets_is_not_backed(h, dirs):
    w = world(h, endorsements=_no_completed(), party_name='A Watch Inc')
    _raw_patch(dirs, w['set']['id'], lambda d: d.update(items=[dict(i, required=None) if i['id'] == 'ai_completed'
                                                                else i for i in d['items']]))
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any('required is neither true nor false' in r for r in _reasons(rows)), rows


# ═════════════════════════ B: a lapse waiver is bound to the term's paper ═════════════════════════
def test_b_a_lapse_waiver_binds_to_the_terms_certificate(h):
    w = _lapsed_world(h, 'B Bind Inc')
    wv = _waive(h, w, 'COMPLETED_OPS_LAPSED')
    cert = w['pol']['gl']['data']['source_document_id']
    be = wv['data']['bound_evidence']
    assert [(b['function'], b['document_id']) for b in be] == [('ai_completed', cert)] and be[0]['sha256'], be
    assert h.state(w['asg']['id'])['state'] == 'meets_accepted'


def test_b_a_renewal_raises_the_lapse_again(h):
    w = _lapsed_world(h, 'B Renew Inc')
    _waive(h, w, 'COMPLETED_OPS_LAPSED')
    new, later = _renew(h, w, 'r7b')
    ev = h.state(w['asg']['id'], today=later)
    assert ev['state'] == 'variance' and codes(ev) == ['COMPLETED_OPS_LAPSED'] and ev['accepted'] == [], ev
    assert any('raised again' in n for n in ev['notes']), ev['notes']


def test_b_a_waiver_bound_to_no_paper_covers_nothing(h, dirs):
    """A 1.6.0 waiver (bound to (ai_completed, None, None)) or any binding with no paper: fail closed."""
    w = _lapsed_world(h, 'B Paperless Inc')
    wv = _waive(h, w, 'COMPLETED_OPS_LAPSED')
    _raw_patch(dirs, wv['id'], lambda d: d.update(bound_evidence=[{'function': 'ai_completed', 'document_id': None,
                                                                   'sha256': None}]))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['COMPLETED_OPS_LAPSED'], ev


def test_b_every_waivable_code_is_raised_with_its_paper():
    """Structural: the only occur() calls with no document are the non-waivable MISSING:<line> and the event-bound
    SCOPE_CHANGED (the class, not the instance: a new waivable code raised with no paper fails here)."""
    src = (ROOT / 'services/biz/packs/risk_transfer.py').read_text(encoding='utf-8')
    paperless = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'occur' and \
                len(node.args) == 3 and isinstance(node.args[2], ast.Constant) and node.args[2].value is None:
            paperless.append(ast.unparse(node.args[0]))
    assert sorted(paperless) == ["'SCOPE_CHANGED'", 'code'], paperless
    i = src.index("occur(code, item_id, None)")
    assert "code = f'MISSING:{line}'" in src[i - 300:i]            # the `code` one is MISSING:<line> (never waivable)
    from services.biz.packs import risk_transfer as rt
    assert rt.EVENT_BOUND_CODES == ('SCOPE_CHANGED',) and not 'MISSING'.startswith(rt.WAIVABLE_PREFIXES)


def test_b_a_paperless_place_is_never_covered_whatever_the_binding_says():
    """The coverage rule itself: a waivable code raised with no document or no file hash is never covered, even by a
    waiver bound to exactly that empty place; the event-bound SCOPE_CHANGED is (bound to its timestamp)."""
    from services.biz.packs import risk_transfer as rt
    for place in [('ai_completed', None, None), ('ai_completed', 'doc-1', None), ('gl_occ', None, 'a' * 64)]:
        assert rt.waiver_uncovered('COMPLETED_OPS_LAPSED', [place], {place}) == [place], place
        assert rt.waiver_uncovered('LIMIT_LOW:gl', [place], {place}) == [place], place
    paper = ('ai_completed', 'doc-1', 'a' * 64)
    assert rt.waiver_uncovered('COMPLETED_OPS_LAPSED', [paper], {paper}) == []          # positive control
    scope = ('scope@2026-11-01T00:00:00Z', None, None)
    assert rt.waiver_uncovered('SCOPE_CHANGED', [scope], {scope}) == []


def test_b_positive_control_the_same_term_stays_accepted(h):
    w = _lapsed_world(h, 'B Same Inc')
    _waive(h, w, 'COMPLETED_OPS_LAPSED')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets_accepted' and [a['code'] for a in ev['accepted']] == ['COMPLETED_OPS_LAPSED'], ev


def test_watch_b_positive_control_the_honest_lapse_waiver_passes(h, dirs):
    """RB: the watcher no longer FAILs the ruled meets_accepted (its R4 never consulted a lapse waiver)."""
    w = _lapsed_world(h, 'B Watch Ok Inc')
    _waive(h, w, 'COMPLETED_OPS_LAPSED')
    assert _review(h, w)['state'] == 'meets_accepted'
    v, rows, _ = _watch(dirs)
    assert v == 'PASS', rows


def test_watch_b_already_held_a_lapse_waiver_bound_to_no_paper_never_backs_the_meets(h, dirs):
    w = _lapsed_world(h, 'B Watch Bad Inc')
    wv = _waive(h, w, 'COMPLETED_OPS_LAPSED')
    _raw_patch(dirs, wv['id'], lambda d: d.update(bound_evidence=[{'function': 'ai_completed', 'document_id': None,
                                                                   'sha256': None}]))
    _forge(h, w, state='meets_accepted')
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


# ═════════════════════════ C: an ISO number however it is written ═════════════════════════
NEVER_FORMS = ['CG 20 10 04 13', 'CG 20 26 04 13', 'CG 20 11 (04/13)', 'ISO CG 20 26', 'CG20100413',
               'CG 20 10 rev', 'Form CG 20 26 ed. 04 13']


@pytest.mark.parametrize('form', NEVER_FORMS)
@pytest.mark.parametrize('manuscript', [False, True], ids=['iso', 'manuscript'])
def test_c_never_completed_ops_however_written(h, form, manuscript):
    w = world(h, endorsements=_completed(form=form, manuscript=manuscript), party_name=f'C {form} {manuscript} Inc')
    if manuscript:
        _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev
    assert not any('confirm it as a manuscript' in n for n in ev['notes']), ev['notes']


@pytest.mark.parametrize('form', ['CG 20 33 04 13', 'ISO CG 20 38', 'CG 20 99 04 13'])
def test_c_an_iso_number_with_its_edition_is_never_invited_as_manuscript(h, form):
    w = world(h, endorsements=_completed(form=form), party_name=f'C Invite {form} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] in ('missing', 'cant_tell') and ev['state'] not in ('meets', 'meets_accepted'), ev
    assert not any('confirm it as a manuscript' in n for n in ev['notes']), ev['notes']


@pytest.mark.parametrize('form', ['CG 20 37 04 13', 'ISO CG 20 37', 'CG 20 37 (04/13)'])
def test_c_positive_control_cg2037_with_its_edition_is_cg2037(h, form):
    w = world(h, endorsements=_completed(form=form), party_name=f'C 2037 {form} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and ev['items']['ai_completed'] == 'OK', ev


def test_c_a_number_printed_with_another_edition_reads_cant_tell(h):
    w = world(h, endorsements=_completed(form='CG 20 37 07 04'), party_name='C Clash Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.edition' in codes(ev), ev
    assert 'UNCONFIRMED:crt_endorsement.form' not in codes(ev), ev


@pytest.mark.parametrize('table,code', [({'CG 20 10 04 13': 'ai_completed'}, 'cg2010_never_completed_ops'),
                                        ({'ISO CG 20 26': 'ai_completed'}, 'iso_form_floor'),
                                        ({'CG 20 33 04 13': 'ai_completed'}, 'iso_form_floor'),
                                        ({'CG 20 10 rev': 'ai_completed'}, 'cg2010_never_completed_ops')])
def test_c_the_sets_table_reads_the_number_however_written(h, table, code):
    w = world(h, party_name=f'C Table {sorted(table)[0]} Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'form_functions': dict(x['data']['form_functions'], **table)})
    assert r.status_code == 422 and r.get_json()['code'] == code, r.get_json()


@pytest.mark.parametrize('form', ['CG 20 10 04 13', 'ISO CG 20 26'])
def test_c_already_held_an_items_form_list_reads_the_number_however_written(h, form):
    w = world(h, party_name=f'C Item {form} Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_completed', {'forms': ['CG 20 37', form]})})
    assert r.status_code == 422 and r.get_json()['code'] in ('cg2010_never_completed_ops', 'safety_floor'), r.get_json()


def test_c_the_diff_shows_each_form_as_the_verdict_reads_it(h):
    w = world(h, party_name='C Read As Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise',
                    {'items': _items_with(x, 'ai_completed', {'forms': ['CG 20 37 04 13']})}), 201)['record']
    d = h.ok(h.get(f'/crt/requirement-sets/{a["id"]}/diff'))
    row = {r['key']: r for r in next(i for i in d['items'] if i['id'] == 'ai_completed')['rows']}
    assert row['forms_read_as']['now'] == ['CG2037'], row


def test_c_one_reading_in_engine_and_watcher():
    """The engine's form_key / iso_numbers_in and the watcher's own _form_parts / _iso_in agree on every spelling."""
    import importlib.util
    from services.biz.packs import risk_transfer as rt
    spec = importlib.util.spec_from_file_location('crt_watch_c', str(ROOT / 'tools/biz/crt-verdict-watch.py'))
    wm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wm)
    spellings = ['CG 20 10', 'CG 20 10 04 13', 'ISO CG 20 10', 'CG20100413', 'CG 20 10 (04/13)', 'CG 20 10 ed. 04 13',
                 'CG 20 10 Edition 04-13', 'CG 20 37 07 04', 'CG 20 37 2013', 'CG 20 37 042013', 'CG 20 10 ED',
                 'ACME 12 34', 'CG 20 10 rev', 'MS CG2026', 'ISOLATED 12', 'CG 72 15 04 13', 'cg2037', '', None]
    for s in spellings:
        assert rt.form_parts(s) == wm._form_parts(s), s
        assert rt.iso_numbers_in(s) == wm._iso_in(s), s
        assert rt.never_completed_ops(s) == wm._never_completed(s), s
    assert rt.form_key('CG 20 26 04 13') == 'CG2026' and rt.never_completed_ops('ISO CG 20 11')


@pytest.mark.parametrize('form', ['CG 20 10 04 13', 'ISO CG 20 26'])
def test_watch_c_an_iso_number_with_its_edition_as_manuscript_meets_is_not_backed(h, dirs, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'C Watch {form} Inc')
    _staff_confirm_wording(h, w)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


def test_watch_c_already_held_a_number_printed_with_another_edition_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(form='CG 20 37 07 04'), party_name='C Watch Clash Inc')
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


def test_watch_c_positive_control_cg2037_with_its_edition_passes(h, dirs):
    w = world(h, endorsements=_completed(form='CG 20 37 04 13'), party_name='C Watch Ok Inc')
    assert _review(h, w)['state'] == 'meets'
    v, rows, _ = _watch(dirs)
    assert v == 'PASS', rows


# ═════════════════════════ D: the dev-page routes never follow a link ═════════════════════════
def _sbx_dir(dirs):
    p = Path(dirs['pages']) / 'sandbox' / SBX
    p.mkdir(parents=True, exist_ok=True)
    return p


def _put(h, name, html='<!doctype html><p>v</p>'):
    h.agent()
    return h.c.put(f'{B}/dev/pages/{name}', json={'html': html, 'source': {'channel': 'web', 'ref': 'r7', 'confidence': 1.0}},
                   headers=h.h, base_url=h.base)


def test_d_publishing_over_a_planted_link_is_refused_and_copies_nothing(h, dirs):
    world(h, party_name='D Put Inc')
    key = Path(dirs['a']) / 'staff-surface.key'
    pdir = _sbx_dir(dirs)
    (pdir / 'planted.html').symlink_to(key)
    before = key.read_bytes()
    r = _put(h, 'planted.html')
    assert r.status_code == 409 and r.get_json()['code'] == 'page_not_a_regular_file', r.get_json()
    hist = pdir / '.history'
    assert not hist.exists() or not any(p.read_bytes() == before for p in hist.iterdir())
    assert key.read_bytes() == before and (pdir / 'planted.html').is_symlink()     # never written through either


def test_d_reading_a_planted_link_returns_nothing(h, dirs, tmp_path):
    world(h, party_name='D Get Inc')
    secret = tmp_path / 'outside.txt'
    secret.write_text('outside-secret-text', encoding='utf-8')
    (_sbx_dir(dirs) / 'peek.html').symlink_to(secret)
    h.agent()
    r = h.c.get(f'{B}/dev/pages/peek.html', headers=h.h, base_url=h.base)
    assert r.status_code == 404 and 'outside-secret-text' not in r.get_data(as_text=True), r.get_data(as_text=True)[:300]


def test_d_a_linked_sandbox_folder_is_never_written_through(h, dirs, tmp_path):
    world(h, party_name='D Dir Inc')
    outside = tmp_path / 'outside-dir'
    outside.mkdir()
    sb = Path(dirs['pages']) / 'sandbox'
    sb.mkdir(parents=True, exist_ok=True)
    (sb / SBX).symlink_to(outside, target_is_directory=True)
    r = _put(h, 'escape.html')
    assert r.status_code == 409, r.get_json()
    assert list(outside.iterdir()) == []


def test_d_a_linked_history_folder_is_never_written_through(h, dirs, tmp_path):
    world(h, party_name='D Hist Inc')
    outside = tmp_path / 'outside-hist'
    outside.mkdir()
    pdir = _sbx_dir(dirs)
    (pdir / 'kept.html').write_text('<!doctype html><p>old</p>', encoding='utf-8')
    (pdir / '.history').symlink_to(outside, target_is_directory=True)
    r = _put(h, 'kept.html')
    assert r.status_code == 409, r.get_json()
    assert list(outside.iterdir()) == []


def test_d_the_page_list_never_lists_a_link(h, dirs):
    world(h, party_name='D List Inc')
    pdir = _sbx_dir(dirs)
    (pdir / 'real.html').write_text('<!doctype html><p>r</p>', encoding='utf-8')
    (pdir / 'link.html').symlink_to(Path(dirs['a']) / 'staff-surface.key')
    h.agent()
    files = [p['file'] for p in h.ok(h.c.get(f'{B}/dev/pages', headers=h.h, base_url=h.base))['pages']]
    assert files == ['real.html'], files


def test_d_the_dev_page_routes_are_covered_by_the_no_follow_guard_not_excused():
    from tests.test_agent_writable_io_guard import ALLOWLIST
    assert not [k for k in ALLOWLIST if k.startswith('routes/biz.py')]
    src = (ROOT / 'routes/biz.py').read_text(encoding='utf-8')
    body = src[src.index('def dev_page_put('):src.index('# ── records')]
    assert 'shutil.' not in body and 'os.replace(' not in body and 'Anchored(' in body
