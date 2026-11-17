"""Gate-4 claim-check regression set (2026-11-17, docs/jambot/contractors-choice-claimcheck-20261117.md).

P1-P9 and W4 are the non-author reviewer's probes, copied verbatim (each asserts the SAFE behaviour; all
failed on 093f758), with ONE change in P5 and W4: the waiver's acceptance document is the contractor's
written OK (kind contractor_waiver_ok). The reviewer used an endorsement document there, which its own H2
says to refuse; run verbatim, P5 and W4 now stop at 422 not_a_waiver_document. W1-W3 asserted that the watcher FAILs a stored Meets the ENGINE produced on those holes;
the engine no longer produces those Meets, so they are kept here in their intended form: the engine now
refuses the Meets, AND a forged Meets in the same world is FAILed by the watcher's own rules (stored review
and live claim). Plus the M1 field-coverage witness and one test per fix.
"""
from datetime import timedelta

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    M, NAMED, SBX, STAFF_1, TODAY, codes, dirs, h, iso, world,
)


def _ends_doc(dk):
    return {
        'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': dk, **NAMED},
        'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': dk, **NAMED},
        'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': dk, **NAMED},
    }


def test_p1_endorsement_backed_only_by_the_certificate_is_not_meets(h):
    w = world(h, endorsements=_ends_doc('cert'))
    ev = h.state(w['asg']['id'])
    print('P1', ev['state'], ev['codes'])
    assert ev['state'] != 'meets', ev


def test_p2_agent_changes_trade_category_to_dodge_an_exclusion(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['XCU'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='excavation', policies=pols)
    assert codes(h.state(w['asg']['id'])) == ['EXCLUSION:xcu']
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.agent()
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {'trade_category': 'steel erection'}}, a['version'])
    print('P2 agent patch status', r.status_code)
    h.staff()
    ev = h.state(w['asg']['id'])
    print('P2', ev['state'], ev['codes'])
    assert ev['state'] != 'meets', ev


def test_p3_agent_repoints_the_assignment_to_a_weaker_approved_set(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['XCU'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='excavation', policies=pols)
    assert codes(h.state(w['asg']['id'])) == ['EXCLUSION:xcu']
    weak = h.ok(h.post('/crt/requirement-sets/from-template', {'template_key': 'finish'}), 201)['record']
    weak = h.ok(h.approve(weak['id']))['record']
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.agent()
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': weak['id']}}, a['version'])
    print('P3 agent patch status', r.status_code)
    h.staff()
    ev = h.state(w['asg']['id'])
    print('P3', ev['state'], ev['codes'])
    assert ev['state'] != 'meets', ev


def test_p4_policy_not_yet_in_force_is_not_meets(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-F', 'effective': iso(TODAY + timedelta(days=45)),
                   'expiration': iso(TODAY + timedelta(days=410)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-F', 'effective': iso(TODAY + timedelta(days=45)),
                         'expiration': iso(TODAY + timedelta(days=410)), 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    print('P4', ev['state'], ev['codes'])
    assert ev['state'] != 'meets', ev


def test_p5_waiver_on_a_missing_line_does_not_read_documents_reviewed(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}}}
    w = world(h, policies=pols)
    assert codes(h.state(w['asg']['id'])) == ['MISSING:umbrella']
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'MISSING:umbrella',
                        'accepted_by_contractor': 'Acme PM', 'acceptance_document_id': w['docs']['waiver_ok']['id'],
                        'expires': iso(TODAY + timedelta(days=60))})
    ev = h.state(w['asg']['id'])
    print('P5', ev['state'], ev['label'], ev['codes'], ev['accepted'])
    assert ev['label'] != 'Meets requirements (documents reviewed)', ev


def test_p6_policy_with_no_expiration_is_not_meets(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'expiration': None,
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'expiration': None,
                         'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    gl = h.ok(h.get(f'/crt_policy/{w["pol"]["gl"]["id"]}'))
    print('P6 stored expiration', gl['data'].get('expiration'))
    ev = h.state(w['asg']['id'])
    print('P6', ev['state'], ev['codes'], ev['expiring'])
    assert ev['state'] != 'meets', ev


def test_p7_named_insured_superset_is_another_entity(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Sparky Electric Holdings of Nevada LLC',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'named_insured': 'Not Sparky Electric Inc',
                         'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    print('P7', ev['state'], ev['codes'])
    assert 'WRONG_INSURED' in ev['codes'], ev


def test_p8_card_number_padded_into_64_hex_is_refused(h):
    h.staff()
    plain = h.post('/contact', {'data': {'name': 'Card Probe A', 'notes': 'card 4111111111111111 exp 12/29'}})
    print('P8 plain', plain.status_code)
    assert plain.status_code == 422
    smuggled = h.post('/contact', {'data': {'name': 'Card Probe B',
                                            'notes': 'ref 4111111111111111' + 'a' * 48 + ' exp 12/29'}})
    print('P8 smuggled', smuggled.status_code)
    h.agent()
    smuggled_agent = h.post('/contact', {'data': {'name': 'Card Probe C',
                                                  'notes': 'x 5555555555554444' + 'b' * 48}})
    print('P8 smuggled agent', smuggled_agent.status_code)
    assert smuggled.status_code == 422 and smuggled_agent.status_code == 422


def test_p9_agent_clears_scope_changed(h):
    w = world(h)
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    a = h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'scope_changed_at': '2099-01-01T00:00:00Z'}}, a['version']))
    from datetime import date
    assert 'SCOPE_CHANGED' in codes(h.state(w['asg']['id'], date.today()))
    h.agent()
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {}, 'clear': ['scope_changed_at']}, a['version'])
    print('P9 agent clear status', r.status_code)
    h.staff()
    ev = h.state(w['asg']['id'], date.today())
    print('P9', ev['state'], ev['codes'])
    assert 'SCOPE_CHANGED' in ev['codes'], ev



# ── watcher probes ──
from tests.test_biz_crt_tools import _watch, _ends  # noqa: E402


def _review_and_watch(h, dirs, w):
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    v, rows, summ = _watch(dirs)
    return rv['review']['data']['state'], v, rows


def test_w4_watch_on_a_legit_accepted_wrong_party(h, dirs):
    w = world(h, endorsements=_ends(scheduled_names=['Zenith General Contractors Inc', 'Mesa Health District']))
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PARTY',
                        'accepted_by_contractor': 'Acme PM', 'acceptance_document_id': w['docs']['waiver_ok']['id'],
                        'expires': iso(TODAY + timedelta(days=60))})
    st, v, rows = _review_and_watch(h, dirs, w)
    print('W4 stored', st, 'watch', v, rows)
    assert v == 'PASS'   # a contractor-accepted variance is a legitimate Meets; FAIL here = false alarm


# ── M1: the reviewer's field-coverage witness (its mutant survived 83 tests) ──
from tests.test_biz_crt_pack import confirm  # noqa: E402


def test_m1_one_field_confirmation_does_not_cover_the_rest(h):
    w = world(h, confirm_all=False)
    # rules 1.2.0: the documents are checked, so field coverage is the ONLY reason left (else the
    # document check would mask this guard)
    from tests.test_biz_crt_pack import DOC_FIELDS
    for d in w['docs'].values():
        confirm(h, 'document', d, DOC_FIELDS)
    for p in w['pol'].values():
        confirm(h, 'crt_policy', p, ['line'], w['docs']['cert']['id'])
    for e in w['end'].values():
        confirm(h, 'crt_endorsement', e, ['function'], e['data'].get('document_id'))
    confirm(h, 'crt_project', w['project'], ['contract_requires_ai'], w['docs']['contract']['id'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell', ev
    assert 'UNCONFIRMED:crt_policy.limits' in ev['codes']


# ── H1 ──
def test_h1_agent_cannot_create_or_repoint_an_assignment(h):
    w = world(h)
    h.agent()
    r = h.post('/crt_assignment', {'data': {'crt_project_id': w['project']['id'], 'party_contact_id': w['party']['id'],
                                            'requirement_set_id': w['set']['id'],
                                            'requirement_set_version': w['set']['version']}})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'
    assert 'requirement_set_id' in r.get_json()['fields']
    # a repoint alone (no version field in the body) is refused on the set id itself
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': w['set']['id']}}, a['version'])
    assert r.status_code == 403 and r.get_json()['fields'] == ['requirement_set_id']


def test_h1_agent_edit_of_trade_makes_the_assignment_unconfirmed(h):
    w = world(h)
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.agent()
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'trade_category': 'steel erection'}}, a['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_assignment.trade_category' in ev['codes']
    # a person on staff confirms the edited assignment: it counts again
    confirm(h, 'crt_assignment', h.ok(h.get(f'/crt_assignment/{a["id"]}')),
            ['crt_project_id', 'party_contact_id', 'requirement_set_id', 'requirement_set_version', 'trade_category',
             'scope_changed_at'])
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_h1_set_version_is_pinned(h):
    w = world(h)
    h.staff()
    r = h.post('/crt_assignment', {'data': {'crt_project_id': w['project']['id'], 'party_contact_id': w['party']['id'],
                                            'requirement_set_id': w['set']['id'],
                                            'requirement_set_version': w['set']['version'] + 5}})
    assert r.status_code == 422 and r.get_json()['code'] == 'set_version_mismatch'
    # staff edit the (unused) set after assigning: the pinned version no longer matches -> can't tell
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.ok(h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'notes': 'tweaked'}}, s['version']))
    assert 'UNCONFIRMED:crt_assignment.requirement_set_version' in h.state(w['asg']['id'])['codes']


def test_h1_agent_may_move_a_scope_change_later_but_not_back(h):
    w = world(h)
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    a = h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'scope_changed_at': '2099-01-01T00:00:00Z'}}, a['version']))
    h.agent()
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {'scope_changed_at': '2098-01-01T00:00:00Z'}}, a['version'])
    assert r.status_code == 403 and r.get_json()['code'] == 'scope_change_needs_person'
    assert h.patch(f'/crt_assignment/{a["id"]}', {'data': {'scope_changed_at': '2099-06-01T00:00:00Z'}},
                   a['version']).status_code == 200


# ── H2 ──
def test_h2_waiver_needs_the_contractors_written_ok(h):
    w = world(h)
    h.staff()
    r = h.post('/crt_waiver', {'data': {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PARTY',
                                        'accepted_by_contractor': 'A', 'expires': iso(TODAY + timedelta(days=60)),
                                        'acceptance_document_id': w['docs']['e_other']['id']}})
    assert r.status_code == 422 and r.get_json()['code'] == 'not_a_waiver_document'


# ── H3 ──
def test_h3_not_in_force_and_no_dates_are_never_meets(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-F', 'effective': iso(TODAY + timedelta(days=45)),
                   'expiration': iso(TODAY + timedelta(days=410)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and 'NOT_IN_FORCE:gl' in ev['codes']
    pols2 = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'effective': None,
                    'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
             'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w2 = world(h, policies=pols2, party_name='Dateless Electric Inc')
    ev2 = h.state(w2['asg']['id'])
    assert ev2['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.effective' in ev2['codes']


# ── H4 ──
def test_h4_a_waiver_never_clears_a_missing_document(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}}}
    w = world(h, policies=pols)
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'MISSING:umbrella',
                        'accepted_by_contractor': 'Acme PM', 'acceptance_document_id': w['docs']['waiver_ok']['id'],
                        'expires': iso(TODAY + timedelta(days=60))})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and ev['codes'] == ['MISSING:umbrella'] and ev['accepted'] == []


# ── M2: the watcher on the reviewer's holes, by its own rules ──
def _forge(h, w, **extra):
    from tests.test_biz_crt_tools import _forge_meets
    return _forge_meets(h, w, **extra)


def test_w1_engine_refuses_and_watcher_fails_a_forged_meets_on_certificate_backed_endorsements(h, dirs):
    w = world(h, endorsements=_ends_doc('cert'))
    assert h.state(w['asg']['id'])['state'] == 'missing'
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for r in rows[0]['reasons']), rows


def test_w2_engine_refuses_and_watcher_fails_a_forged_meets_on_a_policy_not_in_force(h, dirs):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-F', 'effective': iso(TODAY + timedelta(days=45)),
                   'expiration': iso(TODAY + timedelta(days=410)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-F', 'effective': iso(TODAY + timedelta(days=45)),
                         'expiration': iso(TODAY + timedelta(days=410)), 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    assert h.state(w['asg']['id'])['state'] == 'missing'
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R3') for r in rows[0]['reasons']), rows


def test_w3_agent_cannot_repoint_and_watcher_fails_a_meets_on_an_agent_written_assignment(h, dirs):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['XCU'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='excavation', policies=pols)
    weak = h.ok(h.post('/crt/requirement-sets/from-template', {'template_key': 'finish'}), 201)['record']
    weak = h.ok(h.approve(weak['id']))['record']
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.agent()
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': weak['id']}}, a['version'])
    assert r.status_code == 403
    # a bypass that writes as the agent anyway (the engine's guard broken) is caught by the watcher
    store = h.app.extensions['biz']['sandbox:josh']
    store.update('crt_assignment', a['id'], {'requirement_set_id': weak['id'], 'requirement_set_version': weak['version']},
                 expected_version=a['version'], by='agent:test-dev-sbx-josh', agent=False,
                 source={'channel': 'email', 'ref': 'bypass'})
    h.staff()
    from tests.test_biz_crt_tools import _forge_meets
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any('R6' in r for r in rows[0]['reasons']), rows


def test_m2_live_meets_claims_are_judged_too(h, dirs):
    from tests.test_biz_crt_tools import _db, _load
    mod = _load('crt-verdict-watch')
    good = world(h)
    bad = world(h, endorsements=_ends_doc('cert'), party_name='Certonly Electric Inc')
    live_good = h.state(good['asg']['id'])
    assert live_good['state'] == 'meets'
    v, rows, _ = mod.watch(_db(dirs), [live_good])
    assert v == 'PASS' and rows[0]['kind'] == 'live'
    # a live chip that says Meets on the certificate-only sub (as a buggy engine would) -> disagreement
    forged_live = dict(h.state(bad['asg']['id']), state='meets')
    v, rows, _ = mod.watch(_db(dirs), [live_good, forged_live])
    assert v == 'FAIL' and [r['watch'] for r in rows] == ['PASS', 'FAIL']


def test_w1b_watcher_fails_a_forged_meets_on_a_second_certificate_posing_as_endorsements(h, dirs):
    # not the policy's own certificate, but still a certificate (kind coi_acord25), never an endorsement
    w = world(h, endorsements=_ends_doc('cert2'))
    assert h.state(w['asg']['id'])['state'] == 'missing'
    _forge(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for r in rows[0]['reasons']), rows
