#!/usr/bin/env python3
"""crt-verdict-watch.py — independent watcher for pack:risk-transfer verdicts (NEWBORN, advisory only).

Two kinds of Meets claim are judged: every sub-on-project whose LATEST stored review (crt_review) says
Meets (plain, contractor-accepted, or expiring), and, when given (--live-url / --live-json), every LIVE
Meets state the API shows staff. For each it re-derives, from the human-confirmed readings in the
database and the bytes in the private file store, whether those inputs actually support Meets. It is a
different instrument from the engine: it imports nothing from the OVU code, opens business.db READ-ONLY,
re-hashes files itself, and has its own (deliberately narrow) rules. It never changes a verdict, never
writes, never messages anyone.

What a Meets must be backed by (plan S18, design section 4.4 rule 1, claim-check 2026-11-17):
  R1 the requirement set is APPROVED and a PERSON confirmed it (crt_confirmation by clerk:/local:)
  R2 every confirmation the review says it read exists, is not archived and was made by a person
  R3 every required line (limit / ai items) has a policy for this sub, received for THIS account,
     person-confirmed (line, effective, expiration, source document) against a stored file whose bytes
     still hash to the confirmed sha256, that file's document received for THIS account and THIS sub,
     and IN FORCE on the claim date (effective <= date < expiration)
  R4 every required additional-insured endorsement (ai_ongoing / ai_completed, or names_parties) has an
     endorsement on that line, for this account, whose OWN document is of kind 'endorsement' with that
     kind written or confirmed by a PERSON, for this account and sub, hash-verified, whose file is NOT
     any certificate file of this sub, person-confirmed (including which policy it attaches to), that
     names this account EXACTLY (normalised equality, never containment) and this project (or is
     blanket with the project's contract tick person-confirmed true). A naming miss (WRONG_PARTY,
     WRONG_PROJECT, BLANKET_UNBACKED) counts only when a person recorded an unexpired waiver backed by
     THIS account's written OK (kind contractor_waiver_ok). A missing document is never waivable.
     Ongoing ops are not required after substantial completion; completed ops not after the statute
     of repose.
  R5 the account, sub, project and assignment rows exist, the sub is linked to the account, and the
     names, the project's account and its dates were each written or confirmed by a person
  R6 each assignment field the verdict reads (project, sub, set, set version, trade, scope change) was
     last written by a person or person-confirmed (per field, read from the activity log: a staff
     hold or note never vouches for a field the agent wrote), names the set the verdict used, and
     pinned that set's current version
If a record the claim read was changed after the claim, the watcher cannot see the old version:
that claim is CANNOT-TELL, never PASS. If the claim is backed by the documents as they were at the
claim, but a document or record it read was archived since, the row is STALE (right when made, not
now; re-review), never a disagreement.

Verdict: PASS (exit 0) every Meets is backed / FAIL (exit 1) at least one Meets is NOT backed — the
watcher disagrees with the stored verdict / CANNOT-TELL (exit 3) unreadable store, nothing to check, a
review it cannot judge, or a STALE review. Output is a report on stdout (--json for machines).

  tools/biz/crt-verdict-watch.py --db-dir /mnt/clients/<tenant>/openvoiceui/db
  tools/biz/crt-verdict-watch.py --db-dir <db dir>/sandboxes/josh --json
  BIZ_WATCH_KEY=... tools/biz/crt-verdict-watch.py --db-dir <db dir>/sandboxes/josh \\
      --live-url https://test-dev.jam-bot.com --sandbox josh                       (also the live states)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime

PASS, FAIL, CANNOT, STALE = 'PASS', 'FAIL', 'CANNOT-TELL', 'STALE'
EXIT = {PASS: 0, FAIL: 1, CANNOT: 3}
MEETS_STATES = ('meets', 'meets_accepted', 'expiring_30', 'expiring_15')
PERSON = ('clerk:', 'local:')
ASG_FIELDS = ('crt_project_id', 'party_contact_id', 'requirement_set_id', 'requirement_set_version',
              'trade_category', 'scope_changed_at')
PROJECT_FIELDS = ('crt_account_id', 'name', 'substantial_completion', 'repose_years')
_ABSENT = object()
_SUFFIX = {'llc', 'inc', 'co', 'corp', 'corporation', 'company', 'ltd', 'the', 'lp', 'llp', 'pllc', 'pc'}


def _norm(s):
    if not isinstance(s, str):
        return ''
    return ' '.join(w for w in re.split(r'[^a-z0-9]+', s.lower().replace('&', ' and ')) if w and w not in _SUFFIX)


def _named(wanted, texts):
    """Project naming (whole words): the engine reads WRONG_PROJECT the same way."""
    w = _norm(wanted)
    return bool(w) and any(_norm(t) == w or f' {w} ' in f' {_norm(t)} ' for t in (texts or []))


def _named_exact(wanted, texts):
    """Party naming: the same legal name after normalising, never a longer name containing it."""
    w = _norm(wanted)
    return bool(w) and any(_norm(t) == w for t in (texts or []))


def _day(s):
    try:
        return date.fromisoformat(s[:10]) if isinstance(s, str) else None
    except ValueError:
        return None


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


class Store:
    """business.db opened read-only, plus the private file directory."""

    def __init__(self, db_dir):
        self.db_dir = db_dir
        path = os.path.join(db_dir, 'business.db')
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        self.conn.row_factory = sqlite3.Row
        self.rows = {}
        for r in self.conn.execute('SELECT * FROM records'):
            self.rows[r['id']] = {'id': r['id'], 'collection': r['collection'], 'version': r['version'],
                                  'created_by': r['created_by'], 'updated_by': r['updated_by'],
                                  'updated_at': r['updated_at'], 'archived': bool(r['archived_at']),
                                  'archived_at': r['archived_at'], 'data': json.loads(r['data'])}
        self.events = {}
        for a in self.conn.execute("SELECT record_id, type, by, diff FROM activity "
                                   "WHERE type IN ('created', 'updated') ORDER BY id"):
            self.events.setdefault(a['record_id'], []).append(
                (a['type'], a['by'], json.loads(a['diff']) if a['diff'] else {}))
        self.toggles = {}         # archive / unarchive times per record: each bumped the version, not the data
        for a in self.conn.execute("SELECT record_id, at FROM activity WHERE type IN ('archived', 'unarchived')"):
            self.toggles.setdefault(a['record_id'], []).append(a['at'])
        self._hash = {}
        self.at = None            # None: as things are now; an ISO time: as they were at that claim

    def view(self, at):
        v = object.__new__(Store)
        v.__dict__.update(self.__dict__)
        v.at = at
        return v

    def _visible(self, r):
        return not r['archived'] or (self.at is not None and (r['archived_at'] or '') > self.at)

    def all(self, coll):
        return [r for r in self.rows.values() if r['collection'] == coll and self._visible(r)]

    def version(self, rec):
        """The record's version at the claim: archiving and unarchiving since then changed no data."""
        if self.at is None:
            return rec['version']
        return rec['version'] - sum(1 for t in self.toggles.get(rec['id'], []) if t > self.at)

    def all_ever(self, coll):
        return [r for r in self.rows.values() if r['collection'] == coll]

    def get(self, coll, rid):
        r = self.rows.get(rid)
        return r if r and r['collection'] == coll and self._visible(r) else None

    def wrote(self, rec, field):
        """Who last set or changed this ONE field, read from the activity log; None when the log does
        not explain the field's current value (nobody vouches for it)."""
        last, creator = _ABSENT, None
        for typ, by, diff in self.events.get(rec['id'], []):
            if typ == 'created':
                creator = by
                if field in (diff.get('set') or {}):
                    last = (by, diff['set'][field])
            elif field in (diff.get('changed') or {}):
                last = (by, (diff['changed'][field] or [None, None])[-1])
        if last is _ABSENT:
            return creator if field not in rec['data'] else None
        return last[0] if last[1] == rec['data'].get(field) else None

    def file_ok(self, sha):
        """The file is in the private store and its bytes hash to sha (re-hashed here)."""
        if not isinstance(sha, str) or not re.match(r'^[0-9a-f]{64}$', sha):
            return False
        if sha not in self._hash:
            p = os.path.join(self.db_dir, 'files', sha)
            ok = False
            if os.path.isfile(p):
                h = hashlib.sha256()
                with open(p, 'rb') as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b''):
                        h.update(chunk)
                ok = h.hexdigest() == sha
            self._hash[sha] = ok
        return self._hash[sha]

    def doc_sha(self, did):
        d = self.get('document', did) if did else None
        url = (d or {}).get('data', {}).get('file_url') or ''
        return url[len('biz-file:'):] if url.startswith('biz-file:') else None


def _person_field(st, rec, field):
    """THIS field was last written by a person, or a person confirmed it at the record's current version."""
    return str(st.wrote(rec, field) or '').startswith(PERSON) or bool(_person_confs(st, rec, [field], need_file=False))


def _doc_backs(st, did, acct, party=None, kind=None):
    """The document is this account's (and this sub's) paper, of this kind, its identity written or
    confirmed by a person, and its bytes still hash to its id."""
    doc = st.get('document', did) if did else None
    if not doc:
        return False
    d = doc['data']
    if d.get('crt_account_id') != acct['id'] or (party is not None and d.get('contact_id') != party['id']):
        return False
    if kind is not None and d.get('kind') != kind:
        return False
    fields = ['crt_account_id', 'file_url'] + (['contact_id'] if party is not None else []) + \
        (['kind'] if kind is not None else [])
    return all(_person_field(st, doc, f) for f in fields) and st.file_ok(st.doc_sha(did))


def _person_confs(st, rec, fields, need_file=True, acct=None, party=None):
    """Person confirmations of rec at its current version covering every field (file-verified if asked,
    and, when acct is given, read from THIS account's (and sub's) own paper)."""
    out = []
    for c in st.all('crt_confirmation'):
        d = c['data']
        if d.get('record_id') != rec['id'] or d.get('record_version') != st.version(rec):
            continue
        if not str(d.get('confirmed_by') or '').startswith(PERSON) or not str(c['created_by']).startswith(PERSON):
            continue
        if not set(fields) <= set(d.get('fields') or []):
            continue
        if need_file:
            sha = st.doc_sha(d.get('source_document_id'))
            if not sha or sha != d.get('source_sha256') or not st.file_ok(sha):
                continue
            if acct is not None and not _doc_backs(st, d.get('source_document_id'), acct, party):
                continue
        out.append(c)
    return out


WAIVABLE_NAME_CODES = ('WRONG_PARTY', 'WRONG_PROJECT', 'BLANKET_UNBACKED')


def _waived(st, asg, code, on, acct):
    """A person-recorded, unexpired waiver of exactly this code, backed by THIS account's written OK."""
    for w in st.all('crt_waiver'):
        wd = w['data']
        if wd.get('crt_assignment_id') != asg['id'] or wd.get('variance_code') != code:
            continue
        if not str(w['created_by']).startswith(PERSON):
            continue
        exp = _day(wd.get('expires'))
        doc = st.get('document', wd.get('acceptance_document_id'))
        if exp and on and exp > on and doc and _doc_backs(st, doc['id'], acct, kind='contractor_waiver_ok'):
            return True
    return False


def judge(st, claim):
    """(verdict, [reasons]) for one Meets claim: a stored review, or a live state read from the API.

    claim = {assignment_id, state, on (date), at (ISO time the claim was made), set_id or None,
             confirmation_ids or None, kind: 'review' | 'live'}

    Judged twice: with the records as they were at the claim (anything archived since still counted)
    and as they are now. Backed then but not now is STALE (right when made), never a disagreement.
    """
    then = _judge(st.view(claim['at']), claim)
    if then[0] != PASS:
        return then
    now = _judge(st.view(None), claim)
    if now[0] == PASS:
        return now
    return STALE, ['STALE: backed by the records as they were at the claim; since then: ' + '; '.join(now[1])]


def _judge(st, claim):
    why, cannot = [], []
    on, at = claim['on'], claim['at']
    asg = st.get('crt_assignment', claim['assignment_id'])
    if not asg:
        return FAIL, ['R5 assignment missing']

    def changed_since(rec):
        return rec['updated_at'] > at

    # R6 the assignment (set, version, trade, scope) was written by a person, and points at the set judged
    if changed_since(asg):
        cannot.append('R6 the assignment changed after the claim')
    else:
        agent_fields = [f for f in ASG_FIELDS if not _person_field(st, asg, f)]
        if agent_fields:
            why.append('R6 assignment field(s) not written or confirmed by a person: ' + ', '.join(agent_fields))
        if claim.get('set_id') and claim['set_id'] != asg['data'].get('requirement_set_id'):
            why.append('R6 the verdict used a different requirement set than the assignment names')
    proj = st.get('crt_project', asg['data'].get('crt_project_id'))
    acct = st.get('contact', (proj or {}).get('data', {}).get('crt_account_id'))
    party = st.get('contact', asg['data'].get('party_contact_id'))
    if not (proj and acct and party):
        return FAIL, ['R5 project, account or sub missing']
    if not any(ln['data'].get('crt_account_id') == acct['id'] and ln['data'].get('party_contact_id') == party['id']
               and ln['data'].get('status', 'active') == 'active'
               and all(_person_field(st, ln, f) for f in ('crt_account_id', 'party_contact_id', 'status'))
               for ln in st.all('crt_account_party')):
        why.append('R5 sub not linked to the account by a person')
    for rec, fields, what in ((acct, ('name', 'company_name'), 'account name'),
                              (party, ('name', 'company_name'), 'sub name'),
                              (proj, PROJECT_FIELDS, 'project')):
        bad = [f for f in fields if not _person_field(st, rec, f)]
        if bad:
            (cannot if changed_since(rec) else why).append(
                f'R5 {what} field(s) not written or confirmed by a person: ' + ', '.join(bad))

    s = st.get('crt_requirement_set', claim.get('set_id') or asg['data'].get('requirement_set_id'))
    if not s:
        return FAIL, ['R1 requirement set missing']
    if s['data'].get('status') != 'APPROVED' or not _person_confs(st, s, ['status'], need_file=False):
        (cannot if changed_since(s) else why).append('R1 set not approved by a person')
    if not changed_since(s) and not changed_since(asg) and asg['data'].get('requirement_set_version') != s['version']:
        why.append('R6 the assignment did not pin this version of the set')
    for cid in claim.get('confirmation_ids') or []:
        c = st.get('crt_confirmation', cid)
        if not c or not str(c['data'].get('confirmed_by') or '').startswith(PERSON) \
                or not str(c['created_by']).startswith(PERSON):
            why.append('R2 the review read a confirmation that is missing or not made by a person')
            break
    acct_name = acct['data'].get('company_name') or acct['data'].get('name')
    pname = proj['data'].get('name')
    sc = _day(proj['data'].get('substantial_completion'))
    repose = proj['data'].get('repose_years')
    policies = [p for p in st.all('crt_policy') if p['data'].get('party_contact_id') == party['id']
                and p['data'].get('crt_account_id') == acct['id']]
    for it in s['data'].get('items') or []:
        kind, line, fn = it.get('kind'), it.get('line'), it.get('function')
        if it.get('required') is False:
            continue
        if kind in ('limit', 'ai'):
            if kind == 'ai' and line == 'pl':
                continue
            good = changed = False
            for p in policies:
                if p['data'].get('line') != line:
                    continue
                if changed_since(p):
                    changed = True
                    continue
                eff, exp = _day(p['data'].get('effective')), _day(p['data'].get('expiration'))
                if eff and exp and on and eff <= on < exp and \
                        _person_confs(st, p, ['line', 'effective', 'expiration', 'source_document_id'], acct=acct,
                                      party=party):
                    good = True
            if not good:
                (cannot if changed else why).append(f'R3 no person-confirmed, file-backed {line} policy in force')
        elif kind == 'endorsement' and (fn in ('ai_ongoing', 'ai_completed') or it.get('names_parties')):
            if fn == 'ai_ongoing' and sc and on and on > sc:
                continue
            if fn == 'ai_completed' and sc and on and on > sc and isinstance(repose, int) and on >= _add_years(sc, repose):
                continue
            good = changed = False
            # every certificate file of this sub for this account: never the endorsement (host ruling N2)
            cert_shas = {st.doc_sha(d['id']) for d in st.all_ever('document') if d['data'].get('kind') == 'coi_acord25'
                         and d['data'].get('crt_account_id') == acct['id'] and d['data'].get('contact_id') == party['id']}
            cert_shas |= {st.doc_sha(p['data'].get('source_document_id')) for p in policies}
            for e in st.all('crt_endorsement'):
                ed = e['data']
                pol = st.get('crt_policy', ed.get('crt_policy_id'))
                if ed.get('function') != fn or ed.get('crt_account_id') != acct['id'] or not pol \
                        or pol['id'] not in {p['id'] for p in policies} or pol['data'].get('line') != (line or 'gl'):
                    continue
                if changed_since(e) or changed_since(proj):
                    changed = True
                    continue
                doc = st.get('document', ed.get('document_id'))
                # the endorsement's own document: kind endorsement (a person said so), this sub's, not a
                # certificate's file, bytes verified
                if not doc or not _doc_backs(st, doc['id'], acct, party, kind='endorsement') or \
                        doc['id'] == pol['data'].get('source_document_id') or st.doc_sha(doc['id']) in cert_shas:
                    continue
                if not _person_confs(st, e, ['function', 'document_id', 'scheduled_names', 'scheduled_projects',
                                             'blanket', 'crt_policy_id'], acct=acct, party=party):
                    continue
                if ed.get('blanket'):
                    backed = proj['data'].get('contract_requires_ai') is True and \
                        bool(_person_confs(st, proj, ['contract_requires_ai']))
                    name_code = None if backed else 'BLANKET_UNBACKED'
                elif not _named_exact(acct_name, ed.get('scheduled_names')) or \
                        not all(_named_exact(n, ed.get('scheduled_names')) for n in proj['data'].get('contract_ai_parties') or []):
                    name_code = 'WRONG_PARTY'
                elif not _named(pname, ed.get('scheduled_projects')):
                    name_code = 'WRONG_PROJECT'
                else:
                    name_code = None
                if name_code is None or _waived(st, asg, name_code, on, acct):
                    good = True
            if not good:
                (cannot if changed else why).append(
                    f'R4 no person-confirmed {fn} endorsement document naming this account and project '
                    '(or a contractor-accepted waiver of the naming)')
    if why:
        return FAIL, sorted(set(why))
    if cannot:
        return CANNOT, sorted(set(cannot)) + ['(a record changed after the claim; old version not visible)']
    return PASS, []


def fetch_live_claims(url, key, sandbox=None, timeout=20):
    """Live Meets claims from the API (GET /crt_assignment, then /crt/assignment/<id>/state). Read only."""
    import urllib.request
    hdr = {'X-Agent-Key': key, 'Accept': 'application/json'}
    if sandbox:
        hdr['X-Biz-Sandbox'] = sandbox

    def get(path):
        with urllib.request.urlopen(urllib.request.Request(url.rstrip('/') + path, headers=hdr), timeout=timeout) as r:
            return json.loads(r.read())
    out, cursor = [], None
    while True:
        page = get('/api/biz/v1/crt_assignment?limit=500' + (f'&cursor={cursor}' if cursor else ''))
        for a in page['records']:
            ev = get(f'/api/biz/v1/crt/assignment/{a["id"]}/state')
            out.append({'assignment_id': a['id'], 'state': ev['state'], 'as_of': ev['as_of'],
                        'set_id': ev.get('requirement_set_id')})
        cursor = page.get('next_cursor')
        if not cursor:
            return out


def watch(db_dir, live=None):
    """Judge every latest stored Meets review, plus every live Meets claim given (list of state dicts)."""
    try:
        st = Store(db_dir)
    except (OSError, sqlite3.Error) as e:
        return CANNOT, [], f'store unreadable: {type(e).__name__}'
    latest = {}
    for r in st.all('crt_review'):
        aid = r['data'].get('crt_assignment_id')
        key = r['data'].get('reviewed_at') or r['updated_at']
        if aid not in latest or key > (latest[aid]['data'].get('reviewed_at') or latest[aid]['updated_at']):
            latest[aid] = r
    claims = []
    for aid, r in sorted(latest.items(), key=lambda kv: kv[0] or ''):
        if r['data'].get('state') in MEETS_STATES:
            at = r['data'].get('reviewed_at') or r['updated_at']
            claims.append({'kind': 'review', 'review_id': r['id'], 'assignment_id': aid, 'state': r['data']['state'],
                           'on': _day(at), 'at': at, 'set_id': r['data'].get('requirement_set_id'),
                           'confirmation_ids': r['data'].get('confirmation_ids')})
    for c in live or []:
        if c.get('state') in MEETS_STATES:
            claims.append({'kind': 'live', 'assignment_id': c['assignment_id'], 'state': c['state'],
                           'on': _day(c.get('as_of')), 'at': '9999', 'set_id': c.get('set_id'),
                           'confirmation_ids': None})
    rows = []
    for c in claims:
        v, reasons = judge(st, c)
        rows.append({'kind': c['kind'], 'assignment_id': c['assignment_id'], 'review_id': c.get('review_id'),
                     'stored_state': c['state'], 'watch': v, 'reasons': reasons})
    st.conn.close()
    vs = [x['watch'] for x in rows]
    if FAIL in vs:
        return FAIL, rows, f'{vs.count(FAIL)} Meets verdict(s) NOT backed by confirmed inputs'
    if not rows:
        return CANNOT, rows, 'no Meets verdicts to check (silence is not health)'
    if CANNOT in vs or STALE in vs:
        return CANNOT, rows, (f'{vs.count(CANNOT)} claim(s) could not be judged, {vs.count(STALE)} stale review(s) '
                              '(right when made, an input archived since: re-review)')
    return PASS, rows, f'{len(rows)} Meets verdict(s) backed by person-confirmed, file-verified inputs'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Independent watcher for risk-transfer Meets verdicts (advisory)')
    ap.add_argument('--db-dir', required=True, help="the store's db dir (holds business.db and files/)")
    ap.add_argument('--live-url', help='also judge live states: GET them from this server (key from env BIZ_WATCH_KEY)')
    ap.add_argument('--sandbox', help='dev sandbox name for --live-url (X-Biz-Sandbox)')
    ap.add_argument('--live-json', help='also judge live states from this file ([{assignment_id, state, as_of, set_id}])')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    live = None
    if a.live_json:
        live = json.load(open(a.live_json))
    elif a.live_url:
        key = os.environ.get('BIZ_WATCH_KEY', '').strip()
        if not key:
            print('crt-verdict-watch: CANNOT-TELL - no BIZ_WATCH_KEY in env for --live-url')
            return EXIT[CANNOT]
        try:
            live = fetch_live_claims(a.live_url, key, a.sandbox)
        except Exception as e:
            print(f'crt-verdict-watch: CANNOT-TELL - live states unreadable ({type(e).__name__})')
            return EXIT[CANNOT]
    verdict, rows, summary = watch(a.db_dir, live)
    if a.json:
        print(json.dumps({'verdict': verdict, 'summary': summary, 'checked': rows,
                          'advisory': '[probation] NEWBORN watcher: reports only; changes nothing'}, indent=1))
    else:
        for x in rows:
            if x['watch'] != PASS:
                print(f"{'DISAGREE' if x['watch'] == FAIL else x['watch']}: {x['kind']} '{x['stored_state']}' "
                      f"for assignment {x['assignment_id']} - " + '; '.join(x['reasons']))
        print(f'crt-verdict-watch: {verdict} - {summary} [probation, advisory only]')
    return EXIT[verdict]


if __name__ == '__main__':
    sys.exit(main())
