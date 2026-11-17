"""Rules 1.10.0 regression set: gate-4 re-check #10 (docs/jambot/contractors-choice-claimcheck10-20261117.md) under the
host rulings M, FP1, X1, N2. Every case here is written to FAIL on 8b0d564 (rules 1.9.1) and pass on 1.10.0; controls
are folded into cases that also check the change. The verbatim re-checker probes are tests/test_claimcheck10_probes.py
(their WATCH asserts flipped per the rulings). Fake data only.

M    an edition written after the number in month words or with a one-digit month ('CG 20 10 Nov 1985',
     '(Nov 1985)', 'NOV 85', 'Apr 2013', '1/85') is read by the form-list reader: it binds (H5), a pre-2004 one is
     refused (422 pre_2004_edition_refused), a conflict is refused (422 edition_conflict), and a month word that reads
     as no date is refused (422 edition_unreadable) and reads Can't tell when stored, never "any edition". Engine and
     watcher (RM10). On the paper, the 1.7.0 C check sees it.
FP1  a look-alike counts only in an EXACT reading of CG 20 xx; with one real edit only the 1.9.0 map is read; an edition
     gained through the map counts only beside an ISO number. Carrier 'CG <letter><digit> ..' numbers read as they did
     before 1.9.1 (watcher RF10).
X1   the weak glyphs join the exact reading: ( < [ { for C; & 9 C for G; ? 7 for 2; U C () [] 6 8 9 for 0; ] [ J T / '
     for 1. Two weak glyphs never reach a manuscript Meets; a set refuses them (a printed digit read as another digit
     counts as the one-character difference it is on the set side). Watcher RX10.
N2   the fold note names the CG 20 xx the field reads as exactly.
"""
import itertools
import random
import string

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    codes, dirs, h, world, _ends,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed, _staff_confirm_wording
from tests.test_biz_crt_claimcheck8 import _forge, _items_with, _patch_item, _reasons, _set, _watcher


def _revise(h, w, iid, change, drop=(), who='agent'):
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    return h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(x, iid, change, drop=drop)})


MONTH_PRE_2004 = ['CG 20 10 Nov 1985', 'CG 20 10 (Nov 1985)', 'CG 20 10 NOV 85', 'CG 20 10 1/85']


# ═════════════════════════ M: month-word editions in the form list ═════════════════════════
@pytest.mark.parametrize('form', MONTH_PRE_2004)
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_m_a_pre_2004_month_word_edition_in_the_form_list_is_refused(h, form, who):
    w = world(h, party_name=f'M10 Pre {form} {who} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': [form, 'CG 20 33', 'CG 20 38']}, drop=('edition',), who=who)
    assert r.status_code == 422 and r.get_json()['code'] == 'pre_2004_edition_refused', r.get_json()


@pytest.mark.parametrize('change', [{'forms': ['CG 20 10', 'CG 20 10 Nov 2013'], 'edition': '04 13'},
                                    {'forms': ['CG 20 10 Apr 2013', 'CG 20 10 Nov 2013']}],
                         ids=['against-the-field', 'two-in-the-list'])
def test_m_a_month_word_edition_conflict_is_refused(h, change):
    w = world(h, party_name=f'M10 Conflict {len(change)} Inc')
    r = _revise(h, w, 'ai_ongoing', change, drop=() if 'edition' in change else ('edition',))
    assert r.status_code == 422 and r.get_json()['code'] == 'edition_conflict', r.get_json()


@pytest.mark.parametrize('listed', ['CG 20 10 Apr 2013', 'CG 20 10 4/13'])
def test_m_a_month_word_edition_binds_and_the_page_names_it(h, listed):
    w = world(h, endorsements=_ends(edition='07 04'), party_name=f'M10 Bind {listed} Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(
        x, 'ai_ongoing', {'forms': ['CG 20 10', listed, 'CG 20 33', 'CG 20 38']}, drop=('edition',))}), 201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{a["id"]}/diff'))
    assert next(i for i in d['items'] if i['id'] == 'ai_ongoing')['now']['edition_applied'] == '04/2013'
    rec = h.ok(h.post(f'/crt/requirement-sets/{a["id"]}/approve', {'diff_sha256': d['sha256']}))['record']
    asg = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{asg["id"]}', {'data': {'requirement_set_id': rec['id'],
                                                           'requirement_set_version': rec['version']}}, asg['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and 'WRONG_EDITION:ai_ongoing' in codes(ev), ev


@pytest.mark.parametrize('form', ['CG 20 10 Nov', 'CG 20 10 Nov 19x5', 'CG 20 10 (Apr)'])
def test_m_an_unreadable_month_word_edition_is_refused_never_any_edition(h, form):
    w = world(h, party_name=f'M10 Unread {form} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': [form, 'CG 20 33', 'CG 20 38']}, drop=('edition',))
    assert r.status_code == 422 and r.get_json()['code'] == 'edition_unreadable', r.get_json()


@pytest.mark.parametrize('form', ['CG 20 10 Nov 1985', 'CG 20 10 Nov'], ids=['pre-2004', 'unreadable'])
def test_m_a_stored_month_word_edition_that_breaks_the_floor_reads_cant_tell(h, dirs, form):
    """8b0d564 read both as "any edition": the sub's 04 13 paper was a plain Meets."""
    w = world(h, party_name=f'M10 Stored {form} Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']},
                drop=('edition',))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


def test_m_a_month_word_footer_on_the_paper_reads_as_the_form_and_its_edition(h):
    """The paper's 'CG 20 37 Apr 2013' is CG 20 37 at 04/2013 (8b0d564: an unlisted key, Can't tell); 'CG 20 37 Nov
    2013' against an edition field of 04 13 is the 1.7.0 C conflict (Can't tell, the note says which)."""
    w = world(h, endorsements=_completed(form='CG 20 37 Apr 2013'), party_name='M10 Paper Inc')
    assert h.state(w['asg']['id'])['state'] == 'meets'
    w2 = world(h, endorsements=_completed(form='CG 20 37 Nov 2013'), party_name='M10 Paper Two Inc')
    ev = h.state(w2['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.edition' in codes(ev), ev
    assert any('carries edition 11/2013' in n for n in ev['notes']), ev['notes']


@pytest.mark.parametrize('form', ['CG 20 10 Nov 1985', 'CG 20 10 Nov'], ids=['pre-2004', 'unreadable'])
def test_m_watch_a_stored_month_word_edition_is_below_the_floor(h, dirs, form):
    w = world(h, party_name=f'M10 Watch {form} Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']},
                drop=('edition',))
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


def test_m_watch_a_meets_against_a_month_word_edition_is_not_backed(h, dirs):
    w = world(h, endorsements=_ends(edition='07 04'), party_name='M10 Watch Bind Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', 'CG 20 10 Apr 2013', 'CG 20 33',
                                                               'CG 20 38']}, drop=('edition',))
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_ongoing' in r for r in _reasons(rows)), rows


# ═════════════════════════ FP1: carrier 'CG <letter><digit>' numbers read as before 1.9.1 ═════════════════════════
CARRIER = ['CG D3 60 09 07', 'CG S3 60 09 07', 'CG B4 14 02 19', 'CG D4 21 07 08', 'CG Q2 16 07 08']


@pytest.mark.parametrize('form', CARRIER)
def test_fp1_a_carrier_cg_letter_number_is_a_manuscript_again_and_a_set_may_list_it(h, form):
    w = world(h, endorsements=_ends(form=form, manuscript=True), party_name=f'FP10 {form} Inc')
    assert h.state(w['asg']['id'])['state'] == 'meets'
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']})
    assert r.status_code == 201, r.get_json()


def test_fp1_the_dead_end_rate_is_back_and_the_strong_look_alikes_stay_closed():
    """The re-checker's shape (4000 samples): 'CG Ld dd mm yy' dead-ended 35.3% on 1.9.1 (11.3% on 1.9.0). The strong
    look-alikes of CG 20 10 / 20 11 (1.9.1 map, up to three, three separators) stay near and fold."""
    from services.biz.packs import risk_transfer as rt
    rnd = random.Random(10)
    L, D = string.ascii_uppercase, string.digits
    shape = ['CG ' + rnd.choice(L) + rnd.choice(D) + ' ' + rnd.choice(D) + rnd.choice(D)
             + ' %02d %02d' % (rnd.randint(1, 12), rnd.randint(4, 24)) for _ in range(4000)]
    dead = sum(rt.needs_fold(s) for s in shape if not rt.is_iso_form(s)) / len(shape)
    assert dead <= 0.12, dead
    strong = {'C': 'C', 'G': 'G6', '2': '2Z', '0': '0OQD', '1': '1IL|!'}
    for target in ('CG2010', 'CG2011'):
        for combo in itertools.product(*[strong[c] for c in target]):
            for sep in (' ', '', '-'):
                s = combo[0] + combo[1] + sep + combo[2] + combo[3] + sep + combo[4] + combo[5]
                assert rt.near_iso_number(s) and (rt.is_iso_form(s) or rt.needs_fold(s)), s


# ═════════════════════════ X1: the weak glyphs ═════════════════════════
WEAK = ['(G 20 ]0', '(G 20 J0', '(G 20 1]', 'CG ?0 1U', 'C& 20 ]0', 'CG 2() ]0', 'CG 2[] 1t', '<G 20 1T',
        '{G 20 1/', 'CG 2U ]0']


@pytest.mark.parametrize('form', WEAK)
def test_x1_two_weak_glyphs_never_reach_a_manuscript_meets(h, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'X10 {form} Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] in ('cant_tell', 'missing') and ev['items'].get('ai_completed') != 'OK', ev
    assert not any('confirm it as a manuscript' in n for n in ev['notes']), ev['notes']


@pytest.mark.parametrize('form', ['(G 20 ]0', 'CG ?0 1U', 'C& 20 ]0', 'CG 2() ]0'])
def test_x1_watch_a_meets_on_two_weak_glyphs_is_not_backed(h, dirs, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'X10 Watch {form} Inc')
    _staff_confirm_wording(h, w)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


@pytest.mark.parametrize('form', ['(G 20 ]0', 'CG ?0 1U', 'C& 20 ]0', 'CG 2() ]0', 'CG 2U ]0'])
def test_x1_a_set_refuses_two_weak_glyphs(h, form):
    w = world(h, party_name=f'X10 Set {form} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', form]})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_lookalike', r.get_json()


_CONF = {'C': ['c', '(', '[', '<', '{'], 'G': ['g', '6', 'C', '&', '9'], '2': ['z', 'Z', '?', '7'],
         '0': ['O', 'o', 'Q', 'D', 'U', 'u', '()', '[]', 'C', '8', '6', '9'],
         '1': ['I', 'i', 'l', 'L', '|', '!', ']', '[', 'j', 'J', '/', 't', 'T', "'", '7']}


def test_x1_no_two_glyph_spelling_escapes_and_printed_digits_list_honestly():
    """Up to two glyph substitutions in CG 20 10 / 20 11 (the re-checker's table), three separators: none is
    manuscript-eligible (8b0d564: 2166 escaped). A carrier number whose only difference is a printed digit
    ('CG 70 49', 'CG 26 39', 'CG 29 10') is not a look-alike on the set side."""
    from services.biz.packs import risk_transfer as rt
    esc = []
    for target in ('CG2010', 'CG2011'):
        for k in (1, 2):
            for pos in itertools.combinations(range(6), k):
                for glyphs in itertools.product(*[_CONF[target[p]] for p in pos]):
                    ch = list(target)
                    for p, g in zip(pos, glyphs):
                        ch[p] = g
                    for sep in (' ', '', '-'):
                        s = ch[0] + ch[1] + sep + ch[2] + ch[3] + sep + ch[4] + ch[5]
                        if not (rt.is_iso_form(s) or rt.near_iso_number(s) or rt.needs_fold(s)):
                            esc.append(s)
    assert not esc, (len(esc), esc[:10])
    assert not any(rt.needs_fold(s) for s in ('CG 70 49', 'CG 26 39', 'CG 29 10', 'CG 70 49 05 14'))


# ═════════════════════════ N2: the note names the number ═════════════════════════
@pytest.mark.parametrize('form,reads', [('C6 Z0 10', 'CG2010'), ('C6 2Q 11', 'CG2011'), ('CG 2() ]0', 'CG2010')])
def test_n2_the_fold_note_names_the_cg_20_xx_it_reads_as(h, form, reads):
    w = world(h, endorsements=_completed(form=form), party_name=f'N10 {form} Inc')
    ev = h.state(w['asg']['id'])
    assert any(f'reads as {reads}' in n for n in ev['notes']), ev['notes']


# ═════════════════════════ one contract, two copies ═════════════════════════
_ALPHA10 = list("CG20 1OILSZBQD6|!.-/()[]89oilszbqdgcTAJUE7&?<{'NOVAPR")
_SEEDS10 = ['CG 20 10', 'CG 20 11', 'CG 20 37', 'CG 20 37 04 13', 'CG 20 10 Nov 1985', 'CG 20 10 Apr 2013',
            'CG 20 37 1/85', 'CG D3 60 09 07', 'CG 70 49', '(G 20 ]0', 'AUG 2013', 'ISO CG 20 37 Nov 2013']


def _gen(rnd):
    if rnd.random() < 0.6:
        s = list(rnd.choice(_SEEDS10))
        for _ in range(rnd.randint(0, 3)):
            op, i = rnd.random(), rnd.randrange(len(s) + 1)
            if op < 0.5 and s:
                s[min(i, len(s) - 1)] = rnd.choice(_ALPHA10)
            elif op < 0.8:
                s.insert(i, rnd.choice(_ALPHA10))
            elif s:
                del s[min(i, len(s) - 1)]
        return ''.join(s)
    return ''.join(rnd.choice(_ALPHA10) for _ in range(rnd.randint(0, 14)))


def test_engine_and_watcher_agree_on_the_1_10_0_readings():
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    # the month-word form list reads the same in both (8b0d564: neither read it)
    for it, want in [({'forms': ['CG 20 37 Nov 2013']}, (11, 2013)), ({'forms': ['CG 20 10 NOV 85']}, (11, 1985)),
                     ({'forms': ['CG 20 37 1/85']}, (1, 1985))]:
        assert rt.item_edition(it)[0] == w._item_ed(it)[0] == want, it
    assert rt.item_edition({'forms': ['CG 20 10 Nov']})[1] and w._item_ed({'forms': ['CG 20 10 Nov']})[1]
    rnd = random.Random(1100)
    bad = []
    for _ in range(15000):
        s = _gen(rnd)
        e = (rt.iso_exact_reading(s), rt.fold_distance(s), rt.iso_distance(s), rt.iso_distance(s, lookalikes=False),
             rt.needs_fold(s), rt.near_iso_number(s), rt.form_parts(s), rt.edition_parts(s), rt.lookalike_reading(s))
        v = (w._exact_iso(s), w._fold_gap(s), w._gap_to_iso(s), w._gap_to_iso(s, look=False), w._refold(s),
             w._near_iso(s), w._form_parts(s), w._edition_read(s), w._as_digits(s))
        if e != v:
            bad.append((s, e, v))
    for _ in range(4000):
        it = {'forms': [_gen(rnd) for _ in range(rnd.randint(0, 3))]}
        if rnd.random() < 0.6:
            it['edition'] = rnd.choice([None, '', '04 13', 'Nov 2013', _gen(rnd)])
        a, b = rt.item_edition(it), w._item_ed(it)
        if (a[0], bool(a[1])) != (b[0], bool(b[1])):
            bad.append((it, a, b))
    assert not bad, bad[:5]


def test_rules_version_is_1_10_0():
    from services.biz.packs import risk_transfer as rt
    assert rt.RULE_VERSION == 'crt-rules-1.10.0'
