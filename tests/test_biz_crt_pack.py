"""pack:risk-transfer (CCA Risk Transfer Tracker), plan section 7.2, in a "josh" dev sandbox.

Every case goes through the real API: the sandbox key is the agent (exactly what an outside
builder or the tenant's agent holds), a mocked Clerk session is staff. Fixtures inject `today`
and assert the CODE, not just the state; each negative fixture is the fully confirmed positive
world with ONE thing changed, so it fails for one reason only. Fake data only.
"""
import hashlib
import io
import json
import re
from datetime import date, timedelta

import pytest

STAFF_1, STAFF_2 = 'user_cca_staff_1', 'user_cca_staff_2'
KEY_A = 'agent-key-tenant-a'
SBX = 'josh'
SBX_KEY = 'sbx-josh-' + 'j' * 40
SBX_KEY_OTHER = 'sbx-foam-' + 'f' * 40
B = '/api/biz/v1'
# R1 (rules 1.4.0): the staff surface is its own origin. The bridge-handed JWT a canvas page holds carries
# azp = the tenant origin; only a JWT Clerk minted for a page ON the staff origin carries azp = STAFF_ORIGIN.
STAFF_ORIGIN = 'https://crt-staff-test-dev.jam-bot.com'
CANVAS_ORIGIN = 'https://test-dev.jam-bot.com'
SRC = {'channel': 'email', 'ref': 'fwd-from-staff-1', 'confidence': 0.8,
       'words': 'certificate forwarded by CCA staff'}
TODAY = date(2026, 11, 17)
M = 1_000_000
POLICY_FIELDS = ['line', 'policy_number', 'effective', 'expiration', 'limits', 'named_insured', 'addl_insd',
                 'source_document_id', 'producer_is_cca', 'producer_name', 'producer_phone', 'producer_email',
                 'certificate_holder', 'exclusions', 'occurrence_or_claims_made', 'tail', 'carrier']
END_FIELDS = ['function', 'form', 'edition', 'scheduled_names', 'scheduled_projects', 'blanket', 'manuscript',
              'document_id', 'crt_policy_id']          # rules 1.2.0 (N4): staff name the policy it attaches to
DOC_FIELDS = ['kind', 'crt_account_id', 'contact_id', 'file_url']   # rules 1.2.0 (N2/L3): staff check the paper itself


def iso(d):
    return d.isoformat()


# ── harness ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def dirs(tmp_path):
    d = {'a': tmp_path / 'clients' / 'test-dev' / 'db', 'pages': tmp_path / 'clients' / 'test-dev' / 'canvas-pages'}
    d['pages'].mkdir(parents=True)
    return d


class H:
    """One test's view of the josh sandbox: .staff(user) / .agent() switch who is calling."""

    def __init__(self, client, monkeypatch):
        self.c = client
        self.mp = monkeypatch
        self.who = None
        self.surface_ready = False          # set once the pack is on: staff then act through the staff pages
        self.surface = {}
        self.signed_in = None
        self.staff()

    def staff(self, user=STAFF_1):
        import services.auth as auth
        self.mp.setattr(auth, 'get_token_from_request', lambda: 'tok')
        self.mp.setattr(auth, 'verify_clerk_claims', lambda t: claims_for(t, user))
        self.mp.setattr(auth, 'verify_clerk_token', lambda t: (claims_for(t, user) or {}).get('sub'))
        self.who = ('staff', user)
        return self

    def signin(self, user):
        """Sign in on the staff origin the real way: a JWT Clerk minted for the staff origin, exchanged."""
        if self.signed_in == user:
            return
        r = self.c.post(f'{B}/crt/staff/session', json={'token': f'staffjwt:{user}'}, base_url=STAFF_ORIGIN,
                        headers={'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin', 'X-JB-Staff-Signin': '1'})
        assert r.status_code == 200, (r.status_code, r.data[:300])
        self.signed_in = user

    def agent(self):
        self.who = ('agent', None)
        return self

    def surface_token(self, user=None):
        """What a platform staff page holds (host ruling H4): got by opening the page the real way, a top-level
        navigation, as this staff user. None before the pack is on."""
        user = user or self.who[1]
        if not self.surface_ready:
            return None
        self.signin(user)
        if user not in self.surface:
            r = self.c.get(f'{B}/crt/staff/cca-crt-intake.html?sandbox={SBX}', base_url=STAFF_ORIGIN,
                           headers={'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document',
                                    'Sec-Fetch-Site': 'none'})
            assert r.status_code == 200, (r.status_code, r.data[:300])
            self.surface[user] = re.search(rb'"token": "([^"]+)"', r.data).group(1).decode()
        return self.surface[user]

    @property
    def h(self):
        if self.who[0] == 'agent':
            return {'X-Agent-Key': SBX_KEY}
        tok = self.surface_token()
        return {'X-Biz-Sandbox': SBX, **({'X-JB-Staff-Surface': tok, 'Sec-Fetch-Site': 'same-origin',
                                          'Origin': STAFF_ORIGIN} if tok else {})}

    @property
    def base(self):
        """Staff act on the staff origin once the pack is on; the agent (and staff before that) on the tenant's."""
        return STAFF_ORIGIN if self.who[0] == 'staff' and self.surface_ready else 'http://localhost'

    def _src(self, body):
        if self.who[0] == 'agent' and isinstance(body, dict) and 'source' not in body:
            body = dict(body, source=SRC)
        return body

    def get(self, path, **kw):
        hdr = self.h
        return self.c.get(B + path, headers=hdr, base_url=self.base, **kw)

    def post(self, path, body=None):
        hdr = self.h
        return self.c.post(B + path, json=self._src(body if body is not None else {}), headers=hdr, base_url=self.base)

    def patch(self, path, body, version):
        hdr = self.h
        return self.c.patch(B + path, json=self._src(body), headers={**hdr, 'If-Match': str(version)},
                            base_url=self.base)

    def ok(self, resp, code=(200, 201)):
        assert resp.status_code in ((code,) if isinstance(code, int) else code), resp.get_json()
        return resp.get_json()

    def mk(self, coll, data):
        return self.ok(self.post(f'/{coll}', {'data': data}), 201)

    def upload(self, blob, name='doc.pdf'):
        data = {'file': (io.BytesIO(blob), name)}
        if self.who[0] == 'agent':
            data['source'] = json.dumps(SRC)
        hdr = self.h
        r = self.c.post(B + '/files', data=data, headers=hdr, content_type='multipart/form-data', base_url=self.base)
        return self.ok(r)

    def state(self, aid, today=TODAY):
        # rules 1.3.0 (LOW ruling): an as-of date is a staff what-if, so the fixture date is read as staff. The
        # verdict never depends on who reads it; the agent's own ?today= is refused (test_low_today_*).
        who = self.who
        if who[0] == 'agent':
            self.staff()
        try:
            return self.ok(self.get(f'/crt/assignment/{aid}/state?today={iso(today)}'))
        finally:
            self.who = who


@pytest.fixture()
def h(dirs, monkeypatch):
    import routes.canvas as canvas
    monkeypatch.setenv('CLERK_PUBLISHABLE_KEY', 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk')
    monkeypatch.setenv('AGENT_API_KEY', KEY_A)
    monkeypatch.setenv('JAMBOT_TENANT', 'test-dev')
    monkeypatch.setenv('BIZ_DB_DIR', str(dirs['a']))
    monkeypatch.delenv('OVUI_DB_PATH', raising=False)
    monkeypatch.setenv('BIZ_SANDBOXES', 'josh,foam')
    monkeypatch.setenv('BIZ_SANDBOX_KEY_JOSH', SBX_KEY)
    monkeypatch.setenv('BIZ_SANDBOX_KEY_FOAM', SBX_KEY_OTHER)
    monkeypatch.setenv('BIZ_STAFF_ORIGIN', STAFF_ORIGIN)
    monkeypatch.setattr(canvas, 'CANVAS_PAGES_DIR', dirs['pages'])
    monkeypatch.setattr(canvas, 'CANVAS_REQUIRE_AUTH', True)
    monkeypatch.setattr(canvas, 'load_canvas_manifest', lambda: {'pages': {}})
    from app import create_app
    from routes.biz import biz_bp, jb_sdk_bp
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    for bp in (biz_bp, jb_sdk_bp, canvas.canvas_bp):
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)
    helper = H(app.test_client(), monkeypatch)
    helper.app = app
    helper.ok(helper.post('/packs/risk-transfer/enable'))
    helper.surface_ready = True
    yield helper
    for store in list(app.extensions.get('biz', {}).values()):
        if hasattr(store, 'close'):
            store.close()


def claims_for(token, user):
    """The mocked Clerk verifier: 'staffjwt:<user>' is a JWT Clerk minted for a page on the staff origin; any
    other token is the one the canvas bridge hands every canvas page (azp = the tenant origin)."""
    if not token:
        return None
    if str(token).startswith('staffjwt:'):
        return {'sub': str(token).split(':', 1)[1], 'azp': STAFF_ORIGIN}
    return {'sub': user, 'azp': CANVAS_ORIGIN}


def pdf(tag):
    return b'%PDF-1.4\n% ' + tag.encode() + b'\n%%EOF\n'


def confirm(h, coll, rec, fields, doc_id=None, user=STAFF_1):
    h.staff(user)
    cur = h.ok(h.get(f'/{coll}/{rec["id"]}'))
    body = {'record_collection': coll, 'record_id': rec['id'], 'record_version': cur['version'], 'fields': fields}
    if doc_id:
        body['source_document_id'] = doc_id
    return h.ok(h.post('/crt/confirm', body), 201)


def world(h, template='mep', trade='electrical', today=TODAY, policies=None, endorsements=None, project=None,
          confirm_all=True, approve=True, producer_is_cca=False, second_confirmer=False, account_name='Acme Builders LLC',
          party_name='Sparky Electric Inc'):
    """The fully confirmed positive world (one sub, one project, one account), with overrides."""
    w = {}
    h.staff()
    have = h.ok(h.get('/company'))['records']
    w['company'] = have[0] if have else h.mk('company', {'name': 'Contractors Choice Agency Sandbox'})
    w['account'] = h.mk('contact', {'name': account_name, 'type': 'commercial'})
    w['party'] = h.mk('contact', {'name': party_name, 'type': 'subcontractor'})
    w['link'] = h.mk('crt_account_party', {'crt_account_id': w['account']['id'], 'party_contact_id': w['party']['id'],
                                           'status': 'active'})
    # documents forwarded by staff and filed by the agent, into the private store
    h.agent()
    files = {k: h.upload(pdf(f'{k}-{party_name}-{template}'), f'{k}.pdf') for k in
             ('contract', 'cert', 'cert2', 'e_ongoing', 'e_completed', 'e_pnc', 'e_other', 'waiver_ok')}

    def doc(k, kind):
        return h.mk('document', {'title': k, 'file_url': files[k]['url'], 'kind': kind,
                                 'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']})
    w['docs'] = {'contract': doc('contract', 'contract_excerpt'), 'cert': doc('cert', 'coi_acord25'),
                 'cert2': doc('cert2', 'coi_acord25'),
                 'e_ongoing': doc('e_ongoing', 'endorsement'), 'e_completed': doc('e_completed', 'endorsement'),
                 'e_pnc': doc('e_pnc', 'endorsement'), 'e_other': doc('e_other', 'endorsement'),
                 'waiver_ok': doc('waiver_ok', 'contractor_waiver_ok')}
    w['files'] = files
    # the requirement set: DRAFT from the template (agent may draft), approved by staff
    w['set'] = h.ok(h.post('/crt/requirement-sets/from-template', {'template_key': template}), 201)['record']
    h.staff()
    if approve:
        w['set'] = h.ok(h.post(f'/crt/requirement-sets/{w["set"]["id"]}/approve'))['record']
    pdata = {'crt_account_id': w['account']['id'], 'name': 'Mesa Clinic Expansion', 'gc_name': account_name,
             'owner_name': 'Mesa Health District', 'substantial_completion': iso(today + timedelta(days=200)),
             'repose_years': 8, 'repose_source': 'A.R.S. 12-552 (sandbox fixture)', 'size_flag': 'small',
             'contract_requires_ai': True, 'contract_ai_parties': ['Mesa Health District'],
             'contract_document_id': w['docs']['contract']['id']}
    pdata.update(project or {})
    w['project'] = h.mk('crt_project', pdata)
    asg = {'crt_project_id': w['project']['id'], 'party_contact_id': w['party']['id'],
           'requirement_set_id': w['set']['id'], 'trade_category': trade}
    if 'requirement_set_version' in h.ok(h.get('/schema/crt_assignment'))['fields']:   # lets these tests run on 093f758 too
        asg['requirement_set_version'] = w['set']['version']
    w['asg'] = h.mk('crt_assignment', asg)
    # the agent proposes readings (unconfirmed until staff check them)
    h.agent()
    prod = {'producer_name': 'Desert Risk Partners', 'producer_contact': 'Pat Lee',
            'producer_phone': '602-555-0100', 'producer_email': 'pat@example.com',
            'producer_is_cca': producer_is_cca}
    base = {'crt_account_id': w['account']['id'], 'party_contact_id': w['party']['id'], 'carrier': 'Sample Mutual',
            'effective': iso(today - timedelta(days=100)), 'expiration': iso(today + timedelta(days=200)),
            'named_insured': party_name, 'certificate_holder': account_name, 'addl_insd': True,
            'occurrence_or_claims_made': 'occurrence', 'exclusions': ['none'], 'tail': False,   # 1.3.0 (H1): staff say "none"
            'source_document_id': w['docs']['cert']['id'], **prod}
    pols = policies if policies is not None else {
        'gl': {'line': 'gl', 'policy_number': 'GL-100', 'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
        'umbrella': {'line': 'umbrella', 'policy_number': 'UM-200', 'limits': {'each_occurrence': 5 * M}},
        'auto': {'line': 'auto', 'policy_number': 'AU-300', 'addl_insd': False,
                 'limits': {'combined_single_limit': 1 * M}},
    }
    w['pol'] = {}
    for k, pd in pols.items():
        w['pol'][k] = h.mk('crt_policy', {**base, **pd})
    named = {'scheduled_names': [account_name, 'Mesa Health District'], 'scheduled_projects': ['Mesa Clinic Expansion'],
             'blanket': False, 'manuscript': False}
    ends = endorsements if endorsements is not None else {
        'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing', **named},
        'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **named},
        'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **named},
    }
    w['end'] = {}
    for k, ed in ends.items():
        ed = dict(ed)
        dk = ed.pop('doc', 'e_other')
        pol_key = ed.pop('policy', 'gl')
        if dk is not None:
            ed['document_id'] = w['docs'][dk]['id']
        w['end'][k] = h.mk('crt_endorsement', {'crt_account_id': w['account']['id'],
                                                'crt_policy_id': w['pol'][pol_key]['id'], **ed})
    if confirm_all:
        # the agent filed these documents: staff check each one's kind, account, sub and file (rules 1.2.0)
        for d in w['docs'].values():
            confirm(h, 'document', d, DOC_FIELDS)
        users = [STAFF_1, STAFF_2] if second_confirmer else [STAFF_1]
        for u in users:
            for p in w['pol'].values():
                confirm(h, 'crt_policy', p, POLICY_FIELDS, w['docs']['cert']['id'], user=u)
            for e in w['end'].values():
                confirm(h, 'crt_endorsement', e, END_FIELDS,
                        e['data'].get('document_id') or w['docs']['cert']['id'], user=u)
        confirm(h, 'crt_project', w['project'], ['contract_requires_ai', 'contract_ai_parties'],
                w['docs']['contract']['id'])
    h.staff()
    return w


def codes(ev):
    return ev['codes']


# ── positive ────────────────────────────────────────────────────────────────
def test_positive_fully_confirmed_mep_meets_requirements(h):
    w = world(h)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets', ev
    assert ev['label'] == 'Meets requirements (documents reviewed)'
    assert ev['codes'] == [] and ev['disclaimer'].startswith('Evidence check')
    assert 'COMPLIANT' not in json.dumps(ev).upper().replace('NON-COMPLIANT', '')
    assert ev['hold_suggested'] is True and ev['hold_suggested_because'] == ['never reviewed by staff']
    assert sorted(ev['policies_read']) == sorted([w['pol']['gl']['id'], w['pol']['umbrella']['id']])  # MEP has no auto item
    # a staff review freezes it; then hold is no longer suggested
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    assert rv['review']['data']['state'] == 'meets' and rv['review']['data']['disclaimer_text'] == ev['disclaimer']
    assert rv['review']['data']['rule_version'] == ev['rule_version']
    assert h.state(w['asg']['id'], date.today())['hold_suggested'] is False


# ── T-B1 files ──────────────────────────────────────────────────────────────
def test_tb1_files_are_private(h):
    w = world(h)
    fid = w['files']['cert']['id']
    import services.auth as auth
    h.mp.setattr(auth, 'get_token_from_request', lambda: None)
    h.mp.setattr(auth, 'verify_clerk_token', lambda t: None)
    assert h.c.get(f'{B}/files/{fid}', headers={'X-Biz-Sandbox': SBX}).status_code == 401
    assert h.c.get(f'{B}/files/{fid}').status_code == 401
    assert h.c.get(f'{B}/files/{fid}', headers={'X-Agent-Key': SBX_KEY_OTHER}).status_code == 403
    assert h.c.get(f'{B}/files/{fid}/meta', headers={'X-Agent-Key': SBX_KEY_OTHER}).status_code == 404
    h.staff()
    assert h.get(f'/files/{fid}').status_code == 200
    docs = h.ok(h.get('/document?limit=500'))['records']
    assert len(docs) == 8 and sum(1 for d in docs if d['data']['file_url'].startswith('/uploads/')) == 0


def test_tb1_pack_documents_cannot_point_at_uploads(h):
    w = world(h)
    h.agent()
    r = h.post('/document', {'data': {'title': 'coi', 'file_url': '/uploads/crt/acme.pdf', 'kind': 'coi_acord25',
                                      'crt_account_id': w['account']['id']}})
    assert r.status_code == 422 and r.get_json()['code'] == 'file_must_be_private'
    r = h.post('/document', {'data': {'title': 'coi', 'file_url': 'biz-file:' + '0' * 64,
                                      'crt_account_id': w['account']['id']}})
    assert r.status_code == 404


# ── T-B2 agent role: every confirming / staff write is 403 through the real API ──
def test_tb2a_agent_cannot_confirm(h):
    w = world(h, confirm_all=False)
    h.agent()
    r = h.post('/crt/confirm', {'record_collection': 'crt_policy', 'record_id': w['pol']['gl']['id'],
                                'fields': ['limits'], 'source_document_id': w['docs']['cert']['id']})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'


def test_tb2b_agent_cannot_create_a_confirmation_through_the_generic_route(h):
    w = world(h, confirm_all=False)
    h.agent()
    r = h.post('/crt_confirmation', {'data': {'record_collection': 'crt_policy', 'record_id': w['pol']['gl']['id'],
                                              'record_version': 1, 'fields': ['limits']}})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'
    # and not even a person makes one outside the confirm route
    h.staff()
    r = h.post('/crt_confirmation', {'data': {'record_collection': 'crt_policy', 'record_id': w['pol']['gl']['id'],
                                              'record_version': 1, 'fields': ['limits']}})
    assert r.status_code == 409 and r.get_json()['code'] == 'use_pack_route'


def test_tb2c_agent_cannot_create_a_waiver(h):
    w = world(h)
    h.agent()
    r = h.post('/crt_waiver', {'data': {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PARTY',
                                        'accepted_by_contractor': 'A', 'expires': '2027-01-01',
                                        'acceptance_document_id': w['docs']['waiver_ok']['id']}})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'


def test_tb2d_agent_cannot_create_a_review(h):
    w = world(h)
    h.agent()
    r = h.post('/crt_review', {'data': {'crt_assignment_id': w['asg']['id'], 'state': 'meets'}})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'
    assert h.post(f'/crt/assignment/{w["asg"]["id"]}/review').status_code == 403
    h.staff()
    r = h.post('/crt_review', {'data': {'crt_assignment_id': w['asg']['id'], 'state': 'meets'}})
    assert r.status_code == 409 and r.get_json()['code'] == 'use_pack_route'


def test_tb2e_agent_cannot_approve_a_requirement_set(h):
    w = world(h, approve=False)
    s = w['set']
    h.agent()
    r = h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'status': 'APPROVED'}}, s['version'])
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'
    assert h.post(f'/crt/requirement-sets/{s["id"]}/approve').status_code == 403
    # a person cannot approve by a plain PATCH either: approval goes through the route that confirms it
    h.staff()
    r = h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'status': 'APPROVED'}}, s['version'])
    assert r.status_code == 409 and r.get_json()['code'] == 'use_approve_route'


def test_tb2f_agent_cannot_set_or_clear_a_hold(h):
    w = world(h)
    a = w['asg']
    h.agent()
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {'hold': True}}, a['version'])
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'
    h.staff()
    a = h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'hold': True, 'hold_reason': 'GL expired'}}, a['version']))
    h.agent()
    r = h.patch(f'/crt_assignment/{a["id"]}', {'data': {}, 'clear': ['hold']}, a['version'])
    assert r.status_code == 403
    assert h.state(a['id'])['hold'] is True


def test_tb2g_agent_cannot_use_the_core_confirm(h):
    w = world(h)
    h.agent()
    r = h.post(f'/crt_policy/{w["pol"]["gl"]["id"]}/confirm', {'fields': ['limits']})
    assert r.status_code == 403 and r.get_json()['code'] == 'owner_only'


@pytest.mark.parametrize('coll', ['crt_review', 'crt_confirmation', 'crt_waiver'])
def test_tb2h_agent_cannot_archive_or_unarchive_evidence_of_a_verdict(h, coll):
    w = world(h)
    h.staff()
    if coll == 'crt_review':
        rid = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['id']
    elif coll == 'crt_confirmation':
        rid = h.ok(h.get('/crt_confirmation?limit=1'))['records'][0]['id']
    else:
        rid = h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PARTY',
                                  'accepted_by_contractor': 'Acme PM', 'expires': '2027-06-01',
                                  'acceptance_document_id': w['docs']['waiver_ok']['id']})['id']
    h.agent()
    r = h.post(f'/{coll}/{rid}/archive')
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'
    h.staff()
    assert h.ok(h.post(f'/{coll}/{rid}/archive'))['archived_at']
    h.agent()
    r = h.post(f'/{coll}/{rid}/unarchive')
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'


def test_tb2_agent_written_clean_policy_is_unconfirmed(h):
    w = world(h, confirm_all=False)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell'
    assert 'UNCONFIRMED:crt_policy.limits' in codes(ev) and 'UNCONFIRMED:crt_policy.addl_insd' in codes(ev)
    assert ev['label'] == "Can't tell (needs staff check)" and ev['hold_suggested'] is True


def test_tb2_agent_edit_after_confirmation_makes_it_stale(h):
    w = world(h)
    assert h.state(w['asg']['id'])['state'] == 'meets'
    gl = h.ok(h.get(f'/crt_policy/{w["pol"]["gl"]["id"]}'))
    h.agent()
    h.ok(h.patch(f'/crt_policy/{gl["id"]}', {'data': {'limits': {'each_occurrence': 5 * M, 'general_aggregate': 5 * M}}},
                 gl['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.limits' in codes(ev)


def test_tb2_confirmation_hash_must_match_the_stored_file(h):
    w = world(h)
    assert h.state(w['asg']['id'])['state'] == 'meets'
    # the certificate document is repointed at a different stored file: every confirmation read
    # against the old bytes no longer counts
    doc = h.ok(h.get(f'/document/{w["docs"]["cert"]["id"]}'))
    h.agent()
    other = h.upload(pdf('a different certificate'))
    h.ok(h.patch(f'/document/{doc["id"]}', {'data': {'file_url': other['url']}}, doc['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.limits' in codes(ev)
    # rules 1.2.0: even when a person then checks the repointed document itself, the readings made
    # against the OLD bytes still do not count (the hash, not the document check, decides this)
    confirm(h, 'document', doc, DOC_FIELDS)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.limits' in codes(ev)


def test_tb2_confirmation_written_with_a_wrong_hash_does_not_count(h):
    w = world(h, confirm_all=False)
    # rules 1.2.0: everything else confirmed (documents, endorsements, contract), so the wrong hash on the
    # policy confirmations is the ONLY reason left (else the document check would mask this guard)
    for d in w['docs'].values():
        confirm(h, 'document', d, DOC_FIELDS)
    for e in w['end'].values():
        confirm(h, 'crt_endorsement', e, END_FIELDS, e['data']['document_id'])
    confirm(h, 'crt_project', w['project'], ['contract_requires_ai', 'contract_ai_parties'], w['docs']['contract']['id'])
    store = h.app.extensions['biz']['sandbox:josh']
    for p in w['pol'].values():
        store.create('crt_confirmation', {'record_collection': 'crt_policy', 'record_id': p['id'],
                                          'record_version': p['version'], 'fields': POLICY_FIELDS,
                                          'source_document_id': w['docs']['cert']['id'], 'source_sha256': 'f' * 64,
                                          'confirmed_by': 'clerk:' + STAFF_1}, by='clerk:' + STAFF_1, via_pack=True)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.limits' in codes(ev)


# ── T-B3 right party, right project ─────────────────────────────────────────
NAMED = {'scheduled_names': ['Acme Builders LLC', 'Mesa Health District'],
         'scheduled_projects': ['Mesa Clinic Expansion'], 'blanket': False, 'manuscript': False}


def _ends(**ongoing):
    return {
        'ai_ongoing': {'function': 'ai_ongoing', 'form': 'CG 20 10', 'edition': '04 13', 'doc': 'e_ongoing',
                       **{**NAMED, **ongoing}},
        'ai_completed': {'function': 'ai_completed', 'form': 'CG 20 37', 'edition': '04 13', 'doc': 'e_completed', **NAMED},
        'pnc': {'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'doc': 'e_pnc', **NAMED},
    }


def test_tb3a_endorsement_naming_another_gc_is_wrong_party(h):
    w = world(h, endorsements=_ends(scheduled_names=['Zenith General Contractors Inc', 'Mesa Health District']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_PARTY'] and ev['hold_suggested'] is True
    assert ev['items']['ai_ongoing'] == 'WRONG_PARTY'


def test_tb3a_endorsement_missing_the_owner_the_contract_requires_is_wrong_party(h):
    w = world(h, endorsements=_ends(scheduled_names=['Acme Builders LLC']))
    assert codes(h.state(w['asg']['id'])) == ['WRONG_PARTY']


def test_tb3b_endorsement_scheduled_to_another_project_is_wrong_project(h):
    w = world(h, endorsements=_ends(scheduled_projects=['Tempe Warehouse Retrofit']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_PROJECT']


def test_tb3c_blanket_without_a_confirmed_contract_tick_is_cant_tell(h):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=[], scheduled_projects=[], form='CG 20 33'))
    # the contract tick exists but was never confirmed against the contract: re-make the project unconfirmed
    proj = h.ok(h.get(f'/crt_project/{w["project"]["id"]}'))
    h.ok(h.patch(f'/crt_project/{proj["id"]}', {'data': {'contract_ref': 'SC-2026-14'}}, proj['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_project.contract_requires_ai' in codes(ev)


def test_tb3c_blanket_backed_by_a_confirmed_contract_tick_meets(h):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=[], scheduled_projects=[], form='CG 20 33'))
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_tb3c_blanket_when_the_contract_does_not_require_ai_is_unbacked(h):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=[], scheduled_projects=[], form='CG 20 33'),
              project={'contract_requires_ai': False})
    assert codes(h.state(w['asg']['id'])) == ['BLANKET_UNBACKED']


# ── other variance codes ────────────────────────────────────────────────────
def test_certificate_holder_only_is_not_ai(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'addl_insd': False,
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['NOT_AI:gl']


def test_pl_with_certificate_holder_only_is_correct_for_design_pros(h):
    pols = {'pl': {'line': 'pl', 'policy_number': 'PL-1', 'addl_insd': False, 'occurrence_or_claims_made': 'claims_made',
                   'tail': True, 'limits': {'each_claim': 2 * M, 'aggregate': 2 * M}},
            'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}}}
    w = world(h, template='design_pro', trade='architect', policies=pols, endorsements={})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets', ev
    assert not [c for c in codes(ev) if c.startswith('NOT_AI')]


def test_pl_claims_made_without_tail(h):
    pols = {'pl': {'line': 'pl', 'policy_number': 'PL-1', 'addl_insd': False, 'occurrence_or_claims_made': 'claims_made',
                   'tail': False, 'limits': {'each_claim': 2 * M, 'aggregate': 2 * M}},
            'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}}}
    w = world(h, template='design_pro', trade='engineer', policies=pols, endorsements={})
    assert codes(h.state(w['asg']['id'])) == ['PL_NO_TAIL']


def test_pl_certificate_holder_must_be_the_account(h):
    pols = {'pl': {'line': 'pl', 'policy_number': 'PL-1', 'addl_insd': False, 'occurrence_or_claims_made': 'occurrence',
                   'certificate_holder': 'Somebody Else LLC', 'limits': {'each_claim': 2 * M, 'aggregate': 2 * M}},
            'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}}}
    w = world(h, template='design_pro', trade='engineer', policies=pols, endorsements={})
    assert codes(h.state(w['asg']['id'])) == ['WRONG_HOLDER']


def test_limit_low(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 500_000, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    assert codes(ev) == ['LIMIT_LOW:gl'] and ev['items']['gl_occ'] == 'LIMIT_LOW:gl'


@pytest.mark.parametrize('umbrella,expect', [(6 * M, []), (5 * M, ['LIMIT_LOW:gl'])])
def test_high_severity_combined_gl_plus_umbrella(h, umbrella, expect):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': umbrella}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='steel erection', policies=pols)
    ev = h.state(w['asg']['id'])
    assert codes(ev) == expect, ev


def test_high_severity_xcu_exclusion_on_excavation(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['XCU'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='excavation', policies=pols)
    assert codes(h.state(w['asg']['id'])) == ['EXCLUSION:xcu']


def test_envelope_water_exclusion(h):
    # rules 1.3.0 (H1): staff map the wording to a class; unmapped wording is Can't tell (test_h1_* in claimcheck3)
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['water_damage_sublimit'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, template='envelope', trade='roofing', policies=pols)
    assert codes(h.state(w['asg']['id'])) == ['EXCLUSION:water']


def test_producer_contact_incomplete(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'producer_phone': None, 'producer_email': None,
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    assert codes(h.state(w['asg']['id'])) == ['PRODUCER_INCOMPLETE']


def test_certificate_for_another_insured(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'named_insured': 'Sparks Brothers Plumbing',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    assert codes(h.state(w['asg']['id'])) == ['WRONG_INSURED']


def test_missing_line_and_missing_endorsement_document(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and codes(ev) == ['MISSING:umbrella']
    w2 = world(h, endorsements={k: v for k, v in _ends().items() if k != 'pnc'}, party_name='Volt Masters Inc')
    assert codes(h.state(w2['asg']['id'])) == ['MISSING:pnc']


def test_a_certificate_alone_never_meets_an_endorsement_requirement(h):
    # the endorsement was read off the certificate (confirmed against it), but no endorsement document arrived
    w = world(h, endorsements=_ends(doc=None))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and codes(ev) == ['MISSING:ai_ongoing']


def test_an_archived_source_document_makes_its_readings_unconfirmed(h):
    w = world(h)
    doc = h.ok(h.get(f'/document/{w["end"]["ai_ongoing"]["data"]["document_id"]}'))
    h.ok(h.post(f'/document/{doc["id"]}/archive'))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.function' in codes(ev)


def test_scope_change_after_the_last_review(h):
    w = world(h)
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'scope_changed_at': '2099-01-01T00:00:00Z'}}, a['version']))
    assert 'SCOPE_CHANGED' in codes(h.state(w['asg']['id'], date.today()))


# ── expiry and hold ─────────────────────────────────────────────────────────
def test_expired_policy(h):
    w = world(h)
    ev = h.state(w['asg']['id'], TODAY + timedelta(days=200))
    assert ev['state'] == 'expired' and 'EXPIRED:gl' in codes(ev) and ev['hold_suggested'] is True


@pytest.mark.parametrize('days,state', [(185, 'expiring_15'), (175, 'expiring_30'), (160, 'meets')])
def test_expiring_windows(h, days, state):
    w = world(h)
    assert h.state(w['asg']['id'], TODAY + timedelta(days=days))['state'] == state


def test_review_older_than_30_days_suggests_a_hold(h):
    w = world(h)
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    assert h.state(w['asg']['id'], date.today())['hold_suggested'] is False
    later = h.state(w['asg']['id'], date.today() + timedelta(days=31))
    assert later['state'] in ('meets', 'expiring_30', 'expiring_15', 'expired')
    assert later['hold_suggested'] is True and 'no staff review in the last 30 days' in later['hold_suggested_because']


def test_completed_ops_lapsed_on_a_renewal_after_close_inside_repose(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-R2', 'effective': iso(TODAY - timedelta(days=30)),
                   'expiration': iso(TODAY + timedelta(days=335)),
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-R2', 'limits': {'each_occurrence': 5 * M}}}
    ends = {k: v for k, v in _ends().items() if k != 'ai_completed'}
    w = world(h, policies=pols, endorsements=ends,
              project={'substantial_completion': iso(TODAY - timedelta(days=400))})
    ev = h.state(w['asg']['id'])
    assert codes(ev) == ['COMPLETED_OPS_LAPSED'] and ev['items']['ai_ongoing'] == 'N/A'
    # after the statute of repose has run, completed-ops AI is no longer required
    assert h.state(w['asg']['id'], TODAY + timedelta(days=365 * 8))['items']['ai_completed'] == 'N/A'


def test_completed_ops_renewed_after_close_meets(h):
    w = world(h, project={'substantial_completion': iso(TODAY - timedelta(days=400))})
    assert h.state(w['asg']['id'])['state'] == 'meets'


# ── waivers: the contractor accepts, staff record ───────────────────────────
def test_waiver_unexpired_accepts_and_expired_returns_the_variance(h):
    w = world(h, endorsements=_ends(scheduled_names=['Zenith General Contractors Inc', 'Mesa Health District']))
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PARTY',
                        'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                        'acceptance_document_id': w['docs']['waiver_ok']['id'], 'recorded_by': 'clerk:' + STAFF_1,
                        'reason': 'GC renamed mid-job', 'compensating_control': 'contract indemnity',
                        'expires': iso(TODAY + timedelta(days=60))})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets_accepted' and ev['codes'] == []
    assert ev['label'] == 'Meets requirements except contractor-accepted variances (documents reviewed)'
    assert [a['code'] for a in ev['accepted']] == ['WRONG_PARTY']
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']
    assert rv['state'] == 'meets_accepted' and rv['label'] == ev['label'] and rv['accepted_codes'] == ['WRONG_PARTY']
    late = h.state(w['asg']['id'], TODAY + timedelta(days=61))
    assert late['state'] == 'variance' and codes(late) == ['WRONG_PARTY'] and late['accepted'] == []


def test_a_waiver_needs_the_contractors_written_ok_in_the_private_store(h):
    w = world(h)
    h.staff()
    doc = h.mk('document', {'title': 'note', 'file_url': '/uploads/note.pdf'})
    r = h.post('/crt_waiver', {'data': {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PARTY',
                                        'accepted_by_contractor': 'A', 'acceptance_document_id': doc['id'],
                                        'expires': '2027-01-01'}})
    assert r.status_code == 422 and r.get_json()['code'] == 'document_not_private'


# ── requirement sets ────────────────────────────────────────────────────────
def test_draft_set_is_cant_tell(h):
    w = world(h, approve=False)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_requirement_set.status' in codes(ev)


def test_a_used_set_is_frozen_and_revising_makes_a_new_version(h):
    w = world(h)
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']
    before = hashlib.sha256(json.dumps(h.ok(h.get(f'/crt_review/{rv["id"]}')), sort_keys=True).encode()).hexdigest()
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    r = h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'name': 'renamed'}}, s['version'])
    assert r.status_code == 409 and r.get_json()['code'] == 'requirement_set_in_use'
    new = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'name': 'MEP v2'}), 201)['record']
    assert new['data']['set_version'] == 2 and new['data'].get('status') is None and new['data']['supersedes'] == s['id']
    after = hashlib.sha256(json.dumps(h.ok(h.get(f'/crt_review/{rv["id"]}')), sort_keys=True).encode()).hexdigest()
    assert before == after
    assert h.ok(h.get(f'/crt_requirement_set/{s["id"]}'))['version'] == s['version']


def test_editing_an_approved_set_needs_a_new_approval(h):
    w = world(h)
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.ok(h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'name': 'MEP (tweaked)'}}, s['version']))
    assert 'UNCONFIRMED:crt_requirement_set.status' in codes(h.state(w['asg']['id']))


@pytest.mark.parametrize('item,code', [
    ({'id': 'x', 'kind': 'ai', 'line': 'wc', 'required': True}, 'ai_on_wc_refused'),
    ({'id': 'x', 'kind': 'endorsement', 'function': 'ai_ongoing', 'line': 'wc', 'forms': ['CG 20 10']}, 'ai_on_wc_refused'),
    ({'id': 'x', 'kind': 'endorsement', 'function': 'ai_ongoing', 'line': 'gl', 'note': 'Additional Named Insured'},
     'additional_named_insured_refused'),
    ({'id': 'x', 'kind': 'endorsement', 'function': 'ai_ongoing', 'line': 'gl', 'forms': ['CG 20 10'], 'edition': '11 85'},
     'pre_2004_edition_refused'),
    ({'id': 'x', 'kind': 'endorsement', 'function': 'ai_completed', 'line': 'gl', 'forms': ['CG 20 37'], 'edition': '10 01'},
     'pre_2004_edition_refused'),
])
def test_requirement_set_traps_are_refused(h, item, code):
    r = h.post('/crt_requirement_set', {'data': {'name': 'x', 'trade_category': 'custom', 'items': [item]}})
    assert r.status_code == 422 and r.get_json()['code'] == code
    w_set = h.ok(h.post('/crt/requirement-sets/from-template', {'template_key': 'finish'}), 201)['record']
    r = h.post(f'/crt/requirement-sets/{w_set["id"]}/revise', {'items': [item]})
    assert r.status_code == 422 and r.get_json()['code'] == code


def test_ai_on_pl_and_big_umbrella_on_finish_warn(h):
    items = [{'id': 'a', 'kind': 'ai', 'line': 'pl'}, {'id': 'u', 'kind': 'limit', 'line': 'umbrella',
                                                      'limit': 'each_occurrence', 'min': 5 * M}]
    s = h.ok(h.post('/crt/requirement-sets/from-template', {'template_key': 'finish'}), 201)['record']
    out = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items}), 201)
    assert sorted(w['item'] for w in out['warnings']) == ['a', 'u']


def test_templates_are_drafts_with_citations_and_the_p17_conflict(h):
    t = h.ok(h.get('/crt/templates'))
    assert [x['key'] for x in t['templates']] == ['high_severity', 'envelope', 'mep', 'medium', 'finish', 'design_pro']
    assert all(it.get('cite') for x in t['templates'] for it in x['items'])
    env = next(x for x in t['templates'] if x['key'] == 'envelope')
    assert 'p.17' in env['conflict_note']
    assert 'p.17 only' in next(x for x in t['templates'] if x['key'] == 'medium')['single_source']
    s = h.ok(h.post('/crt/requirement-sets/from-template', {'template_key': 'mep'}), 201)['record']
    assert s['data'].get('status') is None


# ── project / assignment rules ──────────────────────────────────────────────
def test_repose_years_need_their_source(h):
    acct = h.mk('contact', {'name': 'Acme', 'type': 'commercial'})
    r = h.post('/crt_project', {'data': {'crt_account_id': acct['id'], 'name': 'P', 'repose_years': 8}})
    assert r.status_code == 422 and r.get_json()['code'] == 'repose_source_required'


def test_a_sub_must_be_linked_to_the_account_to_be_assigned(h):
    w = world(h)
    stranger = h.mk('contact', {'name': 'Unlinked Drywall', 'type': 'subcontractor'})
    r = h.post('/crt_assignment', {'data': {'crt_project_id': w['project']['id'], 'party_contact_id': stranger['id'],
                                            'requirement_set_id': w['set']['id'],
                                            'requirement_set_version': w['set']['version']}})
    assert r.status_code == 422 and r.get_json()['code'] == 'party_not_linked'


def test_agent_created_project_needs_staff_before_it_counts(h):
    w = world(h)
    p = h.ok(h.get(f'/crt_project/{w["project"]["id"]}'))
    h.agent()
    h.ok(h.patch(f'/crt_project/{p["id"]}', {'data': {'gc_name': 'Acme Builders LLC (per email)'}}, p['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and any(c.startswith('UNCONFIRMED:crt_project.') for c in codes(ev))


def test_an_agent_edit_to_the_account_name_needs_staff_before_it_counts(h):
    # contacts carry no confirmations, so only the person-written rule can make this "can't tell".
    # rules 1.2.0 (ruling N1): provenance is PER FIELD. The agent's edit of the NAME (to a spelling that
    # still normalises the same, so provenance is the only reason) is what makes it "can't tell"; the
    # agent editing an unrelated field (phone) no longer does.
    w = world(h)
    acct = h.ok(h.get(f'/contact/{w["account"]["id"]}'))
    h.agent()
    acct = h.ok(h.patch(f'/contact/{acct["id"]}', {'data': {'phone': '602-555-0199'}}, acct['version']))
    assert h.state(w['asg']['id'])['state'] == 'meets'
    h.ok(h.patch(f'/contact/{acct["id"]}', {'data': {'name': 'ACME Builders, LLC'}}, acct['version']))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:contact.name']
    # a person on staff confirms the name: it counts again
    confirm(h, 'contact', h.ok(h.get(f'/contact/{acct["id"]}')), ['name', 'company_name'])
    assert h.state(w['asg']['id'])['state'] == 'meets'


# ── conflict of interest: we are the sub's producer ─────────────────────────
def test_cca_as_producer_needs_a_second_different_staff_confirmer(h):
    w = world(h, producer_is_cca=True)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.limits' in codes(ev)
    w2 = world(h, producer_is_cca=True, second_confirmer=True, party_name='Brightline Electric Inc')
    assert h.state(w2['asg']['id'])['state'] == 'meets'


def test_producer_named_like_our_agency_counts_as_us_even_unflagged(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'producer_name': 'Contractors Choice Agency Sandbox',
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols)
    assert h.state(w['asg']['id'])['state'] == 'cant_tell'


# ── one sub, two accounts (ruling e) ────────────────────────────────────────
def test_one_sub_two_accounts_each_sees_only_its_own_records(h):
    a = world(h)
    h.staff()
    acct_b = h.mk('contact', {'name': 'Basin Homes Inc', 'type': 'commercial'})
    h.mk('crt_account_party', {'crt_account_id': acct_b['id'], 'party_contact_id': a['party']['id'], 'status': 'active'})
    h.agent()
    fb = h.upload(pdf('basin certificate'))
    doc_b = h.mk('document', {'title': 'basin coi', 'file_url': fb['url'], 'kind': 'coi_acord25',
                              'crt_account_id': acct_b['id'], 'contact_id': a['party']['id']})
    pol_b = h.mk('crt_policy', {'crt_account_id': acct_b['id'], 'party_contact_id': a['party']['id'], 'line': 'gl',
                                'policy_number': 'GL-BASIN', 'source_document_id': doc_b['id'],
                                'effective': iso(TODAY - timedelta(days=10)), 'expiration': iso(TODAY + timedelta(days=300)),
                                'limits': {'each_occurrence': 9 * M}})        # in force, later expiry: a leak would pick it
    h.staff()
    m = h.ok(h.get(f'/crt/project/{a["project"]["id"]}/matrix?today={iso(TODAY)}'))
    read = [pid for row in m['rows'] for pid in row['policies_read']]
    assert pol_b['id'] not in read and sum(1 for pid in read if pid == pol_b['id']) == 0
    assert m['rows'][0]['state'] == 'meets'
    view_a = h.ok(h.get(f'/crt/party/{a["party"]["id"]}?account={a["account"]["id"]}'))
    view_b = h.ok(h.get(f'/crt/party/{a["party"]["id"]}?account={acct_b["id"]}'))
    assert pol_b['id'] not in [p['id'] for p in view_a['policies']] and len(view_a['policies']) == 3
    assert [p['id'] for p in view_b['policies']] == [pol_b['id']]
    assert [d['id'] for d in view_b['documents']] == [doc_b['id']]
    assert h.get(f'/crt/party/{a["party"]["id"]}').status_code == 400
    # an endorsement cannot be filed for account B on account A's policy
    h.agent()
    r = h.post('/crt_endorsement', {'data': {'crt_account_id': acct_b['id'], 'crt_policy_id': a['pol']['gl']['id'],
                                             'function': 'ai_ongoing'}})
    assert r.status_code == 422 and r.get_json()['code'] == 'account_mismatch'


# ── read models, renewals, chase ────────────────────────────────────────────
def test_book_account_and_renewals(h):
    w = world(h)
    book = h.ok(h.get(f'/crt/book?today={iso(TODAY)}'))
    row = next(a for a in book['accounts'] if a['account_id'] == w['account']['id'])
    assert row['subs_tracked'] == 1 and row['states']['meets'] == 1 and row['gap_rate'] == 0
    acct = h.ok(h.get(f'/crt/account/{w["account"]["id"]}?today={iso(TODAY)}'))
    assert len(acct['projects']) == 1 and len(acct['subs']) == 1
    ren = h.ok(h.get(f'/crt/renewals?today={iso(TODAY + timedelta(days=190))}'))
    assert {r['line'] for r in ren['columns']['T-15']} == {'gl', 'umbrella'}
    assert 'Drafts only' in ren['note'] and 'expires on' in ren['columns']['T-15'][0]['draft']
    h.agent()
    assert h.post(f'/crt/assignment/{w["asg"]["id"]}/chase-sent', {'stage': 'T-15'}).status_code == 403
    h.staff()
    h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/chase-sent', {'stage': 'T-15'}), 201)
    ev = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}/timeline'))['events']
    assert [e for e in ev if e['type'] == 'crt_chase_marked_sent'][0]['by'] == 'clerk:' + STAFF_1


def test_pack_routes_404_until_the_pack_is_on(h, monkeypatch):
    # the tenant's own store (not the sandbox) never turned the pack on
    import services.auth as auth
    monkeypatch.setattr(auth, 'get_token_from_request', lambda: 'tok')
    monkeypatch.setattr(auth, 'verify_clerk_token', lambda t: STAFF_1)
    r = h.c.get(f'{B}/crt/book')
    assert r.status_code == 404 and r.get_json()['code'] == 'pack_not_enabled'
    assert h.c.get(f'{B}/crt_policy').status_code == 404
    assert h.c.get(f'{B}/health').get_json()['packs'] == []


# ── pages: UI only, no browser storage, published + registered in the sandbox ──
PAGES = ['cca-crt-book.html', 'cca-crt-account.html', 'cca-crt-project.html', 'cca-crt-party.html',
         'cca-crt-intake.html', 'cca-crt-requirements.html', 'cca-crt-renewals.html']


def _page_dir():
    from services.biz.schema import PACKS_DIR
    return PACKS_DIR / 'risk-transfer' / 'pages'


def test_pages_use_no_browser_storage_and_only_the_sdk():
    import re
    texts = {p: (_page_dir() / p).read_text(encoding='utf-8') for p in PAGES}
    for name, t in texts.items():
        assert len(re.findall(r'localStorage|sessionStorage|indexedDB', t, re.I)) == 0, name
        assert '/jb/v1/jb.js' in t, name
        assert 'COMPLIANT' not in t.upper().replace('NON-COMPLIANT', ''), name
        assert not re.search(r'fetch\(\s*[\'"]/api/', t), name       # data goes through jb.js only
        assert '/uploads/' not in t, name


def test_pages_publish_register_and_serve_in_the_sandbox(h):
    """Rules 1.3.0 (host ruling H4): the pack's staff pages are served from the image, never published by the
    agent. The agent's PUT of a platform page name is refused; staff open each page by a top-level navigation and
    get the image's own bytes (scripts nonce'd, a per-render token, not frameable, its own browsing-context group)."""
    h.agent()
    for p in PAGES:
        html = (_page_dir() / p).read_text(encoding='utf-8')
        r = h.c.put(f'{B}/dev/pages/{p}', json={'html': html, 'source': SRC}, headers=h.h)
        assert r.status_code == 409 and r.get_json()['code'] == 'platform_page_reserved', r.get_json()
    h.staff()
    nav = {'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
    h.signin(STAFF_1)                    # rules 1.4.0 (R1): the staff pages live on the staff origin only
    for p in PAGES:
        r = h.c.get(f'{B}/crt/staff/{p}?sandbox={SBX}', headers={'Authorization': 'Bearer tok', **nav})
        assert r.status_code == 302 and r.headers['Location'].startswith(STAFF_ORIGIN + f'{B}/crt/staff/{p}'), p
        r = h.c.get(f'{B}/crt/staff/{p}?sandbox={SBX}', headers=nav, base_url=STAFF_ORIGIN)
        assert r.status_code == 200, (p, r.status_code)
        body = r.get_data(as_text=True)
        assert 'window.__JB_SURFACE__' in body and '"sandbox": "josh"' in body
        assert 'Tick all' not in body
        assert all('nonce="' in tag for tag in re.findall(r'<script[^>]*>', body)), p
        csp = r.headers['Content-Security-Policy']
        assert "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp.split('script-src')[1].split(';')[0]
        assert r.headers['Cross-Origin-Opener-Policy'] == 'same-origin' and r.headers['X-Frame-Options'] == 'DENY'
