"""GATE 4 re-check probes (non-author, 2026-11-17). Derived from the plan (section 4.4 rule 1, ruling b, guide
"A COI is evidence, not coverage") and the PRIOR version's holes, not from the fix. Each test asserts the SAFE
behaviour; a FAIL demonstrates a hole. Scratch only: never part of the PR."""
from datetime import timedelta

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    END_FIELDS, M, POLICY_FIELDS, SBX, STAFF_1, TODAY, codes, confirm, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _watch  # noqa: E402

XCU_POLS = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['XCU'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False,
                     'limits': {'combined_single_limit': M}}}


def _get(h, coll, rid):
    h.staff()
    return h.ok(h.get(f'/{coll}/{rid}'))


def _review_watch(h, dirs, aid):
    from datetime import date
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{aid}/review'), 201)
    v, rows, _ = _watch(dirs)
    return rv['review']['data']['state'], v, rows


# ── N1: a staff edit of ANY field launders the agent's earlier edit (person_ok reads record.updated_by) ──
def test_n1a_staff_set_hold_launders_agent_trade_change(h, dirs):
    w = world(h, template='high_severity', trade='excavation', policies=XCU_POLS)
    aid = w['asg']['id']
    assert codes(h.state(aid)) == ['EXCLUSION:xcu']
    a = _get(h, 'crt_assignment', aid)
    h.agent()
    a = h.ok(h.patch(f'/crt_assignment/{aid}', {'data': {'trade_category': 'steel erection'}}, a['version']))
    h.staff()
    ev0 = h.state(aid)
    print('N1a after agent edit', ev0['state'], ev0['codes'][:2])
    # staff press "Set hold" on the project page (the page PATCHes {hold, hold_reason}), exactly what cant_tell invites
    a = h.ok(h.patch(f'/crt_assignment/{aid}', {'data': {'hold': True, 'hold_reason': 'cant tell'}}, a['version']))
    ev1 = h.state(aid)
    print('N1a after staff Set hold', ev1['state'], ev1['codes'], 'trade now', a['data'].get('trade_category'))
    a = h.ok(h.patch(f'/crt_assignment/{aid}', {'data': {'hold': False}}, a['version']))
    ev2 = h.state(aid)
    print('N1a after staff Clear hold', ev2['state'], ev2['label'])
    st, v, rows = _review_watch(h, dirs, aid)
    print('N1a review', st, 'watch', v, rows)
    assert ev1['state'] != 'meets' and ev2['state'] != 'meets', (ev1, ev2)


def test_n1b_staff_archive_unarchive_launders_agent_trade_change(h):
    w = world(h, template='high_severity', trade='excavation', policies=XCU_POLS)
    aid = w['asg']['id']
    a = _get(h, 'crt_assignment', aid)
    h.agent()
    h.ok(h.patch(f'/crt_assignment/{aid}', {'data': {'trade_category': 'steel erection'}}, a['version']))
    h.staff()
    h.ok(h.post(f'/crt_assignment/{aid}/archive'))
    h.ok(h.post(f'/crt_assignment/{aid}/unarchive'))
    ev = h.state(aid)
    print('N1b', ev['state'], ev['codes'])
    assert ev['state'] != 'meets', ev


def test_n1c_agent_renames_sub_then_staff_edits_its_notes(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Sparky Electric Holdings of Nevada LLC',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1',
                         'named_insured': 'Sparky Electric Holdings of Nevada LLC', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    aid = w['asg']['id']
    assert 'WRONG_INSURED' in codes(h.state(aid))
    p = _get(h, 'contact', w['party']['id'])
    h.agent()
    p = h.ok(h.patch(f'/contact/{p["id"]}', {'data': {'company_name': 'Sparky Electric Holdings of Nevada'}}, p['version']))
    h.staff()
    print('N1c after agent rename', h.state(aid)['state'])
    h.ok(h.patch(f'/contact/{p["id"]}', {'data': {'notes': 'called the sub, left a message'}}, p['version']))
    ev = h.state(aid)
    print('N1c after staff note', ev['state'], ev['codes'])
    assert 'WRONG_INSURED' in ev['codes'] or ev['state'] == 'cant_tell', ev


def test_n1d_agent_moves_substantial_completion_then_staff_touch(h):
    named = {'scheduled_names': ['Acme Builders LLC', 'Mesa Health District'], 'scheduled_projects': ['Mesa Clinic Expansion'],
             'blanket': False, 'manuscript': False}
    ends = {'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **named},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named}}
    w = world(h, endorsements=ends)
    aid = w['asg']['id']
    assert codes(h.state(aid)) == ['MISSING:ai_ongoing']
    pr = _get(h, 'crt_project', w['project']['id'])
    h.agent()
    pr = h.ok(h.patch(f'/crt_project/{pr["id"]}', {'data': {'substantial_completion': iso(TODAY - timedelta(days=5))}},
                      pr['version']))
    h.staff()
    pr = h.ok(h.patch(f'/crt_project/{pr["id"]}', {'data': {'notes': 'GC called'}}, pr['version']))
    confirm(h, 'crt_project', pr, ['contract_requires_ai', 'contract_ai_parties'], w['docs']['contract']['id'])
    ev = h.state(aid)
    print('N1d', ev['state'], ev['codes'], ev['notes'])
    assert ev['state'] != 'meets', ev


# ── N2: the certificate's bytes filed a second time as an "endorsement" document (same threat as P1) ──
def test_n2_endorsement_document_is_the_certificate_file_under_a_new_record(h, dirs):
    w = world(h, confirm_all=False)
    h.agent()
    fake = h.mk('document', {'title': 'endorsements (pages 2-3)', 'file_url': w['files']['cert']['url'],
                             'kind': 'endorsement', 'crt_account_id': w['account']['id'],
                             'contact_id': w['party']['id']})
    for k, e in list(w['end'].items()):
        w['end'][k] = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'document_id': fake['id']}}, e['version']))
    for p in w['pol'].values():
        confirm(h, 'crt_policy', p, POLICY_FIELDS, w['docs']['cert']['id'])
    for e in w['end'].values():
        confirm(h, 'crt_endorsement', e, END_FIELDS, fake['id'])
    confirm(h, 'crt_project', w['project'], ['contract_requires_ai', 'contract_ai_parties'], w['docs']['contract']['id'])
    h.staff()
    ev = h.state(w['asg']['id'])
    print('N2', ev['state'], ev['codes'], 'same file id as cert:',
          fake['data']['file_url'] == w['docs']['cert']['data']['file_url'])
    st, v, rows = _review_watch(h, dirs, w['asg']['id'])
    print('N2 review', st, 'watch', v, rows)
    assert ev['state'] != 'meets', ev


# ── N3: a waiver backed by ANOTHER contractor's written OK ──
def test_n3_waiver_acceptance_from_another_account(h):
    named_bad = {'scheduled_names': ['Zenith General Contractors Inc', 'Mesa Health District'],
                 'scheduled_projects': ['Mesa Clinic Expansion'], 'blanket': False, 'manuscript': False}
    ends = {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named_bad},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **named_bad},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named_bad}}
    a = world(h, endorsements=ends)
    b = world(h, account_name='Other Builders Inc', party_name='Other Sub Inc')
    assert codes(h.state(a['asg']['id'])) == ['WRONG_PARTY']
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': a['asg']['id'], 'variance_code': 'WRONG_PARTY',
                        'accepted_by_contractor': 'Other Builders PM', 'acceptance_document_id': b['docs']['waiver_ok']['id'],
                        'expires': iso(TODAY + timedelta(days=60))})
    ev = h.state(a['asg']['id'])
    print('N3', ev['state'], ev['label'], ev['accepted'])
    assert ev['state'] != 'meets_accepted', ev


# ── N4: endorsement -> policy link is read but never confirmed ──
def test_n4_endorsement_policy_link_needs_confirmation(h):
    w = world(h)
    ev = h.state(w['asg']['id'])
    print('N4', ev['state'], 'confirmed END_FIELDS exclude crt_policy_id:', 'crt_policy_id' not in END_FIELDS)
    assert ev['state'] != 'meets' or 'crt_policy_id' in END_FIELDS


# ── N5: a document removed after the review ──
def test_n5_document_archived_after_review(h, dirs):
    w = world(h)
    aid = w['asg']['id']
    from datetime import date
    h.staff()
    h.ok(h.post(f'/crt/assignment/{aid}/review'), 201)
    h.ok(h.post(f'/document/{w["docs"]["e_ongoing"]["id"]}/archive'))
    ev = h.state(aid, date.today())
    v, rows, _ = _watch(dirs)
    print('N5 live', ev['state'], ev['codes'], '| watch on stored meets review', v, rows)
    assert ev['state'] != 'meets', ev


# ── N6: the pinned set changed underneath ──
def test_n6_agent_edits_items_of_the_approved_pinned_set(h):
    w = world(h)
    s = _get(h, 'crt_requirement_set', w['set']['id'])
    items = [it for it in s['data']['items'] if it.get('kind') != 'endorsement']
    h.agent()
    r = h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'items': items}}, s['version'])
    print('N6 agent edit of approved set items', r.status_code, (r.get_json() or {}).get('code'))
    h.staff()
    ev = h.state(w['asg']['id'])
    print('N6', ev['state'], ev['codes'])
    assert ev['state'] != 'meets', ev


def test_n6b_staff_archive_unarchive_set(h):
    w = world(h)
    h.staff()
    h.ok(h.post(f'/crt_requirement_set/{w["set"]["id"]}/archive'))
    ev_a = h.state(w['asg']['id'])
    h.ok(h.post(f'/crt_requirement_set/{w["set"]["id"]}/unarchive'))
    ev = h.state(w['asg']['id'])
    print('N6b archived', ev_a['state'], '| unarchived', ev['state'], ev['codes'])
    assert ev_a['state'] != 'meets' and ev['state'] != 'meets'


# ── N7: expired / cancelled ──
def test_n7_expired_and_cancelled(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-X', 'effective': iso(TODAY - timedelta(days=400)),
                   'expiration': iso(TODAY - timedelta(days=1)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    print('N7 expired', ev['state'], ev['codes'])
    assert ev['state'] == 'expired'
    # a waiver can not paper over an expiry
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'EXPIRED:gl',
                        'accepted_by_contractor': 'Acme PM', 'acceptance_document_id': w['docs']['waiver_ok']['id'],
                        'expires': iso(TODAY + timedelta(days=60))})
    assert h.state(w['asg']['id'])['state'] == 'expired'
    # cancelled mid-term: staff record the cancel date as the expiration and re-confirm -> expired
    w2 = world(h, party_name='Cancelled Electric Inc')
    gl = _get(h, 'crt_policy', w2['pol']['gl']['id'])
    gl = h.ok(h.patch(f'/crt_policy/{gl["id"]}', {'data': {'expiration': iso(TODAY - timedelta(days=3))}}, gl['version']))
    print('N7 cancelled before reconfirm', h.state(w2['asg']['id'])['state'])
    confirm(h, 'crt_policy', gl, POLICY_FIELDS, w2['docs']['cert']['id'])
    ev2 = h.state(w2['asg']['id'])
    print('N7 cancelled', ev2['state'], ev2['codes'])
    assert ev2['state'] == 'expired'


# ── N8: name containment still decides WRONG_PARTY (M3 residue) ──
def test_n8_scheduled_name_superset_is_another_entity(h):
    named = {'scheduled_names': ['Acme Builders Holdings of Nevada LLC', 'Mesa Health District'],
             'scheduled_projects': ['Mesa Clinic Expansion'], 'blanket': False, 'manuscript': False}
    ends = {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **named},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named}}
    w = world(h, endorsements=ends)
    ev = h.state(w['asg']['id'])
    print('N8', ev['state'], ev['codes'])
    assert 'WRONG_PARTY' in ev['codes'], ev


# ── N9: accepted variance + expiring soon: does the label keep the expiry? ──
def test_n9_meets_accepted_drops_the_expiring_warning(h):
    named_bad = {'scheduled_names': ['Zenith General Contractors Inc', 'Mesa Health District'],
                 'scheduled_projects': ['Mesa Clinic Expansion'], 'blanket': False, 'manuscript': False}
    ends = {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named_bad},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **named_bad},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named_bad}}
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'expiration': iso(TODAY + timedelta(days=9)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, endorsements=ends, policies=pols)
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PARTY',
                        'accepted_by_contractor': 'Acme PM', 'acceptance_document_id': w['docs']['waiver_ok']['id'],
                        'expires': iso(TODAY + timedelta(days=60))})
    ev = h.state(w['asg']['id'])
    print('N9', ev['state'], '|', ev['label'], '| expiring', ev['expiring'], '| hold_suggested', ev['hold_suggested'])
    assert 'expiring' in ev['label'], ev


# ── N10: WRONG_INSURED (a certificate for another legal entity) is waivable ──
def test_n10_wrong_insured_is_waivable(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Different Company LLC',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'named_insured': 'Different Company LLC',
                         'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_INSURED',
                        'accepted_by_contractor': 'Acme PM', 'acceptance_document_id': w['docs']['waiver_ok']['id'],
                        'expires': iso(TODAY + timedelta(days=60))})
    ev = h.state(w['asg']['id'])
    print('N10', ev['state'], ev['label'], ev['accepted'])
    assert ev['state'] != 'meets_accepted', ev


# ── L3 (left open): a confirmation backed by ANOTHER account's document still counts ──
def test_l3_confirmation_source_from_another_account(h):
    b = world(h, account_name='Other Builders Inc', party_name='Other Sub Inc')
    a = world(h, confirm_all=False)
    for p in a['pol'].values():
        confirm(h, 'crt_policy', p, POLICY_FIELDS, b['docs']['cert']['id'])
    for k, e in a['end'].items():
        confirm(h, 'crt_endorsement', e, END_FIELDS, b['docs']['e_' + {'ai_ongoing': 'ongoing', 'ai_completed': 'completed', 'pnc': 'pnc'}[k]]['id'])
    confirm(h, 'crt_project', a['project'], ['contract_requires_ai', 'contract_ai_parties'], b['docs']['contract']['id'])
    ev = h.state(a['asg']['id'])
    print('L3', ev['state'], ev['codes'])
    assert ev['state'] != 'meets', ev
