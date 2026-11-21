"""Route x record matrix (host ruling SYSTEMIC, rules 1.5.0; extended in rules 1.6.0): ONE generated test over every
state-changing route the agent key or a canvas-origin JWT can reach, applied to every record type the verdict or the
watcher reads.

Rules 1.6.0 (re-check #6 found D/M because the 1.5.0 matrix tried only single-step, floor-breaking mutations, and
missed records). Added here:
  * RECORDS FROM THE CODE: the collections (and document kinds, and per-collection fields) the verdict and the watcher
    actually read are traced at run time (store and watcher-store calls wrapped) and scanned statically (AST of the
    engine and the watcher). The test FAILS when the code reads a collection or document kind the matrix does not
    list. New targets: the waiver's written OK, the contract document, a newer (superseding) approved set, the activity
    log and the private file store.
  * EVERY ROUTE, BY SIDE EFFECT: every rule of the full server app (Flask and websocket routes, all prefixes) is
    classified from its code (business-store write / business-store read / runs a process / touches a path the caller
    names / writes its own files / none). The test FAILS on an unclassified route or a class that no longer matches the
    code. Business-store reads are run and must leave the store byte-identical.
  * SEQUENCES AND FLOOR-RESPECTING LOOSENING: multi-step chains (revise then revise, a decoy then a revision of it,
    archive then unarchive) and loosening that respects the floor (a minimum lowered but kept above the floor, a null
    minimum, a key swap, a lowered floor). After the normal staff step (approve what the page showed; re-pin only when
    the verdict says a newer set is available), a Meets is FALSE unless every changed key was on the approval page
    against the APPROVED pinned set, with its true before and after, and the spec allows it.
  * FIELDS: every field the verdict reads on the plain world's records is written by each actor; an accepted write
    that leaves the verdict a Meets is FALSE unless the field is listed as display-only.

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
TARGETS = ('company', 'account', 'party', 'link', 'project', 'assignment', 'set', 'set_newer', 'policy', 'endorsement',
           'doc_cert', 'doc_endorsement', 'doc_waiver_ok', 'doc_contract', 'confirmation', 'waiver', 'review')
COLL = {'company': 'company', 'account': 'contact', 'party': 'contact', 'link': 'crt_account_party',
        'project': 'crt_project', 'assignment': 'crt_assignment', 'set': 'crt_requirement_set',
        'set_newer': 'crt_requirement_set', 'policy': 'crt_policy', 'endorsement': 'crt_endorsement',
        'doc_cert': 'document', 'doc_endorsement': 'document', 'doc_waiver_ok': 'document', 'doc_contract': 'document',
        'confirmation': 'crt_confirmation', 'waiver': 'crt_waiver', 'review': 'crt_review'}
# record kinds that are not rows of `records`: the activity log (per-field provenance, archive stamps) and the private
# file store (bytes + hash); routes reach them only through the record verbs, a chase mark and an upload
NON_RECORD_TARGETS = ('activity', 'file')
# what the code reads -> the matrix targets that stand for it (test_matrix_lists_every_record_the_code_reads)
READ_MAP = {'company': ('company',), 'contact': ('account', 'party'), 'crt_account_party': ('link',),
            'crt_project': ('project',), 'crt_assignment': ('assignment',), 'crt_requirement_set': ('set', 'set_newer'),
            'crt_policy': ('policy',), 'crt_endorsement': ('endorsement',), 'crt_confirmation': ('confirmation',),
            'crt_waiver': ('waiver',), 'crt_review': ('review',), 'activity': ('activity',), 'files': ('file',),
            'document': ('doc_cert', 'doc_endorsement', 'doc_waiver_ok', 'doc_contract')}
DOC_KIND_TARGET = {'coi_acord25': 'doc_cert', 'endorsement': 'doc_endorsement', 'contractor_waiver_ok': 'doc_waiver_ok',
                   'contract_excerpt': 'doc_contract'}
LINKS = {'link': 'crt_account_id', 'project': 'contract_document_id', 'assignment': 'requirement_set_id',
         'policy': 'source_document_id', 'endorsement': 'document_id', 'doc_cert': 'contact_id',
         'doc_endorsement': 'contact_id', 'doc_waiver_ok': 'contact_id', 'doc_contract': 'contact_id',
         'waiver': 'acceptance_document_id', 'review': 'crt_assignment_id', 'confirmation': 'record_id'}
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
    'biz.crt_chase_sent': (('chase',), ('assignment', 'activity')),
    'biz.file_upload': (('upload',), ('file',)),
    'biz.counter_next': (('counter',), None),
    'biz.packs_enable': (('pack_enable',), None),
    'biz.registry_register': (('page_register',), None),
    'biz.registry_patch': (('page_patch',), None),
    'biz.registry_archive': (('page_archive',), None),
    'biz.registry_unarchive': (('page_unarchive',), None),
    'biz.schema_custom': (('schema_custom',), None),
    'biz.schema_adopt': (('schema_adopt',), None),
    'biz.dev_page_put': (('dev_page',), None),
    # the migration importer (PR #616): owner only, a dry run unless ?dry_run=0; it creates only core collections
    # (contact, job, cost_entry, document, invoice, person, product, stock_move) through BizStore.create
    'biz.import_sources': (('import',), None),
}
# routes that by construction write nothing the verdict reads: accepted only if the verdict AND every target record
# stay byte-identical (checked, never assumed)
INERT_OK = {'draft', 'upload', 'counter', 'pack_enable', 'page_register', 'page_patch', 'page_archive',
            'page_unarchive', 'schema_custom', 'schema_adopt', 'dev_page', 'flags_confirm', 'import'}


def _cells():
    out = []
    for ep, (verbs, targets) in sorted(ROUTES.items()):
        for verb in verbs:
            for t in (targets or ('-',)):
                if verb == 'link' and t not in LINKS:
                    continue
                for actor in ACTORS:
                    for wk in (WORLDS if verb in ('create', 'edit', 'archive', 'unarchive', 'link') else ('plain',)):
                        if t == 'doc_waiver_ok' and wk == 'plain' and len(WORLDS) > 1:
                            continue        # no waiver is in force in the plain world: nothing reads that paper
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
    # a batch / import / bulk route must be classified here by name: the importer is the only one (PR #616)
    assert [e for e in found if re.search(r'batch|import|bulk', e)] == ['biz.import_sources']
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
    # rules 1.6.0: a newer APPROVED revision of the pinned set (the label reads it), made and approved by staff
    newer = h.ok(h.post(f'/crt/requirement-sets/{w["set"]["id"]}/revise', {}), 201)['record']
    w['set_newer'] = h.ok(h.approve(newer['id']))['record']
    w['review'] = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']
    w['confirmation'] = h.ok(h.get('/crt_confirmation', query_string={
        'where': json.dumps({'record_id': w['pol']['gl']['id']})}))['records'][0]
    w['other_account'] = h.mk('contact', {'name': f'Other Builders {n} LLC', 'type': 'commercial'})
    return w


def _rid(w, t):
    return {'company': w['company']['id'], 'account': w['account']['id'], 'party': w['party']['id'],
            'link': w['link']['id'], 'project': w['project']['id'], 'assignment': w['asg']['id'],
            'set': w['set']['id'], 'set_newer': w['set_newer']['id'], 'policy': w['pol']['gl']['id'],
            'endorsement': w['end']['ai_completed']['id'], 'doc_cert': w['docs']['cert']['id'],
            'doc_endorsement': w['docs']['staff_e']['id'], 'doc_waiver_ok': w['docs']['waiver_ok']['id'],
            'doc_contract': w['docs']['contract']['id'],
            'confirmation': w['confirmation']['id'], 'waiver': w['waiver']['id'], 'review': w['review']['id']}[t]


def _snapshot(h, w):
    h.staff()
    out = {}
    store = h.app.extensions['biz']['sandbox:josh']
    for t in TARGETS:
        r = h.c.get(f'{B}/{COLL[t]}/{_rid(w, t)}', headers=h.h, base_url=h.base).get_json()
        out[t] = (json.dumps(r.get('data'), sort_keys=True), r.get('archived_at'))
    # the activity rows the verdict reads (created / updated / archived / unarchived of every target) and the files
    out['activity'] = json.dumps([[(e.get('type'), e.get('by'), e.get('diff')) for e in store.timeline(COLL[t], _rid(w, t))
                                   if e.get('type') in ('created', 'updated', 'archived', 'unarchived')]
                                  for t in TARGETS], sort_keys=True, default=str)
    fids = [json.loads(out[t][0]).get('file_url', '')[len('biz-file:'):] for t in TARGETS if COLL[t] == 'document']
    out['file'] = json.dumps(sorted((f, store.file_verified(f)) for f in fids))
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
        'assignment': {'trade_category': 'plumbing'}, 'set': {'name': 'renamed set'}, 'set_newer': {'name': 'renamed'},
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
        if coll == 'document':                                   # re-point the paper at other bytes
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
              'doc_waiver_ok': w['other_account']['id'], 'doc_contract': w['other_account']['id'],
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
        elif t in ('account', 'party', 'project', 'doc_cert', 'doc_endorsement', 'doc_waiver_ok', 'doc_contract'):
            # create a look-alike, then point the record the verdict reads at it (the step that would make it count)
            if t in ('account', 'party'):
                r = a.post('/contact', {'data': {'name': 'Look Alike LLC', 'type': 'commercial'}})
            elif t == 'project':
                r = a.post('/crt_project', {'data': {'crt_account_id': w['account']['id'], 'name': 'Mesa Clinic Expansion'}})
            else:
                up = a.upload(pdf(f'lookalike-{n}-{t}'))
                if up.status_code in REFUSED:
                    return up.status_code, 'upload refused'
                kind = {v: k for k, v in DOC_KIND_TARGET.items()}[t]
                r = a.post('/document', {'data': {'title': 'x', 'file_url': up.get_json()['url'], 'kind': kind,
                                                  'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']}})
            if r.status_code in REFUSED:
                return r.status_code, (r.get_json() or {}).get('code')
            new = r.get_json()['id']
            src, field = {'account': ('project', 'crt_account_id'), 'party': ('assignment', 'party_contact_id'),
                          'project': ('assignment', 'crt_project_id'), 'doc_cert': ('policy', 'source_document_id'),
                          'doc_endorsement': ('endorsement', 'document_id'),
                          'doc_waiver_ok': ('waiver', 'acceptance_document_id'),
                          'doc_contract': ('project', 'contract_document_id')}[t]
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
        elif t == 'set_newer':
            # a decoy "newer" set: a generic create naming the pinned set as the one it replaces (ruling D)
            s = _get(h, 'crt_requirement_set', w['set']['id'])
            r = a.post('/crt_requirement_set', {'data': {'name': s['data']['name'], 'trade_category': 'mep',
                                                         'items': s['data']['items'], 'supersedes': s['id'],
                                                         'set_version': 9}})
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
    elif verb == 'import':
        # hostile: a migration bundle written for real (dry_run=0) that names the party and files paper against it
        party = (w['party'].get('data') or {}).get('name') or f'Matrix {n} Inc'
        r = a.post('/import?dry_run=0', {'sources': {'openclaw/workspace/business/jobs.json': {'jobs': [
            {'id': f'matrix-{n}', 'customer': party, 'status': 'completed', 'revenue_billed': 1.0,
             'costs': {'lump_sum_cost': 1.0}, 'photo_url': '/uploads/matrix.jpg', 'invoice': f'M{n}'}]}}})
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


# ═══════════════════ rules 1.6.0 (a): the records the code reads, enumerated FROM the code ═══════════════════
class _Traced(dict):
    """A record's data that remembers every key the reader asked for."""

    def __init__(self, coll, data, seen):
        super().__init__(data)
        self._c, self._s = coll, seen

    def get(self, k, *a):
        self._s.add((self._c, k))
        return super().get(k, *a)

    def __getitem__(self, k):
        self._s.add((self._c, k))
        return super().__getitem__(k)

    def __contains__(self, k):
        self._s.add((self._c, k))
        return super().__contains__(k)


class _Trace:
    """Wraps the business store and the watcher's store while the verdict / the watcher run: collections, document
    kinds and (collection, field) pairs actually read."""

    def __init__(self):
        self.colls, self.fields, self.kinds = set(), set(), set()

    def _wrap(self, coll, rec):
        if isinstance(rec, dict) and isinstance(rec.get('data'), dict) and not isinstance(rec['data'], _Traced):
            if coll == 'document':
                self.kinds.add(rec['data'].get('kind'))
            rec = dict(rec, data=_Traced(coll, rec['data'], self.fields))
        return rec

    def store(self, mp, store):
        orig_get, orig_list, orig_tl = store.get, store.list, store.timeline
        orig_fv, orig_fm = store.file_verified, store.file_meta
        t = self

        def get(collection, rid):
            t.colls.add(collection)
            return t._wrap(collection, orig_get(collection, rid))

        def lst(collection, **kw):
            t.colls.add(collection)
            page = orig_list(collection, **kw)
            return dict(page, records=[t._wrap(collection, r) for r in page['records']])

        def tl(collection, rid):
            t.colls.add('activity')
            return orig_tl(collection, rid)

        def fv(fid):
            t.colls.add('files')
            return orig_fv(fid)

        def fm(fid):
            t.colls.add('files')
            return orig_fm(fid)
        for name, fn in (('get', get), ('list', lst), ('timeline', tl), ('file_verified', fv), ('file_meta', fm)):
            mp.setattr(store, name, fn)

    def watcher(self, mp, mod):
        t = self
        S = mod.Store
        for name in ('all', 'all_ever'):
            orig = getattr(S, name)

            def f(self_, coll, _o=orig):
                t.colls.add(coll)
                return [t._wrap(coll, r) for r in _o(self_, coll)]
            mp.setattr(S, name, f)
        for name in ('get', 'get_any'):
            orig = getattr(S, name)

            def f(self_, coll, rid, _o=orig):
                t.colls.add(coll)
                return t._wrap(coll, _o(self_, coll, rid))
            mp.setattr(S, name, f)
        orig_ok = S.file_ok

        def fok(self_, sha):
            t.colls.add('files')
            return orig_ok(self_, sha)
        mp.setattr(S, 'file_ok', fok)
        orig_wrote = S.wrote

        def wrote(self_, rec, field):
            t.colls.add('activity')
            return orig_wrote(self_, rec, field)
        mp.setattr(S, 'wrote', wrote)


def _static_reads():
    """Collections named as string literals in the engine's and the watcher's read calls (AST scan)."""
    import ast
    out = set()
    eng = (ROOT / 'services/biz/packs/risk_transfer.py').read_text(encoding='utf-8')
    wat = (ROOT / 'tools/biz/crt-verdict-watch.py').read_text(encoding='utf-8')
    # (function name, receiver or None for a plain call) -> index of the collection argument
    for src, calls in ((eng, {('_all', None): 1, ('_get', None): 1, ('get', 'store'): 0, ('list', 'store'): 0,
                              ('create', 'store'): 0, ('update', 'store'): 0}),
                       (wat, {('all', 'st'): 0, ('all_ever', 'st'): 0, ('get', 'st'): 0, ('get_any', 'st'): 0,
                              ('all', 'self'): 0, ('get', 'self'): 0})):
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                key = (node.func.attr, getattr(node.func.value, 'id', None))
            else:
                key = (getattr(node.func, 'id', None), None)
            if key not in calls:
                continue
            i = calls[key]
            if len(node.args) > i and isinstance(node.args[i], ast.Constant) and isinstance(node.args[i].value, str):
                out.add(node.args[i].value)
    if 'timeline(' in eng or 'FROM activity' in wat:
        out.add('activity')
    if 'file_verified(' in eng or "'files'" in wat:
        out.add('files')
    if "'document'" in eng or "'document'" in wat:
        out.add('document')
    return out


def _rich_world(h):
    """One world that walks most read paths: accepted variance after substantial completion, a waiver, a review,
    a newer approved set, a combined-test template on a second assignment."""
    w = _base(h, 'accepted', 1606)
    w2 = world(h, template='high_severity', trade='steel_erection', party_name='Trace Steel Inc',
               policies={'gl': {'line': 'gl', 'policy_number': 'GL-T', 'limits': {'each_occurrence': 1_000_000,
                                                                                   'general_aggregate': 2_000_000}},
                         'umbrella': {'line': 'umbrella', 'policy_number': 'UM-T', 'limits': {'each_occurrence': 6_000_000}},
                         'auto': {'line': 'auto', 'policy_number': 'AU-T', 'addl_insd': False,
                                  'limits': {'combined_single_limit': 1_000_000}}})
    return w, w2


def test_matrix_lists_every_record_the_code_reads(h, dirs, monkeypatch):
    from services.biz.packs import risk_transfer as rt
    from tests.test_biz_crt_tools import _load, _db
    w, w2 = _rich_world(h)
    h.staff()
    h.ok(h.post(f'/crt/assignment/{w2["asg"]["id"]}/review'), 201)
    store = h.app.extensions['biz']['sandbox:josh']
    tr = _Trace()
    with monkeypatch.context() as mp:
        tr.store(mp, store)
        for aid in (w['asg']['id'], w2['asg']['id']):
            rt.evaluate(store, aid, TODAY)
        rt.set_diff(store, store.get('crt_requirement_set', w['set_newer']['id']))
        mod = _load('crt-verdict-watch')
        tr.watcher(mp, mod)
        mod.watch(_db(dirs))
    read = tr.colls | _static_reads()
    print('READ collections', sorted(read))
    print('READ document kinds', sorted(k for k in tr.kinds if k))
    print('READ fields', len(tr.fields), sorted(f'{c}.{f}' for c, f in tr.fields))
    unlisted = sorted(c for c in read if c not in READ_MAP)
    assert unlisted == [], f'the verdict / watcher read collections the matrix does not list: {unlisted}'
    kinds = sorted(k for k in tr.kinds if k and k not in DOC_KIND_TARGET)
    assert kinds == [], f'the verdict / watcher read document kinds the matrix does not list: {kinds}'
    for c, targets in READ_MAP.items():
        for t in targets:
            assert t in TARGETS or t in NON_RECORD_TARGETS, (c, t)
    # every record target is acted on by at least one route cell
    covered = {t for (_ep, _v, t, _a, _w) in CELLS}
    assert set(TARGETS) | set(NON_RECORD_TARGETS) <= covered, sorted((set(TARGETS) | set(NON_RECORD_TARGETS)) - covered)
    # the activity rows the verdict reads are written ONLY by the record writes the matrix already drives
    src = (ROOT / 'services/biz/store.py').read_text(encoding='utf-8')
    for typ, fn in (('created', 'def create('), ('updated', 'def update('), ('archived', 'def set_archived(')):
        hits = [m.start() for m in re.finditer(rf"type_='{typ}'|'{typ}' if archived", src)]
        assert hits, typ
        owner = src.rfind('\n    def ', 0, hits[0])
        assert src[owner:].lstrip().startswith(fn.split('(')[0]), (typ, src[owner:owner + 60])
    eng = (ROOT / 'services/biz/packs/risk_transfer.py').read_text(encoding='utf-8')
    read_types = set(re.findall(r"get\('type'\) == '([a-z_]+)'", eng))
    for grp in re.findall(r"get\('type'\) in \(([^)]*)\)", eng):
        read_types |= set(re.findall(r"'([a-z_]+)'", grp))
    assert read_types == {'created', 'updated', 'archived', 'unarchived'}, read_types
    # the file store is written only by put_file, content-addressed (the id IS the sha256 of the bytes); the only
    # other function touching files_dir with a write primitive is backup_files, which writes under its mirror only
    import ast
    writers = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef):
            body = ast.get_source_segment(src, node) or ''
            if 'self.files_dir' in body and re.search(r"'wb'|os\.replace\(|shutil\.copy|write_bytes\(", body):
                writers[node.name] = body
    assert set(writers) == {'put_file', 'backup_files'}, sorted(writers)
    assert 'hashlib.sha256(blob)' in writers['put_file'] and 'dest = self.files_dir / fid' in writers['put_file']
    assert 'tmp = mirror /' in writers['backup_files'] and 'os.replace(tmp, dst)' in writers['backup_files']
    global FIELDS_READ
    FIELDS_READ = sorted(tr.fields)


FIELDS_READ = []


# ═══════════════════ rules 1.6.0 (b): every route of the full app, classified by side effect ═══════════════════
ROUTE_CLASSES_FILE = ROOT / 'tests' / 'crt_route_classes.json'
BIZ_MODULES = ('routes.biz', 'services.biz.packs.risk_transfer')
_BIZ_MARK = re.compile(r'\bget_store\(|\bBizStore\(|BIZ_DB_DIR|OVUI_DB_PATH|business\.db|staff-surface\.key|'
                       r'_surface_secret|\bservices\.biz\b|\broutes\.biz\b|biz_db_dir|biz_store')
_EXEC_MARK = re.compile(r'\bpty\.|\bsubprocess\.|\bos\.system\(|\bos\.exec\w*\(|\bPopen\(|\bos\.spawn\w*\(|'
                        r'\bos\.fork\(|create_subprocess_\w+\(')
_FS_MARK = re.compile(r'\bopen\(|send_file\(|send_from_directory\(|\bshutil\.|'
                      r'\bos\.(remove|unlink|rename|replace|makedirs|mkdir|chmod|listdir|scandir|walk|rmdir)\(|'
                      r'\.(write_text|write_bytes|read_text|read_bytes|unlink|mkdir|rename|iterdir|glob|rglob|rmdir)\(|'
                      # rules 1.7.0: PR #597's no-follow file access (services/safe_open.py, services/events_file.py)
                      # is file I/O too
                      r'\bappend_event\(|'
                      r'\bAnchored\(|\b(open|read|write|atomic_write|append|open_dir|lstat|scandir|chmod|rename|send_file)'
                      r'_beneath\(|\.(atomic_write_text|atomic_write_bytes|append_text|append_bytes)\(')
_REQ_MARK = re.compile(r'\brequest\.(json|args|form|files|get_json|values|data|view_args|headers)\b|\bws\.receive\(')
# re-check #6 platform findings (NOT this pack's: a separate platform PR). The classifier must see each one as a
# route that runs a process or touches a path the caller names (its positive control).
# Rules 1.7.0: PR #597 (merged into this branch, 44fd830) closes all four -- the terminal execs the setuid sandbox helper
# (another uid, fresh env), cover_path / story_id / plugin ids are confined -- and the routes still run a process or
# touch a path the caller names, so they stay the classifier's positive control.
PLATFORM_FINDINGS = {'terminal_websocket': 'T (closed by #597): /ws/terminal runs the sandbox helper, not the app uid',
                     'airadio.push_song': 'side audit (closed by #597): cover_path confined',
                     'story.generate_scene': 'side audit (closed by #597): story_id confined',
                     'plugins.uninstall': 'side audit (closed by #597): plugin_id confined before shutil.rmtree'}


def _route_source(fn):
    """The view's own source, the functions its closure holds (pack route wrappers), and the same-module functions it
    calls (one level): what the route does, read from the code."""
    import ast
    import inspect
    seen, out, todo = set(), [], [(inspect.unwrap(fn), 0)]
    while todo:
        f, depth = todo.pop()
        if id(f) in seen or not callable(f):
            continue
        seen.add(id(f))
        try:
            src = inspect.getsource(f)
        except (OSError, TypeError):
            continue
        out.append(src)
        for cell in (getattr(f, '__closure__', None) or ()):
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if inspect.isfunction(v):
                todo.append((v, depth))
        if depth >= 2:
            continue
        try:
            tree = ast.parse(inspect.cleandoc('\n' + src) if src.startswith((' ', '\t')) else src)
        except SyntaxError:
            continue
        g = getattr(f, '__globals__', {})

        def ours(c):                      # project code (not the stdlib, not a library): followed two levels deep
            m = getattr(c, '__module__', '') or ''
            return inspect.isfunction(c) and (m == f.__module__ or m.split('.')[0] in ('routes', 'services', 'server',
                                                                                       'app', 'plugins', 'tools'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = None
            if isinstance(node.func, ast.Name):
                callee = g.get(node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                owner = g.get(node.func.value.id)
                if inspect.ismodule(owner) and owner.__name__.split('.')[0] in ('routes', 'services', 'plugins'):
                    callee = getattr(owner, node.func.attr, None)
            if ours(callee):
                todo.append((callee, depth + 1))
    return '\n'.join(out)


def _classify(app, rule):
    """Side-effect class of one route, from its code (never from its HTTP method)."""
    import inspect
    fn = app.view_functions.get(rule.endpoint)
    mod = getattr(fn, '__module__', '') or ''
    if rule.endpoint in ROUTES:
        return 'biz-state'                     # writes the business store: a matrix route (checked to be in ROUTES)
    if mod in BIZ_MODULES:
        return 'biz-read'                      # reads the business store only: run below, must change nothing
    src = _route_source(fn) if fn else ''
    params = bool(rule.arguments)
    # read-only helpers of routes/biz.py other routes may call (page names a pack ships; the menu shell tags)
    helper = re.compile(r'^\s*from routes\.biz import (platform_page_names|shell_injection)\s*$', re.M)
    uses_helper = bool(helper.search(src))
    if _BIZ_MARK.search(helper.sub('', src)):
        return 'biz-other'                     # must never happen: only routes/biz.py opens the store
    if _EXEC_MARK.search(src):
        return 'exec'                          # runs a process / shell as the app uid
    if uses_helper:
        return 'biz-read-helper'               # reads the page registry through routes/biz.py, never writes it
    if _FS_MARK.search(src):
        return 'fs-caller-path' if (params or _REQ_MARK.search(src)) else 'fs-fixed'
    return 'no-fs'


def _full_app(monkeypatch, tmp):
    monkeypatch.setenv('BIZ_DB_DIR', str(tmp))
    # server.py runs load_plugins() at import, which mkdirs the hard-coded PLUGIN_DIR (/app/plugins). That exists only
    # in the OVU image / a worktree mounted at /app; on a CI runner it is PermissionError at import. Point it at an empty
    # tmp dir: plugin routes are excluded from this table anyway (plugin_* / booking.*), so the result is unchanged.
    import services.plugins as _plugins
    _pdir = tmp.parent / 'plugins'
    _pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_plugins, 'PLUGIN_DIR', _pdir)
    import server
    return server.app


def test_every_route_of_the_full_app_is_classified_by_side_effect(monkeypatch, tmp_path):
    app = _full_app(monkeypatch, tmp_path / 'db')
    got = {}
    for r in app.url_map.iter_rules():
        if r.endpoint == 'static':
            continue
        got[r.endpoint] = _classify(app, r)
    if os.getenv('CRT_ROUTE_CLASSES_WRITE'):
        ROUTE_CLASSES_FILE.write_text(json.dumps(
            {k: v for k, v in sorted(got.items()) if not k.startswith(('plugin_', 'booking.'))}, indent=1) + '\n',
            encoding='utf-8')
    table = json.loads(ROUTE_CLASSES_FILE.read_text(encoding='utf-8'))
    plugin = {k for k in got if k.startswith('plugin_') or '.' in k and app.view_functions[k].__module__.startswith('plugin_')}
    unclassified = sorted(k for k in got if k not in table and k not in plugin)
    assert unclassified == [], f'routes with no side-effect class (add them to {ROUTE_CLASSES_FILE.name}): {unclassified}'
    moved = sorted((k, table[k], got[k]) for k in got if k in table and table[k] != got[k])
    assert moved == [], f'routes whose code no longer matches their class: {moved}'
    gone = sorted(set(table) - set(got))
    assert gone == [], f'classified routes that no longer exist: {gone}'
    # only routes/biz.py (and the pack it loads) touch the business store; installed plugins neither
    assert sorted(k for k, v in got.items() if v == 'biz-other') == []
    assert {k for k, v in got.items() if v == 'biz-state'} == set(ROUTES)
    # the classifier's positive control: every known platform finding is seen as exec / caller-path
    for ep, why in PLATFORM_FINDINGS.items():
        assert got.get(ep) in ('exec', 'fs-caller-path'), (ep, why, got.get(ep))
    counts = {}
    for v in got.values():
        counts[v] = counts.get(v, 0) + 1
    print('ROUTES', len(got), 'by class', dict(sorted(counts.items())))
    print('ROUTES exec', sorted(k for k, v in got.items() if v == 'exec'))
    print('ROUTES platform findings', {k: v for k, v in PLATFORM_FINDINGS.items()})


def _db_fingerprint(dirs):
    import hashlib
    import sqlite3
    conn = sqlite3.connect(f"file:{dirs['a'] / 'sandboxes' / 'josh' / 'business.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute('SELECT id, data, version, archived_at FROM records ORDER BY id').fetchall()
        act = conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0]
    finally:
        conn.close()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest(), act


def test_every_business_store_read_route_changes_nothing(h, dirs):
    """Each biz-read route is RUN (agent key, staff surface) against a staff-built world: the business store's records
    and its activity log must be byte-identical afterwards (a side effect, not a method, decides the class)."""
    w = _base(h, 'plain', 1607)
    fill = {'collection': 'crt_policy', 'rid': w['pol']['gl']['id'], 'aid': w['asg']['id'], 'sid': w['set']['id'],
            'page': 'cca-crt-book.html', 'fid': w['docs']['cert']['data']['file_url'][len('biz-file:'):],
            'file_name': 'cca-crt-book.html', 'name': 'jb.js'}
    ran = []
    for r in h.app.url_map.iter_rules():
        fn = h.app.view_functions.get(r.endpoint)
        if getattr(fn, '__module__', '') not in BIZ_MODULES or r.endpoint in ROUTES or 'GET' not in r.methods:
            continue
        args = {a: fill.get(a, 'x') for a in r.arguments}
        if r.endpoint == 'biz.crt_matrix':
            args['pid'] = w['project']['id']
        elif r.endpoint == 'biz.crt_party':
            args['pid'] = w['party']['id']
        elif r.endpoint == 'biz.crt_account':
            args['aid'] = w['account']['id']
        elif 'pid' in r.arguments:
            args['pid'] = 'x'
        url = h.app.url_map.bind('localhost').build(r.endpoint, args)
        q = '?account=' + w['account']['id'] if r.endpoint == 'biz.crt_party' else ''
        for who in ('agent', 'staff'):
            before = _db_fingerprint(dirs)
            getattr(h, who)()
            resp = h.c.get(url + q, headers=h.h, base_url=h.base)
            after = _db_fingerprint(dirs)
            ran.append((r.endpoint, who, resp.status_code))
            assert after == before, (r.endpoint, who, resp.status_code)
    print('READ ROUTES RUN', len(ran), sorted({e for e, _w, _s in ran}))
    assert len({e for e, _w, _s in ran}) >= 25


# ═══════════════════ rules 1.6.0 (c): sequences and floor-respecting loosening ═══════════════════
M_ = 1_000_000
SEQ_RESULTS = {}


def _seq_world(h, kind, n):
    from tests.test_biz_crt_pack import _ends
    if kind == 'plain':
        return world(h, party_name=f'Seq {n} Inc')
    if kind == 'missing_completed':
        e = _ends()
        e.pop('ai_completed')
        return world(h, endorsements=e, party_name=f'Seq {n} Inc')
    if kind.startswith('lookalike_completed:'):  # rules 1.9.0 (L): the sub's completed-ops form, filed as it looks
        e = _ends()
        e['ai_completed'] = dict(e['ai_completed'], form=kind.split(':', 1)[1])
        return world(h, endorsements=e, party_name=f'Seq {n} Inc')
    if kind == 'old_edition_completed':        # rules 1.8.0 (E): the sub's CG 20 37 is the 07 04 edition
        e = _ends()
        e['ai_completed'] = dict(e['ai_completed'], edition='07 04')
        return world(h, endorsements=e, party_name=f'Seq {n} Inc')
    gl = {'low_gl': 250_000, 'raised_min': 1_500_000}[kind]
    w = world(h, party_name=f'Seq {n} Inc', policies={
        'gl': {'line': 'gl', 'policy_number': 'GL-S', 'limits': {'each_occurrence': gl, 'general_aggregate': 2 * M_}},
        'umbrella': {'line': 'umbrella', 'policy_number': 'UM-S', 'limits': {'each_occurrence': 5 * M_}}})
    if kind == 'raised_min':                   # staff raised the GL minimum to $2M (floor $1M) and re-pinned
        h.staff()
        s = _get(h, 'crt_requirement_set', w['set']['id'])
        items = [dict(it, min=2 * M_) if it['id'] == 'gl_occ' else it for it in s['data']['items']]
        new = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items}), 201)['record']
        new = h.ok(h.approve(new['id']))['record']
        a = _get(h, 'crt_assignment', w['asg']['id'])
        h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': new['id'],
                                                             'requirement_set_version': new['version']}}, a['version']))
        w['set'] = new
    return w


def _items(s, iid, change=None, drop=()):
    out = []
    for it in s['data']['items']:
        if it['id'] == iid:
            if change is None:
                continue
            it = {k: v for k, v in dict(it, **change).items() if k not in drop}
        out.append(it)
    return out


def _visible(d, pinned, now_set):
    """Every key that differs between the APPROVED pinned set and the set now pinned was on the approval page (the
    diff's rows, which the page renders whole: tests/js/crt-requirements-diff.test.js) with its true before / after,
    and the page compared against that approved pinned set. Computed here from the raw records."""
    ag = (d or {}).get('against') or {}
    if ag.get('id') != pinned['id'] or ag.get('approved') is not True:
        return False
    rows = {i['id']: {r['key']: r for r in (i.get('rows') or [])} for i in d.get('items') or []}
    was = {it['id']: it for it in pinned['data'].get('items') or []}
    now = {it['id']: it for it in now_set['data'].get('items') or []}
    for iid in set(was) | set(now):
        a, b = was.get(iid) or {}, now.get(iid) or {}
        for k in set(a) | set(b):
            if a.get(k) != b.get(k):
                r = rows.get(iid, {}).get(k)
                if not r or not r.get('changed') or r.get('was') != a.get(k) or r.get('now') != b.get(k):
                    return False
    # rules 1.7.0 (A): the page's own summary agrees with the verdict: every item's required-applied row says what the
    # engine reads (only false is suggested), and the page's item list says "Suggested" exactly then
    for iid, b in now.items():
        r = rows.get(iid, {}).get('required_applied')
        want = b.get('required') is not False if b.get('kind') == 'endorsement' else True
        if not r or r.get('now') is not want:
            return False
        # rules 1.8.0 (E): every endorsement item's row says the edition the verdict applies (the page lists it)
        if b.get('kind') == 'endorsement' and not rows.get(iid, {}).get('edition_applied'):
            return False
    ff = d.get('form_functions') or {}
    return ff.get('was') == (pinned['data'].get('form_functions') or {}) and \
        ff.get('now') == (now_set['data'].get('form_functions') or {})


def _staff_step(h, w, cand):
    """What staff do after a draft appears: open the approval page, approve exactly what it showed, and re-pin the
    assignment only when its verdict says this set is a newer requirement set (the label is the prompt)."""
    h.staff()
    d = h.get(f'/crt/requirement-sets/{cand["id"]}/diff')
    if d.status_code != 200:
        return None
    d = d.get_json()
    ap = h.post(f'/crt/requirement-sets/{cand["id"]}/approve', {'diff_sha256': d['sha256']})
    if ap.status_code != 200:
        return d
    rec = ap.get_json()['record']
    ev = h.state(w['asg']['id'])
    if (ev.get('newer_requirement_set') or {}).get('id') == rec['id']:
        a = _get(h, 'crt_assignment', w['asg']['id'])
        h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': rec['id'],
                                                        'requirement_set_version': rec['version']}}, a['version'])
    return d


def _revise_steps(a, sid, bodies):
    """The actor's revision chain: each body revises the previous draft. Returns (status, code, last record)."""
    cur = sid
    rec = None
    for body in bodies:
        r = a.post(f'/crt/requirement-sets/{cur}/revise', body)
        if r.status_code in REFUSED:
            return r.status_code, (r.get_json() or {}).get('code'), None
        rec = r.get_json()['record']
        cur = rec['id']
    return 201, None, rec


def _seq_revise(change_fn, second=({},)):
    def run(h, w, a):
        s = _get(h, 'crt_requirement_set', w['set']['id'])
        return _revise_steps(a, s['id'], [{'items': change_fn(s)}, *second])
    return run


def _seq_list_and_map(form, fn='ai_completed'):
    """rules 1.9.0 (L): one revision lists `form` for the item and maps it to the item's function in the set's table."""
    def run(h, w, a):
        s = _get(h, 'crt_requirement_set', w['set']['id'])
        items = [dict(it, forms=list(it.get('forms') or []) + [form]) if it['id'] == fn else it for it in s['data']['items']]
        return _revise_steps(a, s['id'], [{'items': items,
                                           'form_functions': dict(s['data'].get('form_functions') or {}, **{form: fn})}])
    return run


def _seq_decoy(h, w, a):
    s = _get(h, 'crt_requirement_set', w['set']['id'])
    r = a.post('/crt_requirement_set', {'data': {'name': s['data']['name'], 'trade_category': s['data']['trade_category'],
                                                 'items': _items(s, 'ai_completed'),
                                                 'form_functions': s['data']['form_functions']}})
    if r.status_code in REFUSED:
        return r.status_code, (r.get_json() or {}).get('code'), None
    return _revise_steps(a, r.get_json()['id'], [{}])


def _seq_then_staff(h, w, a):
    s = _get(h, 'crt_requirement_set', w['set']['id'])
    st, code, rec = _revise_steps(a, s['id'], [{'items': _items(s, 'ai_completed')}])
    if rec is None:
        return st, code, rec
    h.staff()                                  # staff make an honest edit on top of the agent's draft
    items = [dict(it, cite=(it.get('cite') or '') + ' (staff note)') if it['id'] == 'gl_occ' else it
             for it in rec['data']['items']]
    r = h.post(f'/crt/requirement-sets/{rec["id"]}/revise', {'items': items})
    return (r.status_code, (r.get_json() or {}).get('code'), None) if r.status_code in REFUSED else \
        (201, None, r.get_json()['record'])


# name -> (world, actor chain, expect): 'visible' = a Meets is allowed only when every change was shown against the
# approved pinned set; 'not_meets' = the spec says the change can never make this world pass (the floor applies)
SEQS = {
    'revise-revise-drop-completed-ops': ('missing_completed', _seq_revise(lambda s: _items(s, 'ai_completed')), 'visible'),
    'revise-3-steps-drop-completed-ops': ('missing_completed',
                                          _seq_revise(lambda s: _items(s, 'ai_completed'), ({}, {})), 'visible'),
    'revise-lower-min-above-floor': ('raised_min', _seq_revise(lambda s: _items(s, 'gl_occ', {'min': 1_200_000})),
                                     'visible'),
    'revise-min-null-above-floor': ('raised_min', _seq_revise(lambda s: _items(s, 'gl_occ', {'min': None})), 'visible'),
    'revise-min-null-below-floor': ('low_gl', _seq_revise(lambda s: _items(s, 'gl_occ', {'min': None})), 'not_meets'),
    'revise-min-missing-below-floor': ('low_gl', _seq_revise(lambda s: _items(s, 'gl_occ', {}, drop=('min',))),
                                       'not_meets'),
    'revise-min-zero': ('low_gl', _seq_revise(lambda s: _items(s, 'gl_occ', {'min': 0})), 'not_meets'),
    'revise-key-swap-limit': ('low_gl', _seq_revise(lambda s: _items(s, 'gl_occ', {'limit': 'general_aggregate'})),
                              'visible'),
    'revise-key-rename-min': ('low_gl', _seq_revise(lambda s: _items(s, 'gl_occ', {'minimum': 0}, drop=('min',))),
                              'not_meets'),
    'revise-lower-floor-and-min': ('low_gl', _seq_revise(lambda s: _items(s, 'gl_occ', {'min': 200_000,
                                                                                       'floor': 200_000})), 'visible'),
    # rules 1.7.0 (A): a falsy / non-boolean `required` never drops a required endorsement (only false is suggested)
    'revise-required-null': ('missing_completed', _seq_revise(lambda s: _items(s, 'ai_completed', {'required': None})),
                             'not_meets'),
    'revise-required-zero': ('missing_completed', _seq_revise(lambda s: _items(s, 'ai_completed', {'required': 0})),
                             'not_meets'),
    'revise-required-empty': ('missing_completed', _seq_revise(lambda s: _items(s, 'ai_completed', {'required': ''})),
                              'not_meets'),
    'revise-required-false': ('missing_completed', _seq_revise(lambda s: _items(s, 'ai_completed', {'required': False})),
                              'visible'),
    # rules 1.8.0 (E, H): the edition moved into the form list still binds; two editions or a look-alike are refused
    'revise-edition-into-form-list': ('old_edition_completed', _seq_revise(lambda s: _items(
        s, 'ai_completed', {'forms': ['CG 20 37 04 13']}, drop=('edition',))), 'not_meets'),
    'revise-edition-null-form-list': ('old_edition_completed', _seq_revise(lambda s: _items(
        s, 'ai_completed', {'forms': ['CG 20 37 04 13'], 'edition': None})), 'not_meets'),
    'revise-edition-empty-form-list': ('old_edition_completed', _seq_revise(lambda s: _items(
        s, 'ai_completed', {'forms': ['CG 20 37 04 13'], 'edition': ''})), 'not_meets'),
    'revise-edition-conflict': ('old_edition_completed', _seq_revise(lambda s: _items(
        s, 'ai_completed', {'forms': ['CG 20 37 07 04'], 'edition': '04 13'})), 'not_meets'),
    'revise-edition-dropped': ('old_edition_completed', _seq_revise(lambda s: _items(
        s, 'ai_completed', {}, drop=('edition',))), 'visible'),
    'revise-form-lookalike': ('missing_completed', _seq_revise(lambda s: _items(
        s, 'ai_completed', {'forms': ['\u0421G 20 37']})), 'not_meets'),
    'decoy-create-then-revise': ('missing_completed', _seq_decoy, 'visible'),
    # rules 1.9.0 (L): an ASCII look-alike of CG 20 10 / 20 11, listed and mapped to completed operations, never makes
    # the sub's look-alike paper count (refused at the set: 422 form_lookalike)
    **{f'revise-ascii-lookalike-completed:{f}': (f'lookalike_completed:{f}', _seq_list_and_map(f), 'not_meets')
       for f in ('CG 20 |0', 'CG 20 IO', 'CG 20 lO', 'CG 20 lI', 'CG 20 1|')},
    'agent-revise-then-staff-revise': ('missing_completed', _seq_then_staff, 'visible'),
}
ARCHIVE_SEQ_TARGETS = ('company', 'account', 'party', 'link', 'project', 'assignment', 'policy', 'endorsement',
                       'doc_cert', 'doc_endorsement', 'doc_waiver_ok', 'doc_contract')
SEQ_CELLS = [(name, actor) for name in SEQS for actor in ACTORS] + \
    [(f'archive-then-unarchive:{t}', actor) for t in ARCHIVE_SEQ_TARGETS for actor in ACTORS]


@pytest.fixture(scope='module', autouse=True)
def _print_seq():
    yield
    if not SEQ_RESULTS:
        return
    lines = ['| sequence | ' + ' | '.join(ACTORS) + ' |', '|---|' + '---|' * len(ACTORS)]
    for name in sorted({k[0] for k in SEQ_RESULTS}):
        lines.append(f'| {name} | ' + ' | '.join(SEQ_RESULTS.get((name, a), 'n/a') for a in ACTORS) + ' |')
    bad = sum(1 for v in SEQ_RESULTS.values() if v.startswith('FALSE'))
    lines.append(f'\n{len(SEQ_RESULTS)} sequence cells, {bad} FALSE MEETS')
    print('\nSEQUENCE MATRIX\n' + '\n'.join(lines))
    if os.getenv('CRT_MATRIX_OUT'):
        with open(os.environ['CRT_MATRIX_OUT'], 'a', encoding='utf-8') as fh:
            fh.write('\n' + '\n'.join(lines) + '\n')


@pytest.mark.parametrize('name,actor', SEQ_CELLS, ids=[f'{n}-{a}' for n, a in SEQ_CELLS])
def test_sequence_and_loosening_matrix(h, name, actor):
    n = zlib.crc32(f'{name}|{actor}'.encode()) % 10 ** 8
    a = Actor(h, actor)
    if name.startswith('archive-then-unarchive:'):
        t = name.split(':', 1)[1]
        w = _base(h, 'plain', n)
        before = h.state(w['asg']['id'])
        assert before['state'] in MEETS
        r1 = a.post(f'/{COLL[t]}/{_rid(w, t)}/archive')
        r2 = a.post(f'/{COLL[t]}/{_rid(w, t)}/unarchive')
        ev = h.state(w['asg']['id'])
        if r1.status_code in REFUSED or r2.status_code in REFUSED:
            res = f'refused {r1.status_code}/{r2.status_code}'
        elif ev['state'] not in MEETS:
            res = ev['state']
        else:
            res = f'FALSE {ev["state"]}'
        SEQ_RESULTS[(name, actor)] = res
        assert not res.startswith('FALSE'), (name, actor, codes(ev))
        return
    kind, run, expect = SEQS[name]
    w = _seq_world(h, kind, n)
    before = h.state(w['asg']['id'])
    assert before['state'] not in MEETS, before                     # the hostile chain starts from a real gap
    pinned = _get(h, 'crt_requirement_set', w['set']['id'])
    status, code, cand = run(h, w, a)
    d = _staff_step(h, w, cand) if cand is not None else None
    ev = h.state(w['asg']['id'])
    now_set = _get(h, 'crt_requirement_set', _get(h, 'crt_assignment', w['asg']['id'])['data']['requirement_set_id'])
    if cand is None:
        res = f'refused {status} {code}' if ev['state'] not in MEETS else f'FALSE {ev["state"]} (refused but Meets)'
    elif ev['state'] not in MEETS:
        res = ev['state']
    elif expect == 'visible' and now_set['id'] != pinned['id'] and _visible(d, pinned, now_set):
        res = 'meets: staff approved what the page showed'
    else:
        res = f'FALSE {ev["state"]}'
    SEQ_RESULTS[(name, actor)] = res
    assert not res.startswith('FALSE'), {'seq': name, 'actor': actor, 'state': ev['state'], 'codes': codes(ev),
                                         'against': (d or {}).get('against')}


# ═══════════════════ rules 1.6.0 (a, fields): every field the verdict reads, written by each actor ═══════════════════
# fields the verdict reads but that never decide it (display, notes): an accepted write must leave the verdict as is
# (contact.type and document.title are read only by the provenance scan, field_writers, which looks at every field an
# activity row wrote; neither decides anything)
DISPLAY_ONLY = {('crt_assignment', 'hold'), ('crt_assignment', 'hold_reason'), ('crt_project', 'size_flag'),
                ('crt_requirement_set', 'name'), ('contact', 'type'), ('document', 'title')}
FIELD_RESULTS = {}


def _perturb(v, spec=None):
    spec = spec or {}
    if spec.get('type') == 'enum' and spec.get('values'):
        other = [x for x in spec['values'] if x != v]
        return other[0] if other else v
    if spec.get('type') == 'datetime' and isinstance(v, str):
        return '2031-01-01T00:00:00.000Z' if v < '2031' else '2020-01-01T00:00:00.000Z'
    if isinstance(v, bool):
        return not v
    if isinstance(v, (int, float)):
        return v * 2 + 1
    if isinstance(v, str):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
            from datetime import date
            return (date.fromisoformat(v) + timedelta(days=400)).isoformat()
        return v + ' Two'
    if isinstance(v, list):
        return (v + [_perturb(v[0])]) if v else ['Extra Party LLC']
    if isinstance(v, dict):
        return {k: _perturb(x) for k, x in v.items()}
    return 'x'


_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-')


def _field_world(h, key, n):
    """The plain Meets world; for the waiver's written OK, the world where a staff waiver of it is live (otherwise
    nothing reads that paper and a write to it proves nothing)."""
    if key != 'docs:waiver_ok':
        return world(h, party_name=f'Fields {n} Inc')
    from tests.test_biz_crt_pack import _ends
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name=f'Fields {n} Inc')
    h.staff()
    h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': 'WRONG_PROJECT',
                        'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                        'acceptance_document_id': w['docs']['waiver_ok']['id'], 'recorded_by': 'clerk:' + STAFF_1,
                        'reason': 'accepted in writing', 'compensating_control': 'none',
                        'expires': iso(TODAY + timedelta(days=60))})
    return w


def test_every_field_the_verdict_reads_is_matrixed(h, dirs, monkeypatch):
    from services.biz.packs import risk_transfer as rt
    w0 = world(h, party_name='Fields Trace Inc')
    store = h.app.extensions['biz']['sandbox:josh']
    tr = _Trace()
    with monkeypatch.context() as mp:
        tr.store(mp, store)
        rt.evaluate(store, w0['asg']['id'], TODAY)
    targets = {'company': ['company'], 'contact': ['account', 'party'], 'crt_account_party': ['link'],
               'crt_project': ['project'], 'crt_assignment': ['asg'], 'crt_requirement_set': ['set'],
               'crt_policy': ['pol:gl'], 'crt_endorsement': ['end:ai_completed'],
               'document': ['docs:cert', 'docs:e_completed', 'docs:waiver_ok', 'docs:contract']}
    cells = sorted((c, f) for c, f in tr.fields if c in targets and not f.startswith('_'))
    assert len(cells) >= 40, cells
    n = 0
    for coll, field in cells:
        for key in targets[coll]:
            for actor in ACTORS:
                n += 1
                w = _field_world(h, key, n)
                ref = w[key] if ':' not in key else w[key.split(':')[0]][key.split(':')[1]]
                rec = _get(h, coll, ref['id'])
                before = h.state(w['asg']['id'])
                assert before['state'] in ('meets', 'meets_accepted'), before
                cur = rec['data'].get(field)
                if isinstance(cur, str) and _UUID.match(cur):
                    FIELD_RESULTS[(coll, field, key, actor)] = 'ref: the link verb re-points it'
                    continue
                spec = (h.ok(h.get(f'/schema/{coll}')).get('fields') or {}).get(field) or {}
                r = Actor(h, actor).patch(f'/{coll}/{rec["id"]}', {'data': {field: _perturb(cur, spec)}}, rec['version'])
                ev = h.state(w['asg']['id'])
                same = {k: ev[k] for k in ('state', 'codes', 'items', 'accepted')} == \
                    {k: before[k] for k in ('state', 'codes', 'items', 'accepted')}
                if r.status_code in REFUSED:
                    res = f'refused {r.status_code} {(r.get_json() or {}).get("code")}'
                elif ev['state'] not in MEETS:
                    res = ev['state']
                elif (coll, field) in DISPLAY_ONLY and same:
                    res = 'display-only (verdict unchanged)'
                else:
                    res = f'FALSE {ev["state"]} ({r.status_code})'
                FIELD_RESULTS[(coll, field, key, actor)] = res
                if coll == 'company' and r.status_code not in REFUSED:      # the agency record is shared by every
                    h.staff()                                               # world: staff put it back
                    c = _get(h, 'company', rec['id'])
                    h.ok(h.patch(f'/company/{rec["id"]}', {'data': {field: cur}}, c['version']))
    lines = [f'| {c}.{f} | {k} | {a} | {v} |' for (c, f, k, a), v in sorted(FIELD_RESULTS.items())]
    bad = [x for x in lines if '| FALSE' in x]
    text = '\nFIELD MATRIX\n| field | record | actor | result |\n|---|---|---|---|\n' + '\n'.join(lines) + \
        f'\n\n{len(FIELD_RESULTS)} field cells over {len(cells)} fields read, {len(bad)} FALSE MEETS'
    print(text)
    if os.getenv('CRT_MATRIX_OUT'):
        with open(os.environ['CRT_MATRIX_OUT'], 'a', encoding='utf-8') as fh:
            fh.write(text + '\n')
    assert bad == [], bad
