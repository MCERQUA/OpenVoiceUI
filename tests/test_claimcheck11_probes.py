"""Gate-4 re-check #11 probes (non-author, opus, 2026-11-17), on 762da21 (rules crt-rules-1.10.0).

Written from the spec (plan 4.4 and the 1.10.0 notes, host rulings M / FP1 / X1 / N2 / U1) and from the behaviour of
8b0d564 (rules 1.9.1), never from the 1.10.0 code paths:
- M: "'CG 20 10 Nov 1985', '(Nov 1985)', 'NOV 85', 'Apr 2013' and a one-digit month ('1/85') split off the number ...
  That edition binds (H5), a pre-2004 one is refused, a conflict is refused, and a month word that reads as no date
  ... is refused (422 edition_unreadable) and breaks the floor when stored: never 'any edition'";
- FP1: "A look-alike counts only in an EXACT reading of CG 20 xx ... With one real edit only the 1.9.0 map is read ...
  An edition gained through the map counts only beside an ISO number";
- X1: the host's weak-glyph list ( < [ { for C; & 9 for G; ? 7 for 2; U () [] for 0; ] [ J T / ' for 1;
- R3 "CG 20 10 never satisfies completed operations by any route: not as manuscript, not listed on a set item (422),
  not mapped in a set's table (422)"; F "a built-in ISO number keeps its ISO category (CG 20 01 P&NC ...)";
- H6 "A folded number that names CG 20 10 / 20 26 / 20 11 on a completed-operations item stays MISSING";
- the authority boundaries of H4 / R1 / F / LOW (agent key, canvas-origin JWT, staff surface).
Threat model as in rounds 7-10: a prompt-injected agent holding the agent key, plus honest staff who confirm a field
that reads like the paper, follow the engine's notes, approve what the page shows and re-pin when prompted.

Convention: a HOLE probe asserts the SPEC outcome, so FAILED means the hole is real. CONTROL probes assert the good
outcome and pass. WATCH probes assert TODAY's reading of a ruling-dependent behaviour and pass on 762da21.
"""
import importlib.util
import json
import random
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    END_FIELDS, STAFF_1, TODAY, _ends, codes, confirm, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _watch

ROOT = Path(__file__).resolve().parent.parent


def _watcher():
    spec = importlib.util.spec_from_file_location('crt_watch_r11', ROOT / 'tools' / 'biz' / 'crt-verdict-watch.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _review_and_watch(h, dirs, w, tag):
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']
    v, rows, _ = _watch(dirs)
    print(f'WATCH {tag}', v, rv['state'], json.dumps(rows)[:700])
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


def _revise(h, w, item_id, forms=None, edition=..., form_functions=None):
    """The agent revises the approved set's item (forms and / or edition; edition=None drops the key) and, optionally,
    adds entries to the set's form-equivalence table."""
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
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
    body = {'items': items}
    if form_functions:
        body['form_functions'] = {**(x['data'].get('form_functions') or {}), **form_functions}
    h.agent()
    return x, h.post(f'/crt/requirement-sets/{x["id"]}/revise', body)


def _manuscript(h, w, key, wording=False):
    """Staff follow the manuscript route: the endorsement re-confirmed on the surface (wording too when asked)."""
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


def _item_view(d, iid):
    return next(i for i in d['items'] if i['id'] == iid)['now']


# ───────────── D: the open builder decision (a printed digit read as another digit, set side) ─────────────
# 1.10.0 puts 7 (for 2) and 9 (for G) in the exact reading, so 'CG 70 10' / 'C9 70 10' read EXACTLY as CG 20 10 (the
# engine's own fold note says "it reads as CG2010" for them), and the manuscript route is closed. On the set side a
# digit glyph costs one, so a set may list the spelling and map it in its table. R3 / H6 say CG 20 10 never counts for
# completed operations by any route, and a folded spelling that names it stays MISSING.
DIGIT_GLYPH = {'7-for-2': 'CG 70 10', '9-and-7': 'C9 70 10'}


@pytest.mark.parametrize('label', list(DIGIT_GLYPH))
def test_R11_D1_HOLE_a_digit_glyph_spelling_of_cg2010_never_counts_for_completed_ops(h, dirs, label):
    """R3 / H6 under X1: a field that reads exactly as CG 20 10 through the look-alike classes never satisfies
    completed operations, whether by manuscript (closed in 1.10.0) or by being listed + mapped in the set's table."""
    form = DIGIT_GLYPH[label]
    w = world(h, endorsements=_completed(form=form), party_name=f'D One {label} Inc')
    before = h.state(w['asg']['id'])
    _x, r = _revise(h, w, 'ai_completed', forms=['CG 20 37', form], form_functions={form: 'ai_completed'})
    body = r.get_json() or {}
    print('D1', label, repr(form), 'before revise', before['state'], codes(before), '| revise', r.status_code,
          body.get('code'))
    if r.status_code != 201:
        assert r.status_code == 422, body
        return
    d, rec = _approve_as_page(h, body['record']['id'])
    item = _item_view(d, 'ai_completed')
    print('D1 page shows forms', item.get('forms'), 'read as', item.get('forms_read_as'), 'table added',
          d['form_functions']['added'], 'floor', d['floor_violations'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    v = None
    if ev['state'] in ('meets', 'meets_accepted'):
        v, _rv = _review_and_watch(h, dirs, w, f'D1[{label}]')
    print('D1', label, '->', ev['state'], codes(ev), 'watcher', v)
    assert ev['state'] not in ('meets', 'meets_accepted'), (ev['state'], codes(ev), v)


def test_R11_D2_HOLE_a_digit_glyph_spelling_of_cg2001_never_counts_as_additional_insured(h, dirs):
    """F floor under X1: CG 20 01 is P&NC; 'CG 70 01' reads exactly as CG 20 01 and is never accepted as additional
    insured (ongoing ops)."""
    form = 'CG 70 01'
    w = world(h, endorsements=_ends(form=form), party_name='D Two Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'])
    body = r.get_json() or {}
    print('D2 revise', r.status_code, body.get('code'))
    if r.status_code != 201:
        assert r.status_code == 422, body
        return
    d, rec = _approve_as_page(h, body['record']['id'])
    print('D2 page read as', _item_view(d, 'ai_ongoing').get('forms_read_as'), 'floor', d['floor_violations'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    v = None
    if ev['state'] in ('meets', 'meets_accepted'):
        v, _rv = _review_and_watch(h, dirs, w, 'D2')
    print('D2 ->', ev['state'], codes(ev), 'watcher', v)
    assert ev['state'] not in ('meets', 'meets_accepted'), (ev['state'], codes(ev), v)


@pytest.mark.parametrize('form', ['CG 70 49', 'CG 26 39'])
def test_R11_D0_control_an_honest_one_digit_off_number_can_still_be_listed(h, form):
    """1.10.0 note: 'CG 70 49' / 'CG 26 39' can still be listed (their exact readings name no floor number)."""
    w = world(h, party_name=f'D Zero {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'])
    print('D0', repr(form), r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 201, r.get_json()


@pytest.mark.parametrize('form', ['CG 70 10', 'C9 70 10'])
def test_R11_D3_control_a_digit_glyph_spelling_of_cg2010_is_never_a_manuscript(h, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'D Three {form} Inc')
    ev = _manuscript(h, w, 'ai_completed', wording=True)
    print('D3', repr(form), ev['state'], codes(ev))
    assert ev['state'] in ('cant_tell', 'missing'), ev


def test_R11_D4_control_a_strong_glyph_table_key_for_cg2010_is_refused(h):
    w = world(h, party_name='D Four Inc')
    _x, r = _revise(h, w, 'ai_completed', forms=['CG 20 37', 'CG Z0 10'], form_functions={'CG Z0 10': 'ai_completed'})
    print('D4', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422, r.get_json()


# ───────────── M: month-word / one-digit-month editions in the form list ─────────────
@pytest.mark.parametrize('form', ["CG 20 10 Nov '85", "CG 20 10 (Nov '85)"])
def test_R11_M1_HOLE_a_month_word_edition_written_with_an_apostrophe_is_never_any_edition(h, form):
    """M: a month word in the form list that does not read as a date is refused (422 edition_unreadable), 'never "any
    edition"'. The engine's own edition reader documents "Nov '85" as no date."""
    w = world(h, party_name=f'M One {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=[form, 'CG 20 10', 'CG 20 33', 'CG 20 38'], edition=None)
    body = r.get_json() or {}
    print('M1', repr(form), r.status_code, body.get('code'))
    if r.status_code == 201:
        h.staff()
        d = h.ok(h.get(f'/crt/requirement-sets/{body["record"]["id"]}/diff'))
        print('M1 page edition_applied', _item_view(d, 'ai_ongoing').get('edition_applied'), 'floor',
              d['floor_violations'])
    assert r.status_code == 422, body


def test_R11_M1b_HOLE_stored_apostrophe_month_word_breaks_the_floor_in_both_copies():
    """M: stored, an unreadable month word breaks the floor (engine floor_violations and watcher _floor alike)."""
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    it = {'id': 'ai_ongoing', 'kind': 'endorsement', 'function': 'ai_ongoing', 'line': 'gl', 'required': True,
          'forms': ["CG 20 10 Nov '85", 'CG 20 10']}
    eng = rt.floor_violations({'items': [it]})
    wat = wt._floor({'items': [it]})
    print('M1b engine', eng, 'item_edition', rt.item_edition(it), '| watcher', wat, wt._item_ed(it))
    assert eng and wat


@pytest.mark.parametrize('form', ['CG 20 10 Oct 2013', 'CG 20 10 OCT 13'])
def test_R11_M2_HOLE_an_october_edition_in_the_form_list_binds(h, form):
    """M: a month-word edition in the form list binds (H5). October is a month: with the edition field removed the
    revise is accepted and the page shows edition_applied 10/2013."""
    w = world(h, party_name=f'M Two {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'], edition=None)
    body = r.get_json() or {}
    print('M2', repr(form), r.status_code, body.get('code'), (body.get('message') or '')[:160])
    assert r.status_code == 201, body
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{body["record"]["id"]}/diff'))
    applied = _item_view(d, 'ai_ongoing').get('edition_applied')
    print('M2 page edition_applied', applied)
    assert applied == '10/2013', applied


def test_R11_M2b_HOLE_an_october_edition_on_the_paper_reads_as_its_edition(h):
    """1.7.0 C + M: an endorsement form printed with its edition after it ('CG 20 37 Oct 2013') and an edition field
    of '10 13' agree; with the set's edition removed it reads Meets, like 'CG 20 37 10 13'."""
    w = world(h, endorsements=_completed(form='CG 20 37 Oct 2013', edition='10 13'), party_name='M Two B Inc')
    _x, r = _revise(h, w, 'ai_completed', edition=None)
    rec = h.ok(r, 201)['record']
    _d, rec = _approve_as_page(h, rec['id'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    print('M2b', ev['state'], codes(ev), [n for n in ev['notes'] if n.startswith('ai_completed')][:1])
    assert ev['state'] == 'meets', (ev['state'], codes(ev))


def test_R11_M2c_control_the_numeric_october_edition_on_the_paper_meets(h):
    w = world(h, endorsements=_completed(form='CG 20 37 10 13', edition='10 13'), party_name='M Two C Inc')
    _x, r = _revise(h, w, 'ai_completed', edition=None)
    rec = h.ok(r, 201)['record']
    _d, rec = _approve_as_page(h, rec['id'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    print('M2c', ev['state'], codes(ev))
    assert ev['state'] == 'meets', (ev['state'], codes(ev))


@pytest.mark.parametrize('form,code', [('CG 20 10 Nov 1985', 'pre_2004_edition_refused'),
                                       ('CG 20 10 (Nov 1985)', 'pre_2004_edition_refused'),
                                       ('CG 20 10 NOV 85', 'pre_2004_edition_refused'),
                                       ('CG 20 10 1/85', 'pre_2004_edition_refused'),
                                       ('CG 20 10 Nov', 'edition_unreadable'),
                                       ('CG 20 10 Nov 13 2013', 'edition_unreadable')])
def test_R11_M0_control_month_word_and_one_digit_month_refusals(h, form, code):
    w = world(h, party_name=f'M Zero {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=[form, 'CG 20 33', 'CG 20 38'], edition=None)
    print('M0', repr(form), r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == code, r.get_json()


@pytest.mark.parametrize('listed', ['CG 20 10 Apr 2013', 'CG 20 10 4/13'])
def test_R11_M3_control_a_month_word_or_one_digit_month_edition_binds(h, listed):
    w = world(h, endorsements=_ends(edition='07 04'), party_name=f'M Three {listed} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', listed, 'CG 20 33', 'CG 20 38'], edition=None)
    d, rec = _approve_as_page(h, h.ok(r, 201)['record']['id'])
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    print('M3', repr(listed), _item_view(d, 'ai_ongoing').get('edition_applied'), ev['state'], codes(ev))
    assert ev['state'] == 'variance' and 'WRONG_EDITION:ai_ongoing' in codes(ev), ev


def test_R11_M4_control_a_month_word_conflict_is_refused_and_the_paper_c_check_sees_it(h):
    w = world(h, endorsements=_completed(form='CG 20 37 Nov 2013', edition='04 13'), party_name='M Four Inc')
    ev = h.state(w['asg']['id'])
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', 'CG 20 10 Nov 2013', 'CG 20 33', 'CG 20 38'],
                    edition='04 13')
    print('M4 paper', ev['state'], codes(ev), '| revise', r.status_code, (r.get_json() or {}).get('code'))
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.edition' in codes(ev), ev
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'edition_conflict', r.get_json()


def test_R11_M5_WATCH_an_ed_prefixed_month_word_edition_is_refused_as_unreadable(h):
    """WATCH (fail-closed false refusal): 'CG 20 10 Ed. Nov 2013' is refused 422 edition_unreadable although
    'CG 20 10 ED 11 13' binds. Asserts today's reading."""
    w = world(h, party_name='M Five Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', 'CG 20 10 Ed. Nov 2013', 'CG 20 33', 'CG 20 38'],
                    edition=None)
    print('M5', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'edition_unreadable', r.get_json()


# ───────────── FP1: narrowed ─────────────
CARRIER = {'D-series': 'CG D3 60 09 07', 'S-series': 'CG S3 60 09 07', 'B-series': 'CG B4 14 02 19'}


@pytest.mark.parametrize('label', list(CARRIER))
def test_R11_F0_control_carrier_cg_letter_numbers_read_as_before_1_9_1(h, label):
    form = CARRIER[label]
    w = world(h, endorsements=_ends(form=form, manuscript=True), party_name=f'F Zero {label} Inc')
    ev = h.state(w['asg']['id'])
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'])
    print('F0', label, ev['state'], codes(ev), '| revise', r.status_code, (r.get_json() or {}).get('code'))
    assert ev['state'] == 'meets' and r.status_code == 201, (ev['state'], r.get_json())


@pytest.mark.parametrize('form', ['CG 20 37 I2 I3', 'CG 20 37 (I2/I3)', 'CG 20 37 l2 l3'])
def test_R11_F1_control_an_edition_gained_beside_an_iso_number_is_refused(h, form):
    w = world(h, party_name=f'F One {form} Inc')
    _x, r = _revise(h, w, 'ai_completed', forms=[form], edition=None)
    print('F1', repr(form), r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422, r.get_json()


STRONG_EDIT = {'Z-drop': 'CG Z0 1', 'D-drop': 'CG 2D 1', 'Z-add': 'CG Z0 1X0'}


@pytest.mark.parametrize('label', list(STRONG_EDIT))
def test_R11_F2_WATCH_a_strong_glyph_plus_one_real_edit_is_a_manuscript_again(h, dirs, label):
    """WATCH (ruled by FP1 option 1): a 1.9.1 strong glyph (Z, D) plus one real edit is no longer near-ISO, so it is
    a manuscript again and, with the wording confirmed, reaches Meets for completed ops; the watcher agrees. On
    8b0d564 it read Can't tell. Asserts today's reading."""
    form = STRONG_EDIT[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'F Two {label} Inc')
    ev = _manuscript(h, w, 'ai_completed', wording=True)
    v = None
    if ev['state'] in ('meets', 'meets_accepted'):
        v, _rv = _review_and_watch(h, dirs, w, f'F2[{label}]')
    print('F2', label, repr(form), ev['state'], codes(ev), 'watcher', v)
    assert ev['state'] == 'meets' and v == 'PASS', (ev['state'], codes(ev), v)


# ───────────── X1: weak glyphs ─────────────
WEAK = {'paren-bracket': '(G 20 ]0', 'paren-J': '(G 20 J0', 'paren-bracket-2011': '(G 20 1]', 'brace-amp': '{& 2U 1T',
        'lt-q': '<G ?0 /0', 'pair-0': 'CG 2() 1J', 'apos': "[G 20 1'"}


@pytest.mark.parametrize('label', list(WEAK))
def test_R11_X0_control_host_weak_glyph_spellings_are_never_a_manuscript(h, label):
    form = WEAK[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'X Zero {label} Inc')
    ev = _manuscript(h, w, 'ai_completed', wording=True)
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    print('X0', label, repr(form), ev['state'], codes(ev), note[:1])
    assert ev['state'] in ('cant_tell', 'missing'), ev
    assert not any('confirm it as a manuscript' in n for n in note), note


@pytest.mark.parametrize('form', ['(G 20 ]0', 'CG 2U 10', 'CG ?0 10'])
def test_R11_X0b_control_a_weak_glyph_spelling_is_refused_on_the_set_side(h, form):
    w = world(h, party_name=f'X Zero B {form} Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'])
    print('X0b', repr(form), r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'form_lookalike', r.get_json()


CC = {'ACC': 'ACC 2015 AI', 'CC-dash': 'CC-2019-AI', 'OCC': 'OCC 2004 ADDL INSD'}


@pytest.mark.parametrize('label', list(CC))
def test_R11_X2_WATCH_c_for_g_beyond_the_host_list_dead_ends_cc_numbers(h, label):
    """WATCH: 'C for G' is the builder's addition beyond the host's X1 list. A manuscript number with 'CC' before a
    20xx run cannot be a manuscript (it could not on 1.9.1 either) and now cannot be listed (422 form_lookalike; 201 on
    8b0d564). Asserts today's reading."""
    form = CC[label]
    w = world(h, endorsements=_ends(form=form, manuscript=True), party_name=f'X Two {label} Inc')
    ev = h.state(w['asg']['id'])
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'])
    print('X2', label, repr(form), ev['state'], codes(ev), '| revise', r.status_code, (r.get_json() or {}).get('code'))
    assert ev['state'] == 'cant_tell' and r.status_code == 422, (ev['state'], r.get_json())


# ───────────── N2 ─────────────
@pytest.mark.parametrize('form,want', [('C6 Z0 10', 'CG2010'), ('C6 2Q 11', 'CG2011'), ('(G 20 ]0', 'CG2010'),
                                       ('C9 Z0 1O', 'CG2010')])
def test_R11_N2_control_the_fold_note_names_the_cg_20_xx_it_reads_as(h, form, want):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'N Two {form} Inc')
    ev = _manuscript(h, w, 'ai_completed', wording=True)
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    print('N2', repr(form), ev['state'], note[:1])
    assert any(f'reads as {want}' in n for n in note), note


# ───────────── authority boundaries ─────────────
def test_R11_A1_control_the_agent_cannot_approve_its_own_revision(h):
    w = world(h, party_name='A One Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', 'CG 20 10 Apr 2013', 'CG 20 33', 'CG 20 38'], edition=None)
    sid = h.ok(r, 201)['record']['id']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    h.agent()
    ra = h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']})
    rp = h.patch(f'/crt_requirement_set/{sid}', {'data': {'name': 'x'}}, 1)
    rt_ = h.get(f'/crt/assignment/{w["asg"]["id"]}/state?today=2030-01-01')
    print('A1 approve', ra.status_code, (ra.get_json() or {}).get('code'), '| patch', rp.status_code,
          (rp.get_json() or {}).get('code'), '| ?today', rt_.status_code)
    assert ra.status_code == 403 and rp.status_code == 403 and rt_.status_code == 403


def test_R11_A2_control_a_canvas_origin_staff_jwt_cannot_approve_or_confirm(h):
    w = world(h, party_name='A Two Inc')
    _x, r = _revise(h, w, 'ai_ongoing', forms=['CG 20 10', 'CG 20 10 Apr 2013', 'CG 20 33', 'CG 20 38'], edition=None)
    sid = h.ok(r, 201)['record']['id']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    h.surface_ready = False                       # a staff member's canvas-bridge JWT (azp = tenant origin)
    try:
        h.staff()
        ra = h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']})
        e = w['end']['ai_ongoing']
        rc = h.post('/crt/confirm', {'record_collection': 'crt_endorsement', 'record_id': e['id'], 'fields': ['form']})
    finally:
        h.surface_ready = True
    print('A2 approve', ra.status_code, (ra.get_json() or {}).get('code'), '| confirm', rc.status_code,
          (rc.get_json() or {}).get('code'))
    assert ra.status_code in (401, 403) and rc.status_code in (401, 403)


# ───────────── engine and watcher parity (my own generator) ─────────────
_PIECES = ['CG', 'C', 'G', 'cg', '20', '2', '0', '10', '11', '37', '01', ' ', '-', '/', '.', ',', '(', ')', '[', ']', '{',
           '<', '&', '9', '7', '?', 'U', 'J', 'T', "'", '6', '8', 'Z', 'D', 'Q', 'O', 'I', 'l', '|', '!', 'S', 'B', 'NOV',
           'Oct', 'APR', 'Sept', 'Ed.', 'ISO', '85', '1985', '13', '2013', '04', '4', '1', '()', '[]', '*', '–']


def _gen(rnd):
    if rnd.random() < 0.5:
        return (rnd.choice(['CG 20 10', 'CG 20 37', 'CG2010', 'ISO CG 20 26', 'XX 12 34', 'C9 70 10', 'CG 70 10'])
                + rnd.choice([' ', '', ' (', ', ']) + rnd.choice(['Nov', 'Oct', 'Apr', 'Juil', '1', '4', '11', 'Ed. Nov', ''])
                + rnd.choice([' ', '/', '-', '. ']) + rnd.choice(['85', '1985', '13', "'85", '', '13*']) +
                rnd.choice(['', ')', ' ']))
    return ''.join(rnd.choice(_PIECES) for _ in range(rnd.randint(1, 9)))


def test_R11_N1_control_engine_and_watcher_agree_on_the_1_10_0_readings():
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    rnd = random.Random(20261117)
    pairs = [('form_parts', rt.form_parts, wt._form_parts), ('needs_fold', rt.needs_fold, wt._refold),
             ('near', rt.near_iso_number, wt._near_iso), ('dist', rt.iso_distance, wt._gap_to_iso),
             ('fold_dist', rt.fold_distance, wt._fold_gap), ('exact', rt.iso_exact_reading, wt._exact_iso),
             ('never_completed', rt.never_completed_ops, wt._never_completed)]
    bad = []
    for _ in range(8000):
        s = _gen(rnd)
        for name, a, b in pairs:
            if a(s) != b(s):
                bad.append((name, s, a(s), b(s)))
        it = {'id': 'x', 'kind': 'endorsement', 'function': 'ai_ongoing', 'forms': [s, 'CG 20 33'],
              'edition': rnd.choice([None, '', '04 13', '11 85'])}
        ea, wa = rt.item_edition(it), wt._item_ed(it)
        if ea[0] != wa[0] or (ea[1] is None) != (wa[1] is None):
            bad.append(('item_edition', s, ea, wa))
        if s.isascii() and bool(rt.floor_violations({'items': [it]})) != bool(wt._floor({'items': [it]})):
            bad.append(('floor', s, rt.floor_violations({'items': [it]}), wt._floor({'items': [it]})))
    print('N1 disagreements', len(bad), bad[:8])
    assert not bad


@pytest.mark.parametrize('form', ['\u2013', '\u00e9', '\u2013/'])
def test_R11_N3_WATCH_a_stored_keyless_non_ascii_form_breaks_only_the_engine_floor(form):
    """WATCH (pre-existing, engine stricter): a stored item form with no letters or digits but a non-ASCII mark is a
    floor violation for the engine (needs_fold on every form) but not for the watcher (_floor reads only forms with a
    key). Fail-closed in the engine, so no false Meets; a parity gap in H7's 'one contract, two copies'. Asserts
    today's reading."""
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    it = {'id': 'x', 'kind': 'endorsement', 'function': 'ai_ongoing', 'forms': [form, 'CG 20 33']}
    eng, wat = rt.floor_violations({'items': [it]}), wt._floor({'items': [it]})
    print('N3', ascii(form), eng, wat)
    assert eng and not wat
