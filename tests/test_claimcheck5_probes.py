"""Gate-4 re-check #5 probes (non-author, opus), written from the spec (plan 4.4, rules 1.4.0 notes) and the
threat model (a prompt-injected agent working for a sub). NOT derived from the 1.4.0 fixes.

HOLE probes assert the BAD outcome: PASSED means the hole is real. CONTROL probes assert the good outcome.
"""
import json
import re
import time
from datetime import timedelta

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, CANVAS_ORIGIN, DOC_FIELDS, END_FIELDS, M, POLICY_FIELDS, SBX, SBX_KEY, SRC, STAFF_1, STAFF_2, STAFF_ORIGIN,
    TODAY, _ends, codes, confirm, dirs, h, iso, pdf, world,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed

NAV = {'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Site': 'none'}


def _waive(h, w, code):
    h.staff()
    return h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': code,
                               'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                               'acceptance_document_id': w['docs']['waiver_ok']['id'],
                               'recorded_by': 'clerk:' + STAFF_1, 'reason': 'newer ISO edition, same wording',
                               'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=60))})


# ───────────── E: WRONG_EDITION (new in 1.4.0) short-circuits the party / project / blanket checks ─────────────
def test_E0_control_right_edition_wrong_project_is_wrong_project(h):
    w = world(h, endorsements=_completed(scheduled_projects=['Tempe Warehouse Retrofit']), party_name='E Zero Inc')
    ev = h.state(w['asg']['id'])
    assert codes(ev) == ['WRONG_PROJECT'], ev


def test_E1_HOLE_another_edition_hides_wrong_project(h):
    """The completed-ops endorsement names ANOTHER project and is on the 12 19 edition: only WRONG_EDITION shows."""
    w = world(h, endorsements=_completed(edition='12 19', scheduled_projects=['Tempe Warehouse Retrofit']),
              party_name='E One Inc')
    ev = h.state(w['asg']['id'])
    assert codes(ev) == ['WRONG_EDITION:ai_completed'], ev           # WRONG_PROJECT never reported


def test_E2_HOLE_contractor_accepts_the_edition_and_the_wrong_project_reads_meets_accepted(h, dirs):
    w = world(h, endorsements=_completed(edition='12 19', scheduled_projects=['Tempe Warehouse Retrofit'],
                                         scheduled_names=['Zenith General Contractors Inc']),
              party_name='E Two Inc')
    _waive(h, w, 'WRONG_EDITION:ai_completed')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets_accepted', ev
    assert [a['code'] for a in ev['accepted']] == ['WRONG_EDITION:ai_completed']
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']
    assert rv['state'] == 'meets_accepted'
    v, rows, _ = _watch(dirs)
    print('WATCH E2', v, json.dumps(rows)[:600])


def test_E3_HOLE_after_completion_an_unconfirmed_contract_party_list_is_hidden_too(h, dirs):
    """Ongoing ops are N/A after substantial completion, so the completed-ops endorsement is the only AI check.
    The agent rewrites the contract's AI party list (unconfirmed). Can't tell is never waivable -- but it is
    never computed when the edition differs."""
    w = world(h, endorsements=_completed(edition='12 19'), party_name='E Three Inc',
              project={'substantial_completion': iso(TODAY - timedelta(days=30))})
    h.agent()
    p = h.ok(h.get(f'/crt_project/{w["project"]["id"]}'))
    r = h.patch(f'/crt_project/{p["id"]}', {'data': {'contract_ai_parties': ['Mesa Health District', 'Anyone LLC']}},
                p['version'])
    print('agent patch crt_project', r.status_code, (r.get_json() or {}).get('code'))
    ev0 = h.state(w['asg']['id'])
    print('E3 before waiver', ev0['state'], codes(ev0))
    _waive(h, w, 'WRONG_EDITION:ai_completed')
    ev = h.state(w['asg']['id'])
    assert r.status_code == 200
    assert ev['state'] == 'meets_accepted', ev


def test_E3c_control_right_edition_unconfirmed_party_list_is_cant_tell(h):
    w = world(h, endorsements=_completed(), party_name='E Three C Inc',
              project={'substantial_completion': iso(TODAY - timedelta(days=30))})
    h.agent()
    p = h.ok(h.get(f'/crt_project/{w["project"]["id"]}'))
    h.ok(h.patch(f'/crt_project/{p["id"]}', {'data': {'contract_ai_parties': ['Mesa Health District', 'Anyone LLC']}},
                 p['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_project.contract_ai_parties' in codes(ev), ev


# ───────────── F: the set's form-equivalence table is approved blind ─────────────
def test_F0_requirements_page_never_shows_the_equivalence_table():
    from pathlib import Path
    pages = Path(__file__).resolve().parents[1] / 'default-schemas' / 'packs' / 'risk-transfer' / 'pages'
    hits = [p.name for p in pages.glob('*.html') if 'form_functions' in p.read_text(encoding='utf-8')]
    assert hits == []                                    # HOLE-ish: no staff page renders or edits it


def test_F1_HOLE_agent_revised_table_makes_cg2033_count_for_completed_ops(h, dirs):
    """The sub's 'completed ops' endorsement is a CG 20 33 (ongoing only, guide p.32). The agent revises the set
    (agent key): drops the completed item's form list, maps CG 20 33 -> ai_completed in form_functions. Staff
    approve the revision on the requirements page (which shows no table) and re-pin the assignment."""
    w = world(h, endorsements=_completed(form='CG 20 33'), party_name='F One Inc')
    ev0 = h.state(w['asg']['id'])
    assert 'MISSING:ai_completed' in codes(ev0), ev0                # control: CG 20 33 is not completed ops
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [dict({k: v for k, v in it.items() if k != 'forms'}) if it['id'] == 'ai_completed' else it
             for it in s['data']['items']]
    ff = dict(s['data']['form_functions'], **{'CG 20 33': 'ai_completed'})
    h.agent()
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items, 'form_functions': ff})
    assert r.status_code == 201, r.get_json()
    new = r.get_json()['record']
    h.staff()
    new = h.ok(h.post(f'/crt/requirement-sets/{new["id"]}/approve'))['record']
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': new['id'],
                                                         'requirement_set_version': new['version']}}, a['version']))
    ev = h.state(w['asg']['id'])
    print('F1', ev['state'], codes(ev), ev['items'])
    assert ev['state'] == 'meets' and ev['items']['ai_completed'] == 'OK', ev
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    v, rows, _ = _watch(dirs)
    print('WATCH F1', v, json.dumps(rows)[:600])


def test_F2_table_accepts_any_ongoing_iso_form_as_completed_ops():
    from services.biz.packs import risk_transfer as rt
    for form in ('CG 20 33', 'CG 20 38', 'CG 20 26', 'CG 20 11', 'CG 20 01'):
        rt.validate_form_functions({form: 'ai_completed'})          # only CG 20 10 is refused (R3)


# ───────────── P: provenance has no stamp for archived_at ─────────────
def _staff_owned_endorsement_doc(h, w):
    """Staff upload and file a NEW endorsement paper themselves on the staff surface, and point the completed-ops
    endorsement at it (person-written identity fields: counted by N1 whatever the version)."""
    h.staff()
    up = h.upload(pdf('staff-filed-completed-ops'), 'e2.pdf')
    d2 = h.mk('document', {'title': 'e2', 'file_url': up['url'], 'kind': 'endorsement',
                           'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']})
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_completed"]["id"]}'))
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'document_id': d2['id']}}, e['version']))
    confirm(h, 'crt_endorsement', e, END_FIELDS, d2['id'])
    return d2


@pytest.mark.parametrize('who', ['agent_key', 'canvas_jwt'])
def test_P1_HOLE_staff_archived_paper_is_restored_by_the_agent_and_counts_again(h, who):
    w = world(h, party_name=f'P One {who} Inc')
    d2 = _staff_owned_endorsement_doc(h, w)
    assert h.state(w['asg']['id'])['state'] == 'meets'                         # control
    h.staff()
    h.ok(h.post(f'/document/{d2["id"]}/archive'))                             # staff reject that paper
    ev1 = h.state(w['asg']['id'])
    assert ev1['state'] != 'meets', ev1                                        # control
    if who == 'agent_key':
        h.agent()
        r = h.post(f'/document/{d2["id"]}/unarchive')
    else:
        a = h.app.test_client()                                                # tenant origin, bridged JWT
        r = a.post(f'{B}/document/{d2["id"]}/unarchive', json={},
                   headers={'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX})
    print('P1 unarchive', who, r.status_code, (r.get_json() or {}).get('code'))
    ev2 = h.state(w['asg']['id'])
    print('P1 after', ev1['state'], codes(ev1), '->', ev2['state'], codes(ev2))
    assert r.status_code == 200 and ev2['state'] == 'meets', ev2


# ───────────── C: the agency two-person rule rests on a company record the agent can archive ─────────────
def _cca_policies():
    return {
        'gl': {'line': 'gl', 'policy_number': 'GL-100', 'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M},
               'producer_name': 'Contractors Choice Agency Sandbox'},
        'umbrella': {'line': 'umbrella', 'policy_number': 'UM-200', 'limits': {'each_occurrence': 5 * M},
                     'producer_name': 'Contractors Choice Agency Sandbox'},
    }


def test_C0_control_agency_produced_policy_needs_a_second_person(h):
    w = world(h, policies=_cca_policies(), party_name='C Zero Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell', ev


def test_C1_HOLE_agent_archives_the_company_and_one_person_reaches_meets(h):
    w = world(h, policies=_cca_policies(), party_name='C One Inc')
    h.agent()
    r = h.post(f'/company/{w["company"]["id"]}/archive')
    print('C1 agent archive company', r.status_code, (r.get_json() or {}).get('code'))
    ev = h.state(w['asg']['id'])
    print('C1', ev['state'], codes(ev))
    assert r.status_code == 200 and ev['state'] == 'meets', ev


def test_C2_HOLE_canvas_jwt_archives_the_company(h):
    w = world(h, policies=_cca_policies(), party_name='C Two Inc')
    a = h.app.test_client()
    r = a.post(f'{B}/company/{w["company"]["id"]}/archive', json={},
               headers={'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX})
    ev = h.state(w['asg']['id'])
    print('C2', r.status_code, ev['state'], codes(ev))
    assert r.status_code == 200 and ev['state'] == 'meets', ev


# ───────────── R1: staff origin, from the agent's side ─────────────
def test_R1a_control_staff_cookie_from_another_staff_origin_is_refused(h, monkeypatch):
    h.signin(STAFF_1)
    ck = h.c.get_cookie('__Host-jb_staff', domain=STAFF_ORIGIN.split('//')[1])
    assert ck is not None
    other = 'https://crt-staff-josh.jam-bot.com'
    monkeypatch.setenv('BIZ_STAFF_ORIGIN', other)
    a = h.app.test_client()
    r = a.get(f'{B}/crt_policy', base_url=other, headers={'Cookie': f'__Host-jb_staff={ck.value}', 'X-Biz-Sandbox': SBX})
    assert r.status_code == 401, (r.status_code, r.get_json())


def test_R1b_control_tampered_staff_cookie_names_another_user(h):
    h.signin(STAFF_1)
    ck = h.c.get_cookie('__Host-jb_staff', domain=STAFF_ORIGIN.split('//')[1]).value
    v, exp, rnd, who, mac = ck.split('.')
    forged = '.'.join([v, exp, rnd, ('clerk:' + STAFF_2).encode().hex(), mac])
    a = h.app.test_client()
    r = a.get(f'{B}/crt_policy', base_url=STAFF_ORIGIN, headers={'Cookie': f'__Host-jb_staff={forged}',
                                                                 'X-Biz-Sandbox': SBX})
    assert r.status_code == 401


@pytest.mark.parametrize('host', ['crt-staff-test-dev.jam-bot.com:443', 'CRT-STAFF-TEST-DEV.JAM-BOT.COM',
                                  'crt-staff-test-dev.jam-bot.com.'])
def test_R1c_control_host_spelling_never_grants_a_person_write(h, host):
    """The agent owns its HTTP client: any Host, the bridged JWT, a forged page token. No person-write lands."""
    w = world(h, confirm_all=False, party_name='Host ' + re.sub(r'[^a-z]', '', host.lower())[:8] + ' Inc')
    a = h.app.test_client()
    tok = 'v2.%d.%s.%s' % (int(time.time()) + 3600, 'a' * 24, 'b' * 64)
    pol = w['pol']['gl']
    r = a.post(f'{B}/crt/confirm', json={'record_collection': 'crt_policy', 'record_id': pol['id'], 'fields': ['line'],
                                         'source_document_id': w['docs']['cert']['id']},
               headers={'Host': host, 'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Origin': STAFF_ORIGIN,
                        'Sec-Fetch-Site': 'same-origin', 'X-JB-Staff-Surface': tok})
    print('R1c', host, r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code in (401, 403)


def test_R1d_control_x_forwarded_host_on_the_tenant_origin_gets_no_person_write(h):
    w = world(h, confirm_all=False, party_name='Xfh Inc')
    a = h.app.test_client()
    pol = w['pol']['gl']
    r = a.post(f'{B}/crt/confirm', json={'record_collection': 'crt_policy', 'record_id': pol['id'], 'fields': ['line'],
                                         'source_document_id': w['docs']['cert']['id']},
               headers={'X-Forwarded-Host': STAFF_ORIGIN.split('//')[1], 'Authorization': 'Bearer tok',
                        'X-Biz-Sandbox': SBX, 'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin'})
    print('R1d', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code in (401, 403)


@pytest.mark.parametrize('nxt', ['//evil.example/x', 'https://evil.example/', '/api/biz/v1/crt/staff/..%2F..%2Fx',
                                 '/api/biz/v1/crt/staff/cca-crt-book.html?a=b#x', '\\\\evil.example',
                                 '/api/biz/v1/crt/staff/cca-crt-book.html?u=%2F%2Fevil.example',
                                 'javascript:alert(1)'])
def test_R1e_control_signin_next_stays_on_a_staff_page(h, nxt):
    a = h.app.test_client()
    r = a.get(f'{B}/crt/staff/signin', query_string={'next': nxt}, base_url=STAFF_ORIGIN, headers=NAV)
    m = re.search(rb'var B = (\{.*?\});', r.data)
    nx = json.loads(m.group(1))['next']
    print('R1e', repr(nxt), '->', nx)
    assert nx.startswith('/api/biz/v1/crt/staff/') and '//' not in nx and '\\' not in nx and '..' not in nx


def test_R1f_control_file_on_the_staff_origin_is_an_inert_attachment(h):
    w = world(h, party_name='File Inc')
    h.staff()
    fid = w['files']['cert']['id']
    r = h.get(f'/files/{fid}')
    print('R1f', r.status_code, dict(r.headers))
    assert r.status_code == 200
    assert r.headers['Content-Type'].startswith('application/pdf')
    assert r.headers['Content-Disposition'].startswith('attachment')
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert "default-src 'none'" in r.headers['Content-Security-Policy']


def test_R1g_control_polyglot_html_upload_is_refused_or_typed_pdf(h):
    h.agent()
    blob = b'%PDF-1.4\n<html><script>alert(document.domain)</script></html>\n%%EOF\n'
    r = h.c.post(B + '/files', data={'file': (__import__('io').BytesIO(blob), 'x.html'), 'source': json.dumps(SRC)},
                 headers={'X-Agent-Key': SBX_KEY}, content_type='multipart/form-data')
    meta = r.get_json()
    print('R1g', r.status_code, meta)
    if r.status_code in (200, 201):
        assert meta['content_type'] in ('application/pdf', 'image/png', 'image/jpeg')
