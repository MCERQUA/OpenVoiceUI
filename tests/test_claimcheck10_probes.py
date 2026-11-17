"""Gate-4 re-check #10 probes (non-author, opus, 2026-11-17), on 8b0d564 (rules crt-rules-1.9.1).

Written from the spec and from the prior version's behaviour, never from the 1.9.x code:
- plan 4.4 "Requirement-set validation refuses ... pre-2004 editions (422)"; 1.9.0 P "The edition the verdict applies
  (the edition field, or the edition written in the form list under H5) is refused when it is before 2004";
- H5 (1.8.0) "When the item's edition is absent, null or "", that edition [written in the form list] is the item's
  requirement ... Two different editions for one item (in the list, or the list against the field) are refused on
  write (422 edition_conflict)"; the engine's own edition reader documents 'Nov 1985' / 'Nov 2013' as footer
  spellings, and the 1.9.0 note names 'Nov 2013' and 'JUL 2013' as editions;
- R3 / G / H6 / 1.9.0 L / 1.9.1 (the widened map, the CG-prefix rule, "a look-alike that reads exactly as
  CG 20 10 / 20 11 stays Can't tell");
- 1.9.1 note: "Real manuscript numbers that are not within one edit of CG 20 xx ... read as before";
- H7 "one normalization contract, two copies": my own generator over the 1.9.1 alphabet, not the author's.
Threat model as in rounds 7-9: a prompt-injected agent holding the agent key, plus honest staff who confirm a field
that reads like the paper, follow the engine's notes, approve what the page shows and re-pin when prompted.

Convention (round 7 onward): a HOLE probe asserts the SPEC outcome, so FAILED means the hole is real. CONTROL probes
assert the good outcome and pass. WATCH probes assert TODAY's reading of a ruling-dependent behaviour (a residual or a
false positive the host has to rule on); they pass on 8b0d564 and are there to make a later change visible.
"""
import importlib.util
import itertools
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
    spec = importlib.util.spec_from_file_location('crt_watch_r10', ROOT / 'tools' / 'biz' / 'crt-verdict-watch.py')
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


def _revise_ongoing(h, w, forms=None, edition=...):
    """The agent revises the approved set's ai_ongoing item (forms and / or edition; edition=None drops the key)."""
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = []
    for it in x['data']['items']:
        if it['id'] == 'ai_ongoing':
            it = dict(it)
            if forms is not None:
                it['forms'] = forms
            if edition is None:
                it.pop('edition', None)
            elif edition is not ...:
                it['edition'] = edition
        items.append(it)
    h.agent()
    return x, h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items})


def _manuscript_completed(h, w):
    """Staff follow the manuscript route for the completed-ops endorsement: wording person-confirmed on the surface."""
    h.staff()
    e = w['end']['ai_completed']
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])
    return h.state(w['asg']['id'])


# ───── R10-P2: a pre-2004 edition written in MONTH WORDS in the item's form list is not refused ─────
# 1.9.0 P made the refusal read the applied edition, but the form-list reader only splits a numeric edition off
# ('CG 20 10 11 85', 'CG 20 10 (11/85)'). The engine's own edition reader reads 'CG 20 10 Nov 1985' as form CG 20 10,
# edition 11/1985; the form-list reader keeps 'CG2010NOV1985' as one key, so the item applies "any edition".
@pytest.mark.parametrize('form', ['CG 20 10 Nov 1985', 'CG 20 10 (Nov 1985)', 'CG 20 10 NOV 85'])
def test_R10_P2_HOLE_a_pre_2004_month_word_edition_in_the_form_list_is_refused(h, form):
    """Plan 4.4 + 1.9.0 P: a pre-2004 edition written in the form list is refused (422), as '11 85' is."""
    w = world(h, party_name=f'P Two {form} Inc')
    _x, r = _revise_ongoing(h, w, forms=[form, 'CG 20 33', 'CG 20 38'], edition=None)
    body = r.get_json() or {}
    print('P2', repr(form), r.status_code, body.get('code'))
    if r.status_code == 201:
        d = h.ok(h.get(f'/crt/requirement-sets/{body["record"]["id"]}/diff'))
        item = next(i for i in d['items'] if i['id'] == 'ai_ongoing')
        print('P2 diff edition_applied', item['now'].get('edition_applied'), 'floor', d['floor_violations'])
    assert r.status_code == 422, body


def test_R10_P0_control_a_pre_2004_numeric_edition_in_the_form_list_is_refused(h):
    w = world(h, party_name='P Zero Ten Inc')
    _x, r = _revise_ongoing(h, w, forms=['CG 20 10 11 85', 'CG 20 33', 'CG 20 38'], edition=None)
    print('P0', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'pre_2004_edition_refused', r.get_json()


def test_R10_P3_control_a_look_alike_pre_2004_edition_in_the_form_list_is_refused(h):
    w = world(h, party_name='P Three Ten Inc')
    _x, r = _revise_ongoing(h, w, forms=['CG 20 10 1I 8S', 'CG 20 33', 'CG 20 38'], edition=None)
    print('P3', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422, r.get_json()


# ───── R10-E2: a month-word edition in the form list neither binds (H5) nor conflicts ─────
def test_R10_E2a_HOLE_a_month_word_edition_in_the_form_list_conflicting_with_the_field_is_refused(h):
    """H5: two different editions for one item (the list against the field) are refused (422 edition_conflict)."""
    w = world(h, party_name='E Two A Inc')
    _x, r = _revise_ongoing(h, w, forms=['CG 20 10', 'CG 20 10 Nov 2013', 'CG 20 33', 'CG 20 38'], edition='04 13')
    body = r.get_json() or {}
    print('E2a', r.status_code, body.get('code'))
    if r.status_code == 201:
        d = h.ok(h.get(f'/crt/requirement-sets/{body["record"]["id"]}/diff'))
        item = next(i for i in d['items'] if i['id'] == 'ai_ongoing')
        print('E2a diff edition_applied', item['now'].get('edition_applied'), 'floor', d['floor_violations'])
    assert r.status_code == 422 and body.get('code') == 'edition_conflict', body


def test_R10_E2c_control_a_numeric_edition_in_the_form_list_conflicting_with_the_field_is_refused(h):
    w = world(h, party_name='E Two C Inc')
    _x, r = _revise_ongoing(h, w, forms=['CG 20 10', 'CG 20 10 11 13', 'CG 20 33', 'CG 20 38'], edition='04 13')
    print('E2c', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'edition_conflict', r.get_json()


@pytest.mark.parametrize('listed', ['CG 20 10 Apr 2013', 'CG 20 10 04 13'])
def test_R10_E2b_HOLE_a_month_word_edition_in_the_form_list_binds(h, listed):
    """H5: with the edition field removed, the edition written in the form list is the item's requirement, so the
    sub's 07 04 paper reads WRONG_EDITION. The numeric spelling is the control (it binds since 1.8.0)."""
    w = world(h, endorsements=_ends(edition='07 04'), party_name=f'E Two B {listed} Inc')
    _x, r = _revise_ongoing(h, w, forms=['CG 20 10', listed, 'CG 20 33', 'CG 20 38'], edition=None)
    a = h.ok(r, 201)['record']
    d, rec = _approve_as_page(h, a['id'])
    applied = next(i for i in d['items'] if i['id'] == 'ai_ongoing')['now'].get('edition_applied')
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(w['asg']['id'])
    print('E2b', repr(listed), 'page edition_applied', applied, '->', ev['state'], codes(ev))
    assert ev['state'] == 'variance' and 'WRONG_EDITION:ai_ongoing' in codes(ev), ev


# ───── R10-G: the widened map's exact look-alikes and the CG prefix (spec 1.9.1) ─────
WIDE = {
    '6-and-Z': 'C6 Z0 10',      # CG 20 10: G as 6, 2 as Z (two new-map glyphs)
    'D-and-Q': 'CG 2D 1Q',      # CG 20 10: 0 as D and Q
    'Z-and-D': 'CG Z0 1D',      # CG 20 10
    '6-Q-2011': 'C6 2Q 11',     # CG 20 11
    'gap-CG': 'C-G ZQ |O',      # CG 20 10 with a gap inside the prefix
    'low-zq': 'cg zq l!',       # CG 20 11, lower case
}


@pytest.mark.parametrize('label', list(WIDE))
def test_R10_G0_control_a_widened_map_look_alike_never_counts_for_completed_ops(h, label):
    """1.9.1: S/Z/B/G/Q/D join the map; an exact look-alike of CG 20 10 / 20 11 is never a manuscript, never Meets."""
    form = WIDE[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'G Zero {label} Inc')
    ev = _manuscript_completed(h, w)
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    print('G0', label, repr(form), ev['state'], codes(ev), note[:1])
    assert ev['state'] in ('missing', 'cant_tell'), ev
    assert not any('confirm it as a manuscript' in n for n in note), note


# ───── R10-X: residual ASCII spellings outside the 1.9.1 map (two weak glyphs) ─────
WEAK = {'paren-bracket': '(G 20 ]0', 'paren-J': '(G 20 J0', 'paren-bracket-2011': '(G 20 1]'}


@pytest.mark.parametrize('label', list(WEAK))
def test_R10_X1_WATCH_two_weak_glyphs_outside_the_map_reach_meets_as_a_manuscript(h, dirs, label):
    """WATCH (ruling-dependent): '(' for C plus ']' / 'J' for 1 are outside the 1.9.1 map, so the spelling is two
    edits from CG 20 xx and is read as a manuscript; with the wording confirmed it reaches plain Meets and the watcher
    agrees. Asserts today's reading; flip it if the host widens the map to these glyphs."""
    form = WEAK[label]
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'X One {label} Inc')
    ev = _manuscript_completed(h, w)
    print('X1', label, repr(form), ev['state'], codes(ev))
    v = None
    if ev['state'] in ('meets', 'meets_accepted'):
        v, _rv = _review_and_watch(h, dirs, w, f'X1[{label}]')
    assert ev['state'] == 'meets' and v == 'PASS', (ev['state'], v)


# ───── R10-FP: false positives of the widened map (fail-closed; scope the host has to rule on) ─────
CARRIER = {'D-series': 'CG D3 60 09 07', 'S-series': 'CG S3 60 09 07', 'B-series': 'CG B4 14 02 19'}


@pytest.mark.parametrize('label', list(CARRIER))
def test_R10_FP1_WATCH_a_carrier_cg_letter_series_number_is_a_dead_end(h, label):
    """WATCH: a carrier's own 'CG <letter><digit> ..' GL form with its edition printed after it. On 2d60b14 a set
    could list it and a manuscript ongoing-ops endorsement on it read Meets; on 1.9.1 the set write is 422
    form_lookalike and the endorsement reads Can't tell (no route to Meets). The S and B shapes are NOT within one edit
    of CG 20 xx (the 1.9.1 note says such numbers 'read as before'); they fold through the 'gains an edition' check."""
    form = CARRIER[label]
    w = world(h, endorsements=_ends(form=form, manuscript=True), party_name=f'FP One {label} Inc')
    ev = h.state(w['asg']['id'])
    _x, r = _revise_ongoing(h, w, forms=['CG 20 10', form, 'CG 20 33', 'CG 20 38'])
    print('FP1', label, repr(form), 'endorsement', ev['state'], codes(ev), '| set revise', r.status_code,
          (r.get_json() or {}).get('code'))
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev
    assert r.status_code == 422 and (r.get_json() or {}).get('code') == 'form_lookalike', r.get_json()


# ───── pure readings: honest footers, the strong-glyph space, the two copies ─────
def test_R10_C0_control_honest_iso_footers_and_editions_never_fold():
    from services.biz.packs import risk_transfer as rt
    forms = ['CG 20 37 04 13', 'CG 20 37 (04/13)', 'CG 20 37 ED. 04 13', 'CG 20 37 EDITION 04/13', 'ISO CG 20 37',
             'CG 20 10 12 19', 'CG 20 26 12 19', 'CG 20 01 12 19', 'CG 24 04 05 09', 'CG 20 38 04 13',
             'CG 20 33 12 19', 'CG 25 03 05 09', 'CG 25 45 05 09', 'cg 20 37', 'CG2037']
    eds = ['04 13', '04/13', '(04/13)', '0413', '04/2013', 'APR 2013', 'Nov 2013', 'SEPT 2013', 'DEC 2019', '12 19',
           '07 04', '04-13', 'OCT 2013', 'AUG 2013', 'MAY 2013', 'JUL 2013', 'JAN 2018', 'FEB 2018', 'MAR 2018',
           'JUN 2018', 'SEP 2018']
    bad = [s for s in forms + eds if rt.needs_fold(s)]
    print('C0 folded honest values', bad)
    assert not bad


_STRONG = {'C': ['c'], 'G': ['g', '6'], '2': ['z', 'Z'], '0': ['O', 'o', 'Q', 'D'],
           '1': ['I', 'i', 'l', 'L', '|', '!']}


def test_R10_S0_control_no_strong_glyph_spelling_of_cg2010_or_cg2011_is_manuscript_eligible():
    """Every spelling of CG 20 10 / 20 11 with up to two strong look-alikes (my table, the staff font) and three
    separators is ISO, near-ISO or folded: never manuscript-eligible."""
    from services.biz.packs import risk_transfer as rt
    esc, n = [], 0
    for target in ('CG2010', 'CG2011'):
        for k in (1, 2):
            for pos in itertools.combinations(range(6), k):
                for glyphs in itertools.product(*[_STRONG[target[p]] for p in pos]):
                    ch = list(target)
                    for p, g in zip(pos, glyphs):
                        ch[p] = g
                    for sep in (' ', '', '-'):
                        s = ch[0] + ch[1] + sep + ch[2] + ch[3] + sep + ch[4] + ch[5]
                        n += 1
                        if not (rt.is_iso_form(s) or rt.near_iso_number(s) or rt.needs_fold(s)):
                            esc.append(s)
    print('S0 tried', n, 'escapes', len(esc), esc[:10])
    assert not esc


_ALPHA = list('CG20 1OILSZBQD6|!.-/()[]89oilszbqdgcTAJUE7') + ['​', '０', '１', 'С', 'О',
                                                             'ß', 'ﬀ', 'İ', 'ı', 'ǀ',
                                                             '²', ' ']
_SEEDS10 = ['CG 20 10', 'CG 20 11', 'CG 20 37', 'CG 20 26', 'CG 20 37 04 13', 'CG 20 10 11 85', 'CG D3 60 09 07',
            'AUG 2013', 'CG 20 10 Nov 1985']


def _gen10(rnd):
    if rnd.random() < 0.5:
        s = list(rnd.choice(_SEEDS10))
        for _ in range(rnd.randint(1, 4)):
            op, i = rnd.random(), rnd.randrange(len(s) + 1)
            if op < 0.5 and s:
                s[min(i, len(s) - 1)] = rnd.choice(_ALPHA)
            elif op < 0.8:
                s.insert(i, rnd.choice(_ALPHA))
            elif s:
                del s[min(i, len(s) - 1)]
        return ''.join(s)
    return ''.join(rnd.choice(_ALPHA) for _ in range(rnd.randint(0, 14)))


def test_R10_N1_control_engine_and_watcher_agree_on_the_widened_alphabet():
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    rnd = random.Random(1010)
    bad = []
    for _ in range(20000):
        s = _gen10(rnd)
        e = (rt.lookalike_reading(s), rt.iso_distance(s), rt.iso_distance(s, lookalikes=False), rt.needs_fold(s),
             rt.near_iso_number(s), rt.form_parts(s), sorted(rt.iso_numbers_in(s)), rt.never_completed_ops(s),
             rt.edition_parts(s), rt.is_iso_form(s))
        v = (wt._as_digits(s), wt._gap_to_iso(s), wt._gap_to_iso(s, look=False), wt._refold(s), wt._near_iso(s),
             wt._form_parts(s), sorted(wt._iso_in(s)), wt._never_completed(s), wt._edition_read(s), wt._is_iso(s))
        if e != v:
            bad.append((s, e, v))
    for _ in range(3000):
        it = {'forms': [_gen10(rnd) for _ in range(rnd.randint(0, 3))]}
        if rnd.random() < 0.6:
            it['edition'] = rnd.choice([None, '', '04 13', '07 04', 'Nov 2013', _gen10(rnd)])
        a, b = rt.item_edition(it), wt._item_ed(it)
        if (a[0], bool(a[1])) != (b[0], bool(b[1])):
            bad.append((it, a, b))
    print('N1 disagreements', len(bad), ascii(bad[:3])[:600])
    assert not bad


def test_R10_R0_WATCH_the_two_form_readers_disagree_on_a_month_word_edition():
    """WATCH (the cause of P2 / E2): the engine's edition reader and its form-list reader read the same string two
    ways; the watcher's copies share it, so RE8 / RP9 cannot see P2 / E2 either."""
    from services.biz.packs import risk_transfer as rt
    wt = _watcher()
    s = 'CG 20 37 Nov 1985'
    got = (rt.edition_parts(s), rt.form_parts(s), wt._edition_read(s), wt._form_parts(s))
    print('R0', got)
    assert rt.edition_parts(s) == ('CG2037', (11, 1985)) and rt.form_parts(s) == ('CG2037NOV1985', None)
    assert wt._form_parts(s) == ('CG2037NOV1985', None)
