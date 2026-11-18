"""Gate-4 re-check #4 probes (non-author, opus). Written from the spec (plan 4.4 + host rulings H1-H4) and the
guide, never from the 1.3.0 fixes. A probe that PASSES here means the hole is REAL (it asserts the bad outcome);
controls are marked CONTROL and assert the good outcome. Fake data only.
"""
import re
from datetime import timedelta

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, M, NAMED, POLICY_FIELDS, SBX, SBX_KEY, SRC, STAFF_1, STAFF_2, TODAY, _ends, codes, confirm, dirs, h, iso, world,
    END_FIELDS, DOC_FIELDS,
)

MEETS = ('meets', 'expiring_30', 'expiring_15')


# ───────────────────────── H4: the staff-surface token, from the agent's side ─────────────────────────
# The agent's threat (ruling H4): a page the agent wrote runs in the staff browser. routes/canvas.py injects
# `canvas-auth-bridge` into EVERY canvas page: the OVU parent pushes the staff's live Clerk JWT into it
# (window._canvasAuthToken, refreshed every 50 s, and on demand via the 'request-auth-token' message).
# The page sends that JWT out (img-src https: / connect-src 'self' -> /api/canvas/data -> the agent's rw
# canvas-pages mount). The agent then talks to the backend from its OWN container, as a plain HTTP client:
# no browser, so it sets whatever Sec-Fetch-* it likes and omits Origin.

def _agent_replay_headers(jwt='tok', **extra):
    # what curl in openclaw-josh sends: the stolen bearer, the sandbox, nothing browser-enforced
    return {'Authorization': f'Bearer {jwt}', 'X-Biz-Sandbox': SBX, **extra}


def test_x1_canvas_page_served_to_staff_carries_the_staff_jwt_bridge(h):
    """Browser half (code + served bytes): the agent's own canvas page is served WITH the auth bridge."""
    h.agent()
    r = h.c.put(f'{B}/dev/pages/agent-view.html', json={'html': '<!doctype html><html><body><p>view</p></body></html>',
                                                       'source': SRC}, headers=h.h)
    assert r.status_code == 200
    h.staff()
    got = h.c.get(f'/pages/sandbox/{SBX}/agent-view.html', headers={'Authorization': 'Bearer tok'})
    body = got.get_data(as_text=True)
    assert got.status_code == 200
    assert 'canvas-auth-bridge' in body and '_canvasAuthToken' in body and 'request-auth-token' in body


def test_x2_agent_replays_the_staff_jwt_mints_a_surface_token_and_confirms_everything(h):
    """HOLE: with only the staff JWT (no browser), the agent mints a staff-surface token by FORGING the
    navigation headers, then person-confirms every agent-written reading. Result: plain Meets that no person
    ever looked at; every confirmation reads clerk:<staff>."""
    w = world(h, confirm_all=False)
    ev0 = h.state(w['asg']['id'])
    assert ev0['state'] == 'cant_tell'                                     # CONTROL: nothing confirmed yet
    h.staff(STAFF_1)                                                       # the JWT the bridge handed out
    page = h.c.get(f'{B}/crt/staff/cca-crt-intake.html?sandbox={SBX}',
                   headers={'Authorization': 'Bearer tok', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})
    assert page.status_code == 200
    tok = re.search(r'"token": "([^"]+)"', page.get_data(as_text=True)).group(1)
    hdr = _agent_replay_headers(**{'X-JB-Staff-Surface': tok})              # no Origin, no Sec-Fetch-Site

    def conf(coll, rec, fields, did=None):
        cur = h.c.get(f'{B}/{coll}/{rec["id"]}', headers=hdr).get_json()
        body = {'record_collection': coll, 'record_id': rec['id'], 'record_version': cur['version'], 'fields': fields}
        if did:
            body['source_document_id'] = did
        r = h.c.post(f'{B}/crt/confirm', json=body, headers=hdr)
        assert r.status_code == 201, r.get_json()
        assert r.get_json()['data']['confirmed_by'] == 'clerk:' + STAFF_1
    for d in w['docs'].values():
        conf('document', d, DOC_FIELDS)
    for p in w['pol'].values():
        conf('crt_policy', p, POLICY_FIELDS, w['docs']['cert']['id'])
    for e in w['end'].values():
        conf('crt_endorsement', e, END_FIELDS, e['data'].get('document_id'))
    conf('crt_project', w['project'], ['contract_requires_ai', 'contract_ai_parties'], w['docs']['contract']['id'])
    r = h.c.post(f'{B}/crt/assignment/{w["asg"]["id"]}/review', json={}, headers=hdr)
    assert r.status_code == 201
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and codes(ev) == [] and ev['label'] == 'Meets requirements (documents reviewed)'


def test_x2b_agent_replay_can_also_rewrite_set_up_records_as_a_person(h):
    """HOLE: the same replay makes person-writes on set-up fields (ruling N1 counts these without confirmation)."""
    w = world(h)
    h.staff(STAFF_1)
    page = h.c.get(f'{B}/crt/staff/cca-crt-book.html?sandbox={SBX}',
                   headers={'Authorization': 'Bearer tok', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})
    tok = re.search(r'"token": "([^"]+)"', page.get_data(as_text=True)).group(1)
    hdr = _agent_replay_headers(**{'X-JB-Staff-Surface': tok})
    a = h.c.get(f'{B}/crt_assignment/{w["asg"]["id"]}', headers=hdr).get_json()
    r = h.c.patch(f'{B}/crt_assignment/{a["id"]}', json={'data': {'trade_category': 'steel_erection'}},
                  headers={**hdr, 'If-Match': str(a['version'])})
    assert r.status_code == 200 and r.get_json()['updated_by'] == 'clerk:' + STAFF_1


def test_x2c_control_without_token_the_same_replay_is_refused(h):
    """CONTROL: the replay WITHOUT the minted token is refused (so the token is the only gate, and x2 mints it)."""
    w = world(h, confirm_all=False)
    h.staff(STAFF_1)
    pol = w['pol']['gl']
    cur = h.c.get(f'{B}/crt_policy/{pol["id"]}', headers=_agent_replay_headers()).get_json()
    r = h.c.post(f'{B}/crt/confirm', json={'record_collection': 'crt_policy', 'record_id': pol['id'],
                                           'record_version': cur['version'], 'fields': ['line'],
                                           'source_document_id': w['docs']['cert']['id']},
                 headers=_agent_replay_headers())
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_required'


def test_x2d_control_agent_key_cannot_open_the_staff_page(h):
    h.agent()
    r = h.c.get(f'{B}/crt/staff/cca-crt-intake.html', headers={'X-Agent-Key': SBX_KEY, 'Sec-Fetch-Mode': 'navigate',
                                                               'Sec-Fetch-Dest': 'document'})
    assert r.status_code == 403


def test_x2e_control_csrf_form_post_carries_no_token(h):
    """CONTROL: a top-level form POST (what a canvas page can make the browser send) has no X-JB header."""
    w = world(h, confirm_all=False)
    h.staff(STAFF_1)
    r = h.c.post(f'{B}/crt/confirm', data={'record_collection': 'crt_policy', 'record_id': w['pol']['gl']['id']},
                 headers={'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Sec-Fetch-Mode': 'navigate',
                          'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://localhost'})
    assert r.status_code in (400, 403)


# ───────────────────────── H3 residue: the form number vs the confirmed flags ─────────────────────────
def _ends_completed(**completed):
    e = _ends()
    e['ai_completed'] = {**e['ai_completed'], **completed}
    return e


def test_x3_cg2010_confirmed_manuscript_satisfies_completed_ops(h):
    """Ruling H3 / plan 4.4: 'CG 20 10 never satisfies completed operations'. Confirm manuscript=true on it."""
    w = world(h, endorsements=_ends_completed(form='CG 20 10', manuscript=True))
    ev = h.state(w['asg']['id'])
    assert ev['state'] in MEETS and codes(ev) == [], ev


def test_x3c_control_cg2010_non_manuscript_is_missing(h):
    w = world(h, endorsements=_ends_completed(form='CG 20 10'))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev


def test_x4_automatic_status_form_confirmed_not_blanket_skips_the_contract_tick(h):
    """CG 20 33 is 'Automatic Status When Required in Construction Agreement' (guide App. B): blanket by its
    number. Confirmed blanket=false with scheduled names, and the contract tick NOT confirmed -> spec rule 1
    says Can't tell (blanket without a confirmed contract tick)."""
    w = world(h, endorsements=_ends(form='CG 20 33', blanket=False))
    proj = h.ok(h.get(f'/crt_project/{w["project"]["id"]}'))
    h.ok(h.patch(f'/crt_project/{proj["id"]}', {'data': {'contract_ref': 'SC-2026-14'}}, proj['version']))  # tick now stale
    ev = h.state(w['asg']['id'])
    assert ev['state'] in MEETS and codes(ev) == [], ev


def test_x4c_control_same_form_confirmed_blanket_is_cant_tell(h):
    w = world(h, endorsements=_ends(form='CG 20 33', blanket=True, scheduled_names=[], scheduled_projects=[]))
    proj = h.ok(h.get(f'/crt_project/{w["project"]["id"]}'))
    h.ok(h.patch(f'/crt_project/{proj["id"]}', {'data': {'contract_ref': 'SC-2026-14'}}, proj['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_project.contract_requires_ai' in codes(ev), ev


def test_x5_cg2038_counts_as_ongoing_ops_though_the_guide_calls_it_completed_ops(h):
    """Guide p.32 'CG 20 33 (for ongoing ops) and CG 20 38 (for completed ops)'; App. B p.44 'CG 20 38 ...
    (Completed Operations) Blanket AI for completed ops'. The build lists CG 20 38 as an ONGOING form."""
    w = world(h, endorsements=_ends(form='CG 20 38', blanket=True, scheduled_names=[], scheduled_projects=[]))
    ev = h.state(w['asg']['id'])
    assert ev['state'] in MEETS and ev['items']['ai_ongoing'] == 'OK', ev


def test_x5b_cg2038_as_completed_ops_reads_missing(h):
    w = world(h, endorsements=_ends_completed(form='CG 20 38', blanket=True, scheduled_names=[],
                                              scheduled_projects=[]))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev


# ───────────────────────── editions / repose / misc spec checks ─────────────────────────
def test_x6_item_edition_is_never_compared(h):
    """The set names edition '04 13' per item; an endorsement on a different post-2004 edition reads plain Meets."""
    w = world(h, endorsements=_ends(edition='07 04'))
    ev = h.state(w['asg']['id'])
    assert ev['state'] in MEETS and codes(ev) == [], ev


@pytest.mark.parametrize('ed', ['11 85', 'CG 20 10 11 85', '(11/85)', 'Nov 1985', '10 01', '1185'])
def test_x6c_control_pre_2004_editions_never_plain_meets(h, ed):
    w = world(h, endorsements=_ends(edition=ed), party_name=f'Ed {ed} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] not in MEETS, ev


@pytest.mark.parametrize('ed', ['', 'ISO 2013', "Nov '85", '85', '2013-04', 'Ed. 04 13'])
def test_x6d_control_unreadable_editions_are_cant_tell(h, ed):
    w = world(h, endorsements=_ends(edition=ed), party_name=f'Ed2 {len(ed)} {ed.replace(chr(39), "")} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell', ev


@pytest.mark.parametrize('ry', [0, -3])
def test_x7_repose_years_zero_or_negative_drops_completed_ops(h, ry):
    """A person typing repose 0 (or negative) with any source: completed-ops AI stops being required the day
    after completion. No range check (S11 says person-entered, source required; nothing says 0 is valid)."""
    w = world(h, endorsements={k: v for k, v in _ends().items() if k != 'ai_completed'},
              project={'substantial_completion': iso(TODAY - timedelta(days=10)), 'repose_years': ry,
                       'repose_source': 'x'})
    ev = h.state(w['asg']['id'])
    assert ev['state'] in MEETS and ev['items']['ai_completed'] == 'N/A', ev


def test_x7c_control_repose_8_requires_completed_ops(h):
    w = world(h, endorsements={k: v for k, v in _ends().items() if k != 'ai_completed'},
              project={'substantial_completion': iso(TODAY - timedelta(days=10))})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and 'COMPLETED_OPS_LAPSED' in codes(ev), ev


# ───────────────────────── H2 names (controls from the spec text) ─────────────────────────
@pytest.mark.parametrize('ni', ['Sparky Electric LLC', 'Sparky Electric Corp', 'Sparky Electric', 'Spärky Electric Inc',
                                'Sparky Electric Inc.​ Holdings', 'Sparky Electrical Inc'])
def test_x8_control_named_insured_differs(h, ni):
    w = world(h, policies={'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': ni,
                                  'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
                           'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1',
                                        'limits': {'each_occurrence': 5 * M}},
                           'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False,
                                    'limits': {'combined_single_limit': M}}})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and 'WRONG_INSURED' in codes(ev), (ni, ev)


@pytest.mark.parametrize('ni', ['SPARKY ELECTRIC, INC.', 'Sparky Electric Incorporated', 'Sparky  Electric Inc'])
def test_x8b_control_same_entity_spellings_meet(h, ni):
    w = world(h, policies={'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': ni,
                                  'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
                           'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'named_insured': ni,
                                        'limits': {'each_occurrence': 5 * M}},
                           'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'named_insured': ni,
                                    'limits': {'combined_single_limit': M}}})
    assert h.state(w['asg']['id'])['state'] == 'meets'


# ───────────────────────── H1: trades and classes (spec controls) ─────────────────────────
@pytest.mark.parametrize('trade', ['tunneling', 'pile_driving', 'grading', 'Underground Utilities'])
def test_x9_control_xcu_trades_variance(h, trade):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['explosion'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade=trade, policies=pols, party_name=f'Dig {trade} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and 'EXCLUSION:xcu' in codes(ev), (trade, ev)


def test_x9b_heavy_civil_with_xcu_exclusion_reads_not_applicable(h):
    """Plan 4.3.1 lists 'heavy civil' under high_severity; the XCU item's categories leave heavy_civil out."""
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['xcu'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='heavy_civil', policies=pols)
    ev = h.state(w['asg']['id'])
    assert ev['state'] in MEETS and ev['items']['xcu'] == 'N/A', ev


# ───────────────────────── the watcher on the holes (independent check) ─────────────────────────
def test_x10_watcher_passes_the_replayed_meets_and_the_manuscript_cg2010(h, dirs):
    """HOLE if PASS: the watcher (independent instrument) reads both stored 'meets' reviews as backed."""
    from tests.test_biz_crt_tools import _watch
    w = world(h, endorsements=_ends_completed(form='CG 20 10', manuscript=True), party_name='Manuscript Electric Inc')
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    assert rv['review']['data']['state'] == 'meets'
    # the replay world (x2 path), reviewed through the replayed token
    w2 = world(h, confirm_all=False, party_name='Replay Electric Inc')
    page = h.c.get(f'{B}/crt/staff/cca-crt-intake.html?sandbox={SBX}',
                   headers={'Authorization': 'Bearer tok', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'})
    tok = re.search(r'"token": "([^"]+)"', page.get_data(as_text=True)).group(1)
    hdr = _agent_replay_headers(**{'X-JB-Staff-Surface': tok})

    def conf(coll, rec, fields, did=None):
        cur = h.c.get(f'{B}/{coll}/{rec["id"]}', headers=hdr).get_json()
        body = {'record_collection': coll, 'record_id': rec['id'], 'record_version': cur['version'], 'fields': fields}
        if did:
            body['source_document_id'] = did
        assert h.c.post(f'{B}/crt/confirm', json=body, headers=hdr).status_code == 201
    for d in w2['docs'].values():
        conf('document', d, DOC_FIELDS)
    for p in w2['pol'].values():
        conf('crt_policy', p, POLICY_FIELDS, w2['docs']['cert']['id'])
    for e in w2['end'].values():
        conf('crt_endorsement', e, END_FIELDS, e['data'].get('document_id'))
    conf('crt_project', w2['project'], ['contract_requires_ai', 'contract_ai_parties'], w2['docs']['contract']['id'])
    assert h.c.post(f'{B}/crt/assignment/{w2["asg"]["id"]}/review', json={}, headers=hdr).status_code == 201
    verdict, rows, _ = _watch(dirs)
    assert verdict == 'PASS' and [r['watch'] for r in rows] == ['PASS', 'PASS'], rows
