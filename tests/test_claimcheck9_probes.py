"""Gate-4 re-check #9 probes (non-author, opus, 2026-11-17), on 215858b (rules crt-rules-1.8.0, PR #597 df1bbb7 merged).

Written from the spec and prior versions, never from the 1.8.0 fixes:
- R3 (rules 1.4.0) "CG 20 10 never satisfies completed operations by any route: not as manuscript"; G (1.6.0) "CG 20 26
  and CG 20 11 never count as completed operations ... the note on an unlisted ISO form never tells staff to confirm it
  as a manuscript"; H6 (1.8.0) "A number within one character of a CG 20 xx is never invited, or read, as a
  manuscript" and "a letter O inside a number" is a look-alike;
- plan 4.4 "Requirement-set validation refuses ... pre-2004 editions (422)" and H5 (1.8.0) "When the item's edition is
  absent, null or "", that edition [written in the form list] is the item's requirement";
- W (1.6.0) / 1.4.0 LOW (WRONG_EDITION is a waivable variance) and H5's watcher clause (RE8);
- H7 (1.8.0) "one normalization contract, two copies": my own generator (mixed scripts, zero-width, combining marks,
  full-width and other Unicode digits, look-alike letters), not the author's.
Threat model: a prompt-injected agent holding the agent key (or a dev-sandbox key) with its own HTTP client, plus
honest staff who confirm a field that reads like the paper, follow the engine's notes, approve what the page shows and
re-pin when the label says a newer set exists.

Convention (round 7 onward): a HOLE probe asserts the SPEC outcome, so FAILED means the hole is real. CONTROL probes
assert the good outcome and pass.
"""
import importlib.util
import json
import random
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    END_FIELDS, STAFF_1, TODAY, _ends, codes, confirm, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _watch

ROOT = Path(__file__).resolve().parent.parent


def _watcher():
    spec = importlib.util.spec_from_file_location('crt_watch_r9', ROOT / 'tools' / 'biz' / 'crt-verdict-watch.py')
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


# ───── R9-L: a CG 20 10 / 20 11 written with PLAIN-ASCII look-alikes escapes the near-ISO rule ─────
# H6 folds a letter O inside a run of digits and treats a number one SUBSTITUTION away (in the squashed reading) as
# near-ISO. Two gaps: (a) a punctuation look-alike for a digit ('|' for 1) is squashed away, so 'CG 20 |0' -- one
# character from 'CG 20 10' as written -- squashes to 'CG200' (five characters, no window); (b) the letter I / l for 1
# beside a letter O ('CG 20 IO', 'CG 20 lO') is not "a letter O inside a run of digits" (the O follows a letter), so the
# reading is two characters off and passes as a non-ISO number. Both are plain ASCII: the set-side 422s and the
# endorsement fold never fire.
LOOKALIKE = {
    'pipe-for-1': 'CG 20 |0',        # CG 20 10, one character changed as written
    'I-and-O': 'CG 20 IO',           # CG 20 10
    'l-and-O': 'CG 20 lO',           # CG 20 10
    'l-and-I-2011': 'CG 20 lI',      # CG 20 11 (never completed operations, ruling G)
    'pipe-2011': 'CG 20 1|',         # CG 20 11, one character changed as written
}


@pytest.mark.parametrize('label', list(LOOKALIKE))
def test_R9_L0_HOLE_an_ascii_look_alike_iso_number_is_never_invited_as_a_manuscript(h, label):
    """H6: 'A number within one character of a CG 20 xx is never invited, or read, as a manuscript'; G: the note on an
    ISO form never tells staff to confirm it as a manuscript. The agent files the CG 20 10 / 20 11 endorsement's form
    as it LOOKS ('CG 20 |0', 'CG 20 IO', ...); staff confirm the field (it reads like the paper). Spec: never the
    'confirm it as a manuscript form' invitation."""
    form = LOOKALIKE[label]
    w = world(h, endorsements=_completed(form=form), party_name=f'L Zero {label} Inc')
    ev = h.state(w['asg']['id'])
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    print('L0', label, repr(form), ev['state'], codes(ev), note)
    assert not any('manuscript form' in n and 'never' not in n for n in note), note


@pytest.mark.parametrize('label', list(LOOKALIKE))
def test_R9_L1_HOLE_an_ascii_look_alike_iso_number_never_counts_for_completed_ops(h, dirs, label):
    """R3 / G: CG 20 10 and CG 20 11 never satisfy completed operations 'by any route: not as manuscript'. Staff follow
    the L0 invitation (manuscript true, completed-operations wording person-confirmed on the staff surface). Spec: never
    a plain Meets (MISSING when read as the ISO number; Can't tell when it is only near one)."""
    form = LOOKALIKE[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'L One {label} Inc')
    h.staff()
    e = w['end']['ai_completed']
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])
    ev = h.state(w['asg']['id'])
    print('L1', label, repr(form), ev['state'], codes(ev), ev['items'].get('ai_completed'))
    if ev['state'] in ('meets', 'meets_accepted'):
        _review_and_watch(h, dirs, w, f'L1[{label}]')
    assert ev['state'] in ('missing', 'cant_tell'), ev


@pytest.mark.parametrize('form', ['CG 20 l0', 'CG 20 I0', 'CG 2O 1O', 'CG 20 1O'])
def test_R9_L2_control_a_single_letter_for_a_digit_is_near_iso(h, form):
    """Control: one letter in place of one digit (the H6 window) is never a manuscript and never Meets."""
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'L Two {form} Inc')
    h.staff()
    e = w['end']['ai_completed']
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])
    ev = h.state(w['asg']['id'])
    print('L2', repr(form), ev['state'], codes(ev))
    assert ev['state'] in ('missing', 'cant_tell'), ev


# ───── R9-P: a pre-2004 edition written in the item's FORM LIST is not refused ─────
@pytest.mark.parametrize('form', ['CG 20 10 11 85', 'CG 20 10 (11/85)'])
def test_R9_P1_HOLE_a_pre_2004_edition_in_the_form_list_is_refused(h, form):
    """Plan 4.4: 'Requirement-set validation refuses ... pre-2004 editions (422)'. H5: an edition written after the
    number in the item's form list IS the item's edition when the edition field is empty. The agent revises the
    approved set: ai_ongoing lists 'CG 20 10 11 85' and its edition key is removed. Spec: 422 (as the same edition in
    the edition field is)."""
    w = world(h, party_name=f'P One {form} Inc')
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = []
    for it in x['data']['items']:
        if it['id'] == 'ai_ongoing':
            it = {k: v for k, v in it.items() if k != 'edition'}
            it['forms'] = [form] + [f for f in it['forms'] if f != 'CG 20 10']
        items.append(it)
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items})
    body = r.get_json() or {}
    print('P1', repr(form), r.status_code, body.get('code'))
    if r.status_code == 201:
        d = h.ok(h.get(f'/crt/requirement-sets/{body["record"]["id"]}/diff'))
        item = next(i for i in d['items'] if i['id'] == 'ai_ongoing')
        print('P1 diff edition_applied', item['now'].get('edition_applied'), 'floor', d['floor_violations'])
    assert r.status_code == 422, body


def test_R9_P0_control_a_pre_2004_edition_in_the_edition_field_is_refused(h):
    w = world(h, party_name='P Zero Inc')
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [dict(it, edition='11 85') if it['id'] == 'ai_ongoing' else it for it in x['data']['items']]
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items})
    print('P0', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'pre_2004_edition_refused', r.get_json()


# ───── R9-W: a form-list edition, a WRONG_EDITION and its waiver: engine and watcher agree (H5 x W x RE8) ─────
def test_R9_W0_control_a_waived_form_list_edition_reads_accepted_and_the_watcher_agrees(h, dirs):
    """The set's completed-ops edition lives in its form list ('CG 20 37 04 13', edition key removed: H5). The sub's
    paper is the 07 04 edition: WRONG_EDITION:ai_completed. The contractor accepts it in writing and staff record the
    waiver: meets_accepted, bound to the endorsement's paper, and the watcher PASSes the review (RE8 + RW)."""
    w = world(h, endorsements=_completed(edition='07 04'), party_name='W Zero Inc')
    aid = w['asg']['id']
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = []
    for it in x['data']['items']:
        if it['id'] == 'ai_completed':
            it = {k: v for k, v in it.items() if k != 'edition'}
            it['forms'] = ['CG 20 37 04 13']
        items.append(it)
    h.agent()
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items}), 201)['record']
    d, rec = _approve_as_page(h, a['id'])
    assert next(i for i in d['items'] if i['id'] == 'ai_completed')['now']['edition_applied'] == '04/2013'
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(aid)
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_EDITION:ai_completed'], ev
    h.staff()
    wv = h.mk('crt_waiver', {'crt_assignment_id': aid, 'variance_code': 'WRONG_EDITION:ai_completed',
                             'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                             'acceptance_document_id': w['docs']['waiver_ok']['id'],
                             'recorded_by': 'clerk:' + STAFF_1, 'reason': 'older edition accepted in writing',
                             'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=300))})
    ev = h.state(aid)
    print('W0', ev['state'], codes(ev), wv['data'].get('bound_evidence'))
    assert ev['state'] == 'meets_accepted', ev
    v, rv = _review_and_watch(h, dirs, w, 'W0')
    assert v == 'PASS'


# ───── R9-N: my own generator over the engine's and the watcher's normalization contract (H7) ─────
_POOLS = [
    list('CGISOEDNVAPRMJULTBlio0123456789') + [' ', ' ', '/', '-', '.', ',', '(', ')', "'", '|', '_', '\t'],
    [chr(c) for c in range(0x400, 0x530)], [chr(c) for c in range(0x370, 0x400)],
    [chr(c) for c in range(0x300, 0x370)], ['​', '‌', '‍', '﻿', '⁠', '­', '͏'],
    [chr(c) for c in range(0xff01, 0xff5f)],
    [chr(c) for c in list(range(0x660, 0x66a)) + list(range(0x966, 0x970)) + list(range(0x1d7ce, 0x1d800))
     + list(range(0x2460, 0x2474)) + list(range(0x2070, 0x208f))],
    list('ⅠⅩⅭKıİſßﬁŉǀⲞᴄɡᏟᎳՕ'
         'Ⓒ 　∕／'),
]
_SEEDS = ['CG 20 10', 'CG 20 37', 'CG 20 26', 'CG 20 37 04 13', 'ISO CG 20 10', '04 13', 'Nov 2013', '07/04']


def _gen(rnd):
    if rnd.random() < 0.5:
        b = list(rnd.choice(_SEEDS))
        for _ in range(rnd.randint(1, 3)):
            i, ch = rnd.randrange(len(b) + 1), rnd.choice(rnd.choice(_POOLS))
            op = rnd.random()
            if op < 0.4 and b:
                b[min(i, len(b) - 1)] = ch
            elif op < 0.8:
                b.insert(i, ch)
            elif b:
                del b[min(i, len(b) - 1)]
        return ''.join(b)
    return ''.join(rnd.choice(rnd.choice([_POOLS[0]] * 6 + _POOLS[1:])) for _ in range(rnd.randint(1, 14)))


def test_R9_N0_control_engine_and_watcher_read_alike_on_an_independent_generator():
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    rnd = random.Random(20261117)
    bad = []
    for _ in range(20000):
        s = _gen(rnd)
        eng = (rt.form_parts(s), sorted(rt.iso_numbers_in(s)), rt.never_completed_ops(s), rt.needs_fold(s),
               rt.near_iso_number(s), rt.edition_parts(s), rt.iso_floor_clash(s, 'ai_completed') is None)
        wat = (wt._form_parts(s), sorted(wt._iso_in(s)), wt._never_completed(s), wt._refold(s), wt._near_iso(s),
               wt._edition_read(s), wt._clash(s, 'ai_completed') is None)
        if eng != wat:
            bad.append((s, eng, wat))
    for _ in range(3000):
        it = {'forms': [_gen(rnd) for _ in range(rnd.randint(0, 3))] + rnd.choice([[], ['CG 20 37 04 13']])}
        if rnd.random() < 0.6:
            it['edition'] = rnd.choice([None, '', ' ', 0, False, '04 13', '07 04', _gen(rnd)])
        a, b = rt.item_edition(it), wt._item_ed(it)
        if (a[0], bool(a[1])) != (b[0], bool(b[1])):
            bad.append((it, a, b))
    print('N0 disagreements', len(bad), ascii(bad[:3])[:600])
    assert not bad
