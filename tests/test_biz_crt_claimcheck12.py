"""Rules 1.10.2 regression set: gate-4 re-check #12 (docs/jambot/contractors-choice-claimcheck12-20261117.md) under the
host rulings N3b, D11, M2f. Every case here is written to FAIL on 6b5187c (rules 1.10.1) and pass on 1.10.2; controls
are folded into cases that also check the change. The verbatim re-checker probes are tests/test_claimcheck12_probes.py
(their D11 / M2f WATCH asserts flipped per the rulings). Fake data only.

N3b  the watcher's floor reads every form, keyless ASCII marks included ('[', '-', "'") on a completed-ops item, as the
     engine's does.
D11  the look-alike check covers every number of the floor table (derived from the table on every call): 'CG 74 04'
     reads as CG 24 04, 'CG 75 03' as CG 25 03, 'CG 75 45' as CG 25 45; mapped to another category they are refused
     like D, stored they break the floor, on the paper they are that ISO form (never a manuscript); watcher RD12.
M2f  an unreadable edition never widens to "any edition": '0CT 1985', 'N0V 1985', a bare "'85" in a form list are
     edition_unreadable and break the floor when stored (both copies, RM12); real O-for-zero spellings still fold and
     honest form + edition spellings still bind.
"""
import random

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    codes, dirs, h, world, _ends,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed, _staff_confirm_wording
from tests.test_biz_crt_claimcheck8 import _forge, _items_with, _patch_item, _raw_patch, _reasons, _set, _watcher


def _revise(h, w, iid, change, drop=(), who='agent', table=None):
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    body = {'items': _items_with(x, iid, change, drop=drop)}
    if table:
        body['form_functions'] = dict(x['data']['form_functions'], **table)
    return h.post(f'/crt/requirement-sets/{x["id"]}/revise', body)


def _completed_item(form):
    return {'id': 'x', 'kind': 'endorsement', 'function': 'ai_completed', 'line': 'gl', 'required': True,
            'forms': [form, 'CG 20 37']}


# ═════════════════════════ N3b: the watcher's floor reads keyless ASCII forms ═════════════════════════
@pytest.mark.parametrize('form', ['[', '-', "'", ':', ''])
def test_n3b_a_keyless_ascii_form_on_a_completed_item_breaks_both_floors(form):
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    it = _completed_item(form)
    assert rt.floor_violations({'items': [it]}) and w._floor({'items': [it]}), form
    ok = {'id': 'x', 'kind': 'endorsement', 'function': 'ai_completed', 'line': 'gl', 'required': True,
          'forms': ['CG 20 37']}
    assert not rt.floor_violations({'items': [ok]}) and not w._floor({'items': [ok]})


def test_n3b_watch_a_meets_with_a_stored_keyless_form_on_completed_ops_is_not_backed(h, dirs):
    w = world(h, party_name='N12 Watch Inc')
    _patch_item(dirs, w['set']['id'], 'ai_completed', {'forms': ['[', 'CG 20 37']})
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


# ═════════════════════════ D11: every floor-table number has its look-alike reading ═════════════════════════
@pytest.mark.parametrize('form', ['CG 74 04', 'CG 75 03', 'CG 75 45'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_d11_a_floor_look_alike_mapped_to_another_category_is_refused_and_honest_numbers_list(h, form, who):
    w = world(h, party_name=f'D12 {form} {who} Inc')
    r = _revise(h, w, 'ai_completed', {'forms': ['CG 20 37', form]}, who=who, table={form: 'ai_completed'})
    assert r.status_code == 422, r.get_json()
    ok = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', 'CG 74 49', 'CG 70 49', 'CG 20 33', 'CG 20 38']}, who=who)
    assert ok.status_code == 201, ok.get_json()


def test_d11_a_floor_look_alike_listed_under_another_category_is_refused(h):
    w = world(h, party_name='D12 Ongoing Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', 'CG 74 04', 'CG 20 33', 'CG 20 38']})
    assert r.status_code == 422, r.get_json()


def test_d11_a_floor_look_alike_table_key_is_refused(h):
    w = world(h, party_name='D12 Table Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'form_functions': dict(x['data']['form_functions'], **{'CG 74 04': 'ai_completed'})})
    assert r.status_code == 422 and r.get_json()['code'] == 'iso_form_floor', r.get_json()


def test_d11_a_stored_floor_look_alike_mapping_breaks_the_floor(h, dirs):
    """6b5187c: the stored mapping read clean and the 'CG 74 04' paper read plain Meets for completed ops."""
    w = world(h, endorsements=_completed(form='CG 74 04'), party_name='D12 Stored Inc')

    def fn(d):
        d['form_functions']['CG 74 04'] = 'ai_completed'
        for it in d['items']:
            if it['id'] == 'ai_completed':
                it['forms'] = ['CG 20 37', 'CG 74 04']
    _raw_patch(dirs, w['set']['id'], fn)
    ev = h.state(w['asg']['id'])
    assert ev['state'] not in ('meets', 'meets_accepted') and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


def test_d11_a_floor_look_alike_on_the_paper_is_never_a_manuscript(h):
    w = world(h, endorsements=_completed(form='CG 74 04', manuscript=True), party_name='D12 Paper Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] in ('cant_tell', 'missing') and ev['items'].get('ai_completed') != 'OK', ev
    assert not any('confirm it as a manuscript' in n for n in ev['notes']), ev['notes']


def test_d11_watch_a_meets_on_a_stored_floor_look_alike_mapping_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(form='CG 74 04'), party_name='D12 Watch Inc')

    def fn(d):
        d['form_functions']['CG 74 04'] = 'ai_completed'
        for it in d['items']:
            if it['id'] == 'ai_completed':
                it['forms'] = ['CG 20 37', 'CG 74 04']
    _raw_patch(dirs, w['set']['id'], fn)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


def test_d11_the_readings_come_from_the_floor_table_itself(monkeypatch):
    """A new floor form is covered automatically, in both copies."""
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    for s, want in (('CG 74 04', 'CG2404'), ('CG 75 03', 'CG2503'), ('CG 75 45', 'CG2545'), ('CG 2S 03', 'CG2503'),
                    ('C9 74 O4', 'CG2404')):
        assert want in rt.floor_readings(s) and want in w._floor_exact(s), s
    assert not rt.floor_readings('CG 74 49') and not w._floor_exact('CG 74 49')
    monkeypatch.setitem(rt.ISO_FLOOR, 'CG2799', 'pnc')
    monkeypatch.setitem(w._ISO_FLOOR, 'CG2799', 'pnc')
    assert 'CG2799' in rt.floor_readings('CG 77 99') and 'CG2799' in w._floor_exact('CG 77 99')
    assert rt.iso_floor_clash('CG 77 99', 'ai_ongoing') == 'ISO pnc' and w._clash('CG 77 99', 'ai_ongoing')


# ═════════════════════════ M2f: an unreadable edition never widens to "any edition" ═════════════════════════
@pytest.mark.parametrize('form', ['CG 20 10 0CT 1985', 'CG 20 10 N0V 1985', 'CG 20 10 Ed. 0ct 1985', "CG 20 10 '85",
                                  'CG 20 10 0CT 2013'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_m2f_a_year_that_reads_as_no_date_is_refused_never_any_edition(h, form, who):
    w = world(h, party_name=f'M12 {form} {who} Inc')
    r = _revise(h, w, 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']}, drop=('edition',), who=who)
    assert r.status_code == 422 and r.get_json()['code'] == 'edition_unreadable', r.get_json()


@pytest.mark.parametrize('form', ['CG 20 10 0CT 1985', "CG 20 10 '85"])
def test_m2f_a_stored_one_breaks_the_floor_in_both_copies(h, dirs, form):
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    it = {'id': 'ai_ongoing', 'kind': 'endorsement', 'function': 'ai_ongoing', 'line': 'gl', 'required': True,
          'forms': [form, 'CG 20 10']}
    assert rt.floor_violations({'items': [it]}) and w._floor({'items': [it]})
    wd = world(h, party_name=f'M12 Stored {form} Inc')
    _patch_item(dirs, wd['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', form, 'CG 20 33', 'CG 20 38']},
                drop=('edition',))
    ev = h.state(wd['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


def test_m2f_honest_editions_still_bind_and_real_look_alikes_still_fold():
    """A slice of the re-checker's honest form + edition corpus (every month, 2004-2025, thirteen spellings) binds the
    edition written; a real O-for-zero still folds; and the M2f spellings read as no date (6b5187c: any edition)."""
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    mon = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
    spell = [lambda m, y: f'{m:02d} {y % 100:02d}', lambda m, y: f'({m:02d}/{y % 100:02d})',
             lambda m, y: f'Ed. {m:02d} {y % 100:02d}', lambda m, y: f'{mon[m - 1].title()} {y}',
             lambda m, y: f'{mon[m - 1]} {y % 100:02d}', lambda m, y: f"{mon[m - 1].title()} '{y % 100:02d}",
             lambda m, y: f'Ed. {mon[m - 1].title()} {y}', lambda m, y: f'EDITION {mon[m - 1]} {y}',
             lambda m, y: f'{mon[m - 1].title()}. {y}', lambda m, y: f'({mon[m - 1].title()} {y})',
             lambda m, y: f"{m:02d} '{y % 100:02d}"]
    bad = []
    for fn, f in (('ai_ongoing', 'CG 20 10'), ('ai_completed', 'CG 20 37'), ('pnc', 'CG 20 01'),
                  ('waiver_of_subrogation', 'CG 24 04')):
        for sp in spell:
            for m in range(1, 13):
                for y in (2004, 2013, 2025):
                    s = f'{f} {sp(m, y)}'
                    it = {'id': 'x', 'kind': 'endorsement', 'function': fn, 'forms': [s]}
                    if rt.floor_violations({'items': [it]}) or w._floor({'items': [it]}) or \
                            rt.item_edition(it)[0] != (m, y) or w._item_ed(it)[0] != (m, y):
                        bad.append(s)
    assert not bad, bad[:5]
    for s in ('CG 20 1O Nov 2013', 'CG 2O 1O', 'OCT 2O13', 'CG 20 O4'):
        assert rt.needs_fold(s) and w._refold(s), s
    for f in ('CG 20 10 0CT 1985', 'CG 20 10 N0V 1985', "CG 20 10 '85"):
        assert rt.item_edition({'forms': [f]})[1] and w._item_ed({'forms': [f]})[1], f


# ═════════════════════════ one contract, two copies ═════════════════════════
_P12 = ['CG', 'C', 'G', '20', '24', '25', '2', '0', '04', '03', '45', '74', '75', '10', '37', '01', ' ', '-', '/', '(',
        ')', '[', "'", ':', '`', '*', '0CT', 'N0V', 'Nov', 'Oct', 'Ed.', '85', '1985', '2013', 'O', 'S', 'I', '7', '9',
        '–']


def _gen12(rnd):
    if rnd.random() < 0.5:
        return (rnd.choice(['CG 20 10', 'CG 20 37', 'CG 74 04', 'CG 75 03', 'CG 75 45', 'C9 74 O4', 'CG 2S 03', '[', '-',
                            "'", ''])
                + rnd.choice(['', ' ', ' Ed. ']) + rnd.choice(['', '0CT', 'N0V', 'Nov', 'Oct', "'"])
                + rnd.choice(['', ' ', '/']) + rnd.choice(['', '85', '1985', '13', '2013']))
    return ''.join(rnd.choice(_P12) for _ in range(rnd.randint(1, 8)))


def test_engine_and_watcher_agree_on_the_1_10_2_readings():
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    assert bool(rt.floor_violations({'items': [_completed_item('[')]})) == bool(w._floor({'items': [_completed_item('[')]}))
    assert rt.floor_readings('CG 74 04') == w._floor_exact('CG 74 04') == {'CG2404'}
    rnd = random.Random(1102)
    bad = []
    for _ in range(10000):
        s = _gen12(rnd)
        if (rt.never_completed_ops(s), rt.iso_floor_clash(s, 'ai_completed') is None,
                rt.iso_floor_clash(s, 'ai_ongoing') is None, rt.iso_floor_clash(s, 'pnc') is None,
                rt.form_parts(s), rt.needs_fold(s), sorted(rt.floor_readings(s))) != \
                (w._never_completed(s), w._clash(s, 'ai_completed') is None, w._clash(s, 'ai_ongoing') is None,
                 w._clash(s, 'pnc') is None, w._form_parts(s), w._refold(s), sorted(w._floor_exact(s))):
            bad.append(s)
        fn = rnd.choice(['ai_ongoing', 'ai_completed', 'pnc', 'waiver_of_subrogation'])
        st = {'items': [{'id': 'x', 'kind': 'endorsement', 'function': fn, 'line': 'gl', 'required': True,
                         'forms': [s] + rnd.choice([[], ['CG 20 37'], ['CG 20 10', 'CG 20 33']])}]}
        if rnd.random() < 0.4:
            st['form_functions'] = {s: rnd.choice(['ai_ongoing', 'ai_completed', 'pnc'])}
        if bool(rt.floor_violations(st)) != bool(w._floor(st)):
            bad.append(('floor', s, fn, st.get('form_functions')))
        a, b = rt.item_edition(st['items'][0]), w._item_ed(st['items'][0])
        if a[0] != b[0] or (a[1] is None) != (b[1] is None):
            bad.append(('item', s))
    assert not bad, bad[:5]


def test_rules_version_is_1_10_2():
    from services.biz.packs import risk_transfer as rt
    assert rt.RULE_VERSION == 'crt-rules-1.10.2'
