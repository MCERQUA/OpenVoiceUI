"""Route x record matrix (host ruling SYSTEMIC, rules 1.5.0): ONE generated test over every state-changing route the
agent key or a canvas-origin JWT can reach, applied to every record type the verdict or the watcher reads.

How the matrix is built
  * ROUTES: every rule in the Flask url_map under /api/biz/v1 with a non-safe method, classified by endpoint. The
    first test FAILS if a state-changing endpoint exists that is not classified here (a new route, a batch or import
    route, a DELETE), and if any module other than routes/biz.py opens the business store (so no other route can
    write a record the verdict reads).
  * TARGETS: every record the verdict or the watcher reads in a staff-built world: the agency (company), the account
    and the sub (contact), their link, the project, the assignment, the pinned requirement set, a policy, an
    endorsement, the certificate document, a staff-filed endorsement document, a staff confirmation, a staff waiver,
    a staff review; plus the stored file.
  * VERBS: create, edit, archive, unarchive, link (re-point a reference), confirm (pack confirm and core flag
    confirm), revise, approve, review, chase, draft-from-template, and the non-record routes.
  * WORLDS: 'plain' (staff-built, Meets) and 'accepted' (staff-built, Meets except contractor-accepted variances:
    after substantial completion, the completed-ops endorsement on another edition, that edition waived by staff).
  * Each mutation is the HOSTILE one: it would change what the verdict reads if it counted. Where a route's change
    only counts after a normal staff step (approving a revision the page showed them, re-pinning to it), the cell
    runs that staff step too.

The assertion, per cell: the actor's request is REFUSED (403 / 409 / 422), or the verdict afterwards is NOT a Meets
state (Can't tell, or worse), or the route is INERT by construction (it writes nothing the verdict reads: drafts,
files, counters, page registry, schema proposals) AND the verdict and every target record are byte-identical.
A plain 'meets' or 'meets_accepted' after an accepted change to a record the verdict reads is a FALSE MEETS.

The matrix is printed (and written to $CRT_MATRIX_OUT when set) for the PR comment.
"""
import io
import json
import os
import re
import zlib
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, DOC_FIELDS, END_FIELDS, SBX, SBX_KEY, SRC, STAFF_1, TODAY, codes, confirm, dirs, h, iso, pdf, world,
)
from tests.test_biz_crt_claimcheck4 import _completed

ROOT = Path(__file__).resolve().parents[1]
MEETS = ('meets', 'meets_accepted', 'expiring_30', 'expiring_15')
REFUSED = (403, 409, 422)
ACTORS = ('agent_key', 'canvas_jwt')
WORLDS = ('plain', 'accepted')
TARGETS = ('company', 'account', 'party', 'link', 'project', 'assignment', 'set', 'policy', 'endorsement',
           'doc_cert', 'doc_endorsement', 'confirmation', 'waiver', 'review')
COLL = {'company': 'company', 'account': 'contact', 'party': 'contact', 'link': 'crt_account_party',
        'project': 'crt_project', 'assignment': 'crt_assignment', 'set': 'crt_requirement_set',
        'policy': 'crt_policy', 'endorsement': 'crt_endorsement', 'doc_cert': 'document',
        'doc_endorsement': 'document', 'confirmation': 'crt_confirmation', 'waiver': 'crt_waiver',
        'review': 'crt_review'}
LINKS = {'link': 'crt_account_id', 'project': 'contract_document_id', 'assignment': 'requirement_set_id',
         'policy': 'source_document_id', 'endorsement': 'document_id', 'doc_cert': 'contact_id',
         'doc_endorsement': 'contact_id', 'waiver': 'acceptance_document_id', 'review': 'crt_assignment_id',
         'confirmation': 'record_id'}
# endpoint -> (verbs, applies to targets or None for a non-record route)
ROUTES = {
    'biz.create_record': (('create',), TARGETS),
    'biz.patch_record': (('edit', 'link'), TARGETS),
    'biz.archive_record': (('archive',), TARGETS),
    'biz.unarchive_record': (('unarchive',), TARGETS),
    'biz.confirm_record_flags': (('flags_confirm',), TARGETS),
    'biz.crt_confirm': (('confirm',), TARGETS),
    'biz.crt_set_revise': (('revise',), ('set',)),
    'biz.crt_set_approve': (('approve',), ('set',)),
    'biz.crt_set_from_template': (('draft',), ('set',)),
    'biz.crt_review': (('review',), ('assignment',)),
    'biz.crt_chase_sent': (('chase',), ('assignment',)),
    'biz.file_upload': (('upload',), None),
    'biz.counter_next': (('counter',), None),
    'biz.packs_enable': (('pack_enable',), None),
    'biz.registry_register': (('page_register',), None),
    'biz.registry_patch': (('page_patch',), None),
    'biz.registry_archive': (('page_archive',), None),
    'biz.registry_unarchive': (('page_unarchive',), None),
    'biz.schema_custom': (('schema_custom',), None),
    'biz.schema_adopt': (('schema_adopt',), None),
    'biz.dev_page_put': (('dev_page',), None),
}
# routes that by construction write nothing the verdict reads: accepted only if the verdict AND every target record
# stay byte-identical (checked, never assumed)
INERT_OK = {'draft', 'upload', 'counter', 'pack_enable', 'page_register', 'page_patch', 'page_archive',
            'page_unarchive', 'schema_custom', 'schema_adopt', 'dev_page', 'flags_confirm'}


def _cells():
    out = []
    for ep, (verbs, targets) in sorted(ROUTES.items()):
        for verb in verbs:
            for t in (targets or ('-',)):
                if verb == 'link' and t not in LINKS:
                    continue
                for actor in ACTORS:
                    for wk in (WORLDS if verb in ('create', 'edit', 'archive', 'unarchive', 'link') else ('plain',)):
                        out.append((ep, verb, t, actor, wk))
    return out


CELLS = _cells()
RESULTS = {}


@pytest.fixture(scope='module', autouse=True)
def _print_matrix():
    yield
    if not RESULTS:
        return
    lines = ['| endpoint | verb | record | ' + ' | '.join(f'{a} / {w}' for a in ACTORS for w in WORLDS) + ' |',
             '|' + '---|' * (3 + len(ACTORS) * len(WORLDS))]
    rows = {}
    for (ep, verb, t, actor, wk), res in RESULTS.items():
        rows.setdefault((ep, verb, t), {})[(actor, wk)] = res
    for (ep, verb, t), cols in sorted(rows.items()):
        lines.append(f'| {ep.split(".", 1)[1]} | {verb} | {t} | '
                     + ' | '.join(cols.get((a, w), 'n/a') for a in ACTORS for w in WORLDS) + ' |')
    bad = sum(1 for v in RESULTS.values() if v.startswith('FALSE'))
    lines.append(f'\n{len(RESULTS)} cells, {bad} FALSE MEETS')
    text = '\n'.join(lines)
    print('\nROUTE x RECORD MATRIX\n' + text)
    if os.getenv('CRT_MATRIX_OUT'):
        Path(os.environ['CRT_MATRIX_OUT']).write_text(text + '\n', encoding='utf-8')


def test_matrix_covers_every_state_changing_route(h):
    found = set()
    for r in h.app.url_map.iter_rules():
        if r.rule.startswith('/api/biz/v1') and set(r.methods) - {'GET', 'HEAD', 'OPTIONS'}:
            found.add(r.endpoint)
            assert 'DELETE' not in r.methods, r.rule                              # no delete exists
    assert found == set(ROUTES), {'unclassified': sorted(found - set(ROUTES)), 'gone': sorted(set(ROUTES) - found)}
    assert not [e for e in found if re.search(r'batch|import|bulk', e)]          # none exist; if one appears, classify it
    # only routes/biz.py opens the business store: no other route can write a record the verdict reads
    users = []
    for p in list(ROOT.glob('routes/*.py')) + list(ROOT.glob('services/**/*.py')) + [ROOT / 'server.py', ROOT / 'app.py']:
        if p.is_file() and p.name != 'biz.py' and 'services/biz' not in str(p):
            if re.search(r'\bget_store\(|\bBizStore\(', p.read_text(encoding='utf-8', errors='replace')):
                users.append(str(p.relative_to(ROOT)))
    assert users == [], users


# ── the base worlds ─────────────────────────────────────────────────────────
def _base(h, wk, n):
    if wk == 'plain':
        w = world(h, party_name=f'Matrix {n} Inc')
    else:
        w = world(h, endorsements=_completed(edition='12 19'), party_name=f'Matrix {n} Inc',
                  project={'substantial_completion': iso(TODAY - timedelta(days=30))})
    h.staff()
    # the completed-ops paper is filed by staff themselves (identity person-written, ruling N1 path)
    up = h.upload(pdf(f'staff-completed-{n}'), 'e2.pdf')
    d2 = h.mk('document', {'title': 'staff-filed', 'file_url': up['url'], 'kind': 'endorsement',
                           'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']})
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_completed"]["id"]}'))
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'document_id': d2['id']}}, e['version']))
    confirm(h, 'crt_endorsement', e, END_FIELDS, d2['id'])
    w['docs']['staff_e'] = d2
    w['end']['ai_completed'] = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    # a staff waiver (accepted world: the edition; plain world: a code that is not present, so it changes nothing)
    code = 'WRONG_EDITION:ai_completed' if wk == 'accepted' else 'WRONG_PROJECT'
    w['waiver'] = h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': code,
                                      'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                                      'acceptance_document_id': w['docs']['waiver_ok']['id'],
                                      'recorded_by': 'clerk:' + STAFF_1, 'reason': 'accepted in writing',
                                      'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=60))})
    w['review'] = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']
    w['confirmation'] = h.ok(h.get('/crt_confirmation', query_string={
        'where': json.dumps({'record_id': w['pol']['gl']['id']})}))['records'][0]
    w['other_account'] = h.mk('contact', {'name': f'Other Builders {n} LLC', 'type': 'commercial'})
    return w


def _rid(w, t):
    return {'company': w['company']['id'], 'account': w['account']['id'], 'party': w['party']['id'],
            'link': w['link']['id'], 'project': w['project']['id'], 'assignment': w['asg']['id'],
            'set': w['set']['id'], 'policy': w['pol']['gl']['id'], 'endorsement': w['end']['ai_completed']['id'],
            'doc_cert': w['docs']['cert']['id'], 'doc_endorsement': w['docs']['staff_e']['id'],
            'confirmation': w['confirmation']['id'], 'waiver': w['waiver']['id'], 'review': w['review']['id']}[t]


def _snapshot(h, w):
    h.staff()
    out = {}
    for t in TARGETS:
        r = h.c.get(f'{B}/{COLL[t]}/{_rid(w, t)}', headers=h.h, base_url=h.base).get_json()
        out[t] = (json.dumps(r.get('data'), sort_keys=True), r.get('archived_at'))
    return out


def _verdict(h, w):
    ev = h.state(w['asg']['id'])
    return ev, {k: ev[k] for k in ('state', 'codes', 'items', 'accepted', 'label')}


class Actor:
    """The agent key, or a canvas page's bridged Clerk JWT replayed from the agent's own client (tenant origin)."""

    def __init__(self, h, kind):
        self.h, self.kind = h, kind
        self.c = h.app.test_client()

    def hdr(self, extra=None):
        base = {'X-Agent-Key': SBX_KEY} if self.kind == 'agent_key' else \
            {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX}
        return {**base, **(extra or {})}

    def body(self, b):
        return dict(b, source=SRC) if self.kind == 'agent_key' and isinstance(b, dict) and 'source' not in b else b

    def post(self, path, b=None):
        self.h.staff()                                   # the bridged JWT is a staff member's (mocked verifier)
        return self.c.post(B + path, json=self.body(b if b is not None else {}), headers=self.hdr())

    def patch(self, path, b, version):
        self.h.staff()
        return self.c.patch(B + path, json=self.body(b), headers=self.hdr({'If-Match': str(version)}))

    def put(self, path, b):
        self.h.staff()
        return self.c.put(B + path, json=self.body(b), headers=self.hdr())

    def upload(self, blob):
        self.h.staff()
        data = {'file': (io.BytesIO(blob), 'x.pdf')}
        if self.kind == 'agent_key':
            data['source'] = json.dumps(SRC)
        return self.c.post(B + '/files', data=data, headers=self.hdr(), content_type='multipart/form-data')


def _get(h, coll, rid):
    h.staff()
    return h.ok(h.get(f'/{coll}/{rid}'))


EDIT = {'company': {'name': 'Contractors Choice Agency Sandbox Two'}, 'account': {'name': 'Acme Builders Two LLC'},
        'party': {'name': 'Renamed Electric Inc'}, 'link': {'status': 'ended'},
        'project': {'contract_ai_parties': ['Mesa Health District', 'Anyone LLC']},
        'assignment': {'trade_category': 'plumbing'}, 'set': {'name': 'renamed set'},
        'policy': {'limits': {'each_occurrence': 9_000_000, 'general_aggregate': 9_000_000}},
        'endorsement': {'scheduled_projects': ['Tempe Warehouse Retrofit']},
        'confirmation': {'note': 'x'}, 'waiver': {'expires': iso(TODAY + timedelta(days=365))},
        'review': {'state': 'meets'}}


def _act(h, w, verb, t, actor, n):
    """Run the hostile mutation. Returns (status of the actor's decisive request, note)."""
    a = Actor(h, actor)
    coll = COLL.get(t)
    if verb == 'edit':
        cur = _get(h, coll, _rid(w, t))
        data = EDIT.get(t)
        if t in ('doc_cert', 'doc_endorsement'):                 # re-point the paper at other bytes
            up = a.upload(pdf(f'other-bytes-{n}-{t}'))
            if up.status_code in REFUSED:
                return up.status_code, 'upload refused'
            data = {'file_url': up.get_json()['url']}
        r = a.patch(f'/{coll}/{cur["id"]}', {'data': data}, cur['version'])
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'link':
        cur = _get(h, coll, _rid(w, t))
        to = {'link': w['other_account']['id'], 'project': w['docs']['cert2']['id'],
              'assignment': None, 'policy': w['docs']['cert2']['id'], 'endorsement': w['docs']['e_other']['id'],
              'doc_cert': w['account']['id'], 'doc_endorsement': w['account']['id'],
              'waiver': w['docs']['e_other']['id'], 'review': w['asg']['id'], 'confirmation': w['pol']['umbrella']['id']}[t]
        if t == 'assignment':                                    # re-pin to a DRAFT revision of the set
            h.agent()
            to = h.ok(h.post(f'/crt/requirement-sets/{w["set"]["id"]}/revise', {}), 201)['record']['id']
        r = a.patch(f'/{coll}/{cur["id"]}', {'data': {LINKS[t]: to}}, cur['version'])
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'archive':
        r = a.post(f'/{coll}/{_rid(w, t)}/archive')
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'unarchive':
        h.staff()
        s = h.post(f'/{coll}/{_rid(w, t)}/archive')               # staff archive it on the staff surface first
        r = a.post(f'/{coll}/{_rid(w, t)}/unarchive')
        return r.status_code, f'staff archive {s.status_code}; ' + str((r.get_json() or {}).get('code'))
    if verb == 'create':
        if t == 'company':
            r = a.post('/company', {'data': {'name': 'Contractors Choice Agency Sandbox'}})
        elif t in ('account', 'party', 'project', 'doc_cert', 'doc_endorsement'):
            # create a look-alike, then point the record the verdict reads at it (the step that would make it count)
            if t in ('account', 'party'):
                r = a.post('/contact', {'data': {'name': 'Look Alike LLC', 'type': 'commercial'}})
            elif t == 'project':
                r = a.post('/crt_project', {'data': {'crt_account_id': w['account']['id'], 'name': 'Mesa Clinic Expansion'}})
            else:
                up = a.upload(pdf(f'lookalike-{n}-{t}'))
                if up.status_code in REFUSED:
                    return up.status_code, 'upload refused'
                r = a.post('/document', {'data': {'title': 'x', 'file_url': up.get_json()['url'], 'kind':
                                                  'coi_acord25' if t == 'doc_cert' else 'endorsement',
                                                  'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']}})
            if r.status_code in REFUSED:
                return r.status_code, (r.get_json() or {}).get('code')
            new = r.get_json()['id']
            src, field = {'account': ('project', 'crt_account_id'), 'party': ('assignment', 'party_contact_id'),
                          'project': ('assignment', 'crt_project_id'), 'doc_cert': ('policy', 'source_document_id'),
                          'doc_endorsement': ('endorsement', 'document_id')}[t]
            cur = _get(h, COLL[src], _rid(w, src))
            r = a.patch(f'/{COLL[src]}/{cur["id"]}', {'data': {field: new}}, cur['version'])
        elif t == 'link':
            r = a.post('/crt_account_party', {'data': {'crt_account_id': w['account']['id'],
                                                       'party_contact_id': w['party']['id'], 'status': 'active'}})
        elif t == 'assignment':
            r = a.post('/crt_assignment', {'data': {'crt_project_id': w['project']['id'],
                                                    'party_contact_id': w['party']['id'],
                                                    'requirement_set_id': w['set']['id'],
                                                    'requirement_set_version': w['set']['version'],
                                                    'trade_category': 'electrical'}})
        elif t == 'set':
            s = _get(h, 'crt_requirement_set', w['set']['id'])
            items = [{k: v for k, v in it.items() if k != 'forms'} if it.get('kind') == 'endorsement' else it
                     for it in s['data']['items']]
            r = a.post('/crt_requirement_set', {'data': {'name': 'agent set', 'trade_category': 'mep', 'items': items,
                                                         'form_functions': dict(s['data']['form_functions'],
                                                                                **{'CG 20 33': 'ai_completed'})}})
            if r.status_code in REFUSED:
                return r.status_code, (r.get_json() or {}).get('code')
            _staff_adopts(h, w, r.get_json())
        elif t == 'policy':
            r = a.post('/crt_policy', {'data': {'crt_account_id': w['account']['id'], 'party_contact_id': w['party']['id'],
                                                'line': 'gl', 'policy_number': 'GL-NEW', 'carrier': 'Sample Mutual',
                                                'effective': iso(TODAY - timedelta(days=10)),
                                                'expiration': iso(TODAY + timedelta(days=700)),
                                                'limits': {'each_occurrence': 9_000_000, 'general_aggregate': 9_000_000},
                                                'named_insured': w['party']['data']['name'], 'addl_insd': True,
                                                'source_document_id': w['docs']['cert']['id']}})
        elif t == 'endorsement':
            r = a.post('/crt_endorsement', {'data': {'crt_account_id': w['account']['id'],
                                                     'crt_policy_id': w['pol']['gl']['id'], 'function': 'ai_completed',
                                                     'form': 'CG 20 37', 'edition': '04 13',
                                                     'document_id': w['docs']['e_other']['id'],
                                                     'scheduled_names': ['Acme Builders LLC'],
                                                     'scheduled_projects': ['Mesa Clinic Expansion'],
                                                     'blanket': False, 'manuscript': False}})
        elif t == 'confirmation':
            r = a.post('/crt_confirmation', {'data': {'record_collection': 'crt_policy', 'record_id': w['pol']['gl']['id'],
                                                      'record_version': 1, 'fields': ['line']}})
        elif t == 'waiver':
            r = a.post('/crt_waiver', {'data': {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PROJECT',
                                                'accepted_by_contractor': 'x', 'acceptance_document_id':
                                                w['docs']['waiver_ok']['id'], 'expires': iso(TODAY + timedelta(days=9))}})
        elif t == 'review':
            r = a.post('/crt_review', {'data': {'crt_assignment_id': w['asg']['id'], 'state': 'meets'}})
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'flags_confirm':
        r = a.post(f'/{coll}/{_rid(w, t)}/confirm', {'fields': ['name']})
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'confirm':
        cur = _get(h, coll, _rid(w, t))
        r = a.post('/crt/confirm', {'record_collection': coll, 'record_id': cur['id'], 'record_version': cur['version'],
                                    'fields': ['name'] if coll in ('contact', 'company') else ['crt_account_id'],
                                    'source_document_id': w['docs']['cert']['id']})
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'revise':
        s = _get(h, 'crt_requirement_set', w['set']['id'])
        items = [{k: v for k, v in it.items() if k != 'forms'} if it['id'] == 'ai_completed' else it
                 for it in s['data']['items']]
        r = a.post(f'/crt/requirement-sets/{s["id"]}/revise',
                   {'items': items, 'form_functions': dict(s['data']['form_functions'], **{'CG 20 33': 'ai_completed'})})
        if r.status_code in REFUSED:
            return r.status_code, (r.get_json() or {}).get('code')
        _staff_adopts(h, w, r.get_json()['record'])
        return r.status_code, 'staff approved what the page showed and re-pinned'
    if verb == 'approve':
        h.agent()
        new = h.ok(h.post(f'/crt/requirement-sets/{w["set"]["id"]}/revise', {}), 201)['record']
        r = a.post(f'/crt/requirement-sets/{new["id"]}/approve')
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'draft':
        r = a.post('/crt/requirement-sets/from-template', {'template_key': 'mep'})
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'review':
        r = a.post(f'/crt/assignment/{w["asg"]["id"]}/review')
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'chase':
        r = a.post(f'/crt/assignment/{w["asg"]["id"]}/chase-sent', {'stage': 'T-30'})
        return r.status_code, (r.get_json() or {}).get('code')
    if verb == 'upload':
        r = a.upload(pdf(f'loose-{n}'))
        return r.status_code, None
    if verb == 'counter':
        r = a.post('/counters/matrix/next', {'prefix': 'M-', 'pad': 3})
    elif verb == 'pack_enable':
        r = a.post('/packs/risk-transfer/enable')
    elif verb == 'page_register':
        _page_file(h, f'matrix-{n}.html')
        r = a.post('/registry/pages', {'file': f'matrix-{n}.html', 'label': 'Matrix', 'section': 'home'})
    elif verb in ('page_patch', 'page_archive', 'page_unarchive'):
        _page_file(h, f'matrix-staff-{n}.html')
        h.staff()
        reg = h.post('/registry/pages', {'file': f'matrix-staff-{n}.html', 'label': 'M', 'section': 'home'})
        pid = (reg.get_json() or {}).get('id') or ((reg.get_json() or {}).get('page') or {}).get('id')
        if not pid:
            return None, f'no page to act on (staff register {reg.status_code})'
        if verb == 'page_patch':
            r = a.patch(f'/registry/pages/{pid}', {'label': 'X'}, 1)
        else:
            r = a.post(f'/registry/pages/{pid}/{"archive" if verb == "page_archive" else "unarchive"}')
    elif verb == 'schema_custom':
        r = a.post('/schema/custom', {'collection': 'crt_policy', 'fields': {'x_matrix_note': {'type': 'text'}}})
    elif verb == 'schema_adopt':
        r = a.post('/schema/adopt', {'collection': 'contact', 'key': 'nope'})
    elif verb == 'dev_page':
        r = a.put(f'/dev/pages/matrix-{n}.html', {'html': '<!doctype html><title>x</title>'})
    else:
        raise AssertionError(f'unclassified verb {verb}')
    return r.status_code, (r.get_json() or {}).get('code') if r.is_json else None


def _page_file(h, name):
    """A page file in the sandbox (the sandbox key publishes it), so the registry routes act on a real page."""
    r = Actor(h, 'agent_key').put(f'/dev/pages/{name}', {'html': '<!doctype html><title>m</title><p>m</p>'})
    assert r.status_code in (200, 201), r.get_json()


def _staff_adopts(h, w, new):
    """The normal staff step after a revision: open the approval page (the diff), approve, re-pin the assignment."""
    h.staff()
    d = h.get(f'/crt/requirement-sets/{new["id"]}/diff')
    body = {'diff_sha256': d.get_json()['sha256']} if d.status_code == 200 else {}
    ap = h.post(f'/crt/requirement-sets/{new["id"]}/approve', body)
    if ap.status_code != 200:
        return
    rec = ap.get_json()['record']
    a = _get(h, 'crt_assignment', w['asg']['id'])
    h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': rec['id'],
                                                    'requirement_set_version': rec['version']}}, a['version'])


@pytest.mark.parametrize('ep,verb,target,actor,wk', CELLS,
                         ids=[f'{v}-{t}-{a}-{w}-{e.split(".", 1)[1]}' for e, v, t, a, w in CELLS])
def test_route_x_record_matrix(h, ep, verb, target, actor, wk):
    n = zlib.crc32('|'.join((ep, verb, target, actor, wk)).encode()) % 10 ** 8
    w = _base(h, wk, n)
    before_ev, before = _verdict(h, w)
    assert before_ev['state'] == ('meets' if wk == 'plain' else 'meets_accepted'), before_ev     # the staff-built world
    snap = _snapshot(h, w)
    status, note = _act(h, w, verb, target, actor, n)
    ev, after = _verdict(h, w)
    if status in REFUSED:
        res = f'refused {status}' + (f' {note}' if isinstance(note, str) and re.fullmatch(r'[a-z_]+', note) else '')
    elif ev['state'] not in MEETS:
        res = ev['state']
    elif verb in INERT_OK and after == before and _snapshot(h, w) == snap:
        res = 'inert'
    else:
        res = f'FALSE {ev["state"]} ({status})'
    RESULTS[(ep, verb, target, actor, wk)] = res
    assert not res.startswith('FALSE'), {'cell': (ep, verb, target, actor, wk), 'status': status, 'note': note,
                                         'state': ev['state'], 'codes': codes(ev)}
