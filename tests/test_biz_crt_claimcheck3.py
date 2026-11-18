"""Rules 1.3.0: the host rulings on gate-4 re-check #3 (docs/jambot/contractors-choice-claimcheck3-20261117.md).

One regression set per ruling, each through the real API, each failing on 21408a8 (rules 1.2.0):
  H1  the trade is a staff-chosen CATEGORY (never a substring); exclusions are staff-mapped CLASSES; wording that
      is not a class, or a blank list without a confirmed "none", is Can't tell
  H2  names: NFKC + casefold, every letter kept, the entity suffix kept (LLC != Inc != Corp; spellings of one
      suffix are equal); engine AND watcher (the watcher's own implementation)
  H3  the endorsement's confirmed FORM NUMBER is checked against the item's forms (CG 20 10 is never completed
      operations); no bulk "tick all" on the intake page
  H4  a staff session writes the records a verdict reads only from a PLATFORM staff page (served from the image,
      per-render token bound to the session, top-level navigation only); a page the agent wrote, served to staff,
      gets refused
  MED edition dates read the ways a footer prints them; an empty edition is Can't tell
  MED the watcher reads limits, named insured, exclusions and form numbers itself
  LOW ?today= is staff-only; a newer APPROVED set is noted on the label and never applied silently
Fake data only.
"""
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, M, NAMED, POLICY_FIELDS, SBX, SBX_KEY, SRC, STAFF_1, STAFF_2, TODAY, _ends, codes, confirm, dirs, h, iso, world,
    END_FIELDS, STAFF_ORIGIN,
)
from tests.test_biz_crt_tools import _forge_meets, _watch

MEETS = ('meets', 'expiring_30', 'expiring_15', 'meets_accepted')
ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / 'default-schemas' / 'packs' / 'risk-transfer' / 'pages'


def _hs(excl):
    return {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': excl,
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'exclusions': ['none'],
                         'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'exclusions': ['none'],
                     'limits': {'combined_single_limit': M}}}


def _pols(named_insured=None, gl_occ=2 * M, excl=None):
    gl = {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': gl_occ, 'general_aggregate': 2 * M}}
    um = {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}
    for p in (gl, um):
        if named_insured is not None:
            p['named_insured'] = named_insured
        if excl is not None:
            p['exclusions'] = excl
    return {'gl': gl, 'umbrella': um}


# ── H1: trade category, exclusion classes, blank needs a confirmed "none" ────
@pytest.mark.parametrize('trade,state,code', [
    ('Excavating', 'cant_tell', 'UNCONFIRMED:crt_assignment.trade_category'),      # free text: not a category
    ('excavation contractor', 'cant_tell', 'UNCONFIRMED:crt_assignment.trade_category'),
    ('blasting', 'variance', 'EXCLUSION:xcu'),                                     # the guide's own word (p.47)
    ('Underground utilities', 'variance', 'EXCLUSION:xcu'),                        # a category, staff spelling
])
def test_h1_trade_is_a_category_never_a_substring(h, trade, state, code):
    w = world(h, template='high_severity', trade=trade, policies=_hs(['XCU']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == state and codes(ev) == [code], ev


def test_h1_a_listed_category_outside_the_item_is_not_applicable_but_free_text_is_cant_tell(h):
    w = world(h, template='high_severity', trade='steel_erection', policies=_hs(['XCU']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and ev['items']['xcu'] == 'N/A', ev            # control
    w2 = world(h, template='high_severity', trade='steel erection and excavation', policies=_hs(['XCU']),
               party_name='Mixed Trades Inc')
    ev = h.state(w2['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_assignment.trade_category'], ev


@pytest.mark.parametrize('item,code', [
    ({'id': 'x', 'kind': 'disallowed_exclusion', 'line': 'gl', 'code': 'xcu', 'when_trade': ['excavation']},
     'when_trade_retired'),
    ({'id': 'x', 'kind': 'disallowed_exclusion', 'line': 'gl', 'code': 'xcu', 'trade_categories': ['excavating']},
     'bad_requirement_items'),
    ({'id': 'x', 'kind': 'disallowed_exclusion', 'line': 'gl', 'code': 'mystery'}, 'bad_requirement_items'),
    ({'id': 'x', 'kind': 'disallowed_exclusion', 'line': 'gl', 'code': 'xcu', 'classes': ['explosion damage']},
     'bad_requirement_items'),
])
def test_h1_a_requirement_set_names_categories_and_classes(h, item, code):
    r = h.post('/crt_requirement_set', {'data': {'name': 'x', 'trade_category': 'custom', 'items': [item]}})
    assert r.status_code == 422 and r.get_json()['code'] == code, r.get_json()


@pytest.mark.parametrize('excl,state,code', [
    (['Explosion, Collapse and Underground Property Damage Hazard'], 'cant_tell', 'UNCONFIRMED:crt_policy.exclusions'),
    (['XCU exclusion endorsement'], 'cant_tell', 'UNCONFIRMED:crt_policy.exclusions'),   # wording, not a class
    (['collapse'], 'variance', 'EXCLUSION:xcu'),                                         # one class of the family
])
def test_h1_exclusions_are_classes_wording_is_cant_tell(h, excl, state, code):
    w = world(h, template='high_severity', trade='excavation', policies=_hs(excl))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == state and codes(ev) == ([code] if code else []), ev


def test_h1_a_class_staff_mapped_meets_but_the_same_policy_in_words_does_not(h):
    w = world(h, template='high_severity', trade='excavation', policies=_hs(['other_reviewed']))
    assert h.state(w['asg']['id'])['state'] == 'meets'                            # control
    w2 = world(h, template='high_severity', trade='excavation', policies=_hs(['Cross suits exclusion']),
               party_name='Dig Deep Inc')
    assert codes(h.state(w2['asg']['id'])) == ['UNCONFIRMED:crt_policy.exclusions']


@pytest.mark.parametrize('excl', [[], None, ['none', 'xcu']])
def test_h1_blank_exclusions_count_only_when_staff_confirmed_none(h, excl):
    w = world(h, template='high_severity', trade='excavation', policies=_hs(excl))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_policy.exclusions'], ev
    w2 = world(h, template='high_severity', trade='excavation', policies=_hs(['none']), party_name='Dig Right Inc')
    assert h.state(w2['asg']['id'])['state'] == 'meets'


def test_h1_water_moisture_wording_needs_a_class(h):
    w = world(h, template='envelope', trade='roofing', policies=_pols(excl=['Exterior moisture intrusion exclusion']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_policy.exclusions'], ev
    w2 = world(h, template='envelope', trade='roofing', policies=_pols(excl=['moisture']), party_name='Dry Roof Inc')
    assert codes(h.state(w2['asg']['id'])) == ['EXCLUSION:water']


def test_h1_a_stored_set_still_carrying_the_word_rule_is_not_read(h):
    w = world(h, template='high_severity', trade='excavation', policies=_hs(['XCU']))
    store = h.app.extensions['biz']['sandbox:josh']
    with store._tx() as conn:          # a set approved under rules 1.2.0, before the word rule was retired
        row = conn.execute('SELECT data FROM records WHERE id = ?', (w['set']['id'],)).fetchone()
        import json
        data = json.loads(row[0])
        for it in data['items']:
            if it['id'] == 'xcu':
                it.pop('trade_categories')
                it['when_trade'] = ['excavation']
        conn.execute('UPDATE records SET data = ? WHERE id = ?', (json.dumps(data), w['set']['id']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev


# ── H2: names keep the entity suffix and every letter ────────────────────────
@pytest.mark.parametrize('other,same', [
    ('Sparky Electric LLC', 'Sparky Electric, Inc.'), ('Sparky Electric Corp', 'SPARKY ELECTRIC INCORPORATED'),
    ('Sparky Electric', 'Sparky  Electric  Inc'),
])
def test_h2_the_entity_suffix_is_part_of_the_named_insured(h, other, same):
    w = world(h, policies=_pols(named_insured=other), party_name='Sparky Electric Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_INSURED'], ev
    w2 = world(h, policies=_pols(named_insured=same), party_name='Sparky Electric Inc')     # control: one suffix
    assert h.state(w2['asg']['id'])['state'] == 'meets'
    # the full legal name inside a longer one is still another company (N8: exact, never containment)
    w3 = world(h, policies=_pols(named_insured='Sparky Electric Inc of Nevada'), party_name='Sparky Electric Inc')
    assert codes(h.state(w3['asg']['id'])) == ['WRONG_INSURED']


@pytest.mark.parametrize('scheduled,ok', [
    ('Acme Builders Inc', False), ('Acme Builders Corp', False),
    ('Acme Builders, L.L.C.', True), ('ACME BUILDERS Limited Liability Company', True),
])
def test_h2_the_entity_suffix_is_part_of_the_scheduled_name(h, scheduled, ok):
    w = world(h, endorsements=_ends(scheduled_names=[scheduled, 'Mesa Health District']))
    ev = h.state(w['asg']['id'])
    if ok:
        assert ev['state'] == 'meets', ev
    else:
        assert codes(ev) == ['WRONG_PARTY'], ev
        w2 = world(h, endorsements=_ends(scheduled_names=['Acme Builders LLC Holdings', 'Mesa Health District']),
                   party_name='Second Sub Inc')
        assert codes(h.state(w2['asg']['id'])) == ['WRONG_PARTY']        # containing the legal name is not it


@pytest.mark.parametrize('holder', ['Acme Builders Inc', 'Acme Builders LLC West'])
def test_h2_the_certificate_holder_is_the_account_with_its_suffix(h, holder):
    def pols(hd):
        return {'pl': {'line': 'pl', 'policy_number': 'PL-1', 'addl_insd': False, 'occurrence_or_claims_made': 'occurrence',
                       'certificate_holder': hd, 'limits': {'each_claim': 2 * M, 'aggregate': 2 * M}},
                'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}}}
    w = world(h, template='design_pro', trade='engineer', policies=pols(holder), endorsements={})
    assert codes(h.state(w['asg']['id'])) == ['WRONG_HOLDER']
    w2 = world(h, template='design_pro', trade='engineer', policies=pols('ACME BUILDERS, L.L.C.'), endorsements={},
               party_name='Design Co Inc')
    assert h.state(w2['asg']['id'])['state'] == 'meets'


def test_h2_non_ascii_letters_are_kept(h):
    w = world(h, policies=_pols(named_insured='Möller Electric Inc'), party_name='Müller Electric Inc')
    assert codes(h.state(w['asg']['id'])) == ['WRONG_INSURED']
    w2 = world(h, policies=_pols(named_insured='MÜLLER ELECTRIC INC.'), party_name='Müller Electric Inc')
    assert h.state(w2['asg']['id'])['state'] == 'meets'


def test_watch_h2_fails_a_meets_on_an_insured_with_another_suffix(h, dirs):
    w = world(h, policies=_pols(named_insured='Sparky Electric LLC'), party_name='Sparky Electric Inc')
    assert codes(h.state(w['asg']['id'])) == ['WRONG_INSURED']           # the engine knows
    _forge_meets(h, w, h.state(w['asg']['id'])['confirmation_ids'])
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any('named insured' in r for r in rows[0]['reasons']), rows


def test_watch_h2_fails_a_meets_on_a_scheduled_name_with_another_suffix(h, dirs):
    w = world(h, endorsements=_ends(scheduled_names=['Acme Builders Inc', 'Mesa Health District']))
    assert codes(h.state(w['asg']['id'])) == ['WRONG_PARTY']
    _forge_meets(h, w)
    w2 = world(h, endorsements=_ends(scheduled_names=['Acme Builders LLC Holdings', 'Mesa Health District']),
               party_name='Second Sub Inc')
    _forge_meets(h, w2)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and len(rows) == 2, rows
    assert all(r['watch'] == 'FAIL' and any(x.startswith('R4') for x in r['reasons']) for r in rows), rows


# ── H3: the form number is compared; no bulk tick ───────────────────────────
@pytest.mark.parametrize('end,state,code', [
    ({'form': 'CG 20 10'}, 'missing', 'MISSING:ai_completed'),                 # ongoing-ops form, never completed
    # rules 1.6.0 (ruling G): CG 20 26 is an ISO number that never covers completed operations -> MISSING, like 20 10
    ({'form': 'CG 20 26'}, 'missing', 'MISSING:ai_completed'),
    ({'form': 'ACME 12 34'}, 'cant_tell', 'UNCONFIRMED:crt_endorsement.form'),  # not a form the item lists
])
def test_h3_the_confirmed_form_number_must_be_a_listed_form(h, end, state, code):
    ends = _ends()
    ends['ai_completed'] = {**ends['ai_completed'], **end}
    w = world(h, endorsements=ends)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == state and codes(ev) == ([code] if code else []), ev


def _confirm_wording(h, w):
    """Rules 1.4.0 (R3): staff confirm, on the staff surface, the manuscript's completed-operations wording."""
    h.staff()
    e = w['end']['ai_completed']
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])


@pytest.mark.parametrize('control', [{'form': 'ACME-COMP-OPS 7', 'manuscript': True},   # rests on staff
                                     {'form': 'cg2037'}])                              # same number, respelled
def test_h3_a_manuscript_or_respelled_listed_form_counts_but_cg_20_10_never_does(h, control):
    ends = _ends()
    ends['ai_completed'] = {**ends['ai_completed'], **control}
    w = world(h, endorsements=ends)
    if control.get('manuscript'):        # rules 1.4.0 (R3): only with its completed-ops wording staff-confirmed
        ev = h.state(w['asg']['id'])
        assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_endorsement.completed_ops_wording'], ev
        _confirm_wording(h, w)
    assert h.state(w['asg']['id'])['state'] == 'meets'
    ends['ai_completed'] = {**ends['ai_completed'], 'form': 'CG 20 10', 'manuscript': False}
    ev = h.state(world(h, endorsements=ends, party_name='Second Sub Inc')['asg']['id'])
    assert codes(ev) == ['MISSING:ai_completed'], ev


def test_h3_the_intake_page_has_no_bulk_tick():
    page = (PAGES / 'cca-crt-intake.html').read_text(encoding='utf-8')
    assert 'Tick all' not in page and '.checked = true' not in page
    for p in PAGES.glob('*.html'):
        assert 'Tick all' not in p.read_text(encoding='utf-8'), p.name


# ── MED: edition dates as the footer prints them; empty is Can't tell ────────
@pytest.mark.parametrize('ed,state,code', [
    ('11-85', 'variance', 'WRONG_FORM:ai_ongoing'), ('(11/85)', 'variance', 'WRONG_FORM:ai_ongoing'),
    ('Nov 1985', 'variance', 'WRONG_FORM:ai_ongoing'), ('CG 20 10 11 85', 'variance', 'WRONG_FORM:ai_ongoing'),
    ('', 'cant_tell', 'UNCONFIRMED:crt_endorsement.edition'), ('see form', 'cant_tell', 'UNCONFIRMED:crt_endorsement.edition'),
])
def test_med_edition_dates_read_as_printed_and_empty_is_cant_tell(h, ed, state, code):
    w = world(h, endorsements=_ends(edition=ed))
    ev = h.state(w['asg']['id'])
    # rules 1.5.0 (host ruling E): a pre-2004 finding no longer stops the edition comparison; both codes show
    want = [code] + (['WRONG_EDITION:ai_ongoing'] if code.startswith('WRONG_FORM') else [])
    assert ev['state'] == state and codes(ev) == want, ev


def test_med_controls_2013_spellings_meet_and_1185_is_pre_2004_but_11_dash_85_too(h):
    for i, ed in enumerate(('04/13', '(04 13)')):
        assert h.state(world(h, endorsements=_ends(edition=ed), party_name=f'Sub {i} Inc')['asg']['id'])['state'] == 'meets'
    assert codes(h.state(world(h, endorsements=_ends(edition='1185'), party_name='Sub 3 Inc')['asg']['id'])) == \
        ['WRONG_FORM:ai_ongoing', 'WRONG_EDITION:ai_ongoing']                   # rules 1.5.0 (E): both checks run
    assert codes(h.state(world(h, endorsements=_ends(edition='11-85'), party_name='Sub 4 Inc')['asg']['id'])) == \
        ['WRONG_FORM:ai_ongoing', 'WRONG_EDITION:ai_ongoing']


# ── MED: the watcher reads limits, named insured, exclusions and forms itself ─
def test_watch_med_fails_a_meets_on_a_low_limit(h, dirs):
    w = world(h, policies=_pols(gl_occ=500_000))
    assert codes(h.state(w['asg']['id'])) == ['LIMIT_LOW:gl']
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any('limit is below' in r for r in rows[0]['reasons']), rows


@pytest.mark.parametrize('excl', [['XCU'], [], ['Explosion, Collapse and Underground Property Damage Hazard']])
def test_watch_med_fails_a_meets_on_exclusions(h, dirs, excl):
    w = world(h, template='high_severity', trade='excavation', policies=_hs(excl))
    assert h.state(w['asg']['id'])['state'] not in MEETS
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R3x') for r in rows[0]['reasons']), rows


def test_watch_med_fails_a_meets_on_a_free_text_trade(h, dirs):
    w = world(h, template='high_severity', trade='Excavating', policies=_hs(['none']))
    assert h.state(w['asg']['id'])['state'] == 'cant_tell'
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any('trade categories' in r for r in rows[0]['reasons']), rows


@pytest.mark.parametrize('end', [{'form': 'CG 20 10'}, {'edition': ''}, {'edition': '11 85'}])
def test_watch_med_fails_a_meets_on_the_form_or_edition(h, dirs, end):
    ends = _ends()
    ends['ai_completed'] = {**ends['ai_completed'], **end}
    w = world(h, endorsements=ends)
    assert h.state(w['asg']['id'])['state'] not in MEETS
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in rows[0]['reasons']), rows


def test_watch_med_positive_controls_pass_beside_a_forged_meets(h, dirs):
    w = world(h, template='high_severity', trade='excavation', policies=_hs(['none']))
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    w2 = world(h, policies=_pols(named_insured='Sparky Electric, Inc.'), party_name='Sparky Electric Inc')
    h.ok(h.post(f'/crt/assignment/{w2["asg"]["id"]}/review'), 201)
    w3 = world(h, policies=_pols(named_insured='Sparky Electric LLC'), party_name='Sparky Electric Inc')
    _forge_meets(h, w3)
    v, rows, _ = _watch(dirs)
    by = {r['assignment_id']: r['watch'] for r in rows}
    assert by == {w['asg']['id']: 'PASS', w2['asg']['id']: 'PASS', w3['asg']['id']: 'FAIL'} and v == 'FAIL', rows


# ── LOW: ?today= is staff-only; a newer approved set is noted, never applied ─
def test_low_today_is_a_staff_what_if_only(h):
    w = world(h)
    aid = w['asg']['id']
    h.agent()
    r = h.c.get(f'{B}/crt/assignment/{aid}/state?today={iso(TODAY - timedelta(days=60))}', headers=h.h)
    assert r.status_code == 403 and r.get_json()['code'] == 'today_staff_only'
    for path in (f'/crt/project/{w["project"]["id"]}/matrix', '/crt/book', '/crt/renewals'):
        r = h.c.get(f'{B}{path}?today={iso(TODAY)}', headers=h.h)
        assert r.status_code == 403 and r.get_json()['code'] == 'today_staff_only', path
    assert h.c.get(f'{B}/crt/assignment/{aid}/state', headers=h.h).status_code == 200       # today: fine
    h.staff()
    assert h.ok(h.get(f'/crt/assignment/{aid}/state?today={iso(TODAY - timedelta(days=60))}'))['as_of'] == \
        iso(TODAY - timedelta(days=60))


def test_low_a_newer_approved_set_is_noted_on_the_label_and_never_applied(h):
    w = world(h)
    s = w['set']
    items = [dict(it) for it in s['data']['items']]
    for it in items:
        if it['id'] == 'gl_occ':
            it['min'] = 5 * M
    new = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items}), 201)['record']
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and ev['newer_requirement_set'] is None      # a DRAFT revision is not news yet
    h.ok(h.approve(new['id']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and codes(ev) == []                          # still pinned: verdict unchanged
    assert ev['label'] == 'Meets requirements (documents reviewed); newer requirement set available'
    assert ev['newer_requirement_set']['id'] == new['id'] and any('newer requirement set' in n for n in ev['notes'])


# ── H4: the staff trust boundary ────────────────────────────────────────────
NAV = {'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Site': 'none'}


def _staff_page(h, page='cca-crt-intake.html', user=STAFF_1, sandbox=SBX, **hdr):
    """Rules 1.4.0 (R1): a staff page is opened on the staff origin, signed in there."""
    h.staff(user)
    h.signin(user)
    return h.c.get(f'{B}/crt/staff/{page}?sandbox={sandbox}', headers=hdr, base_url=STAFF_ORIGIN)


def _token(resp):
    import re
    return re.search(r'"token": "([^"]+)"', resp.get_data(as_text=True)).group(1)


def test_h4_a_page_the_agent_wrote_served_to_staff_cannot_person_write(h):
    w = world(h, confirm_all=False)
    h.agent()
    evil = ('<!doctype html><html><body><h1>Certificate intake</h1><script src="/jb/v1/jb.js"></script>'
            '<script>/* on load: confirm every record */</script></body></html>')
    r = h.c.put(f'{B}/dev/pages/agent-intake.html', json={'html': evil, 'source': SRC}, headers=h.h)
    assert r.status_code == 200                                    # the agent may keep its own (read) view
    h.staff()
    got = h.c.get(f'/pages/sandbox/{SBX}/agent-intake.html', headers={'Authorization': 'Bearer tok'})
    assert got.status_code == 200 and b'confirm every record' in got.data     # served to staff, script and all
    # what that page's script can send from the staff browser: the staff session, no platform token
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Sec-Fetch-Site': 'same-origin',
            'Origin': 'http://localhost'}
    pol = w['pol']['gl']
    cur = h.ok(h.get(f'/crt_policy/{pol["id"]}'))
    tries = [
        ('POST', '/crt/confirm', {'record_collection': 'crt_policy', 'record_id': pol['id'],
                                  'record_version': cur['version'], 'fields': POLICY_FIELDS,
                                  'source_document_id': w['docs']['cert']['id']}),
        ('POST', f'/crt/requirement-sets/{w["set"]["id"]}/approve', {}),
        ('POST', f'/crt/assignment/{w["asg"]["id"]}/review', {}),
        ('POST', '/crt_policy', {'data': {'line': 'gl'}}),
        ('POST', f'/crt_policy/{pol["id"]}/archive', None),
    ]
    for method, path, body in tries:
        r = h.c.open(B + path, method=method, json=body, headers=page)
        assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_required', (path, r.get_json())
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    r = h.c.patch(f'{B}/crt_assignment/{a["id"]}', json={'data': {'trade_category': 'roofing'}},
                  headers={**page, 'If-Match': str(a['version'])})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_required'
    # rules 1.4.0 (review H1): contact / document / company stay writable from any page (other apps need them);
    # the write is stamped off-surface, so the verdict never counts it as a person's
    r = h.c.post(f'{B}/contact', json={'data': {'name': 'Planted Inc', 'type': 'subcontractor'}}, headers=page)
    assert r.status_code == 201
    assert h.c.get(f'{B}/crt/assignment/{w["asg"]["id"]}/state', headers=page).status_code == 200   # reads: fine
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.line' in codes(ev)     # nothing got confirmed


def test_h4_the_platform_page_is_minted_only_on_a_top_level_navigation(h):
    for hdr in ({}, {'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty'},          # fetch() / XHR
                {'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'iframe'},        # framed
                {'Sec-Fetch-Mode': 'no-cors', 'Sec-Fetch-Dest': 'object'}):        # embedded
        r = _staff_page(h, **hdr)
        assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_navigation_only', hdr
    h.agent()
    r = h.c.get(f'{B}/crt/staff/cca-crt-intake.html', headers={**h.h, **NAV})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'
    r = h.c.get(f'{B}/crt/staff/cca-crt-intake.html', headers={**h.h, **NAV}, base_url=STAFF_ORIGIN)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'           # never on the staff origin
    assert _staff_page(h, page='no-such-page.html', **NAV).status_code == 404
    r = _staff_page(h, **NAV)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    page_file = (PAGES / 'cca-crt-intake.html').read_text(encoding='utf-8')
    assert 'Certificate intake' in body and len(body) > len(page_file)              # the image's own page
    csp = r.headers['Content-Security-Policy']
    assert "frame-ancestors 'none'" in csp and "script-src 'nonce-" in csp and "'self'" not in csp.split(';')[1]
    assert r.headers['Cross-Origin-Opener-Policy'] == 'same-origin' and r.headers['X-Frame-Options'] == 'DENY'
    assert 'no-store' in r.headers['Cache-Control']


def test_h4_the_token_is_bound_to_the_session_the_sandbox_and_the_site(h):
    w = world(h, confirm_all=False)
    tok1 = _token(_staff_page(h, user=STAFF_1, **NAV))
    pol = w['pol']['gl']

    def confirm_with(user, token, **extra):
        h.staff(user)
        h.signin(user)
        cur = h.c.get(f'{B}/crt_policy/{pol["id"]}', headers={'X-Biz-Sandbox': SBX}, base_url=STAFF_ORIGIN)
        body = {'record_collection': 'crt_policy', 'record_id': pol['id'], 'record_version': cur.get_json()['version'],
                'fields': ['line'], 'source_document_id': w['docs']['cert']['id']}
        return h.c.post(f'{B}/crt/confirm', json=body, base_url=STAFF_ORIGIN,
                        headers={'X-Biz-Sandbox': SBX, 'X-JB-Staff-Surface': token, **extra})
    bad = [
        (STAFF_2, tok1, {}),                                                      # another staff user's page
        (STAFF_1, tok1[:-4] + ('0000' if not tok1.endswith('0000') else '1111'), {}),   # tampered
        (STAFF_1, 'v2.9999999999.' + 'a' * 24 + '.' + 'b' * 64, {}),             # forged
        (STAFF_1, tok1, {'Sec-Fetch-Site': 'cross-site'}),                       # another site
        (STAFF_1, tok1, {'Sec-Fetch-Site': 'same-site'}),                        # the tenant origin, same site
        (STAFF_1, tok1, {'Origin': 'https://evil.example'}),
        (STAFF_1, tok1, {'Origin': 'https://test-dev.jam-bot.com'}),             # the canvas origin
        (STAFF_1, tok1, {'Origin': 'null'}),                                     # an opaque (sandboxed) origin
    ]
    for user, token, extra in bad:
        r = confirm_with(user, token, **extra)
        assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_invalid', (user, extra, r.get_json())
    r = confirm_with(STAFF_1, tok1, **{'Sec-Fetch-Site': 'same-origin', 'Origin': STAFF_ORIGIN})
    assert r.status_code == 201 and r.get_json()['data']['confirmed_by'] == 'clerk:' + STAFF_1
    # a token from the josh sandbox's page is not the foam sandbox's
    h.staff(STAFF_1)
    r = h.c.post(f'{B}/contact', json={'data': {'name': 'x', 'type': 'commercial'}}, base_url=STAFF_ORIGIN,
                 headers={'X-Biz-Sandbox': 'foam', 'X-JB-Staff-Surface': tok1, 'Origin': STAFF_ORIGIN})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_invalid'


def test_h4_platform_page_names_are_never_written_by_the_agent(h):
    h.agent()
    for p in sorted(x.name for x in PAGES.glob('*.html')):
        r = h.c.put(f'{B}/dev/pages/{p}', json={'html': '<p>mine</p>', 'source': SRC}, headers={'X-Agent-Key': SBX_KEY})
        assert r.status_code == 409 and r.get_json()['code'] == 'platform_page_reserved', p


def test_h4_the_sdk_sends_the_token_only_from_a_platform_page():
    js = (ROOT / 'jb' / 'v1' / 'jb.js').read_text(encoding='utf-8')
    assert "opts.headers['X-JB-Staff-Surface'] = SURFACE.token" in js
    assert 'delete global.__JB_SURFACE__' in js                    # nothing later on the page can pick it up
