"""Gate-4 re-check #12 probes (non-author, opus, 2026-11-17), on 6b5187c (rules crt-rules-1.10.1); before = 762da21.

Written from the spec (plan 4.4, the 1.10.1 notes and the host rulings D / M1 / M2 / M5 / N3 as written), from R3 / F /
H6 / H8 / P / X1 / FP1, and from the behaviour of 762da21 (rules 1.10.0). Never from the 1.10.1 code paths:
- D: "the floor reads every exact reading ... 'CG 70 10' / 'C9 70 10' (read exactly as CG 20 10) mapped to completed
  operations, or 'CG 70 01' (CG 20 01, P&NC) listed as additional insured, are refused (422) and break the floor when
  stored. On the paper, after the fold check, an exact reading of CG 20 10 / 20 26 / 20 11 on completed operations
  reads MISSING (R3). ... 'CG 70 49' / 'CG 26 39' still list. The near note names the exact reading."
- M1: "In a form list "Nov '85" reads as Nov 1985 ...: pre-2004, refused. Any other mark beside a month word
  ('Nov 13*', "Nov '2013") is no date (422 edition_unreadable), never 'any edition'; stored, both copies' floors see
  it. An endorsement's edition FIELD is unchanged."
- M2: "A run of letters that begins a month name ('OCT', 'Oct', 'OCTOBER') is read as that word before the
  O-inside-a-number test, in the look-alike check and the form-list reader ... on the set and on the paper. A real
  look-alike still folds ('CG 20 1O Nov 2013', 'OCT 2O13')."
- M5: "'Ed.' / 'EDITION' before a form-list edition is skipped ('CG 20 10 Ed. Nov 2013' binds)."
- N3: "the watcher's floor reads every form, as the engine's does."
Threat model as in rounds 7-11: a prompt-injected agent holding the agent key, plus honest staff who confirm a field
that reads like the paper, follow the engine's notes, approve what the page shows and re-pin when prompted.

Convention: a HOLE probe asserts the SPEC outcome, so FAILED means the hole is real. CONTROL probes pass on both trees
unless marked NEW (a 1.10.1 behaviour: passes on 6b5187c, fails on 762da21). WATCH probes pin TODAY's reading of a
ruling-dependent behaviour on 6b5187c.
"""
import importlib.util
import json
import random

from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    END_FIELDS, TODAY, _ends, codes, confirm, dirs, h, world,
)
from tests.test_biz_crt_tools import _watch

ROOT = Path(__file__).resolve().parent.parent


def _watcher():
    spec = importlib.util.spec_from_file_location('crt_watch_r12', ROOT / 'tools' / 'biz' / 'crt-verdict-watch.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _review_and_watch(h, dirs, w, tag):
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']
    v, rows, _ = _watch(dirs)
    print(f'WATCH {tag}', v, rv['state'], json.dumps(rows)[:600])
    return v, rv


def _completed(**kw):
    e = _ends()
    e['ai_completed'] = {**e['ai_completed'], **kw}
    return e


def _approve_as_page(h, sid):
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    rec = h.ok(h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']}))['record']
    return d, rec


def _repin_if_prompted(h, w, rec):
    ev = h.state(w['asg']['id'])
    if (ev.get('newer_requirement_set') or {}).get('id') != rec['id']:
        return False
    h.staff()
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': rec['id'],
                                                         'requirement_set_version': rec['version']}}, a['version']))
    return True


def _items_with(x, item_id, forms=None, edition=...):
    items = []
    for it in x['data']['items']:
        if it['id'] == item_id:
            it = dict(it)
            if forms is not None:
                it['forms'] = forms
            if edition is None:
                it.pop('edition', None)
            elif edition is not ...:
                it['edition'] = edition
        items.append(it)
    return items


def _revise(h, w, item_id, forms=None, edition=..., form_functions=None):
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    body = {'items': _items_with(x, item_id, forms, edition)}
    if form_functions:
        body['form_functions'] = {**(x['data'].get('form_functions') or {}), **form_functions}
    h.agent()
    return x, h.post(f'/crt/requirement-sets/{x["id"]}/revise', body)


def _item_view(d, iid):
    return next(i for i in d['items'] if i['id'] == iid)['now']


def _manuscript(h, w, key, wording=False):
    h.staff()
    e = w['end'][key]
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    fields = list(END_FIELDS)
    if wording:
        h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
        cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
        fields.append('completed_ops_wording')
    confirm(h, 'crt_endorsement', cur, fields, cur['data']['document_id'])
    return h.state(w['asg']['id'])


def _code(r):
    return (r.get_json() or {}).get('code')


# ───────────── D: every exact reading, other routes ─────────────
# The paper route: no set change at all. The completed-ops endorsement is filed with a spelling that reads exactly as a
# never-completed number. D: "On the paper, after the fold check, an exact reading of CG 20 10 / 20 26 / 20 11 on
# completed operations reads MISSING (R3)". These spellings need no fold (all ASCII digits and letters).
PAPER_EXACT = {'7-for-2 2010': 'CG 70 10', '9-and-7 2010': 'C9 70 10', '6-for-0 2011': 'CG 26 11',
               '7-for-2 2026': 'CG 70 26', '8-for-0 2010': 'CG 28 10'}


@pytest.mark.parametrize('label', list(PAPER_EXACT))
def test_R12_D5_HOLE_paper_exact_reading_of_a_never_completed_number_reads_missing(h, label):
    form = PAPER_EXACT[label]
    w = world(h, endorsements=_completed(form=form), party_name=f'D Five {label} Inc')
    ev = h.state(w['asg']['id'])
    print('D5', label, repr(form), ev['state'], codes(ev), [n for n in ev['notes'] if n.startswith('ai_completed')][:1])
    assert ev['state'] not in ('meets', 'meets_accepted'), ev
    assert 'MISSING:ai_completed' in codes(ev), (ev['state'], codes(ev))


@pytest.mark.parametrize('label', ['7-for-2 2010', '9-and-7 2010'])
def test_R12_D5b_HOLE_paper_exact_reading_as_manuscript_with_wording_reads_missing(h, label):
    """Same, through the manuscript route (staff confirm the completed-ops wording). R3: never by any route."""
    form = PAPER_EXACT[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'D Five B {label} Inc')
    ev = _manuscript(h, w, 'ai_completed', wording=True)
    print('D5b', label, repr(form), ev['state'], codes(ev))
    assert 'MISSING:ai_completed' in codes(ev), (ev['state'], codes(ev))


def test_R12_D6_HOLE_table_only_mapping_of_an_exact_cg2010_reading_is_refused(h):
    """D: a table entry alone (the item's list unchanged) mapping 'CG 70 10' to completed operations is refused."""
    w = world(h, party_name='D Six Inc')
    _x, r = _revise(h, w, 'ai_completed', form_functions={'CG 70 10': 'ai_completed'})
    print('D6', r.status_code, _code(r))
    assert r.status_code == 422, r.get_json()


@pytest.mark.parametrize('form', ['CG 70 10', 'C9 70 10', 'CG 26 11'])
def test_R12_D7_HOLE_staff_edit_of_a_draft_cannot_map_an_exact_cg2010_reading(h, form):
    """D on another set-write route: a staff edit of a DRAFT set ("422 on every requirement-set write")."""
    w = world(h, party_name=f'D Seven {form} Inc', approve=False)
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    data = {'items': _items_with(x, 'ai_completed', ['CG 20 37', form]),
            'form_functions': {**(x['data'].get('form_functions') or {}), form: 'ai_completed'}}
    h.staff()
    r = h.patch(f'/crt_requirement_set/{x["id"]}', {'data': data}, x['version'])
    print('D7', repr(form), r.status_code, _code(r))
    assert r.status_code == 422, r.get_json()


@pytest.mark.parametrize('form', ['CG 70 10', 'C9 70 10'])
def test_R12_D8_HOLE_agent_generic_create_cannot_map_an_exact_cg2010_reading(h, form):
    """D on the agent's generic DRAFT create."""
    from services.biz.packs import risk_transfer as rt
    tpl = next(t for t in rt.load_templates()['templates'] if t['key'] == 'mep')
    items = [dict(it, forms=['CG 20 37', form]) if it.get('id') == 'ai_completed' else it for it in tpl['items']]
    ff = dict(tpl.get('form_functions') or {}, **{form: 'ai_completed'})
    h.agent()
    r = h.post('/crt_requirement_set', {'data': {'name': 'x', 'trade_category': 'mep', 'items': items,
                                                 'form_functions': ff}})
    print('D8', repr(form), r.status_code, _code(r))
    assert r.status_code == 422, r.get_json()


def test_R12_D9_control_a_two_number_field_hiding_an_exact_cg2010_reading_never_meets(h):
    """1.7.0 C ("any CG 20 xx written inside other text") + D: 'CG 20 37 / CG 70 10' on completed-ops paper names CG 20 10
    exactly; it never counts for completed operations."""
    w = world(h, endorsements=_completed(form='CG 20 37 / CG 70 10'), party_name='D Nine Inc')
    _x, r = _revise(h, w, 'ai_completed', forms=['CG 20 37', 'CG 20 37 / CG 70 10'])
    print('D9 revise', r.status_code, _code(r))
    if r.status_code == 201:
        _d, rec = _approve_as_page(h, r.get_json()['record']['id'])
        _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    print('D9', ev['state'], codes(ev))
    assert ev['state'] not in ('meets', 'meets_accepted'), ev


@pytest.mark.parametrize('label', ['9-and-7 2010'])
def test_R12_D10_control_new_the_near_note_names_the_exact_reading(h, label):
    """1.10.1: 'The near note names the exact reading' (re-check #11 note-quality item)."""
    form = PAPER_EXACT[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'D Ten {label} Inc')
    ev = _manuscript(h, w, 'ai_completed', wording=True)
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    print('D10', repr(form), note[:2])
    assert any('CG2010' in n or 'CG 20 10' in n for n in note), note


@pytest.mark.parametrize('form', ['CG 70 49', 'CG 26 39', 'CG 20 18', 'CG 20 28', 'CG 20 12'])
def test_R12_D0_control_honest_numbers_whose_exact_reading_is_no_floor_number_still_list(h, form):
    w = world(h, party_name=f'D Zero {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'])
    print('D0', repr(form), r.status_code, _code(r))
    assert r.status_code == 201, r.get_json()


def test_R12_D11_WATCH_a_digit_glyph_spelling_of_a_non_cg20_floor_number_maps_to_completed_ops(h, dirs):
    """WATCH (spec gap, ruling needed): the exact reading is defined for CG 20 xx only (FP1). 'CG 74 04' reads, with the
    same 7-for-2 glyph as D, as CG 24 04 (the floor's waiver-of-subrogation number), but no exact reading is taken, so
    the set may map it to completed operations and the paper reads Meets with a watcher PASS. Asserts today's reading."""
    form = 'CG 74 04'
    w = world(h, endorsements=_completed(form=form), party_name='D Eleven Inc')
    _x, r = _revise(h, w, 'ai_completed', forms=['CG 20 37', form], form_functions={form: 'ai_completed'})
    print('D11 revise', r.status_code, _code(r))
    # WATCH flipped by the host ruling (rules 1.10.2, D11): a floor-table look-alike is refused when mapped, like D
    assert r.status_code == 422, r.get_json()
    return
    assert r.status_code == 201, r.get_json()
    _d, rec = _approve_as_page(h, r.get_json()['record']['id'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    v = None
    if ev['state'] in ('meets', 'meets_accepted'):
        v, _rv = _review_and_watch(h, dirs, w, 'D11')
    print('D11', ev['state'], codes(ev), 'watcher', v)
    assert ev['state'] == 'meets' and v == 'PASS', (ev['state'], codes(ev), v)


# ───────────── M1: the apostrophe year ─────────────
@pytest.mark.parametrize('form', ["CG 20 10 Nov '85", "CG 20 10 (Nov '85)", "CG 20 10 Nov'85", "CG 20 10 Nov. '85",
                                  "CG 20 10 Ed. Nov '85", "CG 20 10 Jun '03", "CG 20 10 Dec '99"])
def test_R12_M1_control_new_apostrophe_year_before_2004_is_refused(h, form):
    w = world(h, party_name=f'M One {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'], edition=None)
    print('M1', repr(form), r.status_code, _code(r))
    assert r.status_code == 422 and _code(r) == 'pre_2004_edition_refused', r.get_json()


@pytest.mark.parametrize('form', ["CG 20 10 Nov 13*", "CG 20 10 Nov '2013", "CG 20 10 Nov `85", "CG 20 10 'Nov 85",
                                  "CG 20 10 Nov '85'", "CG 20 10 Nov 'O4", "CG 20 10 {Nov 1985}"])
def test_R12_M1b_control_new_any_other_mark_beside_a_month_word_is_unreadable(h, form):
    w = world(h, party_name=f'M One B {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'], edition=None)
    print('M1b', repr(form), r.status_code, _code(r))
    assert r.status_code == 422, r.get_json()
    assert _code(r) in ('edition_unreadable', 'form_lookalike'), r.get_json()


def test_R12_M1c_control_new_apostrophe_year_after_2004_binds(h):
    w = world(h, endorsements=_ends(edition='04 13'), party_name='M One C Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', "CG 20 10 Nov '13", 'CG 20 33', 'CG 20 38'], edition=None)
    d, rec = _approve_as_page(h, h.ok(r, 201)['record']['id'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    applied = _item_view(d, 'ai_ongoing').get('edition_applied')
    print('M1c', applied, ev['state'], codes(ev))
    assert applied == '11/2013' and 'WRONG_EDITION:ai_ongoing' in codes(ev), (applied, codes(ev))


def test_R12_M1d_control_apostrophe_month_word_on_the_paper_against_another_edition_is_cant_tell(h):
    """1.7.0 C: a form printed with an edition other than the edition field reads Can't tell."""
    w = world(h, endorsements=_completed(form="CG 20 37 Nov '85", edition='04 13'), party_name='M One D Inc')
    ev = h.state(w['asg']['id'])
    print('M1d', ev['state'], codes(ev))
    assert ev['state'] not in ('meets', 'meets_accepted'), ev


def test_R12_M1e_HOLE_stored_apostrophe_and_marked_month_words_break_both_floors():
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    bad = []
    for f in ["CG 20 10 Nov '85", "CG 20 10 Jun '03", "CG 20 10 Nov 13*", "CG 20 10 Nov '2013", "CG 20 10 Ed. Nov '85"]:
        it = {'id': 'ai_ongoing', 'kind': 'endorsement', 'function': 'ai_ongoing', 'line': 'gl', 'required': True,
              'forms': [f, 'CG 20 10']}
        eng, wat = rt.floor_violations({'items': [it]}), wt._floor({'items': [it]})
        print('M1e', repr(f), bool(eng), bool(wat), rt.item_edition(it))
        if not (eng and wat):
            bad.append(f)
    assert not bad, bad


@pytest.mark.parametrize('field', ["Nov '85", "Nov '13"])
def test_R12_M1f_WATCH_the_set_item_edition_field_with_an_apostrophe_year(h, field):
    """WATCH (the edition-field / form-list split, ruled: the FIELD is unchanged). Records what a set item's edition
    FIELD written "Nov '85" / "Nov '13" does on the revise; asserts it is never accepted as 'any edition'."""
    w = world(h, party_name=f'M One F {field} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', edition=field)
    applied = None
    if r.status_code == 201:
        h.staff()
        d = h.ok(h.get(f'/crt/requirement-sets/{r.get_json()["record"]["id"]}/diff'))
        applied = _item_view(d, 'ai_ongoing').get('edition_applied')
    print('M1f', repr(field), r.status_code, _code(r), applied)
    assert r.status_code == 422 or (applied and applied != 'any edition'), (r.status_code, applied)


# ───────────── M2: month words before the O fold ─────────────
@pytest.mark.parametrize('form', ['CG 20 10 OCTOBER 2013', 'CG 20 10 Oct. 2013', 'CG2010OCT2013', 'CG 20 10 oct 13',
                                  'CG 20 10 Ed. Oct 2013'])
def test_R12_M2_control_new_october_binds_in_the_form_list(h, form):
    w = world(h, party_name=f'M Two {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'], edition=None)
    print('M2', repr(form), r.status_code, _code(r))
    assert r.status_code == 201, r.get_json()
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{r.get_json()["record"]["id"]}/diff'))
    assert _item_view(d, 'ai_ongoing').get('edition_applied') == '10/2013'


@pytest.mark.parametrize('form', ['CG 20 1O Oct 2013', 'CG 20 10 Oct 2O13', 'CG 20 10 O4 13', 'CG 2O 10', 'CG 20 1O',
                                  'CG 20 10 OC 2013', 'CG 20 10 O 2013', 'CG 2O 10 OCT 2013'])
def test_R12_M2b_control_a_real_o_for_zero_still_folds_on_the_set(h, form):
    w = world(h, party_name=f'M Two B {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'], edition=None)
    print('M2b', repr(form), r.status_code, _code(r))
    assert r.status_code == 422, r.get_json()


@pytest.mark.parametrize('form,edition', [('CG 2O 37', '04 13'), ('CG 20 37 Oct 2O13', '10 13'),
                                          ('CG 20 37 O4 13', '04 13'), ('CG 2O 37 Oct 2013', '10 13')])
def test_R12_M2c_control_a_real_o_for_zero_on_the_paper_reads_cant_tell(h, form, edition):
    """H6: a value that needed folding reads Can't tell (the M2 exemption must not swallow an O that is a zero)."""
    w = world(h, endorsements=_completed(form=form, edition=edition), party_name=f'M Two C {form} Inc')
    ev = h.state(w['asg']['id'])
    print('M2c', repr(form), ev['state'], codes(ev))
    assert ev['state'] not in ('meets', 'meets_accepted'), ev


def test_R12_M2d_control_new_october_on_the_paper_meets(h, dirs):
    w = world(h, endorsements=_completed(form='CG 20 37 OCTOBER 2013', edition='10 13'), party_name='M Two D Inc')
    _x, r = _revise(h, w, 'ai_completed', edition=None)
    _d, rec = _approve_as_page(h, h.ok(r, 201)['record']['id'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    v = None
    if ev['state'] == 'meets':
        v, _rv = _review_and_watch(h, dirs, w, 'M2d')
    print('M2d', ev['state'], codes(ev), v)
    assert ev['state'] == 'meets' and v == 'PASS', (ev['state'], codes(ev), v)


@pytest.mark.parametrize('field,setted', [('Nov 2013', '11 13'), ('Oct 2013', '10 13')])
def test_R12_M2e_control_october_in_the_paper_edition_field_reads_like_november(h, field, setted):
    """M2 "in the look-alike check": an endorsement edition FIELD 'Oct 2013' is read as October like 'Nov 2013' is read
    as November (L: month names 'JUL 2013', 'Nov 2013' are unaffected by the look-alike check). Both against a set
    edition written numerically must give the same state."""
    w = world(h, endorsements=_completed(edition=field), party_name=f'M Two E {field} Inc')
    _x, r = _revise(h, w, 'ai_completed', edition=setted)
    _d, rec = _approve_as_page(h, h.ok(r, 201)['record']['id'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    print('M2e', repr(field), 'vs', setted, ev['state'], codes(ev))
    assert 'UNCONFIRMED:crt_endorsement.edition' not in codes(ev), (ev['state'], codes(ev))


@pytest.mark.parametrize('form', ['CG 20 10 0CT 1985', 'CG 20 10 N0V 1985', 'CG 20 10 Ed. 0ct 1985', "CG 20 10 '85"])
def test_R12_M2f_WATCH_a_digit_for_letter_month_or_bare_year_reads_any_edition(h, form):
    """WATCH (residual of the M rule "never 'any edition'"; same on 762da21): a 1985 edition written with a digit zero
    for the letter O ('0CT', 'N0V') or as a bare apostrophe year is not a month word, so the entry is kept as an inert
    key and the item reads 'any edition'. Asserts today's reading (201, any edition)."""
    w = world(h, party_name=f'M Two F {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'], edition=None)
    applied = None
    if r.status_code == 201:
        h.staff()
        d = h.ok(h.get(f'/crt/requirement-sets/{r.get_json()["record"]["id"]}/diff'))
        applied = _item_view(d, 'ai_ongoing').get('edition_applied')
    print('M2f', repr(form), r.status_code, _code(r), applied)
    # WATCH flipped by the host ruling (rules 1.10.2, M2f): an unreadable edition never widens to "any edition"
    assert r.status_code == 422 and _code(r) == 'edition_unreadable', (r.status_code, applied)


# ───────────── M5: the Ed. prefix ─────────────
@pytest.mark.parametrize('form,code', [('CG 20 10 Ed. Nov 1985', 'pre_2004_edition_refused'),
                                       ('CG 20 10 EDITION NOV 85', 'pre_2004_edition_refused'),
                                       ('CG 20 10 Ed. Nov', 'edition_unreadable'),
                                       ('CG 20 10 Ed. Ed. Nov 2013', 'edition_unreadable'),
                                       ('CG 20 10 Ed: Nov 2013', 'edition_unreadable')])
def test_R12_M5_control_an_ed_prefix_never_hides_a_refusal(h, form, code):
    w = world(h, party_name=f'M Five {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'], edition=None)
    print('M5', repr(form), r.status_code, _code(r))
    assert r.status_code == 422, r.get_json()


def test_R12_M5b_control_new_edition_prefix_binds_and_the_paper_c_check_sees_it(h):
    w = world(h, endorsements=_completed(form='CG 20 37 EDITION Nov 2013', edition='04 13'), party_name='M Five B Inc')
    ev = h.state(w['asg']['id'])
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', 'CG 20 10 EDITION Nov 2013', 'CG 20 33', 'CG 20 38'],
                    edition=None)
    print('M5b paper', ev['state'], codes(ev), '| revise', r.status_code, _code(r))
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.edition' in codes(ev), ev
    assert r.status_code == 201, r.get_json()


def test_R12_M5c_WATCH_the_endorsement_edition_field_ed_prefix_is_still_no_date(h):
    """WATCH (ruled: 'An edition FIELD "Ed. 04 13" is still no date')."""
    w = world(h, endorsements=_completed(edition='Ed. 04 13'), party_name='M Five C Inc')
    ev = h.state(w['asg']['id'])
    print('M5c', ev['state'], codes(ev))
    assert ev['state'] not in ('meets', 'meets_accepted'), ev


# ───────────── N3 + parity (my own generator) ─────────────
_PIECES = ['CG', 'C', 'G', 'cg', '20', '2', '0', '10', '11', '26', '37', '01', '18', ' ', '-', '/', '.', ',', '(', ')', '[',
           ']', '{', '<', '&', '9', '7', '?', 'U', 'J', 'T', "'", '`', '*', '#', ':', '6', '8', 'Z', 'D', 'Q', 'O', 'I',
           'l', '|', '!', 'S', 'B', 'NOV', 'Nov', 'Oct', 'OCT', 'OCTOBER', '0CT', 'N0V', 'APR', 'Sept', 'Ed.', 'ED',
           'EDITION', 'ISO', '85', "'85", "'13", "'O4", '1985', '13', '2013', '2O13', '04', 'O4', '4', '1', '()', '[]',
           '–', 'é', 'Ｏ', 'О']
_MONTHS = ['Nov', 'NOV', 'Oct', 'OCT', 'oct', 'October', 'OCTOBER', '0CT', 'Oct.', 'Apr', 'Sept', 'May', 'Dec', '11', '1',
           '10', 'O4']


def _gen(r):
    k = r.random()
    if k < 0.45:
        return (r.choice(['CG 20 10', 'CG 20 37', 'CG2010', 'ISO CG 20 26', 'CG 70 10', 'C9 70 10', 'CG 26 11',
                          'CG 20 1O', 'XX 12 34', 'CG 70 01', 'CG 20 01', 'CG 74 04'])
                + r.choice([' ', '', ' (', ', ', ' Ed. ', ' EDITION ', ' ED ', ' Ed ', ' Ed: ']) + r.choice(_MONTHS)
                + r.choice([' ', '/', '-', '. ', " '", "'", ' `', ''])
                + r.choice(['85', '1985', '13', '2013', '03', '04', '2O13', 'O4', '', '13*']) + r.choice(['', ')', ' ', "'", '*']))
    if k < 0.55:
        return ''.join(r.choice('CG(<[{&9687?2Z0OUDQ1Il]JT/\' ') for _ in range(r.randint(4, 9)))
    return ''.join(r.choice(_PIECES) for _ in range(r.randint(1, 9)))


def test_R12_N1_control_engine_and_watcher_agree_on_the_1_10_1_readings():
    """Readings, applied editions and floor (forms with a key), over my generator incl. non-ASCII."""
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    rnd = random.Random(20261117 + 12)
    pairs = [('form_parts', rt.form_parts, wt._form_parts), ('needs_fold', rt.needs_fold, wt._refold),
             ('near', rt.near_iso_number, wt._near_iso), ('exact', rt.iso_exact_reading, wt._exact_iso),
             ('never_completed', rt.never_completed_ops, wt._never_completed)]
    fns = ['ai_ongoing', 'ai_completed', 'pnc', 'waiver_of_subrogation']
    bad = []
    for _ in range(6000):
        s = _gen(rnd)
        for name, a, b in pairs:
            if a(s) != b(s):
                bad.append((name, s, a(s), b(s)))
        it = {'id': 'x', 'kind': 'endorsement', 'function': rnd.choice(fns), 'line': 'gl', 'required': True,
              'forms': [s, rnd.choice(['CG 20 33', 'CG 20 37', 'CG 20 01'])]}
        ed = rnd.choice([None, '', '04 13', '11 85', "Nov '85", 'Oct 2013'])
        if ed is not None:
            it['edition'] = ed
        st = {'items': [it]}
        if rnd.random() < 0.3:
            st['form_functions'] = {s: rnd.choice(fns)}
        ea, wa = rt.item_edition(it), wt._item_ed(it)
        if ea[0] != wa[0] or (ea[1] is None) != (wa[1] is None):
            bad.append(('item_edition', s, ed, ea, wa))
        if rt.form_parts(s)[0] and bool(rt.floor_violations(st)) != bool(wt._floor(st)):
            bad.append(('floor', s, it['function'], st.get('form_functions')))
    print('N1 disagreements', len(bad), bad[:8])
    assert not bad


@pytest.mark.parametrize('form', ['[', '-', "'", '–'])
def test_R12_N3b_HOLE_a_keyless_form_beside_cg2037_on_a_completed_item_breaks_both_floors(form):
    """N3 ("the watcher's floor reads every form, as the engine's does"): on a completed-operations item a keyless
    form next to CG 20 37 breaks the engine's floor (the item lists a form that is not completed operations); the
    watcher must see the same."""
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    it = {'id': 'x', 'kind': 'endorsement', 'function': 'ai_completed', 'line': 'gl', 'required': True,
          'forms': [form, 'CG 20 37']}
    eng, wat = rt.floor_violations({'items': [it]}), wt._floor({'items': [it]})
    print('N3b', ascii(form), eng, wat)
    assert bool(eng) == bool(wat), (eng, wat)


# ───────────── authority boundaries (re-run on the 1.10.1 routes) ─────────────
def test_R12_A1_control_the_agent_cannot_approve_edit_or_what_if(h):
    w = world(h, party_name='A One Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', 'CG 20 10 Ed. Nov 2013', 'CG 20 33', 'CG 20 38'], edition=None)
    sid = h.ok(r, 201)['record']['id'] if r.status_code == 201 else w['set']['id']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    h.agent()
    ra = h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']})
    rp = h.patch(f'/crt_requirement_set/{sid}', {'data': {'name': 'x'}}, 1)
    rt_ = h.get(f'/crt/assignment/{w["asg"]["id"]}/state?today=2030-01-01')
    print('A1', ra.status_code, _code(ra), rp.status_code, _code(rp), rt_.status_code)
    assert ra.status_code == 403 and rp.status_code == 403 and rt_.status_code == 403
