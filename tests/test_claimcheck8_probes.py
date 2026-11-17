"""Gate-4 re-check #8 probes (non-author, opus, 2026-11-17), on the COMBINED tree (70e5666 + PR #597 47d88bf).

Written from the spec, never from the 1.7.0 fixes:
- plan 4.3 (items[] {... forms, edition ...}), 4.4 rules 2/3, the 1.4.0 LOW note "the set item's edition is a
  requirement: an endorsement on another edition is WRONG_EDITION", R3 "CG 20 10 never satisfies completed operations
  by any route: not as manuscript", the 1.6.0 G note "the note on an unlisted ISO form never tells staff to confirm it
  as a manuscript", the 1.7.0 C note "an ISO number however it is written ... one reading ... the watcher's own
  _form_parts / _iso_in, cross-checked" and the 1.7.0 D note "a link or a non-plain folder at the page path is refused
  (409), never followed, read or copied";
- the shipped approval page (cca-crt-requirements.html itemText: `(it.forms || []).join('/') + ' ' + (it.edition ||
  '')` for an endorsement item; the li's text collapses whitespace when rendered);
- PR #597's guard idea (no agent-written path is followed).
Threat model: a prompt-injected agent holding the agent key (or a dev-sandbox key) with its own HTTP client, plus
honest staff who approve exactly what the page shows and re-pin when the label says a newer set exists.

Convention (round 7 onward): a HOLE probe asserts the SPEC outcome, so FAILED means the hole is real. CONTROL probes
assert the good outcome and pass.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, END_FIELDS, SBX, _ends, codes, confirm, dirs, h, world,
)
from tests.test_biz_crt_tools import _watch

ROOT = Path(__file__).resolve().parent.parent


def _review_and_watch(h, dirs, w, tag):
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']
    v, rows, _ = _watch(dirs)
    print(f'WATCH {tag}', v, rv['state'], json.dumps(rows)[:700])
    return v, rv


def _approve_as_page(h, sid):
    """What cca-crt-requirements.html does: GET the diff, render it, POST approve with the diff's sha256."""
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    rec = h.ok(h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']}))['record']
    return d, rec


def _repin_if_prompted(h, w, rec):
    """Staff re-pin only when the assignment's label says this set is the newer requirement set."""
    ev = h.state(w['asg']['id'])
    if (ev.get('newer_requirement_set') or {}).get('id') != rec['id']:
        return False
    h.staff()
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': rec['id'],
                                                         'requirement_set_version': rec['version']}}, a['version']))
    return True


def _page_item_text(it):
    """cca-crt-requirements.html itemText for an endorsement item, as the browser renders the <li> (runs of
    whitespace collapse to one space)."""
    s = ('Suggested: ' if it.get('required') is False else '') + '/'.join(it.get('forms') or []) + ' ' + \
        (it.get('edition') or '') + ' (' + it['function'].replace('_', ' ') + ')' + \
        (', naming you and this project' if it.get('names_parties') else '')
    return re.sub(r'\s+', ' ', s).strip()


def _completed(**kw):
    e = _ends()
    e['ai_completed'] = {**e['ai_completed'], **kw}
    return e


# ───── R8-E: an edition written into the item's FORM LIST is dropped; the page shows the same requirement ─────
def test_R8_E0_control_an_older_edition_against_the_template_set_is_wrong_edition(h):
    """Control: the mep template's completed-ops item asks for CG 20 37, edition 04 13. The sub's CG 20 37 is the
    07 04 edition: WRONG_EDITION:ai_completed (plan 1.4.0 LOW: the item's edition is a requirement, not a note)."""
    w = world(h, endorsements=_completed(edition='07 04'), party_name='E Zero Inc')
    ev = h.state(w['asg']['id'])
    print('E0', ev['state'], codes(ev))
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_EDITION:ai_completed'], ev


@pytest.mark.parametrize('how', ['absent', 'null', 'empty'])
def test_R8_E1_HOLE_an_edition_moved_into_the_form_list_still_binds(h, dirs, how):
    """The agent revises the approved, pinned set with ONE item changed: ai_completed's forms ['CG 20 37'] +
    edition '04 13' become forms ['CG 20 37 04 13'] with the edition key removed (or null / ""). The approval page's
    item list renders the SAME requirement text before and after ('CG 20 37 04 13 (ai completed), naming you and this
    project'), the diff base is the approved set, there is no floor violation and forms_read_as is unchanged
    ('CG2037'). Staff approve what the page showed and re-pin as the label prompts. The engine now reads no edition
    requirement at all, so the 07 04 endorsement reaches plain Meets. Spec (1.4.0 LOW + 1.7.0 C 'an edition after the
    number ... applies to the item's form list'): the 04 13 edition stays a requirement (WRONG_EDITION), or the set
    is refused / Can't tell -- never a plain Meets."""
    w = world(h, endorsements=_completed(edition='07 04'), party_name=f'E One {how} Inc')
    aid = w['asg']['id']
    ev0 = h.state(aid)
    assert codes(ev0) == ['WRONG_EDITION:ai_completed'], ev0                    # control: honest state
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    before = next(it for it in x['data']['items'] if it['id'] == 'ai_completed')
    items = []
    for it in x['data']['items']:
        if it['id'] == 'ai_completed':
            it = {k: v for k, v in it.items() if k != 'edition'}
            it['forms'] = ['CG 20 37 04 13']
            if how != 'absent':
                it['edition'] = None if how == 'null' else ''
        items.append(it)
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items})
    print('E1 agent revise', r.status_code, (r.get_json() or {}).get('code'), (r.get_json() or {}).get('warnings'))
    a = h.ok(r, 201)['record']
    d, rec = _approve_as_page(h, a['id'])
    after = next(it for it in rec['data']['items'] if it['id'] == 'ai_completed')
    item = next(i for i in d['items'] if i['id'] == 'ai_completed')
    changed = [(row['key'], row['was'], row['now']) for row in item['rows'] if row['changed']]
    print('E1 page item list before:', repr(_page_item_text(before)), '| after:', repr(_page_item_text(after)),
          '| changed rows', changed, '| floor', d['floor_violations'], '| against', d['against'].get('approved'))
    assert d['against']['id'] == x['id'] and d['against']['approved'] is True and not d['floor_violations']
    assert _page_item_text(before) == _page_item_text(after)                    # the page's requirement text is the same
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(aid)
    print('E1', how, ev0['state'], codes(ev0), '->', ev['state'], codes(ev), ev['items'].get('ai_completed'))
    if ev['state'] in ('meets', 'meets_accepted'):
        _review_and_watch(h, dirs, w, f'E1[{how}]')
    assert ev['state'] != 'meets', ev                                           # spec: never a plain Meets


# ───── R8-H: an ISO number written with a look-alike character escapes "ISO beats manuscript" ─────
HOMOGLYPHS = {'cyrillic-C': 'СG 20 10', 'letter-O-for-zero': 'CG 2O 1O', 'cyrillic-C-20-26': 'СG 20 26'}


@pytest.mark.parametrize('label', list(HOMOGLYPHS))
def test_R8_H0_HOLE_a_look_alike_iso_number_is_never_invited_as_a_manuscript(h, label):
    """Ruling G / 1.7.0 C: CG 20 10 / 20 26 'anywhere in the field' are never invited as manuscripts. The agent files
    the CG 20 10 endorsement's form as it LOOKS on the page ('СG 20 10' with a Cyrillic Es, or 'CG 2O 1O' with the
    letter O); staff confirm the field (it reads exactly like the paper). Not manuscript yet. Spec: MISSING (CG 20 10
    is never completed operations) or a refusal at write; never the 'confirm it as a manuscript form' invitation."""
    form = HOMOGLYPHS[label]
    w = world(h, endorsements=_completed(form=form), party_name=f'H Zero {label} Inc')
    ev = h.state(w['asg']['id'])
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    print('H0', label, repr(form), ev['state'], codes(ev), note)
    assert not any('manuscript form' in n and 'never as a manuscript' not in n for n in note), note


@pytest.mark.parametrize('label', list(HOMOGLYPHS))
def test_R8_H1_HOLE_a_look_alike_iso_number_never_counts_for_completed_ops(h, dirs, label):
    """R3: 'CG 20 10 never satisfies completed operations by any route: not as manuscript'. Staff follow the H0
    invitation (manuscript true, completed-operations wording person-confirmed). Spec: MISSING:ai_completed."""
    form = HOMOGLYPHS[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'H One {label} Inc')
    h.staff()
    e = w['end']['ai_completed']
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])
    ev = h.state(w['asg']['id'])
    print('H1', label, repr(form), ev['state'], codes(ev), ev['items'].get('ai_completed'))
    if ev['state'] in ('meets', 'meets_accepted'):
        _review_and_watch(h, dirs, w, f'H1[{label}]')
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev


def test_R8_H2_control_the_ascii_number_is_missing(h):
    """Control: the plain 'CG 20 10' in the same manuscript world is MISSING (R3 holds for ASCII)."""
    w = world(h, endorsements=_completed(form='CG 20 10', manuscript=True), party_name='H Two Inc')
    ev = h.state(w['asg']['id'])
    assert 'MISSING:ai_completed' in codes(ev), ev


# ───── R8-F: the watcher's own form reader agrees with the engine (1.7.0 C 'cross-checked by a test') ─────
def _watcher():
    spec = importlib.util.spec_from_file_location('crt_watch_r8', ROOT / 'tools' / 'biz' / 'crt-verdict-watch.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


AGREE = ['CG 20 10 04 13', 'ISO CG 20 10', 'CG 20 37 (04/13)', 'CG 20 10 CG 20 37', 'CG 20 37 042013']
DISAGREE = {'cyrillic-C': 'СG 20 10', 'cyrillic-O-inside': 'cg20О37', 'cyrillic-in-20-26': 'CGО/С2026',
            'arabic-zero': 'CG 20 1٠'}


def _reading(rt, w, s):
    eng = (rt.form_parts(s), sorted(rt.iso_numbers_in(s)), rt.never_completed_ops(s),
           rt.iso_floor_clash(s, 'ai_completed') is None, rt.iso_floor_clash(s, 'ai_ongoing') is None)
    wat = (w._form_parts(s), sorted(w._iso_in(s)), w._never_completed(s),
           w._clash(s, 'ai_completed') is None, w._clash(s, 'ai_ongoing') is None)
    return eng, wat


@pytest.mark.parametrize('s', AGREE)
def test_R8_F0_control_engine_and_watcher_read_ascii_form_numbers_alike(s):
    from services.biz.packs import risk_transfer as rt
    eng, wat = _reading(rt, _watcher(), s)
    assert eng == wat, (eng, wat)


@pytest.mark.parametrize('label', list(DISAGREE))
def test_R8_F1_HOLE_engine_and_watcher_read_every_form_field_alike(label):
    """1.7.0 C: one reading of a form number; the watcher has its own copy 'cross-checked by a test'. The engine's
    _squash drops every non-ASCII character ([^A-Z0-9]); the watcher's keeps every isalnum() one. Spec: both read the
    same string the same way (or both refuse it)."""
    from services.biz.packs import risk_transfer as rt
    s = DISAGREE[label]
    eng, wat = _reading(rt, _watcher(), s)
    print('F1', label, repr(s), 'engine', eng, 'watcher', wat)
    assert eng == wat, (eng, wat)


# ───── R8-D: dev-page routes never follow a link anywhere on the path (1.7.0 D) ─────
def _db_files(dirs):
    return sorted(p.name for p in Path(dirs['a']).iterdir())


def test_R8_D2_HOLE_a_history_folder_link_never_carries_a_write_out_of_the_canvas_folder(h, dirs):
    """The dev-sandbox agent replaces its sandbox's .history folder with a link to the db dir, then republishes a page
    it owns. Spec (1.7.0 D): a link on the page path is refused (409), never followed: nothing lands in the db dir."""
    world(h, party_name='D Two Inc')
    pdir = Path(dirs['pages']) / 'sandbox' / SBX
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / 'own-view.html').write_text('<!doctype html><p>old</p>', encoding='utf-8')
    (pdir / '.history').symlink_to(dirs['a'], target_is_directory=True)
    before = _db_files(dirs)
    h.agent()
    r = h.c.put(f'{B}/dev/pages/own-view.html', json={'html': '<!doctype html><p>new</p>',
                                                       'source': {'channel': 'web', 'ref': 'r8d2', 'confidence': 1.0}},
                headers=h.h, base_url=h.base)
    after = _db_files(dirs)
    print('D2', r.status_code, (r.get_json() or {}).get('code'), 'db dir gained', sorted(set(after) - set(before)))
    assert set(after) == set(before), 'a write landed in the db dir'
    assert r.status_code == 409, r.get_json()


def test_R8_D3_HOLE_a_linked_sandbox_folder_is_never_listed_read_or_written(h, dirs):
    """The sandbox folder itself (canvas-pages/sandbox/<sb>) is a link to the db dir. Spec: the list shows nothing
    from the db dir, a GET reads nothing and a PUT writes nothing there."""
    world(h, party_name='D Three Inc')
    sb = Path(dirs['pages']) / 'sandbox'
    sb.mkdir(parents=True, exist_ok=True)
    (Path(dirs['a']) / 'probe-in-db.html').write_text('<!doctype html><p>db side</p>', encoding='utf-8')
    (sb / SBX).symlink_to(dirs['a'], target_is_directory=True)
    before = _db_files(dirs)
    h.agent()
    ls = h.c.get(f'{B}/dev/pages', headers=h.h, base_url=h.base)
    g = h.c.get(f'{B}/dev/pages/probe-in-db.html', headers=h.h, base_url=h.base)
    p = h.c.put(f'{B}/dev/pages/new-in-db.html', json={'html': '<!doctype html><p>x</p>',
                                                       'source': {'channel': 'web', 'ref': 'r8d3', 'confidence': 1.0}},
                headers=h.h, base_url=h.base)
    after = _db_files(dirs)
    listed = [x['file'] for x in (ls.get_json() or {}).get('pages', [])]
    print('D3 list', ls.status_code, listed, '| get', g.status_code, '| put', p.status_code,
          (p.get_json() or {}).get('code'), '| db gained', sorted(set(after) - set(before)))
    assert 'probe-in-db.html' not in listed
    assert g.status_code != 200
    assert set(after) == set(before) and p.status_code == 409


def test_R8_D4_control_a_plain_republish_keeps_the_previous_version(h, dirs):
    world(h, party_name='D Four Inc')
    pdir = Path(dirs['pages']) / 'sandbox' / SBX
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / 'plain-view.html').write_text('<!doctype html><p>old</p>', encoding='utf-8')
    h.agent()
    r = h.c.put(f'{B}/dev/pages/plain-view.html', json={'html': '<!doctype html><p>new</p>',
                                                         'source': {'channel': 'web', 'ref': 'r8d4', 'confidence': 1.0}},
                headers=h.h, base_url=h.base)
    kept = r.get_json().get('previous_kept_as')
    assert r.status_code == 200 and kept and (pdir / '.history' / kept).read_text() == '<!doctype html><p>old</p>'


# ───── R8-G: the edition field may carry ANOTHER form's number; nothing compares it with the form field ─────
@pytest.mark.parametrize('edition', ['CG 20 37 04 13', 'CG 20 10 04 13'], ids=['same-form', 'other-form'])
def test_R8_G1_HOLE_an_edition_field_naming_another_form_is_never_a_pass(h, dirs, edition):
    """MED ruling (1.3.0) 'editions are read as the footer prints them' and 1.7.0 C 'a number printed with an edition
    other than the edition field reads Can't tell'. The reverse case: the form field says CG 20 37 while the edition
    field, as the footer prints it, says CG 20 10 04 13 (the paper's real footer). The engine strips any form number
    from the edition field without comparing it. Spec reading: a field pair that names two different ISO numbers is
    Can't tell, never a pass. 'same-form' is the control."""
    w = world(h, endorsements=_completed(edition=edition), party_name=f'G One {edition} Inc')
    ev = h.state(w['asg']['id'])
    print('G1', repr(edition), ev['state'], codes(ev), ev['items'].get('ai_completed'))
    if edition == 'CG 20 37 04 13':
        assert ev['state'] == 'meets', ev
    else:
        if ev['state'] in ('meets', 'meets_accepted'):
            _review_and_watch(h, dirs, w, 'G1')
        assert ev['state'] != 'meets', ev


# ───── R8-B: 1.7.0 B controls (lapse waiver bound to the GL certificate; the watcher's RB) ─────
def test_R8_B2_control_an_honest_same_term_lapse_waiver_is_accepted_and_the_watcher_agrees(h, dirs):
    """1.7.0 B: 'The watcher accepts the honest same-term lapse waiver (RB), which it used to FAIL.'"""
    from datetime import timedelta
    from tests.test_biz_crt_pack import STAFF_1, TODAY, iso
    e = _ends()
    e.pop('ai_completed')
    w = world(h, endorsements=e, party_name='B Two Inc', project={'substantial_completion': iso(TODAY - timedelta(days=30))})
    aid = w['asg']['id']
    assert codes(h.state(aid)) == ['COMPLETED_OPS_LAPSED']
    h.staff()
    wv = h.mk('crt_waiver', {'crt_assignment_id': aid, 'variance_code': 'COMPLETED_OPS_LAPSED',
                             'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                             'acceptance_document_id': w['docs']['waiver_ok']['id'],
                             'recorded_by': 'clerk:' + STAFF_1, 'reason': 'accepted in writing for this term',
                             'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=700))})
    print('B2 bound', wv['data'].get('bound_evidence'))
    ev = h.state(aid)
    assert ev['state'] == 'meets_accepted', ev
    assert [b['document_id'] for b in wv['data']['bound_evidence']] == [w['docs']['cert']['id']]
    v, rv = _review_and_watch(h, dirs, w, 'B2')
    assert v == 'PASS'


# ───── R8-A: 1.7.0 A control (a staff edit sending a non-boolean `required` is refused) ─────
@pytest.mark.parametrize('value', [None, 0, '', 'false'], ids=['null', 'zero', 'empty', 'string-false'])
def test_R8_A2_control_a_staff_edit_with_a_non_boolean_required_is_refused(h, value):
    w = world(h, party_name=f'A Two {value!r} Inc', approve=False)
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [dict(it, required=value) if it['id'] == 'ai_completed' else it for it in x['data']['items']]
    h.staff()
    r = h.patch(f'/crt_requirement_set/{x["id"]}', {'data': {'items': items}}, x['version'])
    print('A2', repr(value), r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'required_not_boolean', r.get_json()
