"""Rules 1.5.0 regression set: gate-4 re-check #5 (docs/jambot/contractors-choice-claimcheck5-20261117.md) under
host rulings E, F, P and C. Each case is written to FAIL on 3cd3d6b (rules 1.4.0) and pass here; positive controls
say so in their name. The verbatim re-checker probes are tests/test_claimcheck5_probes{,_b}.py. Fake data only.

E  a waivable code never short-circuits: every check runs, the verdict aggregates ALL codes, a waiver accepts
   exactly the codes it names; the watcher runs every check too.
F  the agent only drafts requirement sets; approving and re-pinning are staff-surface actions; the approval page
   shows the full diff; a built-in safety floor (ISO category, never an empty form list) no set can loosen;
   the watcher checks the same floor.
P  archive / unarchive is a provenance event: stamped with who and where; off the staff surface -> Can't tell
   until staff vouch again on the staff surface (ruling d).
C  the two-person rule fails closed: missing / archived / unvouched company or producer_is_cca -> Can't tell;
   producer_is_cca is staff-surface written; two distinct staff confirmers, both on the staff surface.
"""
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, DOC_FIELDS, END_FIELDS, M, POLICY_FIELDS, SBX, SBX_KEY, SRC, STAFF_1, STAFF_2, STAFF_ORIGIN, TODAY, _ends,
    codes, confirm, dirs, h, iso, pdf, world,
)
from tests.test_biz_crt_tools import _db, _forge_meets, _watch
from tests.test_biz_crt_claimcheck4 import _completed

ROOT = Path(__file__).resolve().parents[1]
MEETS = ('meets', 'meets_accepted', 'expiring_30', 'expiring_15')


def _canvas(h):
    """A canvas page's bridged Clerk JWT, replayed by the agent from its own HTTP client on the tenant origin."""
    a = h.app.test_client()
    hdr = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX}

    class C:
        def post(self, path, body=None):
            return a.post(B + path, json=body if body is not None else {}, headers=hdr)

        def patch(self, path, body, version):
            return a.patch(B + path, json=body, headers={**hdr, 'If-Match': str(version)})
    return C()


def _waive(h, w, code, days=60):
    h.staff()
    return h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': code,
                               'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                               'acceptance_document_id': w['docs']['waiver_ok']['id'],
                               'recorded_by': 'clerk:' + STAFF_1, 'reason': 'accepted in writing',
                               'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=days))})


def _review(h, w):
    h.staff()
    return h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']


def _agent_patch(h, coll, rid, data):
    h.agent()
    cur = h.ok(h.get(f'/{coll}/{rid}'))
    r = h.patch(f'/{coll}/{rid}', {'data': data}, cur['version'])
    h.staff()
    return r


def _staff_doc(h, w, tag='staff-filed'):
    """Staff upload and file an endorsement paper themselves on the staff surface (identity person-written)."""
    h.staff()
    up = h.upload(pdf(tag), 'e2.pdf')
    return h.mk('document', {'title': tag, 'file_url': up['url'], 'kind': 'endorsement',
                             'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']})


def _point_completed_at(h, w, doc):
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_completed"]["id"]}'))
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'document_id': doc['id']}}, e['version']))
    confirm(h, 'crt_endorsement', e, END_FIELDS, doc['id'])


# ═════════════════════════ E: a waivable code never short-circuits ═════════════════════════
def test_e_edition_and_wrong_project_are_both_reported(h):
    w = world(h, endorsements=_completed(edition='12 19', scheduled_projects=['Tempe Warehouse Retrofit']),
              party_name='E5a Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and set(codes(ev)) == {'WRONG_EDITION:ai_completed', 'WRONG_PROJECT'}, ev


def test_e_wrong_party_does_not_hide_wrong_project(h):
    w = world(h, endorsements=_completed(scheduled_names=['Zenith General Contractors Inc'],
                                         scheduled_projects=['Tempe Warehouse Retrofit']), party_name='E5b Inc')
    assert set(codes(h.state(w['asg']['id']))) == {'WRONG_PARTY', 'WRONG_PROJECT'}


def test_e_pre2004_does_not_hide_the_edition_or_the_project(h):
    w = world(h, endorsements=_completed(edition='11 85', scheduled_projects=['Tempe Warehouse Retrofit']),
              party_name='E5c Inc')
    assert set(codes(h.state(w['asg']['id']))) == {'WRONG_FORM:ai_completed', 'WRONG_EDITION:ai_completed',
                                                    'WRONG_PROJECT'}


def test_e_a_waiver_of_the_edition_leaves_the_project_and_the_party(h):
    w = world(h, endorsements=_completed(edition='12 19', scheduled_projects=['Tempe Warehouse Retrofit'],
                                         scheduled_names=['Zenith General Contractors Inc']), party_name='E5d Inc')
    _waive(h, w, 'WRONG_EDITION:ai_completed')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and set(codes(ev)) == {'WRONG_PARTY', 'WRONG_PROJECT'}, ev
    assert [a['code'] for a in ev['accepted']] == ['WRONG_EDITION:ai_completed']
    assert _review(h, w)['state'] == 'variance'


def test_e_a_waiver_never_clears_a_cant_tell_on_the_same_endorsement(h):
    w = world(h, endorsements=_completed(edition='12 19'), party_name='E5e Inc',
              project={'substantial_completion': iso(TODAY - timedelta(days=30))})
    assert _agent_patch(h, 'crt_project', w['project']['id'],
                        {'contract_ai_parties': ['Mesa Health District', 'Anyone LLC']}).status_code == 200
    _waive(h, w, 'WRONG_EDITION:ai_completed')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_project.contract_ai_parties' in codes(ev), ev


def test_e_positive_control_every_code_waived_reads_meets_accepted(h):
    w = world(h, endorsements=_completed(edition='12 19', scheduled_projects=['Tempe Warehouse Retrofit']),
              party_name='E5f Inc')
    _waive(h, w, 'WRONG_EDITION:ai_completed')
    _waive(h, w, 'WRONG_PROJECT')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets_accepted' and codes(ev) == [], ev
    assert sorted(a['code'] for a in ev['accepted']) == ['WRONG_EDITION:ai_completed', 'WRONG_PROJECT']


def test_e_an_unchecked_second_endorsement_on_a_met_item_is_cant_tell(h):
    w = world(h, party_name='E5g Inc')
    assert h.state(w['asg']['id'])['state'] == 'meets'
    h.agent()
    h.mk('crt_endorsement', {'crt_account_id': w['account']['id'], 'crt_policy_id': w['pol']['gl']['id'],
                             'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13',
                             'document_id': w['docs']['e_other']['id'], 'scheduled_names': ['Acme Builders LLC'],
                             'scheduled_projects': ['Mesa Clinic Expansion'], 'blanket': False, 'manuscript': False})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and ev['items']['ai_completed'] == 'UNCONFIRMED', ev


def test_e_positive_control_another_projects_confirmed_endorsement_does_not_spoil_a_met_item(h):
    w = world(h, party_name='E5h Inc')
    h.agent()
    e2 = h.mk('crt_endorsement', {'crt_account_id': w['account']['id'], 'crt_policy_id': w['pol']['gl']['id'],
                                  'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13',
                                  'document_id': w['docs']['e_other']['id'], 'scheduled_names': ['Acme Builders LLC',
                                                                                                'Mesa Health District'],
                                  'scheduled_projects': ['Tempe Warehouse Retrofit'], 'blanket': False,
                                  'manuscript': False})
    confirm(h, 'crt_endorsement', e2, END_FIELDS, w['docs']['e_other']['id'])
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_e_an_unvouched_extra_link_for_the_pair_is_cant_tell(h):
    w = world(h, party_name='E5i Inc')
    h.agent()
    h.mk('crt_account_party', {'crt_account_id': w['account']['id'], 'party_contact_id': w['party']['id'],
                               'status': 'active'})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and any(c.startswith('UNCONFIRMED:crt_account_party') for c in codes(ev)), ev


def test_watch_e_an_edition_waiver_never_backs_a_wrong_project(h, dirs):
    w = world(h, endorsements=_completed(edition='12 19', scheduled_projects=['Tempe Warehouse Retrofit']),
              party_name='E5w1 Inc')
    _waive(h, w, 'WRONG_EDITION:ai_completed')
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for r in rows[0]['reasons']), rows


def test_watch_e_a_party_waiver_never_backs_a_wrong_project(h, dirs):
    w = world(h, endorsements=_completed(scheduled_names=['Zenith General Contractors Inc'],
                                         scheduled_projects=['Tempe Warehouse Retrofit']), party_name='E5w2 Inc')
    _waive(h, w, 'WRONG_PARTY')
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for r in rows[0]['reasons']), rows


def test_watch_e_an_unconfirmed_contract_party_list_is_not_backed(h, dirs):
    """The agent shrinks the contract's AI list to names the endorsement carries: no naming miss, but nobody
    confirmed the list. The 1.4.0 watcher read the raw list and passed."""
    w = world(h, party_name='E5w3 Inc', project={'substantial_completion': iso(TODAY - timedelta(days=30))},
              endorsements=_completed(scheduled_names=['Acme Builders LLC']))
    assert _agent_patch(h, 'crt_project', w['project']['id'], {'contract_ai_parties': ['Acme Builders LLC']}
                        ).status_code == 200
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL', rows


def test_watch_e_an_unchecked_extra_endorsement_is_not_backed(h, dirs):
    w = world(h, party_name='E5w4 Inc')
    h.agent()
    h.mk('crt_endorsement', {'crt_account_id': w['account']['id'], 'crt_policy_id': w['pol']['gl']['id'],
                             'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13',
                             'document_id': w['docs']['e_other']['id'], 'scheduled_names': ['Acme Builders LLC'],
                             'scheduled_projects': ['Mesa Clinic Expansion'], 'blanket': False, 'manuscript': False})
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RE') for r in rows[0]['reasons']), rows


def test_watch_e_an_unvouched_extra_link_is_not_backed(h, dirs):
    w = world(h, party_name='E5w5 Inc')
    h.agent()
    h.mk('crt_account_party', {'crt_account_id': w['account']['id'], 'party_contact_id': w['party']['id'],
                               'status': 'active'})
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RE') for r in rows[0]['reasons']), rows


# ═════════════════════════ F: requirement sets ═════════════════════════
@pytest.mark.parametrize('form', ['CG 20 33', 'CG 20 38', 'CG 20 26', 'CG 20 11', 'CG 20 01', 'CG 20 10'])
def test_f_the_table_never_moves_an_iso_form_out_of_its_category(form):
    from services.biz.packs import risk_transfer as rt
    with pytest.raises(rt.SchemaError) as ex:
        rt.validate_form_functions({form: 'ai_completed'})
    assert ex.value.status == 422


def test_f_agent_revision_loosening_completed_ops_is_refused(h):
    w = world(h, endorsements=_completed(form='CG 20 33'), party_name='F5a Inc')
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [{k: v for k, v in it.items() if k != 'forms'} if it['id'] == 'ai_completed' else it
             for it in s['data']['items']]
    h.agent()
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise',
               {'items': items, 'form_functions': dict(s['data']['form_functions'], **{'CG 20 33': 'ai_completed'})})
    assert r.status_code == 422 and r.get_json()['code'] in ('iso_form_floor', 'forms_required'), r.get_json()
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items})
    assert r.status_code == 422 and r.get_json()['code'] == 'forms_required', r.get_json()
    listed = [dict(it, forms=['CG 20 37', 'CG 20 33']) if it['id'] == 'ai_completed' else it for it in s['data']['items']]
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': listed})
    assert r.status_code == 422 and r.get_json()['code'] == 'safety_floor', r.get_json()


def test_f_a_completed_form_must_be_cg2037_or_listed_as_completed_in_the_table(h):
    w = world(h, party_name='F5b Inc')
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    listed = [dict(it, forms=['CG 20 37', 'ACME CO 77']) if it['id'] == 'ai_completed' else it
              for it in s['data']['items']]
    h.agent()
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': listed})
    assert r.status_code == 422 and r.get_json()['code'] == 'safety_floor'
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise',               # positive control: listed in the table
               {'items': listed, 'form_functions': dict(s['data']['form_functions'], **{'ACME CO 77': 'ai_completed'})})
    assert r.status_code == 201 and r.get_json()['record']['data'].get('status') is None


def test_f_generic_create_of_a_set_with_an_empty_form_list_is_refused(h):
    from services.biz.packs import risk_transfer as rt
    tpl = next(t for t in rt.load_templates()['templates'] if t['key'] == 'mep')
    items = [dict(it, forms=[]) if it.get('kind') == 'endorsement' else it for it in tpl['items']]
    h.agent()
    r = h.post('/crt_requirement_set', {'data': {'name': 'x', 'trade_category': 'mep', 'items': items}})
    assert r.status_code == 422 and r.get_json()['code'] == 'forms_required', r.get_json()


def test_f_agent_never_edits_archives_or_unarchives_a_set(h):
    w = world(h, party_name='F5c Inc')
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.agent()
    r = h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'name': 'renamed'}}, s['version'])
    assert r.status_code == 403 and r.get_json()['code'] == 'set_change_staff_only'
    assert h.post(f'/crt_requirement_set/{s["id"]}/archive').status_code == 403
    h.staff()
    h.ok(h.post(f'/crt_requirement_set/{s["id"]}/archive'))
    h.agent()
    assert h.post(f'/crt_requirement_set/{s["id"]}/unarchive').status_code == 403


def test_f_only_staff_on_the_staff_surface_approve_and_re_pin(h):
    w = world(h, party_name='F5d Inc')
    h.agent()
    new = h.ok(h.post(f'/crt/requirement-sets/{w["set"]["id"]}/revise', {}), 201)['record']
    assert h.post(f'/crt/requirement-sets/{new["id"]}/approve').status_code == 403
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': new['id'],
                                                        'requirement_set_version': new['version']}}, a['version'])
    assert r.status_code == 403
    c = _canvas(h.staff())
    assert c.post(f'/crt/requirement-sets/{new["id"]}/approve').status_code == 403
    assert c.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': new['id'],
                                                           'requirement_set_version': new['version']}},
                   a['version']).status_code == 403


def test_f_the_diff_shows_every_item_and_the_whole_table_and_binds_the_approval(h):
    w = world(h, party_name='F5e Inc')
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [dict(it, edition='12 19') if it['id'] == 'ai_ongoing' else it for it in s['data']['items']]
    h.agent()
    new = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise',
                      {'items': items, 'form_functions': dict(s['data']['form_functions'], **{'ACME 9': 'pnc'})}),
               201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{new["id"]}/diff'))
    assert d['against']['kind'] == 'set' and d['against']['id'] == s['id']
    by = {it['id']: it for it in d['items']}
    assert by['ai_ongoing']['change'] == 'changed' and by['ai_ongoing']['changed_fields'] == ['edition']
    assert by['ai_completed']['now']['forms'] == ['CG 20 37'] and by['ai_completed']['change'] == 'same'
    assert set(by) == {it['id'] for it in s['data']['items']}                       # EVERY item, same or not
    assert d['form_functions']['added'] == {'ACME 9': 'pnc'} and 'CG 20 37' in d['form_functions']['now']
    assert d['floor_violations'] == []
    r = h.post(f'/crt/requirement-sets/{new["id"]}/approve', {'diff_sha256': '0' * 64})
    assert r.status_code == 409 and r.get_json()['code'] == 'diff_changed'
    ok = h.ok(h.post(f'/crt/requirement-sets/{new["id"]}/approve', {'diff_sha256': d['sha256']}))
    assert ok['confirmation']['data']['approved_diff_sha256'] == d['sha256']
    assert ok['confirmation']['data']['on_staff_surface'] is True


def test_f_the_approval_page_renders_the_diff_and_sends_its_hash():
    page = (ROOT / 'default-schemas/packs/risk-transfer/pages/cca-crt-requirements.html').read_text(encoding='utf-8')
    # rules 1.6.0 (ruling M): the item table renders every key of every item (it.rows), not fixed columns; the
    # behaviour is run in tests/js/crt-requirements-diff.test.js
    # rules 1.7.0 (A): each cell goes through shown(), which is val() for every key but `required`
    for needle in ("'/diff'", 'diff_sha256', 'form_functions', '(it.rows || []).map(function (r) {',
                   'shown(r.key, r.was, !!it.was)', 'shown(r.key, r.now, !!it.now)',
                   "if (key !== 'required') return val(v);", 'r.changed ?', 'floor_violations',
                   "box.appendChild(el('h2', {text: 'Form equivalences (which form counts as which coverage)'}));",
                   "keys.map(function (k) {"):
        assert needle in page, needle
    assert 'innerHTML' not in page


def test_f_a_form_the_set_table_lists_is_never_read_as_manuscript(h):
    """The set's own table (not only the built-in ISO list) decides what a form number is (R3 + R4, kept by F)."""
    w = world(h, endorsements=_ends(form='ACME 9', manuscript=True), party_name='F5g Inc')
    assert h.state(w['asg']['id'])['state'] == 'meets'                  # control: an unlisted manuscript, ongoing ops
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.agent()
    new = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise',
                      {'form_functions': dict(s['data']['form_functions'], **{'ACME 9': 'ai_ongoing'})}), 201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{new["id"]}/diff'))
    new = h.ok(h.post(f'/crt/requirement-sets/{new["id"]}/approve', {'diff_sha256': d['sha256']}))['record']
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': new['id'],
                                                         'requirement_set_version': new['version']}}, a['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev


def test_f_a_stored_set_below_the_floor_reads_cant_tell(h, dirs):
    w = world(h, party_name='F5f Inc')
    db = Path(_db(dirs)) / 'business.db'
    con = sqlite3.connect(db)
    data = json.loads(con.execute('SELECT data FROM records WHERE id = ?', (w['set']['id'],)).fetchone()[0])
    data['form_functions']['CG 20 33'] = 'ai_completed'                    # written around the API
    con.execute('UPDATE records SET data = ? WHERE id = ?', (json.dumps(data), w['set']['id']))
    con.commit()
    con.close()
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.items' in codes(ev), ev
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RF') for r in rows[0]['reasons']), rows


# ═════════════════════════ P: archive / unarchive is a provenance event ═════════════════════════
def test_p_toggles_are_stamped_with_who_and_where(h):
    w = world(h, party_name='P5a Inc')
    d = _staff_doc(h, w)
    h.ok(h.post(f'/document/{d["id"]}/archive'))
    h.agent()
    h.ok(h.post(f'/document/{d["id"]}/unarchive'))
    h.staff()
    ev = [e for e in h.ok(h.get(f'/document/{d["id"]}/timeline'))['events'] if e['type'] in ('archived', 'unarchived')]
    assert ev[0]['by'] == 'clerk:' + STAFF_1 and ev[0]['diff'] == {'staff_surface': ['archived_at']}
    assert ev[1]['by'].startswith('agent:') and 'staff_surface' not in (ev[1]['diff'] or {})


@pytest.mark.parametrize('who', ['agent_key', 'canvas_jwt'])
def test_p_an_off_surface_unarchive_of_a_staff_archived_paper_is_cant_tell(h, who):
    w = world(h, party_name=f'P5b {who} Inc')
    d = _staff_doc(h, w)
    _point_completed_at(h, w, d)
    assert h.state(w['asg']['id'])['state'] == 'meets'
    h.ok(h.post(f'/document/{d["id"]}/archive'))
    if who == 'agent_key':
        h.agent()
        assert h.post(f'/document/{d["id"]}/unarchive').status_code == 200
    else:
        assert _canvas(h.staff()).post(f'/document/{d["id"]}/unarchive').status_code == 200
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:document.archived_at' in codes(ev), ev
    # staff vouch for it again on the staff surface (a confirmation at its current version): counts again
    cur = h.ok(h.get(f'/document/{d["id"]}'))
    confirm(h, 'document', cur, DOC_FIELDS)
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_p_positive_control_staff_archive_and_unarchive_on_the_surface(h):
    w = world(h, party_name='P5c Inc')
    d = _staff_doc(h, w)
    _point_completed_at(h, w, d)
    h.ok(h.post(f'/document/{d["id"]}/archive'))
    assert h.state(w['asg']['id'])['state'] != 'meets'
    h.ok(h.post(f'/document/{d["id"]}/unarchive'))
    assert h.state(w['asg']['id'])['state'] == 'meets'


@pytest.mark.parametrize('coll,key', [('crt_policy', 'umbrella'), ('crt_endorsement', 'pnc'), ('document', 'cert'),
                                      ('crt_project', None), ('contact', 'party'), ('crt_assignment', None),
                                      ('crt_account_party', None)])
def test_p_an_agent_archive_of_a_record_the_verdict_reads_is_cant_tell_not_a_skipped_check(h, coll, key):
    w = world(h, party_name=f'P5d {coll} Inc')
    rid = {'crt_policy': lambda: w['pol'][key]['id'], 'crt_endorsement': lambda: w['end'][key]['id'],
           'document': lambda: w['docs'][key]['id'], 'crt_project': lambda: w['project']['id'],
           'contact': lambda: w['party']['id'], 'crt_assignment': lambda: w['asg']['id'],
           'crt_account_party': lambda: w['link']['id']}[coll]()
    h.agent()
    assert h.post(f'/{coll}/{rid}/archive').status_code == 200
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell', ev


def test_watch_p_an_agent_unarchive_is_not_backed(h, dirs):
    w = world(h, party_name='P5w Inc')
    d = _staff_doc(h, w)
    _point_completed_at(h, w, d)
    h.ok(h.post(f'/document/{d["id"]}/archive'))
    h.agent()
    h.ok(h.post(f'/document/{d["id"]}/unarchive'))
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('RP') for r in rows[0]['reasons']), rows


# ═════════════════════════ C: the two-person rule fails closed ═════════════════════════
def _cca_pols():
    return {
        'gl': {'line': 'gl', 'policy_number': 'GL-100', 'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M},
               'producer_name': 'Contractors Choice Agency Sandbox'},
        'umbrella': {'line': 'umbrella', 'policy_number': 'UM-200', 'limits': {'each_occurrence': 5 * M},
                     'producer_name': 'Contractors Choice Agency Sandbox'},
    }


@pytest.mark.parametrize('who', ['agent_key', 'canvas_jwt', 'staff'])
def test_c_an_archived_company_is_cant_tell_even_with_two_confirmers(h, who):
    w = world(h, policies=_cca_pols(), second_confirmer=True, party_name=f'C5a {who} Inc')
    assert h.state(w['asg']['id'])['state'] == 'meets'                       # positive control: two people
    if who == 'agent_key':
        h.agent()
        r = h.post(f'/company/{w["company"]["id"]}/archive')
    elif who == 'canvas_jwt':
        r = _canvas(h.staff()).post(f'/company/{w["company"]["id"]}/archive')
    else:
        h.staff()
        r = h.post(f'/company/{w["company"]["id"]}/archive')
    assert r.status_code == 200
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:company.name' in codes(ev), ev


def test_c_a_company_name_written_off_the_surface_is_cant_tell(h):
    w = world(h, party_name='C5b Inc')
    c = h.ok(h.get(f'/company/{w["company"]["id"]}'))
    r = _canvas(h.staff()).patch(f'/company/{c["id"]}', {'data': {'name': 'Some Other Agency'}}, c['version'])
    assert r.status_code == 200
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:company.name' in codes(ev), ev
    # staff confirm the agency record on the staff surface: vouched again
    confirm(h, 'company', h.ok(h.get(f'/company/{c["id"]}')), ['name'])
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_c_producer_is_cca_is_staff_only_and_missing_is_cant_tell(h):
    w = world(h, party_name='C5c Inc')
    r = _agent_patch(h, 'crt_policy', w['pol']['gl']['id'], {'producer_is_cca': False})
    assert r.status_code in (200, 403)
    h.agent()
    r = h.post('/crt_policy', {'data': {'crt_account_id': w['account']['id'], 'party_contact_id': w['party']['id'],
                                        'line': 'gl', 'producer_is_cca': False}})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field', r.get_json()
    # a policy whose producer staff never stated: Can't tell
    w2 = world(h, party_name='C5c2 Inc')
    store = h.app.extensions['biz']['sandbox:josh']
    p = store.get('crt_policy', w2['pol']['gl']['id'])
    store.update('crt_policy', p['id'], {}, clear=['producer_is_cca'], expected_version=p['version'],
                 by='clerk:' + STAFF_1, agent=False, surface=True)
    p = store.get('crt_policy', p['id'])
    confirm(h, 'crt_policy', p, [f for f in POLICY_FIELDS if f != 'producer_is_cca'], w2['docs']['cert']['id'])
    ev = h.state(w2['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.producer_is_cca' in codes(ev), ev


def test_c_producer_is_cca_written_by_a_person_off_the_surface_is_cant_tell(h):
    """A person's write that the store did not stamp as made on the staff surface vouches for nothing."""
    w = world(h, party_name='C5c3 Inc')
    store = h.app.extensions['biz']['sandbox:josh']
    p = store.get('crt_policy', w['pol']['gl']['id'])
    store.update('crt_policy', p['id'], {'producer_is_cca': True}, expected_version=p['version'],
                 by='clerk:' + STAFF_1, agent=False, surface=False)
    p = store.get('crt_policy', p['id'])
    store.update('crt_policy', p['id'], {'producer_is_cca': False}, expected_version=p['version'],
                 by='clerk:' + STAFF_1, agent=False, surface=False)
    p = store.get('crt_policy', p['id'])
    confirm(h, 'crt_policy', p, POLICY_FIELDS, w['docs']['cert']['id'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.producer_is_cca' in codes(ev), ev


def test_c_agency_produced_needs_two_people_and_one_is_cant_tell(h):
    w = world(h, producer_is_cca=True, party_name='C5d Inc')
    assert h.state(w['asg']['id'])['state'] == 'cant_tell'
    w2 = world(h, producer_is_cca=True, second_confirmer=True, party_name='C5d2 Inc')
    assert h.state(w2['asg']['id'])['state'] == 'meets'


def test_c_a_confirmation_not_made_on_the_staff_surface_never_counts(h, dirs):
    """A confirmation written around the route (no staff-surface stamp) is not a person's staff-surface check."""
    w = world(h, policies=_cca_pols(), party_name='C5e Inc')
    assert h.state(w['asg']['id'])['state'] == 'cant_tell'
    store = h.app.extensions['biz']['sandbox:josh']
    # a second person's confirmations of every policy AND endorsement, written around the route (no surface)
    for coll, recs, fields in (('crt_policy', w['pol'].values(), POLICY_FIELDS),
                               ('crt_endorsement', w['end'].values(), END_FIELDS)):
        for p in recs:
            cur = store.get(coll, p['id'])
            did = cur['data'].get('document_id') or w['docs']['cert']['id']
            sha = store.get('document', did)['data']['file_url'].split(':', 1)[1]
            store.create('crt_confirmation', {'record_collection': coll, 'record_id': p['id'],
                                              'record_version': cur['version'], 'fields': fields,
                                              'source_document_id': did, 'source_sha256': sha,
                                              'confirmed_by': 'clerk:' + STAFF_2},
                         by='clerk:' + STAFF_2, via_pack=True)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell', ev
    # the same confirmations made on the staff surface: two people, so it meets (proves the forged ones were whole)
    w2 = world(h, policies=_cca_pols(), second_confirmer=True, party_name='C5e2 Inc')
    assert h.state(w2['asg']['id'])['state'] == 'meets'
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL', rows


@pytest.mark.parametrize('how', ['company_archived', 'producer_unset', 'one_confirmer'])
def test_watch_c_fails_closed(h, dirs, how):
    if how == 'one_confirmer':
        w = world(h, producer_is_cca=True, party_name='C5w1 Inc')
    else:
        w = world(h, policies=_cca_pols(), second_confirmer=True, party_name=f'C5w {how} Inc')
    if how == 'company_archived':
        h.agent()
        h.ok(h.post(f'/company/{w["company"]["id"]}/archive'))
    if how == 'producer_unset':
        store = h.app.extensions['biz']['sandbox:josh']
        for p in w['pol'].values():
            cur = store.get('crt_policy', p['id'])
            store.update('crt_policy', p['id'], {}, clear=['producer_is_cca'], expected_version=cur['version'],
                         by='clerk:' + STAFF_1, agent=False, surface=True)
            cur = store.get('crt_policy', p['id'])
            for u in (STAFF_1, STAFF_2):
                confirm(h, 'crt_policy', cur, [f for f in POLICY_FIELDS if f != 'producer_is_cca'],
                        w['docs']['cert']['id'], user=u)
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith(('RC', 'R3')) for r in rows[0]['reasons']), rows


def test_watch_positive_control_two_confirmers_on_the_agencys_own_policy_pass(h, dirs):
    w = world(h, policies=_cca_pols(), second_confirmer=True, party_name='C5wp Inc')
    assert _review(h, w)['state'] == 'meets'
    v, rows, _ = _watch(dirs)
    assert v == 'PASS', rows


# ═════════════════════════ gate-3b/5 cheap items done now ═════════════════════════
def test_l1_every_staff_page_carries_a_sign_out_control(h):
    js = (ROOT / 'jb/v1/jb.js').read_text(encoding='utf-8')
    assert "'signout'" in js and 'staffSignOut' in js
    for needle in ("b.id = 'jb-staff-signout';", "b.textContent = 'Sign out';", 'global.document.body.appendChild(b);',
                   "jb.staffSignOut(); });"):
        assert needle in js, needle                    # behaviour: tests/js/jb-pack.test.js (sign-out case)
    for p in (ROOT / 'default-schemas/packs/risk-transfer/pages').glob('*.html'):
        assert '<script src="/jb/v1/jb.js"></script>' in p.read_text(encoding='utf-8'), p.name
    h.signin(STAFF_1)
    r = h.c.post(f'{B}/crt/staff/signout', base_url=STAFF_ORIGIN,
                 headers={'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin'})
    assert r.status_code == 200 and '__Host-jb_staff=;' in r.headers.get('Set-Cookie', '')


def test_rules_version_is_1_5_0():
    from services.biz.packs import risk_transfer as rt
    assert rt.RULE_VERSION == 'crt-rules-1.7.0'          # rules 1.6.0 / 1.7.0 (re-checks #6, #7) keep every 1.5.0 rule
