"""GATE 4 re-check #3 probes (non-author, opus, 2026-11-17). Derived from the SPEC (plan 4.4, the guide's own
words, host rulings), never from the 1.2.0 fixes. Each test asserts the SAFE behaviour; a FAIL demonstrates a hole.
Scratch only: never part of the PR."""
from datetime import timedelta

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    END_FIELDS, M, POLICY_FIELDS, SBX, STAFF_1, TODAY, codes, confirm, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _watch  # noqa: E402

MEETS = ('meets', 'expiring_30', 'expiring_15')


def _hs(excl):
    return {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': excl,
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False,
                     'limits': {'combined_single_limit': M}}}


def _review_watch(h, dirs, aid):
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{aid}/review'), 201)
    v, rows, _ = _watch(dirs)
    return rv['review']['data']['state'], v, rows


# ── X1: the XCU rule is skipped for trades the guide names (p.47 glossary: "excavation, demolition, or blasting")
@pytest.mark.parametrize('trade', ['blasting', 'Excavating', 'trenching', 'earthwork', 'underground utilities'])
def test_x1_xcu_exclusion_on_a_guide_trade_spelled_another_way(h, dirs, trade):
    w = world(h, template='high_severity', trade=trade, policies=_hs(['XCU']))
    ev = h.state(w['asg']['id'])
    print('X1', trade, ev['state'], ev['codes'], ev['items'].get('xcu'))
    if trade == 'blasting':
        st, v, rows = _review_watch(h, dirs, w['asg']['id'])
        print('X1 review', st, 'watch', v)
    assert ev['state'] not in MEETS, ev


# ── X2: the XCU exclusion recorded in the guide's own words (p.47: "Explosion, Collapse, and Underground")
@pytest.mark.parametrize('excl', [['Explosion, Collapse and Underground Property Damage Hazard'],
                                  ['Exclusion - Explosion, Collapse, Underground (X, C, U)']])
def test_x2_xcu_exclusion_written_out(h, excl):
    w = world(h, template='high_severity', trade='excavation', policies=_hs(excl))
    ev = h.state(w['asg']['id'])
    print('X2', excl, ev['state'], ev['codes'])
    assert ev['state'] not in MEETS, ev


def test_x2b_water_exclusion_named_as_moisture(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['Exterior moisture intrusion exclusion'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, template='envelope', trade='roofing', policies=pols)
    ev = h.state(w['asg']['id'])
    print('X2b', ev['state'], ev['codes'])
    assert ev['state'] not in MEETS, ev


def test_x2c_exclusions_never_entered_reads_as_none(h):
    """The intake page's policy form has no exclusions input; a certificate never lists exclusions."""
    w = world(h, template='high_severity', trade='excavation', policies=_hs(None))
    ev = h.state(w['asg']['id'])
    print('X2c (exclusions absent, "confirmed")', ev['state'], ev['codes'], ev['items'].get('xcu'))
    assert ev['state'] not in MEETS, ev


# ── X3: "exact normalised match" drops the legal-entity type, so a different entity matches
def test_x3a_named_insured_inc_vs_llc_is_a_different_company(h, dirs):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Sparky Electric LLC',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'named_insured': 'Sparky Electric LLC',
                         'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols, party_name='Sparky Electric Inc')
    ev = h.state(w['asg']['id'])
    print('X3a', ev['state'], ev['codes'])
    st, v, rows = _review_watch(h, dirs, w['asg']['id'])
    print('X3a review', st, 'watch', v)
    assert 'WRONG_INSURED' in ev['codes'], ev


def test_x3b_scheduled_ai_inc_vs_account_llc(h, dirs):
    named = {'scheduled_names': ['Acme Builders Inc', 'Mesa Health District'], 'scheduled_projects': ['Mesa Clinic Expansion'],
             'blanket': False, 'manuscript': False}
    ends = {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **named},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named}}
    w = world(h, endorsements=ends)          # account 'Acme Builders LLC'
    ev = h.state(w['asg']['id'])
    print('X3b', ev['state'], ev['codes'])
    st, v, rows = _review_watch(h, dirs, w['asg']['id'])
    print('X3b review', st, 'watch', v)
    assert 'WRONG_PARTY' in ev['codes'], ev


def test_x3c_non_ascii_letters_vanish(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Möller Electric Inc',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'named_insured': 'Möller Electric Inc',
                         'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols, party_name='Müller Electric Inc')
    ev = h.state(w['asg']['id'])
    print('X3c', ev['state'], ev['codes'])
    assert 'WRONG_INSURED' in ev['codes'], ev


def test_x3_control_different_name_is_caught(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Sparky Electrical Inc',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'named_insured': 'Sparky Electrical Inc',
                         'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols, party_name='Sparky Electric Inc')
    assert 'WRONG_INSURED' in h.state(w['asg']['id'])['codes']


# ── X4: a pre-2004 edition written the way the form footer prints it is not recognised
def _ends_edition(ed):
    named = {'scheduled_names': ['Acme Builders LLC', 'Mesa Health District'], 'scheduled_projects': ['Mesa Clinic Expansion'],
             'blanket': False, 'manuscript': False}
    return {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': ed, 'doc': 'e_ongoing', **named},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **named},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named}}


def test_x4_control_pre_2004_edition_is_wrong_form(h):
    w = world(h, endorsements=_ends_edition('11 85'))
    ev = h.state(w['asg']['id'])
    print('X4 control', ev['state'], ev['codes'])
    assert 'WRONG_FORM:ai_ongoing' in ev['codes']


@pytest.mark.parametrize('ed', ['CG 20 10 11 85', '11-85', '(11/85)', '11/1985', 'Nov 1985', ''])
def test_x4_pre_2004_edition_in_footer_format(h, ed):
    w = world(h, endorsements=_ends_edition(ed))
    ev = h.state(w['asg']['id'])
    print('X4', repr(ed), ev['state'], ev['codes'])
    assert ev['state'] not in MEETS, ev


# ── X5: the item names the ISO form; a non-manuscript CG 20 10 confirmed as "completed ops" is read as CG 20 37
def test_x5_iso_form_contradicts_the_function(h):
    named = {'scheduled_names': ['Acme Builders LLC', 'Mesa Health District'], 'scheduled_projects': ['Mesa Clinic Expansion'],
             'blanket': False, 'manuscript': False}
    ends = {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named}}
    w = world(h, endorsements=ends)
    ev = h.state(w['asg']['id'])
    print('X5', ev['state'], ev['codes'])
    assert ev['state'] not in MEETS, ev


# ── X6: expiry on as_of and the ?today= what-if
def test_x6a_expiration_equal_to_today_is_expired(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'expiration': iso(TODAY),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    print('X6a', ev['state'], ev['codes'])
    assert ev['state'] not in MEETS, ev


def test_x6b_effective_tomorrow_is_not_in_force(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'effective': iso(TODAY + timedelta(days=1)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    print('X6b', ev['state'], ev['codes'])
    assert ev['state'] not in MEETS, ev


def test_x6c_today_query_lets_any_caller_ask_for_a_past_meets(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'expiration': iso(TODAY - timedelta(days=3)),
                   'effective': iso(TODAY - timedelta(days=368)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    h.agent()
    ev = h.ok(h.get(f'/crt/assignment/{w["asg"]["id"]}/state?today={iso(TODAY - timedelta(days=60))}'))
    print('X6c agent asks as of 60 days ago:', ev['state'], ev['label'], 'as_of', ev['as_of'])
    assert ev['state'] not in MEETS, ev


# ── X7: a stricter approved version of the set does not reach assignments pinned to the old one
def test_x7_superseded_set_still_meets_silently(h):
    w = world(h)
    s = w['set']
    items = [dict(it) for it in s['data']['items']]
    for it in items:
        if it['id'] == 'gl_occ':
            it['min'] = 5 * M
    h.staff()
    new = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items}), 201)['record']
    h.ok(h.post(f'/crt/requirement-sets/{new["id"]}/approve'))
    ev = h.state(w['asg']['id'])
    print('X7', ev['state'], ev['codes'], 'notes', ev['notes'])
    assert ev['state'] not in MEETS or any('supersed' in n or 'newer' in n for n in ev['notes']), ev


# ── X8: the agent authors the page staff confirm on; the page runs with the staff session
def test_x8_agent_written_page_runs_with_the_staff_session(h):
    w = world(h, confirm_all=False)
    h.agent()
    evil = ('<!doctype html><html><body><h1>Certificate intake</h1><script src="/jb/v1/jb.js"></script>'
            '<script>/* on load: POST /api/biz/v1/crt/confirm for every record, fields "Tick all" */</script></body></html>')
    r = h.c.put('/api/biz/v1/dev/pages/cca-crt-intake.html', json={'html': evil, 'source': {'channel': 'web', 'ref': 'page-edit', 'confidence': 1.0}},
                headers=h.h)
    print('X8 agent PUT page', r.status_code, r.get_json())
    # what the page's script does in the staff browser: the same call the real intake page makes
    h.staff()
    got = h.c.get('/pages/sandbox/josh/cca-crt-intake.html', headers={'Authorization': 'Bearer tok'})
    csp = got.headers.get('Content-Security-Policy', '')
    print('X8 served to staff', got.status_code, 'agent script served:', b'Tick all' in got.data,
          "connect-src 'self' in CSP:", "connect-src 'self'" in csp)
    c = confirm(h, 'crt_policy', w['pol']['gl'], POLICY_FIELDS, w['docs']['cert']['id'])
    print('X8 staff-session confirm accepted, by', c['data']['confirmed_by'])
    assert r.status_code >= 400, 'the agent can write the page that holds the staff-only buttons'
