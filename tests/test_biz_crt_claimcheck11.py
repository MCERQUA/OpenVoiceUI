"""Rules 1.10.1 regression set: gate-4 re-check #11 (docs/jambot/contractors-choice-claimcheck11-20261117.md) under the
host rulings D, M1, M2, M5, N3. Every case here is written to FAIL on 762da21 (rules 1.10.0) and pass on 1.10.1;
controls are folded into cases that also check the change. The verbatim re-checker probes are
tests/test_claimcheck11_probes.py (their M5 / N3 WATCH asserts flipped per the rulings). Fake data only.

D   the floor (ISO category and never-completed) and the endorsement-side R3 check read EVERY exact look-alike
    reading of a form number, digit glyphs included: 'CG 70 10' / 'C9 70 10' (CG 20 10) mapped to completed ops and
    'CG 70 01' (CG 20 01, P&NC) listed as additional insured are refused (422) and break the floor when stored; the
    watcher (RD11) reads them the same. Honest one-digit-off numbers whose exact reading is no floor number
    ('CG 70 49', 'CG 26 39') still list. The near-ISO note names the exact reading.
M1  "Nov '85" in a form list reads as Nov 1985 (the two-digit-year path): pre-2004, refused; another mark beside a month
    word ('Nov 13*') is no date: refused, never "any edition"; stored, both floors see it (RM11).
M2  a month word is read before the O-inside-a-number fold: 'CG 20 10 Oct 2013' / 'OCT 13' / 'CG2010OCT2013' bind as
    October, on the set and on the paper; real look-alikes ('CG 20 1O Nov 2013', 'OCT 2O13') still fold.
M5  'CG 20 10 Ed. Nov 2013' binds (the Ed. prefix after a form number, as the footer prints it).
N3  the watcher's floor reads every form, as the engine's does (a keyless non-ASCII form breaks both).
"""
import random

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    codes, dirs, h, world, _ends,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed
from tests.test_biz_crt_claimcheck8 import _forge, _items_with, _patch_item, _raw_patch, _reasons, _set, _watcher


def _revise(h, w, iid, change, drop=(), who='agent', table=None):
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    body = {'items': _items_with(x, iid, change, drop=drop)}
    if table:
        body['form_functions'] = dict(x['data']['form_functions'], **table)
    return h.post(f'/crt/requirement-sets/{x["id"]}/revise', body)


def _edition_applied(h, rec_id, iid):
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{rec_id}/diff'))
    return next(i for i in d['items'] if i['id'] == iid)['now']['edition_applied']


# ═════════════════════════ D: the floor reads every exact reading ═════════════════════════
@pytest.mark.parametrize('form', ['CG 70 10', 'C9 70 10'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_d_a_digit_glyph_cg2010_mapped_to_completed_ops_is_refused_and_honest_numbers_still_list(h, form, who):
    w = world(h, party_name=f'D11 {form} {who} Inc')
    r = _revise(h, w, 'ai_completed', {'forms': ['CG 20 37', form]}, who=who, table={form: 'ai_completed'})
    assert r.status_code == 422, r.get_json()
    ok = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', 'CG 70 49', 'CG 26 39', 'CG 20 33', 'CG 20 38']}, who=who)
    assert ok.status_code == 201, ok.get_json()


def test_d_a_digit_glyph_cg2001_is_never_additional_insured(h):
    w = world(h, party_name='D11 Two Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', 'CG 70 01', 'CG 20 33', 'CG 20 38']})
    assert r.status_code == 422, r.get_json()


@pytest.mark.parametrize('key', ['CG 70 10', 'C9 70 10'])
def test_d_a_digit_glyph_table_key_for_cg2010_is_refused(h, key):
    w = world(h, party_name=f'D11 Table {key} Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'form_functions': dict(x['data']['form_functions'], **{key: 'ai_completed'})})
    assert r.status_code == 422 and r.get_json()['code'] == 'iso_form_floor', r.get_json()


def test_d_a_stored_digit_glyph_mapping_breaks_the_floor_and_the_paper_reads_missing(h, dirs):
    """762da21: the stored set read clean and the 'CG 70 10' paper read plain Meets for completed ops."""
    w = world(h, endorsements=_completed(form='CG 70 10'), party_name='D11 Stored Inc')

    def fn(d):
        d['form_functions']['CG 70 10'] = 'ai_completed'
        for it in d['items']:
            if it['id'] == 'ai_completed':
                it['forms'] = ['CG 20 37', 'CG 70 10']
    _raw_patch(dirs, w['set']['id'], fn)
    ev = h.state(w['asg']['id'])
    assert ev['state'] not in ('meets', 'meets_accepted'), ev
    assert 'UNCONFIRMED:crt_requirement_set.items' in codes(ev) and ev['items']['ai_completed'].startswith('MISSING'), ev
    from services.biz.packs import risk_transfer as rt
    assert rt.never_completed_ops('CG 70 10') and rt.never_completed_ops('C9 70 10')
    assert not rt.never_completed_ops('CG 70 49') and not rt.never_completed_ops('CG 20 37')


def test_d_watch_a_meets_on_a_stored_digit_glyph_mapping_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(form='CG 70 10'), party_name='D11 Watch Inc')

    def fn(d):
        d['form_functions']['CG 70 10'] = 'ai_completed'
        for it in d['items']:
            if it['id'] == 'ai_completed':
                it['forms'] = ['CG 20 37', 'CG 70 10']
    _raw_patch(dirs, w['set']['id'], fn)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


def test_d_the_near_note_names_the_exact_reading(h):
    w = world(h, endorsements=_ends(form='C9 70 10'), party_name='D11 Note Inc')
    ev = h.state(w['asg']['id'])
    assert any('it reads as CG2010' in n and 'never read as a manuscript' in n for n in ev['notes']), ev['notes']


# ═════════════════════════ M1: the apostrophe year ═════════════════════════
@pytest.mark.parametrize('form', ["CG 20 10 Nov '85", "CG 20 10 (Nov '85)"])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_m1_an_apostrophe_year_reads_as_its_year_and_pre_2004_is_refused(h, form, who):
    w = world(h, party_name=f'M11 One {form} {who} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': [form, 'CG 20 33', 'CG 20 38']}, drop=('edition',), who=who)
    assert r.status_code == 422 and r.get_json()['code'] == 'pre_2004_edition_refused', r.get_json()


@pytest.mark.parametrize('form', ['CG 20 10 Nov 13*', "CG 20 10 Nov '2013", 'CG 20 10 Nov 2013 #2'])
def test_m1_another_mark_beside_a_month_word_is_no_date_never_any_edition(h, form):
    w = world(h, party_name=f'M11 Mark {form} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': [form, 'CG 20 33', 'CG 20 38']}, drop=('edition',))
    assert r.status_code == 422 and r.get_json()['code'] == 'edition_unreadable', r.get_json()


def test_m1_an_apostrophe_year_binds_and_the_page_names_it(h):
    w = world(h, party_name='M11 Bind Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', "CG 20 10 Apr '13", 'CG 20 33', 'CG 20 38']},
                drop=('edition',))
    assert r.status_code == 201, r.get_json()
    assert _edition_applied(h, r.get_json()['record']['id'], 'ai_ongoing') == '04/2013'


@pytest.mark.parametrize('form', ["CG 20 10 Nov '85", 'CG 20 10 Nov 13*'])
def test_m1_a_stored_one_breaks_the_floor_in_both_copies(h, dirs, form):
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    it = {'id': 'ai_ongoing', 'kind': 'endorsement', 'function': 'ai_ongoing', 'line': 'gl', 'required': True,
          'forms': [form, 'CG 20 10']}
    assert rt.floor_violations({'items': [it]}) and w._floor({'items': [it]})
    wd = world(h, party_name=f'M11 Stored {form} Inc')
    _patch_item(dirs, wd['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']},
                drop=('edition',))
    ev = h.state(wd['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


# ═════════════════════════ M2: October ═════════════════════════
@pytest.mark.parametrize('form', ['CG 20 10 Oct 2013', 'CG 20 10 OCT 13', 'CG2010OCT2013', 'CG 20 10 October 2013'])
def test_m2_october_in_the_form_list_binds(h, form):
    w = world(h, party_name=f'M11 Oct {form} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']}, drop=('edition',))
    assert r.status_code == 201, r.get_json()
    assert _edition_applied(h, r.get_json()['record']['id'], 'ai_ongoing') == '10/2013'


def test_m2_october_on_the_paper_reads_as_its_edition_and_look_alikes_still_fold(h):
    """762da21: 'CG 20 37 Oct 2013' read as a look-alike (Can't tell). It is October: against the set's 04 13 it is a
    WRONG_EDITION variance, as 'CG 20 37 10 13' is. A real look-alike next to a month word still folds."""
    w = world(h, endorsements=_completed(form='CG 20 37 Oct 2013', edition='10 13'), party_name='M11 Paper Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_EDITION:ai_completed'], ev
    from services.biz.packs import risk_transfer as rt
    w2 = _watcher()
    for s in ('CG 20 1O Nov 2013', 'OCT 2O13', 'CG 2O 1O', 'CG 20 O4'):
        assert rt.needs_fold(s) and w2._refold(s), s
    for s in ('CG 20 10 Oct 2013', 'CG 20 37 OCT 13', 'CG2010OCT2013'):
        assert not rt.needs_fold(s) and not w2._refold(s), s


# ═════════════════════════ M5: the Ed. prefix ═════════════════════════
@pytest.mark.parametrize('form', ['CG 20 10 Ed. Nov 2013', 'CG 20 10 (Ed. Nov 2013)', 'CG 20 10 EDITION NOV 13'])
def test_m5_an_ed_prefixed_month_word_edition_binds(h, form):
    w = world(h, party_name=f'M11 Ed {form} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']}, drop=('edition',))
    assert r.status_code == 201, r.get_json()
    assert _edition_applied(h, r.get_json()['record']['id'], 'ai_ongoing') == '11/2013'
    from services.biz.packs import risk_transfer as rt
    assert rt.edition_parts('Ed. 04 13')[1] is None              # an edition FIELD still reads it as no date (x6d)


# ═════════════════════════ N3: the watcher's floor reads every form ═════════════════════════
@pytest.mark.parametrize('form', ['–', 'é', '–/'])
def test_n3_a_keyless_non_ascii_form_breaks_both_floors(form):
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    it = {'id': 'x', 'kind': 'endorsement', 'function': 'ai_ongoing', 'forms': [form, 'CG 20 33']}
    assert rt.floor_violations({'items': [it]}) and w._floor({'items': [it]})


def test_n3_watch_a_meets_with_a_stored_keyless_non_ascii_form_is_not_backed(h, dirs):
    w = world(h, party_name='N11 Watch Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', '–', 'CG 20 33', 'CG 20 38']})
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


# ═════════════════════════ one contract, two copies ═════════════════════════
_PIECES11 = ['CG', 'C', 'G', '20', '2', '0', '10', '11', '01', '37', '70', '9', '7', ' ', '-', '/', '.', '(', ')', "'",
             '*', '#', 'NOV', 'Oct', 'OCT', 'October', 'Apr', 'Ed.', 'EDITION', '85', '13', '2013', '1985', 'O', 'I',
             'l', '–', 'é']


def _gen11(rnd):
    if rnd.random() < 0.5:
        return (rnd.choice(['CG 20 10', 'CG 20 37', 'CG2010', 'CG 70 10', 'C9 70 10', 'CG 70 01', 'CG 70 49'])
                + rnd.choice([' ', '', ' (']) + rnd.choice(['Nov', 'Oct', 'OCT', 'Apr', 'Ed. Nov', 'Ed. Oct', ''])
                + rnd.choice([' ', '/', ' \''])
                + rnd.choice(['85', '13', '1985', '2013', '13*', '']) + rnd.choice(['', ')', ' #2']))
    return ''.join(rnd.choice(_PIECES11) for _ in range(rnd.randint(1, 9)))


def test_engine_and_watcher_agree_on_the_1_10_1_readings():
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    # the rulings themselves, in both copies (762da21: neither copy read them)
    for s in ('CG 70 10', 'C9 70 10'):
        assert rt.never_completed_ops(s) and w._never_completed(s), s
    assert rt.iso_floor_clash('CG 70 01', 'ai_ongoing') and w._clash('CG 70 01', 'ai_ongoing')
    for f, want in (("CG 20 37 Nov '85", (11, 1985)), ('CG 20 37 Oct 2013', (10, 2013)),
                    ('CG 20 37 Ed. Nov 2013', (11, 2013))):
        assert rt.item_edition({'forms': [f]})[0] == w._item_ed({'forms': [f]})[0] == want, f
    rnd = random.Random(1101)
    bad = []
    for _ in range(12000):
        s = _gen11(rnd)
        e = (rt.form_parts(s), rt.needs_fold(s), rt.never_completed_ops(s), rt.iso_floor_clash(s, 'ai_completed') is None,
             rt.iso_floor_clash(s, 'ai_ongoing') is None, rt.near_iso_number(s), rt.fold_distance(s))
        v = (w._form_parts(s), w._refold(s), w._never_completed(s), w._clash(s, 'ai_completed') is None,
             w._clash(s, 'ai_ongoing') is None, w._near_iso(s), w._fold_gap(s))
        if e != v:
            bad.append((s, e, v))
        it = {'id': 'x', 'kind': 'endorsement', 'function': rnd.choice(['ai_ongoing', 'ai_completed']),
              'forms': [s, 'CG 20 33'], 'edition': rnd.choice([None, '', '04 13', '11 85'])}
        a, b = rt.item_edition(it), w._item_ed(it)
        if a[0] != b[0] or (a[1] is None) != (b[1] is None):
            bad.append(('item', it, a, b))
        if bool(rt.floor_violations({'items': [it]})) != bool(w._floor({'items': [it]})):
            bad.append(('floor', it))
    assert not bad, bad[:5]


def test_rules_version_is_1_10_1():
    from services.biz.packs import risk_transfer as rt
    assert rt.RULE_VERSION == 'crt-rules-1.10.1'
