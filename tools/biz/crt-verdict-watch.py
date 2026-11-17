#!/usr/bin/env python3
"""crt-verdict-watch.py — independent watcher for pack:risk-transfer verdicts (NEWBORN, advisory only).

For every sub-on-project whose LATEST stored review (crt_review) says "Meets requirements", it
re-derives, from the human-confirmed readings in the database and the bytes in the private file
store, whether those inputs actually support Meets. It is a different instrument from the engine:
it imports nothing from the OVU code, opens business.db READ-ONLY, re-hashes files itself, and has
its own (deliberately narrow) rules. It never changes a verdict, never writes, never messages anyone.

What a stored Meets must be backed by (plan S18, design section 4.4 rule 1):
  R1 the requirement set is APPROVED and a PERSON confirmed it (crt_confirmation by clerk:/local:)
  R2 every confirmation the review says it read exists, is not archived and was made by a person
  R3 every required line (limit / ai items) has a policy for this sub, received for THIS account,
     person-confirmed against a stored file whose bytes still hash to the confirmed sha256, and not
     expired on the review date
  R4 every required additional-insured endorsement (ai_ongoing / ai_completed, or names_parties) has an
     endorsement on that line, for this account, with its own document in the private store
     (hash-verified), person-confirmed against a verified file, that names this account and this project
     (or is blanket with the project's contract tick person-confirmed true). Ongoing ops are not required
     after substantial completion; completed ops not after the statute of repose.
  R5 the account, sub, project and assignment rows exist and the sub is linked to the account
If a record the review read was changed after the review, the watcher cannot see the old version:
that review is CANNOT-TELL, never PASS.

Verdict: PASS (exit 0) every Meets is backed / FAIL (exit 1) at least one Meets is NOT backed — the
watcher disagrees with the stored verdict / CANNOT-TELL (exit 3) unreadable store, nothing to check, or
a review it cannot judge. Output is a report on stdout (--json for machines). Nothing else.

  tools/biz/crt-verdict-watch.py --db-dir /mnt/clients/<tenant>/openvoiceui/db
  tools/biz/crt-verdict-watch.py --db-dir <db dir>/sandboxes/josh --json
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

PASS, FAIL, CANNOT = 'PASS', 'FAIL', 'CANNOT-TELL'
EXIT = {PASS: 0, FAIL: 1, CANNOT: 3}
MEETS_STATES = ('meets', 'expiring_30', 'expiring_15')
PERSON = ('clerk:', 'local:')
_SUFFIX = {'llc', 'inc', 'co', 'corp', 'corporation', 'company', 'ltd', 'the', 'lp', 'llp', 'pllc', 'pc'}


def _norm(s):
    if not isinstance(s, str):
        return ''
    return ' '.join(w for w in re.split(r'[^a-z0-9]+', s.lower().replace('&', ' and ')) if w and w not in _SUFFIX)


def _named(wanted, texts):
    w = _norm(wanted)
    return bool(w) and any(_norm(t) == w or f' {w} ' in f' {_norm(t)} ' for t in (texts or []))


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
                                  'data': json.loads(r['data'])}
        self._hash = {}

    def all(self, coll):
        return [r for r in self.rows.values() if r['collection'] == coll and not r['archived']]

    def get(self, coll, rid):
        r = self.rows.get(rid)
        return r if r and r['collection'] == coll and not r['archived'] else None

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


def _person_confs(st, rec, fields, need_file=True):
    """Person confirmations of rec at its current version covering every field (file-verified if asked)."""
    out = []
    for c in st.all('crt_confirmation'):
        d = c['data']
        if d.get('record_id') != rec['id'] or d.get('record_version') != rec['version']:
            continue
        if not str(d.get('confirmed_by') or '').startswith(PERSON) or not str(c['created_by']).startswith(PERSON):
            continue
        if not set(fields) <= set(d.get('fields') or []):
            continue
        if need_file:
            sha = st.doc_sha(d.get('source_document_id'))
            if not sha or sha != d.get('source_sha256') or not st.file_ok(sha):
                continue
        out.append(c)
    return out


def judge(st, review):
    """(verdict, [reasons]) for one stored Meets review."""
    rd = review['data']
    why, cannot = [], []
    on = _day(rd.get('reviewed_at')) or _day(review['updated_at'])
    asg = st.get('crt_assignment', rd.get('crt_assignment_id'))
    if not asg:
        return FAIL, ['R5 assignment missing']
    proj = st.get('crt_project', asg['data'].get('crt_project_id'))
    acct = st.get('contact', (proj or {}).get('data', {}).get('crt_account_id'))
    party = st.get('contact', asg['data'].get('party_contact_id'))
    if not (proj and acct and party):
        return FAIL, ['R5 project, account or sub missing']
    if not any(ln['data'].get('crt_account_id') == acct['id'] and ln['data'].get('party_contact_id') == party['id']
               and ln['data'].get('status', 'active') == 'active' for ln in st.all('crt_account_party')):
        why.append('R5 sub not linked to the account')
    reviewed_at = rd.get('reviewed_at') or review['updated_at']

    def changed_since(rec):
        return rec['updated_at'] > reviewed_at

    s = st.get('crt_requirement_set', rd.get('requirement_set_id'))
    if not s:
        return FAIL, ['R1 requirement set missing']
    if s['data'].get('status') != 'APPROVED' or not _person_confs(st, s, ['status'], need_file=False):
        (cannot if changed_since(s) else why).append('R1 set not approved by a person')
    for cid in rd.get('confirmation_ids') or []:
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
                exp = _day(p['data'].get('expiration'))
                if exp and on and exp > on and _person_confs(st, p, ['line', 'expiration', 'source_document_id']):
                    good = True
            if not good:
                (cannot if changed else why).append(f'R3 no person-confirmed, file-backed, current {line} policy')
        elif kind == 'endorsement' and (fn in ('ai_ongoing', 'ai_completed') or it.get('names_parties')):
            if fn == 'ai_ongoing' and sc and on and on > sc:
                continue
            if fn == 'ai_completed' and sc and on and on > sc and isinstance(repose, int) and on >= _add_years(sc, repose):
                continue
            good = changed = False
            for e in st.all('crt_endorsement'):
                ed = e['data']
                pol = st.get('crt_policy', ed.get('crt_policy_id'))
                if ed.get('function') != fn or ed.get('crt_account_id') != acct['id'] or not pol \
                        or pol['id'] not in {p['id'] for p in policies} or pol['data'].get('line') != (line or 'gl'):
                    continue
                if changed_since(e) or changed_since(proj):
                    changed = True
                    continue
                if not st.file_ok(st.doc_sha(ed.get('document_id'))):
                    continue
                if not _person_confs(st, e, ['function', 'document_id', 'scheduled_names', 'scheduled_projects',
                                             'blanket']):
                    continue
                if ed.get('blanket'):
                    if proj['data'].get('contract_requires_ai') is True and \
                            _person_confs(st, proj, ['contract_requires_ai']):
                        good = True
                elif _named(acct_name, ed.get('scheduled_names')) and _named(pname, ed.get('scheduled_projects')):
                    good = True
            if not good:
                (cannot if changed else why).append(
                    f'R4 no person-confirmed {fn} endorsement document naming this account and project')
    if why:
        return FAIL, sorted(set(why))
    if cannot:
        return CANNOT, sorted(set(cannot)) + ['(a record changed after the review; old version not visible)']
    return PASS, []


def watch(db_dir):
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
    rows = []
    for aid, r in sorted(latest.items(), key=lambda kv: kv[0] or ''):
        if r['data'].get('state') not in MEETS_STATES:
            continue
        v, reasons = judge(st, r)
        rows.append({'assignment_id': aid, 'review_id': r['id'], 'stored_state': r['data'].get('state'),
                     'watch': v, 'reasons': reasons})
    st.conn.close()
    vs = [x['watch'] for x in rows]
    if FAIL in vs:
        return FAIL, rows, f'{vs.count(FAIL)} stored Meets verdict(s) NOT backed by confirmed inputs'
    if not rows:
        return CANNOT, rows, 'no stored Meets verdicts to check (silence is not health)'
    if CANNOT in vs:
        return CANNOT, rows, f'{vs.count(CANNOT)} review(s) could not be judged'
    return PASS, rows, f'{len(rows)} stored Meets verdict(s) backed by person-confirmed, file-verified inputs'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Independent watcher for risk-transfer Meets verdicts (advisory)')
    ap.add_argument('--db-dir', required=True, help="the store's db dir (holds business.db and files/)")
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    verdict, rows, summary = watch(a.db_dir)
    if a.json:
        print(json.dumps({'verdict': verdict, 'summary': summary, 'checked': rows,
                          'advisory': '[probation] NEWBORN watcher: reports only; changes nothing'}, indent=1))
    else:
        for x in rows:
            if x['watch'] != PASS:
                print(f"{'DISAGREE' if x['watch'] == FAIL else 'CANNOT-TELL'}: stored '{x['stored_state']}' "
                      f"for assignment {x['assignment_id']} - " + '; '.join(x['reasons']))
        print(f'crt-verdict-watch: {verdict} - {summary} [probation, advisory only]')
    return EXIT[verdict]


if __name__ == '__main__':
    sys.exit(main())
