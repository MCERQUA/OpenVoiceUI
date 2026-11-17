"""Rules 1.9.0 regression set: gate-4 re-check #9 (docs/jambot/contractors-choice-claimcheck9-20261117.md), findings L
and P. Every case here is written to FAIL on 215858b (rules 1.8.0) and pass on 1.9.0; a positive control is folded
into a case that also checks the hole, so no case passes on 215858b. The verbatim re-checker probes are
tests/test_claimcheck9_probes.py. Fake data only.

L  the ASCII look-alike map. I, L (l), | and ! read as 1 and O as 0, on the field AS WRITTEN (folded, never squashed
   first). A number within one edit (substitution, insertion or deletion) of CG 20 xx read that way is never invited,
   or read, as a manuscript ('CG 20 |0', 'CG 20 IO', 'CG 20 lO', 'CG 20 lI', 'CG 20 1|', and the insertion /
   deletion spellings 'CG 2 37', 'CG X20 37', 'CG 2| 37', 'CG 20 3!'). A requirement set refuses a form or edition
   whose look-alikes give it an ISO number, an edition, or that closeness (422 form_lookalike), and a stored one
   reads Can't tell. The watcher (RL9) reads the same way with its own code; a look-alike generator cross-checks.
P  the pre-2004 refusal reads the APPLIED edition (the edition field, or the edition written in the item's form list,
   H5): 'CG 20 10 11 85' / 'CG 20 10 (11/85)' in the form list is refused (422 pre_2004_edition_refused); a stored
   one breaks the floor (engine: Can't tell; watcher RP9).
"""
import random

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    END_FIELDS, codes, dirs, h, world,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed, _staff_confirm_wording
from tests.test_biz_crt_claimcheck8 import (
    _engine_reading, _forge, _items_with, _patch_item, _raw_patch, _reasons, _set, _watcher, _watcher_reading,
)

# the five re-check #9 spellings (CG 20 10 x3, CG 20 11 x2), plus one insertion, one deletion and two punctuation ones
L_SPELLINGS = ['CG 20 |0', 'CG 20 IO', 'CG 20 lO', 'CG 20 lI', 'CG 20 1|']
L_MORE = ['CG 2| 37', 'CG 20 3!', 'CG 2 37', 'CG X20 37']
L_IDS = ['pipe-for-1', 'I-and-O', 'l-and-O', 'l-and-I-2011', 'pipe-2011']


# ═════════════════════════ L: the look-alike map on the paper (engine) ═════════════════════════
@pytest.mark.parametrize('form', L_SPELLINGS + L_MORE, ids=L_IDS + ['pipe-2037', 'bang-2037', 'deletion', 'insertion'])
def test_l_an_ascii_look_alike_is_never_invited_as_a_manuscript(h, form):
    """215858b invited every one of these as a manuscript (the squashed reading dropped '|' / '!', read the O after a
    letter as a letter, and counted substitutions only)."""
    w = world(h, endorsements=_completed(form=form), party_name=f'L9 Invite {form} Inc')
    ev = h.state(w['asg']['id'])
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev
    assert note and not any('confirm it as a manuscript' in n for n in note), note


@pytest.mark.parametrize('form', L_SPELLINGS + L_MORE, ids=L_IDS + ['pipe-2037', 'bang-2037', 'deletion', 'insertion'])
def test_l_a_confirmed_look_alike_manuscript_never_meets(h, form):
    """Staff follow the old invitation (manuscript true, completed-operations wording confirmed): never Meets."""
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'L9 Manuscript {form} Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] in ('cant_tell', 'missing') and ev['items'].get('ai_completed') != 'OK', ev


def test_l_the_look_alike_note_names_the_number_it_reads_as_and_a_real_manuscript_is_still_invited(h):
    w = world(h, endorsements=_completed(form='CG 20 IO'), party_name='L9 Note Inc')
    ev = h.state(w['asg']['id'])
    assert any('reads as CG2010' in n and 'never read as a manuscript' in n for n in ev['notes']), ev['notes']
    w2 = world(h, endorsements=_completed(form='ACME COMP 7'), party_name='L9 Note Control Inc')
    assert any('confirm it as a manuscript form' in n for n in h.state(w2['asg']['id'])['notes'])


# ═════════════════════════ L: the same map on the set side (422 form_lookalike) ═════════════════════════
@pytest.mark.parametrize('form', L_SPELLINGS + ['CG 20 I0', 'CG 20 37 I1 13', 'CG 20 37 CG 20 IO', 'CG 2I I7'],
                         ids=L_IDS + ['I-for-1', 'edition-I1', 'second-number', 'near-only'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_l_a_set_refuses_an_ascii_look_alike_in_a_form_list(h, form, who):
    """215858b accepted each of these in an ongoing-operations form list (201)."""
    w = world(h, party_name=f'L9 Set {form} {who} Inc')
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_ongoing', {'forms': ['CG 20 10', form]})})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_lookalike', r.get_json()


@pytest.mark.parametrize('form', L_SPELLINGS, ids=L_IDS)
def test_l_a_sets_table_refuses_an_ascii_look_alike_mapped_to_completed_ops(h, form):
    """The re-check's set-side note: a table key and form-list entry 'CG 20 IO' -> completed operations was accepted."""
    w = world(h, party_name=f'L9 Table {form} Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_completed', {'forms': ['CG 20 37', form]}),
                'form_functions': dict(x['data']['form_functions'], **{form: 'ai_completed'})})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_lookalike', r.get_json()


@pytest.mark.parametrize('edition', ['I1 13', 'l0 13'])
def test_l_a_set_refuses_a_look_alike_edition_and_keeps_month_names(h, edition):
    """An edition that reads as a date only through the map is refused as a look-alike; 'JUL 2013' (a month name with
    an L) is still an edition."""
    w = world(h, party_name=f'L9 Edition {edition} Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(x, 'ai_completed', {'edition': edition})})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_lookalike', r.get_json()
    ok = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(x, 'ai_completed', {'edition': 'JUL 2013'})})
    assert ok.status_code == 201, ok.get_json()


def test_l_a_stored_ascii_look_alike_in_a_sets_form_list_reads_cant_tell(h, dirs):
    w = world(h, party_name='L9 Stored List Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', 'CG 20 IO']})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


def test_l_a_stored_ascii_look_alike_table_key_reads_cant_tell(h, dirs):
    w = world(h, party_name='L9 Stored Table Inc')
    _raw_patch(dirs, w['set']['id'], lambda d: d['form_functions'].update({'CG 20 |0': 'ai_ongoing'}))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


# ═════════════════════════ L: the watcher's own copy (RL9) ═════════════════════════
@pytest.mark.parametrize('form', L_SPELLINGS, ids=L_IDS)
def test_l_watch_a_meets_on_a_look_alike_manuscript_is_not_backed(h, dirs, form):
    """215858b's watcher PASSed a Meets on each (re-check #9: WATCH L1 PASS)."""
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'L9 Watch {form} Inc')
    _staff_confirm_wording(h, w)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


def test_l_watch_a_stored_ascii_look_alike_in_a_sets_form_list_is_below_the_floor(h, dirs):
    w = world(h, party_name='L9 Watch Stored Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', 'CG 20 IO']})
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


# ═════════════════════════ L: one map, two copies (engine vs watcher over a look-alike generator) ═════════════════
_LOOK = {'1': 'IiLl|!', '0': 'OoQqDd', '2': 'Zz', '5': 'Ss', '8': 'Bb', '6': 'Gg'}    # 1.9.1: the widened map
_ISO_SEEDS = ['CG 20 10', 'CG 20 11', 'CG 20 26', 'CG 20 37', 'CG 20 01', 'CG 20 33', 'CG 20 38', 'CG 20 10 04 13',
              'CG 20 11 07 04', 'ISO CG 20 10']
_ASCII = 'CGILOcgilo0123456789|!#_*:;~ ./-(),' + "'"
_LEGIT = ['ACME COMP 7', 'ACME-ONG 2', 'ACME-COMP-OPS 7', 'IL 00 21', 'ISOLATED 12', 'JUL 2004', 'JULY 2004',
          'OCT 2013', 'APRIL 2013', 'Nov 2013', 'MS-LIMITED-01', 'ACME CO 77', 'CO77', 'CG 20 37', 'CG 20 10 04 13',
          'ISO CG 20 10', 'CG 20 10 EDITION 04 13', '04 13', 'CG 72 15 04 13']


def _look_alike_spelling(rnd):
    """A CG 20 xx number (seed) with at least one of its 1 / 0 digits written as a look-alike, plus at most one
    arbitrary edit (insert any ASCII mark or letter, delete or replace one character). By construction it is within
    one character of CG 20 xx when look-alikes are read as digits."""
    seed = rnd.choice(_ISO_SEEDS)
    start = seed.index('CG')
    s = list(seed)
    spots = [i for i in range(start + 3, start + 8) if s[i] in _LOOK]
    for i in rnd.sample(spots, rnd.randint(1, len(spots))):
        s[i] = rnd.choice(_LOOK[s[i]])
    op = rnd.random()
    i = rnd.randrange(len(s) + 1)
    if op < 0.25:
        s.insert(i, rnd.choice(_ASCII))
    elif op < 0.5 and i < len(s):
        del s[i]
    elif op < 0.75 and i < len(s):
        s[i] = rnd.choice(_ASCII)
    return ''.join(s)


def test_l_engine_and_watcher_agree_on_a_look_alike_generator():
    """Every generated look-alike spelling is near an ISO number in BOTH copies (215858b: neither copy saw them), the
    legitimate spellings are not look-alikes in either, and every reading of both copies agrees, including over
    random ASCII strings built from the look-alike alphabet."""
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    rnd = random.Random(19)
    spellings = L_SPELLINGS + L_MORE + [_look_alike_spelling(rnd) for _ in range(12000)]
    missed = [s for s in spellings if not (rt.near_iso_number(s) and w._near_iso(s))]
    assert not missed, missed[:10]
    wrong = [s for s in _LEGIT if rt.needs_fold(s) or w._refold(s)]
    assert not wrong, wrong
    randoms = [''.join(rnd.choice(_ASCII + 'CG2O1O') for _ in range(rnd.randint(1, 16))) for _ in range(12000)]
    bad = [(s, _engine_reading(rt, s), _watcher_reading(w, s)) for s in spellings + randoms + _LEGIT
           if _engine_reading(rt, s) != _watcher_reading(w, s)]
    assert not bad, bad[:5]
    dist = [s for s in spellings + randoms
            if (rt.iso_distance(s), rt.iso_distance(s, lookalikes=False), rt.lookalike_reading(s)) !=
            (w._gap_to_iso(s), w._gap_to_iso(s, look=False), w._as_digits(s))]
    assert not dist, dist[:5]


def test_l_the_map_reads_what_the_ruling_names():
    from services.biz.packs import risk_transfer as rt
    # I, l, |, ! read as 1 and O as 0 on the field as written; nothing is dropped first
    assert {rt.form_key(rt.lookalike_reading(s)) for s in L_SPELLINGS} == {'CG2010', 'CG2011'}
    assert all(rt.near_iso_number(s) and rt.needs_fold(s) for s in L_SPELLINGS + ['CG 20 I0'])
    # one insertion or one deletion is one character away; two are not
    assert rt.near_iso_number('CG 2 37') and rt.near_iso_number('CG X20 37') and not rt.near_iso_number('CG 2 3')
    assert not any(rt.needs_fold(s) for s in _LEGIT)


# ═════════════════════════ P: the pre-2004 refusal reads the applied edition ═════════════════════════
@pytest.mark.parametrize('form', ['CG 20 10 11 85', 'CG 20 10 (11/85)', 'CG 20 10 1185'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_p_a_pre_2004_edition_in_the_form_list_is_refused_on_revise(h, form, who):
    w = world(h, party_name=f'P9 Revise {form} {who} Inc')
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_ongoing', {'forms': [form]}, drop=('edition',))})
    assert r.status_code == 422 and r.get_json()['code'] == 'pre_2004_edition_refused', r.get_json()


def test_p_a_staff_edit_with_a_pre_2004_edition_in_the_form_list_is_refused(h):
    w = world(h, party_name='P9 Edit Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    draft = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {}), 201)['record']
    r = h.patch(f'/crt_requirement_set/{draft["id"]}', {'data': {'items': _items_with(
        draft, 'ai_ongoing', {'forms': ['CG 20 10 11 85'], 'edition': ''})}}, draft['version'])
    assert r.status_code == 422 and r.get_json()['code'] == 'pre_2004_edition_refused', r.get_json()


@pytest.mark.parametrize('change,drop', [({'forms': ['CG 20 10 11 85']}, ('edition',)), ({'edition': '11 85'}, ())],
                         ids=['form-list', 'edition-field'])
def test_p_a_stored_pre_2004_requirement_reads_cant_tell(h, dirs, change, drop):
    """215858b read the stored 1985 requirement as a plain WRONG_EDITION variance (waivable): it is below the floor."""
    w = world(h, party_name=f'P9 Stored {drop} Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', change, drop=drop)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


def test_p_the_approval_page_names_a_pre_2004_draft_and_refuses_to_approve_it(h, dirs):
    w = world(h, party_name='P9 Page Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    draft = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {}), 201)['record']
    _patch_item(dirs, draft['id'], 'ai_ongoing', {'forms': ['CG 20 10 (11/85)']}, drop=('edition',))
    d = h.ok(h.get(f'/crt/requirement-sets/{draft["id"]}/diff'))
    assert any('pre-2004' in v for v in d['floor_violations']), d['floor_violations']
    r = h.post(f'/crt/requirement-sets/{draft["id"]}/approve', {'diff_sha256': d['sha256']})
    assert r.status_code == 422, r.get_json()


def test_p_watch_a_stored_pre_2004_requirement_is_below_the_floor(h, dirs):
    w = world(h, party_name='P9 Watch Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10 11 85']}, drop=('edition',))
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') and 'pre-2004' in r for r in _reasons(rows)), rows


def test_rules_version_is_1_9_0():
    from services.biz.packs import risk_transfer as rt
    assert rt.RULE_VERSION == 'crt-rules-1.9.1'          # rules 1.9.1 (host ruling: widened map) keeps every 1.9.0 rule


# ═════════════════════════ 1.9.1 (host ruling): the widened map, S 5 / Z 2 / B 8 / G 6 / Q and D 0 ═════════════════
# Each case fails on 2d60b14 (rules 1.9.0) and passes on 1.9.1, except the manuscript controls, which read the same on
# both (the ruling: real manuscript numbers that are not within one edit of CG 20 xx are unaffected).
# one new look-alike letter each: within one edit of CG 20 xx already on 1.9.0 (Can't tell on the paper), but the SET
# accepted them there; 1.9.1 reads them as the ISO number and refuses them (422 form_lookalike)
W_ONE = ['CG Z0 10', 'CG 20 1Q', 'CG 2D 11', 'CG 20 3B', 'CG 20 3G', 'CG 20 S0']
W_ONE_IDS = ['Z', 'Q', 'D', 'B', 'G', 'S']
# two new look-alike letters each: two edits from CG 20 xx on 1.9.0, so the paper was still invited as a manuscript
W_TWO = ['CG ZD 10', 'CG 2D 1Q', 'CG 2Q 3B', 'CG 2D 3G', 'CG ZO 3S']
W_TWO_IDS = ['Z-D-2010', 'D-Q-2010', 'Q-B-2038', 'D-G-2036', 'Z-S-2035']
# real-shaped manuscript numbers carrying S / B / G / Z / Q / D that are NOT within one edit of CG 20 xx
W_MANUSCRIPTS = ['ZURICH U-GL-1175-B', 'QBE AI-BLANKET 2019', 'DESIGNATED INSURED SB-0042', 'GSB 4471 ADD',
                 'SBG-AI-0417']


@pytest.mark.parametrize('form', W_ONE, ids=W_ONE_IDS)
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_w_a_set_refuses_a_widened_look_alike_in_a_form_list(h, form, who):
    w = world(h, party_name=f'W91 Set {form} {who} Inc')
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_ongoing', {'forms': ['CG 20 10', form]})})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_lookalike', r.get_json()


@pytest.mark.parametrize('form', W_ONE, ids=W_ONE_IDS)
def test_w_a_sets_table_refuses_a_widened_look_alike(h, form):
    w = world(h, party_name=f'W91 Table {form} Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_completed', {'forms': ['CG 20 37', form]}),
                'form_functions': dict(x['data']['form_functions'], **{form: 'ai_completed'})})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_lookalike', r.get_json()


@pytest.mark.parametrize('form', W_TWO, ids=W_TWO_IDS)
def test_w_a_widened_look_alike_is_never_invited_as_a_manuscript(h, form):
    w = world(h, endorsements=_completed(form=form), party_name=f'W91 Invite {form} Inc')
    ev = h.state(w['asg']['id'])
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev
    assert note and not any('confirm it as a manuscript' in n for n in note), note


@pytest.mark.parametrize('form', W_TWO, ids=W_TWO_IDS)
def test_w_a_confirmed_widened_look_alike_manuscript_never_meets(h, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'W91 Manuscript {form} Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] in ('cant_tell', 'missing') and ev['items'].get('ai_completed') != 'OK', ev


@pytest.mark.parametrize('form', W_TWO, ids=W_TWO_IDS)
def test_w_watch_a_meets_on_a_widened_look_alike_manuscript_is_not_backed(h, dirs, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'W91 Watch {form} Inc')
    _staff_confirm_wording(h, w)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


@pytest.mark.parametrize('form', W_MANUSCRIPTS)
def test_w_positive_control_real_manuscripts_with_s_b_g_z_q_d_read_as_before(h, form):
    """Not within one edit of CG 20 xx: invited as a manuscript on the paper, a confirmed one Meets, and a set may
    list and map it (the same on 2d60b14 and 1.9.1)."""
    from services.biz.packs import risk_transfer as rt
    assert not rt.near_iso_number(form) and not rt.needs_fold(form)
    w = world(h, endorsements=_completed(form=form), party_name=f'W91 Control {form} Inc')
    assert any('confirm it as a manuscript form' in n for n in h.state(w['asg']['id'])['notes'])
    w2 = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'W91 Control Ms {form} Inc')
    _staff_confirm_wording(h, w2)
    assert h.state(w2['asg']['id'])['state'] == 'meets'
    x = _set(h, w['set']['id'])
    h.staff()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_completed', {'forms': ['CG 20 37', form]}),
                'form_functions': dict(x['data']['form_functions'], **{form: 'ai_completed'})})
    assert r.status_code == 201, r.get_json()


def test_w_engine_and_watcher_agree_on_the_widened_map():
    """Every widened spelling is near (and a look-alike) in both copies, the controls are neither, and the readings
    agree over random strings built from the widened alphabet."""
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    assert all(rt.near_iso_number(s) and w._near_iso(s) and rt.needs_fold(s) and w._refold(s) for s in W_ONE + W_TWO)
    assert not any(rt.needs_fold(s) or w._refold(s) for s in W_MANUSCRIPTS + _LEGIT + ['AUG 2013', 'SEP 2013', 'DEC 2013'])
    rnd = random.Random(191)
    alpha = 'CGSZBQDILOcgszbqdilo0123456789|! ./-'
    strs = [''.join(rnd.choice(alpha) for _ in range(rnd.randint(1, 14))) for _ in range(12000)]
    bad = [(s, _engine_reading(rt, s), _watcher_reading(w, s)) for s in strs
           if _engine_reading(rt, s) != _watcher_reading(w, s) or rt.lookalike_reading(s) != w._as_digits(s)]
    assert not bad, bad[:5]


def test_w_the_cg_prefix_keeps_its_g():
    from services.biz.packs import risk_transfer as rt
    assert rt.lookalike_reading('CG 2D 3G') == 'CG 20 36' and rt.lookalike_reading('C G Z0 1O') == 'C G 20 10'
    assert rt.form_key(rt.lookalike_reading('CG 20 37 I1 13')) == 'CG2037' and rt.RULE_VERSION == 'crt-rules-1.9.1'
    # the class distance: 6 fills the G, two look-alike marks are two digits (never dropped first)
    assert rt.iso_distance('C6 20 10') == 0 and rt.near_iso_number('CG 20 |!') and rt.iso_distance('CG 20 |!') == 0
