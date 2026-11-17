"""Gate-4 re-check regression set, rules 1.2.0 (2026-11-17, docs/jambot/contractors-choice-claimcheck2-20261117.md).

One test per host ruling (N1 per-field provenance, N2 endorsement document, L3/N3/N4 backing documents,
N8 exact party names, N9 accepted label keeps the expiry, N10 WRONG_INSURED never waivable, N5 watcher
STALE vs DISAGREE), plus the watcher mirror of each. Every test here asserts the SAFE behaviour and
FAILED on edc6c9f (the head the re-check judged); the re-checker's own probes are kept verbatim in
tests/test_claimcheck2_probes.py.

The helpers below run on edc6c9f too (no 1.2.0-only imports), so a "fail before" is the rule, not an
ImportError.
"""
from datetime import timedelta

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    M, NAMED, POLICY_FIELDS, STAFF_1, TODAY, codes, confirm, dirs, h, iso, pdf, world,
)
from tests.test_biz_crt_tools import _forge_meets, _watch  # noqa: E402

DOC_FIELDS = ['kind', 'crt_account_id', 'contact_id', 'file_url']
END_FIELDS_OLD = ['function', 'form', 'edition', 'scheduled_names', 'scheduled_projects', 'blanket', 'manuscript',
                  'document_id']
END_FIELDS_12 = END_FIELDS_OLD + ['crt_policy_id']
CONTRACT = ['contract_requires_ai', 'contract_ai_parties']
XCU_POLS = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['XCU'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False,
                     'limits': {'combined_single_limit': M}}}


def _get(h, coll, rid):
    h.staff()
    return h.ok(h.get(f'/{coll}/{rid}'))


def _confirm_doc(h, doc):
    """A person checks a document's kind, account, sub and file (rules 1.2.0). edc6c9f has no such
    confirmation (422 not_confirmable) and needs none, so there it is a no-op."""
    cur = _get(h, 'document', doc['id'])
    r = h.post('/crt/confirm', {'record_collection': 'document', 'record_id': doc['id'],
                                'record_version': cur['version'], 'fields': DOC_FIELDS})
    if r.status_code == 422 and (r.get_json() or {}).get('code') == 'not_confirmable':
        return None
    return h.ok(r, 201)


def _staff_doc(h, w, title, file_url, kind, contact_id=None):
    h.staff()
    return h.mk('document', {'title': title, 'file_url': file_url, 'kind': kind,
                             'crt_account_id': w['account']['id'], 'contact_id': contact_id or w['party']['id']})


def _repoint(h, w, key, doc):
    e = _get(h, 'crt_endorsement', w['end'][key]['id'])
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'document_id': doc['id']}}, e['version']))
    w['end'][key] = e
    return e


def _named_ends(names):
    named = {'scheduled_names': names, 'scheduled_projects': ['Mesa Clinic Expansion'], 'blanket': False,
             'manuscript': False}
    return {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed',
                             **named},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named}}


def _waive(h, w, code, ok_doc):
    h.staff()
    return h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': code,
                               'accepted_by_contractor': 'Acme PM', 'acceptance_document_id': ok_doc['id'],
                               'expires': iso(TODAY + timedelta(days=60))})


# ── N1: per-field provenance ────────────────────────────────────────────────
def _agent_trade(h, w, trade='steel erection'):
    a = _get(h, 'crt_assignment', w['asg']['id'])
    h.agent()
    a = h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'trade_category': trade}}, a['version']))
    h.staff()
    return a


def test_n1_a_staff_hold_never_vouches_for_the_agents_trade(h):
    w = world(h, template='high_severity', trade='excavation', policies=XCU_POLS)
    aid = w['asg']['id']
    a = _agent_trade(h, w)
    a = h.ok(h.patch(f'/crt_assignment/{aid}', {'data': {'hold': True, 'hold_reason': 'check trade'}}, a['version']))
    a = h.ok(h.patch(f'/crt_assignment/{aid}', {'data': {'hold': False}}, a['version']))
    ev = h.state(aid)
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_assignment.trade_category' in codes(ev), ev
    # a person writing THAT field makes it count (and the exclusion is read against the real trade)
    h.ok(h.patch(f'/crt_assignment/{aid}', {'data': {'trade_category': 'excavation'}}, a['version']))
    assert codes(h.state(aid)) == ['EXCLUSION:xcu']


def test_n1_a_staff_archive_and_unarchive_never_vouches_for_the_agents_trade(h):
    w = world(h, template='high_severity', trade='excavation', policies=XCU_POLS)
    _agent_trade(h, w)
    h.ok(h.post(f'/crt_assignment/{w["asg"]["id"]}/archive'))
    h.ok(h.post(f'/crt_assignment/{w["asg"]["id"]}/unarchive'))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_assignment.trade_category'], ev


def test_n1_the_subs_legal_name_written_by_the_agent_needs_a_person(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Sparky Electric Holdings of Nevada LLC',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1',
                         'named_insured': 'Sparky Electric Holdings of Nevada LLC', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    p = _get(h, 'contact', w['party']['id'])
    h.agent()
    p = h.ok(h.patch(f'/contact/{p["id"]}', {'data': {'company_name': 'Sparky Electric Holdings of Nevada'}},
                     p['version']))
    h.staff()
    p = h.ok(h.patch(f'/contact/{p["id"]}', {'data': {'notes': 'called the sub'}}, p['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:contact.company_name'], ev
    # a person confirming THAT field is the only other way it counts
    confirm(h, 'contact', p, ['company_name'])
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_n1_project_completion_and_account_need_a_person_per_field(h):
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
    confirm(h, 'crt_project', pr, CONTRACT, w['docs']['contract']['id'])
    ev = h.state(aid)
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_project.substantial_completion' in codes(ev), ev
    # staff confirm that one field: ongoing ops are over, and it counts
    pr = _get(h, 'crt_project', pr['id'])
    h.ok(h.post('/crt/confirm', {'record_collection': 'crt_project', 'record_id': pr['id'], 'record_version': pr['version'],
                                 'fields': ['substantial_completion'] + CONTRACT,
                                 'source_document_id': w['docs']['contract']['id']}), 201)
    ev = h.state(aid)
    assert ev['state'] == 'meets' and any('substantial completion' in n for n in ev['notes']), ev
    # the account the project belongs to (which account's evidence is read at all): agent-written -> can't tell
    other = h.mk('contact', {'name': 'Other Builders Inc', 'type': 'commercial'})
    pr = _get(h, 'crt_project', pr['id'])
    h.agent()
    pr = h.ok(h.patch(f'/crt_project/{pr["id"]}', {'data': {'crt_account_id': other['id']}}, pr['version']))
    pr = h.ok(h.patch(f'/crt_project/{pr["id"]}', {'data': {'crt_account_id': w['account']['id']}}, pr['version']))
    h.staff()
    pr = h.ok(h.patch(f'/crt_project/{pr["id"]}', {'data': {'notes': 'moved back'}}, pr['version']))
    h.ok(h.post('/crt/confirm', {'record_collection': 'crt_project', 'record_id': pr['id'], 'record_version': pr['version'],
                                 'fields': ['substantial_completion'] + CONTRACT,
                                 'source_document_id': w['docs']['contract']['id']}), 201)
    ev = h.state(aid)
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_project.crt_account_id'], ev


def test_n1_the_account_link_written_by_the_agent_needs_a_person(h):
    w = world(h)
    ln = _get(h, 'crt_account_party', w['link']['id'])
    h.agent()
    ln = h.ok(h.patch(f'/crt_account_party/{ln["id"]}', {'data': {'status': 'ended'}}, ln['version']))
    ln = h.ok(h.patch(f'/crt_account_party/{ln["id"]}', {'data': {'status': 'active'}}, ln['version']))
    h.staff()
    h.ok(h.post(f'/crt_account_party/{ln["id"]}/archive'))
    h.ok(h.post(f'/crt_account_party/{ln["id"]}/unarchive'))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_account_party.status'], ev


def test_watch_n1_fails_a_meets_on_an_agent_written_trade_after_a_staff_hold(h, dirs):
    w = world(h)
    a = _agent_trade(h, w, trade='electrical contractor')
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'hold': True, 'hold_reason': 'x'}}, a['version']))
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R6') and 'trade_category' in r for r in rows[0]['reasons']), rows


# ── N2: the endorsement document ───────────────────────────────────────────
def _cert_file_as_endorsement(h, w):
    fake = _staff_doc(h, w, 'endorsement pages', w['files']['cert']['url'], 'endorsement')
    e = _repoint(h, w, 'ai_ongoing', fake)
    confirm(h, 'crt_endorsement', e, END_FIELDS_12, fake['id'])
    h.staff()
    return fake


def test_n2_the_certificate_file_under_an_endorsement_record_is_never_the_endorsement(h):
    w = world(h)
    _cert_file_as_endorsement(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and codes(ev) == ['MISSING:ai_ongoing'], ev


def test_n2_staff_cannot_confirm_a_certificate_file_as_an_endorsement(h):
    w = world(h)
    h.agent()
    fake = h.mk('document', {'title': 'endorsements', 'file_url': w['files']['cert']['url'], 'kind': 'endorsement',
                             'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']})
    h.staff()
    r = h.post('/crt/confirm', {'record_collection': 'document', 'record_id': fake['id'],
                                'record_version': fake['version'], 'fields': DOC_FIELDS})
    assert r.status_code == 422 and r.get_json()['code'] == 'endorsement_is_the_certificate', r.get_json()


def _agent_relabels_a_page(h, w):
    h.staff()
    up = h.upload(pdf('a page the agent calls an endorsement'), 'page.pdf')
    doc = _staff_doc(h, w, 'misc page', up['url'], 'contract_excerpt')       # staff filed it as something else
    h.agent()
    doc = h.ok(h.patch(f'/document/{doc["id"]}', {'data': {'kind': 'endorsement'}}, doc['version']))
    h.staff()
    e = _repoint(h, w, 'ai_ongoing', doc)
    confirm(h, 'crt_endorsement', e, END_FIELDS_12, doc['id'])
    h.staff()
    return doc


def test_n2_an_agent_kind_label_alone_never_makes_an_endorsement(h):
    w = world(h)
    doc = _agent_relabels_a_page(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:document.kind'], ev
    _confirm_doc(h, doc)                       # a person checks the paper: now it counts
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_watch_n2_fails_a_meets_on_the_certificate_file_under_an_endorsement_record(h, dirs):
    w = world(h)
    _cert_file_as_endorsement(h, w)
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and rows[0]['reasons'] == [r for r in rows[0]['reasons'] if r.startswith('R4')], rows
    assert any('ai_ongoing' in r for r in rows[0]['reasons'])


def test_watch_n2_fails_a_meets_on_an_agent_kind_label(h, dirs):
    w = world(h)
    _agent_relabels_a_page(h, w)
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4 no person-confirmed ai_ongoing') for r in rows[0]['reasons']), rows


# ── L3 / N3 / N4: backing documents belong to this sub and this account ─────
def _l3_world(h):
    w = world(h, confirm_all=False)
    other = h.mk('contact', {'name': 'Other Sub Inc', 'type': 'subcontractor'})
    h.mk('crt_account_party', {'crt_account_id': w['account']['id'], 'party_contact_id': other['id'], 'status': 'active'})
    odoc = _staff_doc(h, w, 'other sub certificate', w['files']['cert2']['url'], 'coi_acord25', contact_id=other['id'])
    for d in list(w['docs'].values()) + [odoc]:
        _confirm_doc(h, d)
    for p in w['pol'].values():
        confirm(h, 'crt_policy', p, POLICY_FIELDS, odoc['id'])           # read from ANOTHER sub's paper
    for e in w['end'].values():
        confirm(h, 'crt_endorsement', e, END_FIELDS_12, e['data']['document_id'])
    confirm(h, 'crt_project', w['project'], CONTRACT, w['docs']['contract']['id'])
    h.staff()
    return w


def test_l3_a_reading_confirmed_from_another_subs_paper_never_counts(h):
    w = _l3_world(h)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.limits' in codes(ev), ev


def test_watch_l3_fails_a_meets_on_readings_from_another_subs_paper(h, dirs):
    w = _l3_world(h)
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R3') for r in rows[0]['reasons']), rows


def _n3_worlds(h):
    a = world(h, endorsements=_named_ends(['Zenith General Contractors Inc', 'Mesa Health District']))
    b = world(h, account_name='Other Builders Inc', party_name='Other Sub Inc')
    assert codes(h.state(a['asg']['id'])) == ['WRONG_PARTY']
    _waive(h, a, 'WRONG_PARTY', b['docs']['waiver_ok'])            # ANOTHER contractor's written OK
    return a, b


def test_n3_a_waiver_ok_from_another_contractor_never_counts(h):
    a, _ = _n3_worlds(h)
    ev = h.state(a['asg']['id'])
    assert ev['state'] == 'variance' and ev['accepted'] == [] and codes(ev) == ['WRONG_PARTY'], ev
    _waive(h, a, 'WRONG_PARTY', a['docs']['waiver_ok'])            # this contractor's own OK does
    assert h.state(a['asg']['id'])['state'] == 'meets_accepted'


def test_watch_n3_fails_a_meets_on_another_contractors_waiver(h, dirs):
    a, _ = _n3_worlds(h)
    _forge_meets(h, a)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for r in rows[0]['reasons']), rows


def test_n4_an_endorsement_confirmation_must_name_its_policy(h):
    w = world(h, confirm_all=False)
    for d in w['docs'].values():
        _confirm_doc(h, d)
    for p in w['pol'].values():
        confirm(h, 'crt_policy', p, POLICY_FIELDS, w['docs']['cert']['id'])
    for e in w['end'].values():
        confirm(h, 'crt_endorsement', e, END_FIELDS_OLD, e['data']['document_id'])
    confirm(h, 'crt_project', w['project'], CONTRACT, w['docs']['contract']['id'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.crt_policy_id' in codes(ev), ev
    for e in w['end'].values():
        confirm(h, 'crt_endorsement', e, ['crt_policy_id'], e['data']['document_id'])
    assert h.state(w['asg']['id'])['state'] == 'meets'


# ── N8: exact normalised names, no containment ─────────────────────────────
def test_n8_a_longer_name_containing_the_account_is_another_entity(h):
    w = world(h, endorsements=_named_ends(['Acme Builders Holdings of Nevada LLC', 'Mesa Health District']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_PARTY'], ev


def test_n8_a_longer_certificate_holder_is_the_wrong_holder(h):
    pols = {'pl': {'line': 'pl', 'policy_number': 'PL-1', 'addl_insd': False, 'occurrence_or_claims_made': 'occurrence',
                   'certificate_holder': 'Acme Builders Holdings of Nevada LLC',
                   'limits': {'each_claim': 2 * M, 'aggregate': 2 * M}},
            'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}}}
    w = world(h, template='design_pro', trade='engineer', policies=pols, endorsements={})
    assert codes(h.state(w['asg']['id'])) == ['WRONG_HOLDER']


def test_watch_n8_fails_a_meets_on_a_longer_name(h, dirs):
    w = world(h, endorsements=_named_ends(['Acme Builders Holdings of Nevada LLC', 'Mesa Health District']))
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for r in rows[0]['reasons']), rows


# ── N9 / N10: accepted variances ───────────────────────────────────────────
def test_n9_an_accepted_variance_keeps_the_expiry_in_its_label(h):
    for days, n in ((9, 15), (25, 30)):
        pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'expiration': iso(TODAY + timedelta(days=days)),
                       'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
                'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
        w = world(h, endorsements=_named_ends(['Zenith General Contractors Inc', 'Mesa Health District']),
                  policies=pols, party_name=f'Expiring {days} Electric Inc')
        _waive(h, w, 'WRONG_PARTY', w['docs']['waiver_ok'])
        ev = h.state(w['asg']['id'])
        assert ev['state'] == 'meets_accepted' and ev['label'] == (
            'Meets requirements except contractor-accepted variances (documents reviewed), '
            f'expiring within {n} days'), ev


def test_n10_a_certificate_for_another_company_is_never_waivable(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Different Company LLC',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'named_insured': 'Different Company LLC',
                         'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    _waive(h, w, 'WRONG_INSURED', w['docs']['waiver_ok'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_INSURED'] and ev['accepted'] == [], ev


# ── N5: the watcher tells STALE from DISAGREE ──────────────────────────────
def test_n5_watch_reports_a_review_made_stale_by_an_archived_document_as_stale(h, dirs):
    w = world(h)
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    h.ok(h.post(f'/document/{w["docs"]["e_ongoing"]["id"]}/archive'))
    v, rows, summary = _watch(dirs)
    assert [r['watch'] for r in rows] == ['STALE'] and v == 'CANNOT-TELL', (v, [r['reasons'] for r in rows])
    assert rows[0]['reasons'][0].startswith('STALE') and 'stale' in summary


def test_n1_unexplained_value_has_no_writer(h):
    """A value no logged write explains (a restore, a hand edit of the database) is vouched for by nobody."""
    w = world(h)
    assert h.state(w['asg']['id'])['state'] == 'meets'
    store = h.app.extensions['biz']['sandbox:josh']
    with store._tx() as conn:
        conn.execute("UPDATE records SET data = json_set(data, '$.trade_category', 'steel erection') WHERE id = ?",
                     (w['asg']['id'],))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_assignment.trade_category'], ev


# ── host ruling (a): WRONG_PROJECT is an exact normalised match too ─────────
def _longer_project_ends():
    named = {'scheduled_names': ['Acme Builders LLC', 'Mesa Health District'],
             'scheduled_projects': ['Mesa Clinic Expansion Phase 2'], 'blanket': False, 'manuscript': False}
    return {'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named},
            'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed',
                             **named},
            'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named}}


def test_a_a_longer_project_name_containing_this_project_is_another_project(h):
    w = world(h, endorsements=_longer_project_ends())
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_PROJECT'], ev


def test_watch_a_fails_a_meets_on_a_longer_project_name(h, dirs):
    w = world(h, endorsements=_longer_project_ends())
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for r in rows[0]['reasons']), rows
