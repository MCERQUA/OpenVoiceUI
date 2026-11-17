"""Rules 1.8.0 regression set: gate-4 re-check #8 (docs/jambot/contractors-choice-claimcheck8-20261117.md) and the host
rulings H5-H8. Each case is written to FAIL on 70e5666 (rules 1.7.0) and pass here; positive controls say so in their
name; guards that already held on 70e5666 (by another rule) say `already_held` in theirs. The verbatim re-checker probes
are tests/test_claimcheck8_probes.py. Fake data only.

E / H5  an edition written inside an item's form list ('CG 20 37 04 13') binds when the edition field is absent, null or
        ""; two different editions (in the list, or the list against the field) are refused on write (422
        edition_conflict) and a stored one reads Can't tell. Engine and watcher (RE8) read it the same way; the diff
        shows edition_applied and the approval page's item list renders the edition the verdict applies.
H / H6  form numbers and editions a requirement set is written with are ASCII (422 form_not_ascii; a letter O inside a
        number, 422 form_lookalike). STORED data is folded (NFKC, look-alike letters, any Unicode digit, an O inside a
        number) in both readers and a value that needed folding reads Can't tell; a number one character from a
        CG 20 xx is never a manuscript and never invited as one (engine and watcher RN8).
F / H7  the engine and the watcher keep their own copies of one normalization contract: a test runs both over
        adversarial strings, random non-ASCII included, and they agree on every reading.
G / H8  an item's edition field holds an edition only: a form number there is refused on write (422 edition_not_mm_yy)
        and a stored one reads Can't tell. An endorsement's edition field may print its own number (the footer), but
        one naming ANOTHER form reads Can't tell (engine) / is not backed (watcher RG8).
"""
import importlib.util
import json
import random
import sqlite3
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, END_FIELDS, M, SBX, STAFF_1, TODAY, _ends, codes, confirm, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed, _staff_confirm_wording

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'default-schemas/packs/risk-transfer/pages/cca-crt-requirements.html'


def _store(h):
    return h.app.extensions['biz']['sandbox:josh']


def _set(h, sid):
    h.staff()
    return h.ok(h.get(f'/crt_requirement_set/{sid}'))


def _items_with(x, iid, change, drop=()):
    return [{k: v for k, v in dict(it, **change).items() if k not in drop} if it['id'] == iid else it
            for it in x['data']['items']]


def _db(dirs):
    return dirs['a'] / 'sandboxes' / 'josh' / 'business.db'


def _raw_patch(dirs, rid, fn):
    """Change a stored record's data around the API (written before rules 1.8.0, or by a bug); the version stays."""
    conn = sqlite3.connect(str(_db(dirs)))
    try:
        data = json.loads(conn.execute('SELECT data FROM records WHERE id = ?', (rid,)).fetchone()[0])
        fn(data)
        conn.execute('UPDATE records SET data = ? WHERE id = ?', (json.dumps(data, separators=(',', ':')), rid))
        conn.commit()
    finally:
        conn.close()


def _patch_item(dirs, sid, iid, change, drop=()):
    def fn(d):
        d['items'] = [{k: v for k, v in dict(it, **change).items() if k not in drop} if it['id'] == iid else it
                      for it in d['items']]
    _raw_patch(dirs, sid, fn)


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


def _revise_approve_repin(h, w, items, who='staff'):
    """A revision (by the agent or staff), approved on the page with the diff's hash, and re-pinned."""
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items(x)}), 201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{a["id"]}/diff'))
    rec = h.ok(h.post(f'/crt/requirement-sets/{a["id"]}/approve', {'diff_sha256': d['sha256']}))['record']
    asg = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{asg["id"]}', {'data': {'requirement_set_id': rec['id'],
                                                           'requirement_set_version': rec['version']}}, asg['version']))
    return d, rec


def _watcher():
    spec = importlib.util.spec_from_file_location('crt_watch_r8_reg', str(ROOT / 'tools/biz/crt-verdict-watch.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ═════════════════════════ E (H5): an edition in the item's form list binds ═════════════════════════
@pytest.mark.parametrize('how', ['absent', 'null', 'empty'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_e_an_edition_in_the_form_list_is_the_items_edition(h, how, who):
    w = world(h, endorsements=_completed(edition='07 04'), party_name=f'E Bind {how} {who} Inc')
    change = {'forms': ['CG 20 37 04 13']} if how == 'absent' else \
        {'forms': ['CG 20 37 04 13'], 'edition': None if how == 'null' else ''}
    _revise_approve_repin(h, w, lambda x: _items_with(x, 'ai_completed', change,
                                                      drop=('edition',) if how == 'absent' else ()), who)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_EDITION:ai_completed'], ev


def test_e_positive_control_the_form_list_edition_matching_the_paper_meets(h):
    w = world(h, party_name='E Bind Ok Inc')
    _revise_approve_repin(h, w, lambda x: _items_with(x, 'ai_completed', {'forms': ['CG 20 37 04 13']},
                                                      drop=('edition',)))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and ev['items']['ai_completed'] == 'OK', ev


@pytest.mark.parametrize('change', [{'forms': ['CG 20 37 04 13'], 'edition': '07 04'},
                                    {'forms': ['CG 20 37 04 13', 'CG 20 37 07 04'], 'edition': None},
                                    {'forms': ['CG 20 37 (07/04)'], 'edition': '04 13'}],
                         ids=['list-vs-field', 'two-in-the-list', 'parenthesised-vs-field'])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_e_two_editions_for_one_item_are_refused_on_revise(h, change, who):
    w = world(h, party_name=f'E Conflict {sorted(change["forms"])[-1]} {change["edition"]} {who} Inc')
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(x, 'ai_completed', change)})
    assert r.status_code == 422 and r.get_json()['code'] == 'edition_conflict', r.get_json()


def test_e_a_staff_edit_with_two_editions_is_refused(h):
    w = world(h, party_name='E Conflict Edit Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    draft = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {}), 201)['record']
    r = h.patch(f'/crt_requirement_set/{draft["id"]}', {'data': {'items': _items_with(
        draft, 'ai_completed', {'forms': ['CG 20 37 07 04'], 'edition': '04 13'})}}, draft['version'])
    assert r.status_code == 422 and r.get_json()['code'] == 'edition_conflict', r.get_json()


def test_e_a_stored_edition_conflict_reads_cant_tell(h, dirs):
    """Stored around the API: the form list says 07 04, the field 04 13, the paper is 04 13 (70e5666: plain Meets)."""
    w = world(h, party_name='E Stored Conflict Inc')
    _patch_item(dirs, w['set']['id'], 'ai_completed', {'forms': ['CG 20 37 07 04']})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev
    assert any('another edition' in n for n in ev['notes']), ev['notes']


def test_e_the_diff_shows_the_edition_the_verdict_applies(h):
    w = world(h, party_name='E Diff Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    moved = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(
        x, 'ai_completed', {'forms': ['CG 20 37 04 13']}, drop=('edition',))}), 201)['record']
    d = h.ok(h.get(f'/crt/requirement-sets/{moved["id"]}/diff'))
    row = {r['key']: r for r in next(i for i in d['items'] if i['id'] == 'ai_completed')['rows']}
    assert row['edition_applied']['was'] == row['edition_applied']['now'] == '04/2013', row   # the requirement stands
    assert row['edition_applied']['changed'] is False and row['edition']['changed'] is True, row
    dropped = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(
        x, 'ai_completed', {}, drop=('edition',))}), 201)['record']
    d2 = h.ok(h.get(f'/crt/requirement-sets/{dropped["id"]}/diff'))
    row2 = {r['key']: r for r in next(i for i in d2['items'] if i['id'] == 'ai_completed')['rows']}
    assert row2['edition_applied']['now'] == 'any edition' and row2['edition_applied']['changed'] is True, row2


def test_e_the_page_lists_the_items_as_the_verdict_reads_them():
    page = PAGE.read_text(encoding='utf-8')
    assert 'it.edition_applied' in page                                      # itemText says the applied edition
    assert "itemsList(d.items.filter(function (x) { return x.now; })" in page   # the list is the server's reading
    assert "itemsList(s.data.items || [])" not in page


def test_e_watch_a_meets_against_the_form_list_edition_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(edition='07 04'), party_name='E Watch Inc')
    _revise_approve_repin(h, w, lambda x: _items_with(x, 'ai_completed', {'forms': ['CG 20 37 04 13']},
                                                      drop=('edition',)), 'agent')
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


def test_e_watch_positive_control_the_form_list_edition_matching_passes(h, dirs):
    w = world(h, party_name='E Watch Ok Inc')
    _revise_approve_repin(h, w, lambda x: _items_with(x, 'ai_completed', {'forms': ['CG 20 37 04 13']},
                                                      drop=('edition',)))
    assert _review(h, w)['state'] == 'meets'
    v, rows, _ = _watch(dirs)
    assert v == 'PASS', rows


def test_e_watch_a_stored_edition_conflict_is_below_the_floor(h, dirs):
    w = world(h, party_name='E Watch Conflict Inc')
    _patch_item(dirs, w['set']['id'], 'ai_completed', {'forms': ['CG 20 37 07 04']})
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


# ═════════════════════════ H (H6): ASCII on write, folded and Can't tell when stored ═════════════════════════
@pytest.mark.parametrize('change,where', [({'forms': ['СG 20 37']}, 'cyrillic-form'),
                                         ({'forms': ['CG 20 37'], 'edition': '٠٤ 13'}, 'arabic-edition'),
                                         ({'forms': ['CG 20 37', 'ＣＧ 20 37']}, 'fullwidth-form')],
                         ids=lambda v: v if isinstance(v, str) else None)
def test_h_a_set_refuses_non_ascii_form_numbers_and_editions(h, change, where):
    w = world(h, party_name=f'H Ascii {where} Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(x, 'ai_completed', change)})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_not_ascii', r.get_json()


def test_h_a_set_refuses_a_letter_o_inside_a_number(h):
    w = world(h, party_name='H Letter O Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'items': _items_with(x, 'ai_ongoing', {'forms': ['CG 20 10', 'CG 2O 33']})})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_lookalike', r.get_json()


def test_h_a_sets_table_refuses_a_non_ascii_key(h):
    w = world(h, party_name='H Table Inc')
    x = _set(h, w['set']['id'])
    h.staff()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise',
               {'form_functions': dict(x['data']['form_functions'], **{'МС 77 01': 'ai_completed'})})
    assert r.status_code == 422 and r.get_json()['code'] == 'form_not_ascii', r.get_json()


@pytest.mark.parametrize('form', ['СG 20 37', 'CG 20 3٧', 'Сɢ 2O 37'],
                         ids=['cyrillic-C', 'arabic-seven', 'mixed-and-letter-O'])
def test_h_a_look_alike_cg2037_filed_as_manuscript_never_meets(h, form):
    """70e5666 read 'СG 20 37' as the non-ISO 'G2037' and let the manuscript route (wording confirmed) reach Meets."""
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'H Manuscript {form!a} Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev
    assert not any('confirm it as a manuscript' in n for n in ev['notes']), ev['notes']


@pytest.mark.parametrize('form', ['CG 2I 37', 'CQ 20 37', 'CG 20 3Z'])
def test_h_a_number_one_character_from_an_iso_number_is_never_a_manuscript(h, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'H Near {form} Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev
    assert not any('confirm it as a manuscript' in n for n in ev['notes']), ev['notes']


def test_h_near_iso_number_is_never_invited_as_a_manuscript(h):
    w = world(h, endorsements=_completed(form='CQ 20 37'), party_name='H Near Invite Inc')
    ev = h.state(w['asg']['id'])
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    assert note and not any('confirm it as a manuscript' in n for n in note), note


def test_h_positive_control_a_real_manuscript_is_still_invited(h):
    w = world(h, endorsements=_completed(form='ACME COMP 7'), party_name='H Manuscript Ok Inc')
    ev = h.state(w['asg']['id'])
    assert any('confirm it as a manuscript form' in n for n in ev['notes']), ev['notes']


@pytest.mark.parametrize('form', ['СG 20 10', 'CG 2O 1O', 'CG 20 1٠'], ids=['cyrillic', 'letter-O', 'arabic'])
def test_h_a_look_alike_listed_number_reads_cant_tell(h, form):
    """ai_ongoing lists CG 20 10. A look-alike of it reads as CG 20 10 but Can't tell: never as written (70e5666: the
    letter-O and Arabic spellings were not CG 20 10 at all, and the Cyrillic one was invited as a manuscript)."""
    w = world(h, endorsements=_ends(form=form), party_name=f'H Listed {form!a} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and ev['items']['ai_ongoing'] == 'UNCONFIRMED', ev
    assert any('only look like letters or digits' in n and 'reads as CG2010' in n for n in ev['notes']), ev['notes']


@pytest.mark.parametrize('edition', ['٠٤ 13', '０４ １３'], ids=['arabic-zero', 'fullwidth'])
def test_h_a_look_alike_edition_on_the_paper_reads_cant_tell(h, edition):
    """70e5666's edition regex read every Unicode digit as a digit: '٠4 13' was 04 13 and a plain Meets."""
    w = world(h, endorsements=_completed(edition=edition), party_name=f'H Edition {edition!a} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.edition' in codes(ev), ev


def test_h_a_stored_look_alike_in_a_sets_form_list_reads_cant_tell(h, dirs):
    w = world(h, party_name='H Stored List Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', 'CG 2O 33']})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


def test_h_watch_a_look_alike_manuscript_meets_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(form='СG 20 37', manuscript=True), party_name='H Watch Inc')
    _staff_confirm_wording(h, w)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


def test_h_watch_a_near_iso_manuscript_meets_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(form='CQ 20 37', manuscript=True), party_name='H Watch Near Inc')
    _staff_confirm_wording(h, w)
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


def test_h_watch_a_stored_look_alike_in_a_sets_form_list_is_below_the_floor(h, dirs):
    w = world(h, party_name='H Watch Stored Inc')
    _patch_item(dirs, w['set']['id'], 'ai_ongoing', {'forms': ['CG 20 10', 'CG 2O 33']})
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


# ═════════════════════════ F (H7): one contract, two copies, one answer ═════════════════════════
_SEED_PARTS = (list('CGISOEDITION0123456789 ()/.-,') +
               ['С', 'О', 'Ο', 'о', '٠', '١', '۲', '０', 'Ｃ', 'Ｇ',
                'ǅ', 'ß', 'ﬁ', 'Ⅳ', '①', '²', ' ', '–', 'З', 'ɢ',
                'NOV', 'JAN', 'CG 20 10', 'CG 20 37', 'CG 20 26', '04 13', 'ED'])
FIXED = ['СG 20 10', 'cg20О37', 'CGО/С2026', 'CG 20 1٠', '13)3', '/213', '3,13', 'NOV85',
         'CG 2O 1O', 'CG 20 10 04 13', 'ISO CG 20 10', 'CG 20 37 (04/13)', 'CG 20 10 CG 20 37', 'CG 20 37 042013',
         'CG 20 10 11 85', 'Nov 1985', '11/1985', '(11/85)', 'ＣＧ２０１０', '', None,
         'ACME COMP 7', 'ACME CO 77', 'CO77', 'OO1', 'CG 20 O4', 'ISOLATED 12', 'CG 72 15 04 13', 'CQ 20 37', 'CG 2I 37', 'NO 2013', 'OCT 2O13', "Nov '85", 'Ed. 04 13', '2013-04', '04_13', '04 13!']


def _random_strings(n, seed=8):
    r = random.Random(seed)
    out = []
    for _ in range(n):
        s = []
        for _ in range(r.randint(0, 14)):
            x = r.random()
            s.append(r.choice(_SEED_PARTS) if x < 0.75 else chr(r.randint(0x80, 0x2FFF)) if x < 0.9 else
                     chr(r.randint(0x3000, 0x10FFF)))
        out.append(''.join(s))
    return out


def _engine_reading(rt, s):
    return (rt.form_parts(s), sorted(rt.iso_numbers_in(s)), rt.never_completed_ops(s),
            rt.iso_floor_clash(s, 'ai_completed') is None, rt.iso_floor_clash(s, 'ai_ongoing') is None,
            rt.edition_parts(s), rt.needs_fold(s), rt.near_iso_number(s), rt.is_iso_form(s))


def _watcher_reading(w, s):
    fk = w._form(s)
    iso = fk in w._ISO_FLOOR or fk in w._NOT_COMPLETED or bool(w._ISO_AI.match(fk or '')) or bool(w._iso_in(s))
    return (w._form_parts(s), sorted(w._iso_in(s)), w._never_completed(s), w._clash(s, 'ai_completed') is None,
            w._clash(s, 'ai_ongoing') is None, w._edition_read(s), w._refold(s), w._near_iso(s), iso)


def test_f_engine_and_watcher_read_every_string_alike():
    """Fixed adversarial strings plus 20000 seeded random ones (random non-ASCII included): every reading agrees."""
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    bad = [(s, _engine_reading(rt, s), _watcher_reading(w, s)) for s in FIXED + _random_strings(20000)
           if _engine_reading(rt, s) != _watcher_reading(w, s)]
    assert not bad, bad[:5]


def test_f_engine_and_watcher_read_every_items_edition_alike():
    from services.biz.packs import risk_transfer as rt
    w = _watcher()
    r = random.Random(88)
    strings = FIXED + _random_strings(3000, seed=89)
    bad = []
    for _ in range(4000):
        it = {'forms': [r.choice(strings + ['CG 20 37 04 13', 'CG 20 37 07 04']) for _ in range(r.randint(0, 3))]}
        if r.random() < 0.8:
            it['edition'] = r.choice([None, '', '04 13', '07 04', 'CG 20 37 04 13', r.choice(strings)])
        a, b = rt.item_edition(it), w._item_ed(it)
        if a[0] != b[0] or bool(a[1]) != bool(b[1]):
            bad.append((it, a, b))
    assert not bad, bad[:5]


def test_f_the_contract_folds_what_the_ruling_names():
    from services.biz.packs import risk_transfer as rt
    assert rt.form_key('СG 20 10') == rt.form_key('CG 2O 1O') == rt.form_key('CG 20 1٠') == 'CG2010'
    assert rt.needs_fold('СG 20 10') and rt.needs_fold('CG 2O 1O') and not rt.needs_fold('CG 20 10 04 13')
    assert rt.edition_parts('CG 20 10 11 85') == ('CG2010', (11, 1985)) and rt.edition_parts('04 13') == (None, (4, 2013))
    assert rt.edition_parts("Nov '85") == (None, None) and rt.edition_parts('(11/85)')[1] == (11, 1985)
    assert rt.near_iso_number('CQ 20 37') and not rt.near_iso_number('ACME COMP 7')
    assert not rt.needs_fold('ACME CO 77') and not rt.needs_fold('CO77') and rt.needs_fold('CG 20 O4')   # an O in a word stays


# ═════════════════════════ G (H8): the edition field holds an edition only ═════════════════════════
@pytest.mark.parametrize('edition', ['CG 20 37 04 13', 'CG 20 10 04 13', 'CG2037 0413'])
def test_g_an_items_edition_field_holding_a_form_number_is_refused(h, edition):
    w = world(h, party_name=f'G Item {edition} Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': _items_with(x, 'ai_completed', {'edition': edition})})
    assert r.status_code == 422 and r.get_json()['code'] == 'edition_not_mm_yy', r.get_json()


def test_g_a_stored_items_edition_holding_a_form_number_reads_cant_tell(h, dirs):
    w = world(h, party_name='G Item Stored Inc')
    _patch_item(dirs, w['set']['id'], 'ai_completed', {'edition': 'CG 20 10 04 13'})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


def test_g_an_endorsement_edition_naming_another_form_reads_cant_tell(h):
    w = world(h, endorsements=_completed(edition='CG 20 10 04 13'), party_name='G Paper Other Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.edition' in codes(ev), ev
    assert any('names form CG2010' in n for n in ev['notes']), ev['notes']


def test_g_already_held_an_endorsement_edition_printing_its_own_number_meets(h):
    w = world(h, endorsements=_completed(edition='CG 20 37 04 13'), party_name='G Paper Same Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets', ev


def test_g_watch_an_endorsement_edition_naming_another_form_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(edition='CG 20 10 04 13'), party_name='G Watch Inc')
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in _reasons(rows)), rows


def test_g_watch_positive_control_the_same_form_in_the_edition_passes(h, dirs):
    w = world(h, endorsements=_completed(edition='CG 20 37 04 13'), party_name='G Watch Same Inc')
    assert _review(h, w)['state'] == 'meets'
    v, rows, _ = _watch(dirs)
    assert v == 'PASS', rows


def test_g_watch_a_stored_items_edition_holding_a_form_number_is_below_the_floor(h, dirs):
    w = world(h, party_name='G Watch Item Inc')
    _patch_item(dirs, w['set']['id'], 'ai_completed', {'edition': 'CG 20 10 04 13'})
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in _reasons(rows)), rows


def test_rules_version_is_1_8_0():
    from services.biz.packs import risk_transfer as rt
    assert rt.RULE_VERSION == 'crt-rules-1.10.0'         # rules 1.9.0-1.10.0 (re-checks #9, #10) keep every 1.8.0 rule
