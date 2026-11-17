"""pack:risk-transfer — the certificate and endorsement tracker (CCA Risk Transfer Tracker).

Plan: docs/jambot/contractors-choice-risk-transfer-app-plan.md (rev 2), section 4.4. The guide it
follows is cited per value in default-schemas/packs/risk-transfer/templates.json.

The verdict reads ONLY confirmed inputs (rule 1, ruling b):
  - evidence fields (crt_policy, crt_endorsement, and the contract fields of crt_project) count
    only when a crt_confirmation covers that field at the record's CURRENT version and names a
    source document whose private file still hashes to the confirmation's source_sha256;
  - set-up fields a person types (account and sub names, project name / GC / owner / dates)
    count when a person wrote the record's current version, or a confirmation covers them;
  - a requirement set counts only when APPROVED and confirmed by staff at its current version;
  - when we (the agency) are the sub's producer, evidence needs two different staff confirmers.
Anything else is "Can't tell (needs staff check)", code UNCONFIRMED:<collection>.<field>.

Nothing here sends, holds or pays anything. hold_suggested is advice; the stored hold is staff-set.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from services.biz.schema import SchemaError
from services.biz.store import FILE_ID_RE, FILE_URL_PREFIX

PACK = 'risk-transfer'
RULE_VERSION = 'crt-rules-1.0.0'
DISCLAIMER = ('Evidence check against the requirement set you approved. '
              'Not a coverage opinion or legal advice.')
LABELS = {
    'meets': 'Meets requirements (documents reviewed)',
    'expiring_30': 'Meets requirements (documents reviewed), expiring within 30 days',
    'expiring_15': 'Meets requirements (documents reviewed), expiring within 15 days',
    'cant_tell': "Can't tell (needs staff check)",
    'missing': 'Missing documents',
    'variance': 'Variance',
    'expired': 'Expired',
}
UNMET_STATES = ('cant_tell', 'missing', 'variance', 'expired')
REVIEW_STALE_DAYS = 30
TEMPLATES_PATH = Path(__file__).resolve().parents[3] / 'default-schemas' / 'packs' / 'risk-transfer' / 'templates.json'

EVIDENCE = {'crt_policy', 'crt_endorsement'}
PROJECT_CONTRACT_FIELDS = ('contract_requires_ai', 'contract_ai_parties')
CONFIRMABLE = {'crt_policy', 'crt_endorsement', 'crt_project', 'contact', 'crt_account_party', 'crt_assignment'}
POLICY_BASE_FIELDS = ('line', 'policy_number', 'effective', 'expiration', 'limits', 'named_insured', 'addl_insd',
                      'source_document_id', 'producer_is_cca')
PRODUCER_FIELDS = ('producer_name', 'producer_phone', 'producer_email')
ENDORSEMENT_FIELDS = ('function', 'form', 'edition', 'scheduled_names', 'scheduled_projects', 'blanket',
                      'manuscript', 'document_id')
AI_FUNCTIONS = ('ai_ongoing', 'ai_completed')
WAIVABLE_PREFIXES = ('NOT_AI', 'LIMIT_LOW', 'WRONG_', 'EXCLUSION', 'PRODUCER_INCOMPLETE', 'PL_NO_TAIL',
                     'COMPLETED_OPS_LAPSED', 'SCOPE_CHANGED', 'BLANKET_UNBACKED', 'MISSING')
_ANI_RE = re.compile(r'additional\s+named\s+insured', re.I)
_SUFFIXES = {'llc', 'inc', 'co', 'corp', 'corporation', 'company', 'ltd', 'the', 'lp', 'llp', 'pllc', 'pc'}


# ── small helpers ───────────────────────────────────────────────────────────
def _err(status, code, message, **extra):
    return SchemaError(status, code, message, **extra)


def norm_name(s) -> str:
    if not isinstance(s, str):
        return ''
    s = s.lower().replace('&', ' and ')
    words = [w for w in re.split(r'[^a-z0-9]+', s) if w and w not in _SUFFIXES]
    return ' '.join(words)


def names_match(wanted, candidates) -> bool:
    """True when the wanted name appears, as whole words, in one of the candidate texts."""
    w = norm_name(wanted)
    if not w:
        return False
    for c in candidates or []:
        n = norm_name(c)
        if n == w or f' {w} ' in f' {n} ':
            return True
    return False


def _add(lst, code):
    if code not in lst:
        lst.append(code)


def _d(s):
    try:
        return date.fromisoformat(s) if isinstance(s, str) else None
    except ValueError:
        return None


def _dt(s):
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')) if isinstance(s, str) else None
    except ValueError:
        return None


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:              # 29 Feb
        return d.replace(year=d.year + years, day=28)


def _edition_year(edition):
    m = re.match(r'^\s*(\d{2})\s*/?\s*(\d{2})\s*$', str(edition or ''))
    if not m:
        return None
    yy = int(m.group(2))
    return 1900 + yy if yy >= 50 else 2000 + yy


def load_templates() -> dict:
    return json.loads(TEMPLATES_PATH.read_text(encoding='utf-8'))


# ── requirement-set validation (refusals 422, warnings returned) ─────────────
def validate_items(items, trade_category=None) -> list:
    """Raise 422 on a trap the guide forbids; return warnings for the ones it cautions about."""
    if not isinstance(items, list) or not items:
        raise _err(422, 'bad_requirement_items', 'items must be a non-empty list of requirement objects')
    warnings, ids = [], set()
    for it in items:
        if not isinstance(it, dict):
            raise _err(422, 'bad_requirement_items', 'each requirement item is an object')
        text = json.dumps(it)
        if _ANI_RE.search(text) or it.get('function') == 'additional_named_insured':
            raise _err(422, 'additional_named_insured_refused',
                       'Never require "additional named insured" (guide p.41): it makes you a co-owner of the '
                       'policy. Require additional insured status instead.', item=it.get('id'))
        kind, line = it.get('kind'), it.get('line')
        if kind not in ('limit', 'ai', 'endorsement', 'disallowed_exclusion', 'producer', 'tail', 'cert_holder'):
            raise _err(422, 'bad_requirement_items', 'unknown requirement kind', item=it.get('id'), kind=str(kind)[:30])
        iid = it.get('id')
        if not isinstance(iid, str) or not iid or iid in ids:
            raise _err(422, 'bad_requirement_items', 'each item needs a unique id')
        ids.add(iid)
        is_ai = kind == 'ai' or (kind == 'endorsement' and it.get('function') in AI_FUNCTIONS)
        if is_ai and line == 'wc':
            raise _err(422, 'ai_on_wc_refused', 'Workers compensation never carries additional insured '
                                                '(guide p.11, p.37)', item=iid)
        if kind == 'endorsement':
            yr = _edition_year(it.get('edition'))
            if it.get('edition') and yr is None:
                raise _err(422, 'bad_requirement_items', 'edition is "MM YY", e.g. "04 13"', item=iid)
            if yr is not None and yr < 2004:
                raise _err(422, 'pre_2004_edition_refused', 'Pre-2004 ISO forms are not issuable by most '
                                                            'carriers (guide p.18, p.41). Use 2004 or later.', item=iid)
        if is_ai and line in ('pl', 'auto'):
            warnings.append({'item': iid, 'warning': f'Additional insured on {line.upper()} is cautioned against '
                                                     '(guide p.25, p.23). PL takes certificate holder only.'})
        if kind == 'limit':
            for k in ('min', 'floor'):
                v = it.get(k)
                if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                    raise _err(422, 'bad_requirement_items', f'{k} must be a dollar amount', item=iid)
            if trade_category == 'finish' and line == 'umbrella' and (it.get('min') or 0) > 2_000_000:
                warnings.append({'item': iid, 'warning': 'Umbrella above $2M on a finish trade over-demands '
                                                         '(guide p.18, p.25).'})
    return warnings


# ── evidence gathering (account-scoped) ─────────────────────────────────────
def _all(store, collection, where=None, archived=False):
    out, cursor = [], None
    while True:
        page = store.list(collection, where=where, limit=500, cursor=cursor, archived=archived, sort='created_at')
        out.extend(r for r in page['records'] if archived or not r['archived_at'])
        cursor = page['next_cursor']
        if not cursor:
            return out


def _get(store, collection, rid):
    try:
        return store.get(collection, rid)
    except SchemaError:
        return None


def gather(store, assignment_id) -> dict:
    """Everything one verdict reads, scoped to the project's account."""
    asg = store.get('crt_assignment', assignment_id)
    project = _get(store, 'crt_project', asg['data'].get('crt_project_id'))
    account_id = (project or {}).get('data', {}).get('crt_account_id')
    party_id = asg['data'].get('party_contact_id')
    company = _all(store, 'company')
    inputs = {
        'assignment': asg, 'project': project,
        'account': _get(store, 'contact', account_id) if account_id else None,
        'party': _get(store, 'contact', party_id) if party_id else None,
        'set': _get(store, 'crt_requirement_set', asg['data'].get('requirement_set_id')),
        'company_name': (company[0]['data'].get('name') if company else None),
        'links': [r for r in _all(store, 'crt_account_party', {'party_contact_id': party_id})
                  if r['data'].get('crt_account_id') == account_id] if account_id and party_id else [],
        'policies': [], 'endorsements': [], 'documents': {}, 'files_ok': {}, 'confirmations': [],
        'waivers': _all(store, 'crt_waiver', {'crt_assignment_id': assignment_id}),
        'reviews': _all(store, 'crt_review', {'crt_assignment_id': assignment_id}),
    }
    if account_id and party_id:
        inputs['policies'] = _all(store, 'crt_policy', {'party_contact_id': party_id, 'crt_account_id': account_id})
        pids = [p['id'] for p in inputs['policies']]
        if pids:
            inputs['endorsements'] = [e for e in _all(store, 'crt_endorsement', {'crt_account_id': account_id})
                                      if e['data'].get('crt_policy_id') in pids]
    record_ids = [r['id'] for r in inputs['policies'] + inputs['endorsements']]
    for k in ('project', 'account', 'party', 'set', 'assignment'):
        if inputs[k]:
            record_ids.append(inputs[k]['id'])
    record_ids += [r['id'] for r in inputs['links']]
    for i in range(0, len(record_ids), 200):
        inputs['confirmations'] += _all(store, 'crt_confirmation', {'record_id': {'$in': record_ids[i:i + 200]}})
    doc_ids = set()
    for p in inputs['policies']:
        doc_ids.add(p['data'].get('source_document_id'))
    for e in inputs['endorsements']:
        doc_ids.add(e['data'].get('document_id'))
    for w in inputs['waivers']:
        doc_ids.add(w['data'].get('acceptance_document_id'))
    for c in inputs['confirmations']:
        doc_ids.add(c['data'].get('source_document_id'))
    if project:
        doc_ids.add(project['data'].get('contract_document_id'))
    for did in filter(None, doc_ids):
        doc = _get(store, 'document', did)
        if doc and not doc['archived_at']:
            inputs['documents'][did] = doc
            fid = _file_id(doc)
            if fid and fid not in inputs['files_ok']:
                inputs['files_ok'][fid] = store.file_verified(fid)
    return inputs


def _file_id(doc):
    url = ((doc or {}).get('data') or {}).get('file_url') or ''
    fid = url[len(FILE_URL_PREFIX):] if url.startswith(FILE_URL_PREFIX) else None
    return fid if fid and FILE_ID_RE.match(fid) else None


# ── the pure verdict ───────────────────────────────────────────────────────
class _Confirmed:
    def __init__(self, inputs):
        self.i = inputs
        self.used = set()
        self.by_record = {}
        for c in inputs['confirmations']:
            if c['archived_at']:
                continue
            self.by_record.setdefault(c['data'].get('record_id'), []).append(c)

    def doc_ok(self, did) -> bool:
        doc = self.i['documents'].get(did)
        fid = _file_id(doc)
        return bool(fid and self.i['files_ok'].get(fid))

    def _valid(self, c, rec, collection, field, need_source):
        d = c['data']
        if d.get('record_collection') != collection or d.get('record_version') != rec['version']:
            return False
        if field not in (d.get('fields') or []):
            return False
        if need_source:
            doc = self.i['documents'].get(d.get('source_document_id'))
            fid = _file_id(doc)
            if not fid or fid != d.get('source_sha256') or not self.i['files_ok'].get(fid):
                return False
        return True

    def ok(self, rec, collection, field, *, need_source=True, two=False, person_ok=False) -> bool:
        if rec is None:
            return False
        if person_ok and not str(rec.get('updated_by') or '').startswith('agent:'):
            return True
        hits = [c for c in self.by_record.get(rec['id'], []) if self._valid(c, rec, collection, field, need_source)]
        people = {c['data'].get('confirmed_by') or c['created_by'] for c in hits}
        if not hits or (two and len(people) < 2):
            return False
        self.used.update(c['id'] for c in hits)
        return True


def evaluate_inputs(inputs: dict, today: date) -> dict:
    """The verdict for one sub on one project. Pure: same inputs + same today -> same answer."""
    conf = _Confirmed(inputs)
    asg, project, s_rec = inputs['assignment'], inputs['project'], inputs['set']
    codes, missing, variances, expired, suggestions, notes = [], [], [], [], [], []
    unconfirmed = []
    cells = {}
    expiring = None

    def unc(coll, field):
        code = f'UNCONFIRMED:{coll}.{field}'
        if code not in unconfirmed:
            unconfirmed.append(code)
        return code

    # the set: approved by staff at its current version
    items = []
    if not s_rec or s_rec['archived_at']:
        unc('crt_assignment', 'requirement_set_id')
    else:
        approved = s_rec['data'].get('status') == 'APPROVED' and \
            conf.ok(s_rec, 'crt_requirement_set', 'status', need_source=False) and \
            conf.ok(s_rec, 'crt_requirement_set', 'items', need_source=False)
        if not approved:
            unc('crt_requirement_set', 'status')
        items = s_rec['data'].get('items') or []
    if not project:
        unc('crt_assignment', 'crt_project_id')
    account, party = inputs['account'], inputs['party']
    if not [ln for ln in inputs['links'] if ln['data'].get('status', 'active') == 'active']:
        unc('crt_account_party', 'status')

    pd = (project or {}).get('data', {})
    # set-up fields a person types: person-written, or confirmed
    acct_name = party_name = None
    if account:
        if conf.ok(account, 'contact', 'name', need_source=False, person_ok=True):
            acct_name = account['data'].get('company_name') or account['data'].get('name')
        else:
            unc('contact', 'name')
    if party:
        if conf.ok(party, 'contact', 'name', need_source=False, person_ok=True):
            party_name = party['data'].get('company_name') or party['data'].get('name')
        else:
            unc('contact', 'name')

    def proj(field):
        if project and conf.ok(project, 'crt_project', field, need_source=False, person_ok=True):
            return pd.get(field), True
        unc('crt_project', field)
        return None, False

    # current policy per line: in force today, else the latest
    by_line = {}
    for p in inputs['policies']:
        by_line.setdefault(p['data'].get('line'), []).append(p)

    def current(line):
        ps = by_line.get(line) or []
        if not ps:
            return None
        def key(p):
            eff, exp = _d(p['data'].get('effective')), _d(p['data'].get('expiration'))
            in_force = bool(eff and exp and eff <= today < exp)
            return (in_force, exp or date.min, eff or date.min)
        return max(ps, key=key)

    company_name = inputs.get('company_name')

    def is_cca(pol):
        return bool(pol['data'].get('producer_is_cca')) or \
            (company_name and names_match(company_name, [pol['data'].get('producer_name')]))

    checked_policies = {}

    def pol_ok(pol, field):
        return conf.ok(pol, 'crt_policy', field, two=is_cca(pol))

    def policy_for(line, item_id):
        """The confirmed current policy for a line, or None (code recorded)."""
        pol = current(line)
        if pol is None:
            code = f'MISSING:{line}'
            if code not in missing:
                missing.append(code)
            cells[item_id] = code
            return None
        bad = [f for f in POLICY_BASE_FIELDS if not pol_ok(pol, f)]
        if bad:
            for f in bad:
                unc('crt_policy', f)
            cells[item_id] = 'UNCONFIRMED'
            return None
        if not conf.doc_ok(pol['data'].get('source_document_id')):
            code = f'MISSING:{line}_certificate'
            if code not in missing:
                missing.append(code)
            cells[item_id] = code
            return None
        checked_policies[pol['id']] = pol
        return pol

    def endorsements_for(pol, function):
        return [e for e in inputs['endorsements'] if e['data'].get('crt_policy_id') == pol['id']
                and e['data'].get('function') == function]

    sc, sc_ok = (proj('substantial_completion') if project else (None, False))
    sc_d = _d(sc) if sc_ok else None
    trade = str(asg['data'].get('trade_category') or '').lower()
    size = pd.get('size_flag')

    for it in items:
        iid, kind, line = it.get('id'), it.get('kind'), it.get('line')
        required = it.get('required', True)
        if kind == 'limit':
            pol = policy_for(line, iid)
            if pol is None:
                continue
            val = (pol['data'].get('limits') or {}).get(it.get('limit'))
            need = it.get('min', it.get('floor')) or 0
            ok = isinstance(val, (int, float)) and not isinstance(val, bool) and val >= need
            comb = it.get('combined')
            if not ok and comb and line == 'gl' and isinstance(val, (int, float)) and val >= comb.get('gl_occ_floor', 0):
                umb = current('umbrella')
                if umb is not None and all(pol_ok(umb, f) for f in POLICY_BASE_FIELDS) and \
                        conf.doc_ok(umb['data'].get('source_document_id')):
                    uv = (umb['data'].get('limits') or {}).get('each_occurrence') or 0
                    u_exp = _d(umb['data'].get('expiration'))
                    if u_exp and u_exp > today and val + uv >= comb.get('gl_occ_plus_umbrella_min', 0):
                        ok = True
                        notes.append(f'{iid}: met by the combined GL + umbrella test')
            if not ok:
                code = f'LIMIT_LOW:{line}'
                if code not in variances:
                    variances.append(code)
                cells[iid] = code
            else:
                cells[iid] = 'OK'
        elif kind == 'ai':
            if line == 'pl':
                cells[iid] = 'N/A'
                continue
            pol = policy_for(line, iid)
            if pol is None:
                continue
            if pol['data'].get('addl_insd') is True:
                cells[iid] = 'OK'
            else:
                _add(variances, f'NOT_AI:{line}')
                cells[iid] = f'NOT_AI:{line}'
        elif kind == 'cert_holder':
            pol = policy_for(line, iid)
            if pol is None:
                continue
            if not pol_ok(pol, 'certificate_holder'):
                unc('crt_policy', 'certificate_holder')
                cells[iid] = 'UNCONFIRMED'
            elif acct_name and names_match(acct_name, [pol['data'].get('certificate_holder')]):
                cells[iid] = 'OK'
            else:
                variances.append('WRONG_HOLDER')
                cells[iid] = 'WRONG_HOLDER'
        elif kind == 'tail':
            pol = policy_for(line, iid)
            if pol is None:
                continue
            if not (pol_ok(pol, 'occurrence_or_claims_made') and pol_ok(pol, 'tail')):
                unc('crt_policy', 'tail')
                cells[iid] = 'UNCONFIRMED'
            elif pol['data'].get('occurrence_or_claims_made') == 'claims_made' and not pol['data'].get('tail'):
                variances.append('PL_NO_TAIL')
                cells[iid] = 'PL_NO_TAIL'
            else:
                cells[iid] = 'OK'
        elif kind == 'disallowed_exclusion':
            when = it.get('when_trade')
            if when and not any(w in trade for w in when):
                cells[iid] = 'N/A'
                continue
            pol = policy_for(line, iid)
            if pol is None:
                continue
            if not pol_ok(pol, 'exclusions'):
                unc('crt_policy', 'exclusions')
                cells[iid] = 'UNCONFIRMED'
                continue
            have = [str(x).lower() for x in (pol['data'].get('exclusions') or [])]
            code = f"EXCLUSION:{it.get('code')}"
            if any(it.get('code') in h for h in have):
                variances.append(code)
                cells[iid] = code
            else:
                cells[iid] = 'OK'
        elif kind == 'producer':
            cells[iid] = 'OK'          # checked on every policy below
        elif kind == 'endorsement':
            fn = it.get('function')
            if not required:
                when = it.get('suggested_when') or []
                if size in when:
                    suggestions.append(f'SUGGESTED:{fn}')
                cells[iid] = 'SUGGESTED' if size in when else 'N/A'
                continue
            if fn == 'ai_ongoing' and sc_d and today > sc_d:
                cells[iid] = 'N/A'      # ongoing operations are over after substantial completion
                notes.append(f'{iid}: not needed after substantial completion')
                continue
            lapsed_rule = False
            if fn == 'ai_completed' and sc_d and today > sc_d:
                ry, ry_ok = proj('repose_years')
                if not ry_ok or not isinstance(ry, int):
                    unc('crt_project', 'repose_years')
                    cells[iid] = 'UNCONFIRMED'
                    continue
                if today >= _add_years(sc_d, ry):
                    cells[iid] = 'N/A'
                    notes.append(f'{iid}: statute of repose has run')
                    continue
                lapsed_rule = True
            pol = policy_for(line, iid)
            if pol is None:
                continue
            ends = endorsements_for(pol, fn)
            if not ends:
                code = 'COMPLETED_OPS_LAPSED' if lapsed_rule else f'MISSING:{fn}'
                (variances if lapsed_rule else missing).append(code)
                cells[iid] = code
                continue
            two = is_cca(pol)
            good, item_codes = False, []
            for e in ends:
                bad = [f for f in ENDORSEMENT_FIELDS if not conf.ok(e, 'crt_endorsement', f, two=two)]
                if bad:
                    for f in bad:
                        unc('crt_endorsement', f)
                    item_codes.append('UNCONFIRMED')
                    continue
                if not conf.doc_ok(e['data'].get('document_id')):
                    item_codes.append(f'MISSING:{fn}')
                    continue
                ed_year = _edition_year(e['data'].get('edition'))
                if ed_year is not None and ed_year < 2004:
                    item_codes.append(f'WRONG_FORM:{fn}')
                    continue
                if fn in AI_FUNCTIONS or it.get('names_parties'):
                    if e['data'].get('blanket'):
                        cr, cr_ok = (pd.get('contract_requires_ai'),
                                     project and conf.ok(project, 'crt_project', 'contract_requires_ai'))
                        if not cr_ok:
                            unc('crt_project', 'contract_requires_ai')
                            item_codes.append('UNCONFIRMED')
                            continue
                        if cr is not True:
                            item_codes.append('BLANKET_UNBACKED')
                            continue
                    else:
                        parties_ok = project and conf.ok(project, 'crt_project', 'contract_ai_parties')
                        if not parties_ok:
                            unc('crt_project', 'contract_ai_parties')
                            item_codes.append('UNCONFIRMED')
                            continue
                        pname, pn_ok = proj('name')
                        if not acct_name or not pn_ok:
                            item_codes.append('UNCONFIRMED')
                            continue
                        sched = e['data'].get('scheduled_names') or []
                        wanted = [acct_name] + list(pd.get('contract_ai_parties') or [])
                        if not all(names_match(w, sched) for w in wanted):
                            item_codes.append('WRONG_PARTY')
                            continue
                        if not names_match(pname, e['data'].get('scheduled_projects') or []):
                            item_codes.append('WRONG_PROJECT')
                            continue
                good = True
                break
            if good:
                cells[iid] = 'OK'
                continue
            if 'UNCONFIRMED' in item_codes:
                cells[iid] = 'UNCONFIRMED'
                continue
            code = item_codes[0]
            _add(missing if code == f'MISSING:{fn}' else variances, code)
            cells[iid] = code
        else:
            cells[iid] = 'N/A'

    # named insured + producer on every policy the verdict read
    for pol in checked_policies.values():
        if party_name and not names_match(party_name, [pol['data'].get('named_insured')]):
            if 'WRONG_INSURED' not in variances:
                variances.append('WRONG_INSURED')
        prod_bad = [f for f in PRODUCER_FIELDS if not pol_ok(pol, f)]
        if prod_bad:
            for f in prod_bad:
                unc('crt_policy', f)
        else:
            pdd = pol['data']
            if not pdd.get('producer_name') or not (pdd.get('producer_phone') or pdd.get('producer_email')):
                if 'PRODUCER_INCOMPLETE' not in variances:
                    variances.append('PRODUCER_INCOMPLETE')
        exp = _d(pol['data'].get('expiration'))
        if exp is not None:
            days = (exp - today).days
            line = pol['data'].get('line')
            if days <= 0:
                expired.append(f'EXPIRED:{line}')
            elif expiring is None or days < expiring['days']:
                expiring = {'line': line, 'days': days, 'expiration': exp.isoformat(), 'policy_id': pol['id']}

    # scope changed after the last review
    reviews = sorted((r for r in inputs['reviews'] if not r['archived_at']),
                     key=lambda r: r['data'].get('reviewed_at') or r['created_at'])
    last_review = reviews[-1] if reviews else None
    sca = _dt(asg['data'].get('scope_changed_at'))
    if last_review and sca:
        lr = _dt(last_review['data'].get('reviewed_at') or last_review['created_at'])
        if lr and sca > lr:
            variances.append('SCOPE_CHANGED')

    # contractor-accepted variances (written OK on file, unexpired)
    accepted = []
    live_waivers = {}
    for w in inputs['waivers']:
        wd = w['data']
        exp_w = _d(wd.get('expires'))
        if w['archived_at'] or not exp_w or exp_w <= today or not conf.doc_ok(wd.get('acceptance_document_id')):
            continue
        live_waivers[wd.get('variance_code')] = w
    for lst in (missing, variances):
        for code in list(lst):
            if code in live_waivers and code.startswith(WAIVABLE_PREFIXES):
                lst.remove(code)
                accepted.append({'code': code, 'waiver_id': live_waivers[code]['id'],
                                 'accepted_by_contractor': live_waivers[code]['data'].get('accepted_by_contractor'),
                                 'expires': live_waivers[code]['data'].get('expires')})

    if unconfirmed:
        state = 'cant_tell'
    elif missing:
        state = 'missing'
    elif variances:
        state = 'variance'
    elif expired:
        state = 'expired'
    elif expiring and expiring['days'] <= 15:
        state = 'expiring_15'
    elif expiring and expiring['days'] <= 30:
        state = 'expiring_30'
    else:
        state = 'meets'
    codes = unconfirmed + missing + variances + expired
    stale = True
    if last_review:
        lr = _dt(last_review['data'].get('reviewed_at') or last_review['created_at'])
        stale = lr is None or (today - lr.date()).days > REVIEW_STALE_DAYS
    hold_suggested = state in UNMET_STATES or stale
    why = []
    if state in UNMET_STATES:
        why.append(LABELS[state])
    if stale:
        why.append('no staff review in the last 30 days' if last_review else 'never reviewed by staff')
    return {
        'assignment_id': asg['id'], 'state': state, 'label': LABELS[state], 'codes': codes,
        'accepted': accepted, 'suggestions': suggestions, 'notes': notes, 'items': cells,
        'expiring': expiring, 'hold_suggested': hold_suggested, 'hold_suggested_because': why,
        'hold': bool(asg['data'].get('hold')), 'hold_reason': asg['data'].get('hold_reason'),
        'last_review_at': (last_review['data'].get('reviewed_at') if last_review else None),
        'requirement_set_id': s_rec['id'] if s_rec else None,
        'requirement_set_version': s_rec['data'].get('set_version') if s_rec else None,
        'policies_read': sorted(checked_policies), 'confirmation_ids': sorted(conf.used),
        'rule_version': RULE_VERSION, 'disclaimer': DISCLAIMER, 'as_of': today.isoformat(),
        'advisory': '[probation] advisory only; never auto-holds, auto-sends or gates a pay application',
    }


def evaluate(store, assignment_id, today: date) -> dict:
    return evaluate_inputs(gather(store, assignment_id), today)


# ── write hooks (contract: services/biz/packs/__init__.py) ─────────────────
def _used(store, set_id) -> bool:
    return bool(store.list('crt_review', where={'requirement_set_id': set_id}, limit=1, archived=True)['records'])


def _active_link(store, account_id, party_id) -> bool:
    links = store.list('crt_account_party', where={'crt_account_id': account_id, 'party_contact_id': party_id},
                       limit=50)['records']
    return any(ln['data'].get('status', 'active') == 'active' for ln in links)


def _private_doc(store, did, field):
    doc = _get(store, 'document', did) if did else None
    if doc is None or _file_id(doc) is None:
        raise _err(422, 'document_not_private', 'This must point at a document whose file is in the private '
                                                'store (biz-file:<id>)', field=field)
    return doc


def check_write(store, op, collection, data, current, *, by, agent, via_pack):
    if collection == 'document':
        url = data.get('file_url') or ''
        if url.startswith(FILE_URL_PREFIX):
            fid = url[len(FILE_URL_PREFIX):]
            if not FILE_ID_RE.match(fid):
                raise _err(422, 'file_not_found', 'biz-file:<id> must name a stored file', field='file_url')
            store.file_meta(fid)                       # 404 when no such stored file
            if data.get('crt_sha256') and data['crt_sha256'] != fid:
                raise _err(422, 'hash_mismatch', 'crt_sha256 must equal the stored file id', field='crt_sha256')
        elif data.get('crt_account_id') or str(data.get('kind') or '') in (
                'coi_acord25', 'endorsement', 'contract_excerpt', 'contractor_waiver_ok'):
            raise _err(422, 'file_must_be_private', 'Certificates, endorsements and contract papers are kept in '
                                                    'the private file store (POST /api/biz/v1/files), never '
                                                    'under /uploads', field='file_url')
        return
    if collection in ('crt_confirmation', 'crt_review'):
        if op == 'create' and not via_pack:
            raise _err(409, 'use_pack_route', f'{collection} records are made only by their own route '
                                              '(POST /api/biz/v1/crt/confirm or /crt/assignment/<id>/review)')
        return
    if collection == 'crt_requirement_set':
        if op in ('create', 'edit'):
            validate_items(data.get('items'), data.get('trade_category'))
            was = (current or {}).get('data', {}).get('status')
            if data.get('status') == 'APPROVED' and was != 'APPROVED' and not via_pack:
                raise _err(409, 'use_approve_route', 'A requirement set is approved only through '
                                                     'POST /api/biz/v1/crt/requirement-sets/<id>/approve')
        if op == 'edit' and not via_pack and _used(store, current['id']):
            raise _err(409, 'requirement_set_in_use', 'This set was used in a review, so it is frozen. Make a new '
                                                      'version: POST /api/biz/v1/crt/requirement-sets/<id>/revise')
        return
    if collection == 'crt_project' and op in ('create', 'edit'):
        if data.get('repose_years') is not None and not str(data.get('repose_source') or '').strip():
            raise _err(422, 'repose_source_required', 'Repose years need their source (statute citation); '
                                                      'there is no default', field='repose_source')
        return
    if collection == 'crt_account_party' and op in ('create', 'edit'):
        if data.get('crt_account_id') == data.get('party_contact_id'):
            raise _err(422, 'bad_link', 'An account cannot be linked to itself')
        return
    if collection == 'crt_assignment' and op in ('create', 'edit'):
        project = store.get('crt_project', data.get('crt_project_id'))
        if not _active_link(store, project['data'].get('crt_account_id'), data.get('party_contact_id')):
            raise _err(422, 'party_not_linked', "This sub has no active link to the project's account "
                                                '(crt_account_party)', field='party_contact_id')
        return
    if collection == 'crt_policy' and op in ('create', 'edit'):
        links = store.list('crt_account_party', where={'crt_account_id': data.get('crt_account_id'),
                                                        'party_contact_id': data.get('party_contact_id')},
                           limit=1)['records']
        if not links:
            raise _err(422, 'party_not_linked', 'A policy is filed for an account the sub is linked to',
                       field='crt_account_id')
        return
    if collection == 'crt_endorsement' and op in ('create', 'edit'):
        pol = store.get('crt_policy', data.get('crt_policy_id'))
        if pol['data'].get('crt_account_id') != data.get('crt_account_id'):
            raise _err(422, 'account_mismatch', "An endorsement belongs to the same account as its policy",
                       field='crt_account_id')
        return
    if collection == 'crt_waiver' and op in ('create', 'edit'):
        _private_doc(store, data.get('acceptance_document_id'), 'acceptance_document_id')
        return


# ── routes (contract: services/biz/packs/__init__.py) ──────────────────────
def register_routes(bp, api, ctx):
    from flask import jsonify, request

    base = f'{api}/crt'

    def today_arg():
        raw = request.args.get('today')
        if raw:
            d = _d(raw)
            if d is None:
                raise _err(400, 'bad_query', 'today must be YYYY-MM-DD')
            return d
        return datetime.now(timezone.utc).date()

    def route(rule, methods, name):
        def deco(fn):
            def view(**kw):
                ctx.require_pack(PACK)
                return fn(**kw)
            view.__name__ = 'crt_' + name
            bp.add_url_rule(base + rule, endpoint='crt_' + name, view_func=view, methods=methods)
            return fn
        return deco

    @route('/templates', ['GET'], 'templates')
    def templates():
        return jsonify(load_templates())

    @route('/requirement-sets/from-template', ['POST'], 'set_from_template')
    def set_from_template():
        body = ctx.body()
        key = body.get('template_key')
        tpl = next((t for t in load_templates()['templates'] if t['key'] == key), None)
        if tpl is None:
            raise _err(404, 'unknown_template', 'No such template', template_key=str(key)[:40])
        warnings = validate_items(tpl['items'], tpl['trade_category'])
        rec = ctx.store().create('crt_requirement_set', {
            'name': str(body.get('name') or tpl['name'])[:200], 'trade_category': tpl['trade_category'],
            'template_key': key, 'items': tpl['items'], 'guide_cites': tpl.get('guide_cites') or [],
            'notes': ' '.join(filter(None, [tpl.get('conflict_note'), tpl.get('single_source')])) or None,
            'set_version': 1},                      # no status = DRAFT; only staff approve
            by=ctx.by(), agent=ctx.is_agent(), source=body.get('source'), via_pack=True)
        return jsonify({'record': rec, 'warnings': warnings}), 201

    @route('/requirement-sets/<sid>/approve', ['POST'], 'set_approve')
    def set_approve(sid):
        ctx.require_person('approve a requirement set')
        store = ctx.store()
        rec = store.get('crt_requirement_set', sid)
        warnings = validate_items(rec['data'].get('items'), rec['data'].get('trade_category'))
        if rec['data'].get('status') != 'APPROVED':
            rec = store.update('crt_requirement_set', sid, {'status': 'APPROVED'}, expected_version=rec['version'],
                               by=ctx.by(), agent=False, via_pack=True)
        conf = store.create('crt_confirmation', {
            'record_collection': 'crt_requirement_set', 'record_id': sid, 'record_version': rec['version'],
            'fields': ['items', 'status'], 'confirmed_by': ctx.by(),
            'note': str(ctx.body().get('note') or 'approved for use')[:500]},
            by=ctx.by(), agent=False, via_pack=True)
        return jsonify({'record': rec, 'confirmation': conf, 'warnings': warnings})

    @route('/requirement-sets/<sid>/revise', ['POST'], 'set_revise')
    def set_revise(sid):
        body = ctx.body()
        store = ctx.store()
        old = store.get('crt_requirement_set', sid)
        d = dict(old['data'])
        if body.get('items') is not None:
            d['items'] = body['items']
        if body.get('name'):
            d['name'] = str(body['name'])[:200]
        warnings = validate_items(d.get('items'), d.get('trade_category'))
        d.pop('status', None)                   # a new version is a DRAFT until staff approve it
        d.update({'set_version': int(old['data'].get('set_version') or 1) + 1, 'supersedes': sid})
        rec = store.create('crt_requirement_set', d, by=ctx.by(), agent=ctx.is_agent(),
                           source=body.get('source'), via_pack=True)
        return jsonify({'record': rec, 'warnings': warnings}), 201

    @route('/confirm', ['POST'], 'confirm')
    def confirm():
        ctx.require_person('confirm a reading')
        body = ctx.body()
        store = ctx.store()
        coll, rid, fields = body.get('record_collection'), body.get('record_id'), body.get('fields')
        if coll not in CONFIRMABLE:
            raise _err(422, 'not_confirmable', 'Confirm one of: ' + ', '.join(sorted(CONFIRMABLE)))
        rec = store.get(coll, rid)
        if body.get('record_version') is not None and body['record_version'] != rec['version']:
            raise _err(409, 'version_conflict', 'The record changed since you read it; re-check it',
                       current=rec)
        schema_fields = (store.registry.get(coll) or {}).get('fields') or {}
        if not isinstance(fields, list) or not fields or not all(isinstance(f, str) for f in fields) or \
                any(f not in schema_fields for f in fields) or len(fields) > 60:
            raise _err(422, 'bad_request', '"fields" must list fields of this record that you checked')
        did, sha = body.get('source_document_id'), None
        needs_doc = coll in EVIDENCE or (coll == 'crt_project' and set(fields) & set(PROJECT_CONTRACT_FIELDS))
        if needs_doc or did:
            doc = _private_doc(store, did, 'source_document_id')
            sha = _file_id(doc)
            if not store.file_verified(sha):
                raise _err(422, 'source_file_unverified', 'The source file is missing or does not match its hash')
        rec2 = store.create('crt_confirmation', {
            'record_collection': coll, 'record_id': rid, 'record_version': rec['version'], 'fields': fields,
            'source_document_id': did, 'source_sha256': sha, 'confirmed_by': ctx.by(),
            'note': str(body.get('note'))[:500] if body.get('note') else None},
            by=ctx.by(), agent=False, via_pack=True)
        return jsonify(rec2), 201

    @route('/assignment/<aid>/state', ['GET'], 'state')
    def state(aid):
        return jsonify(evaluate(ctx.store(), aid, today_arg()))

    @route('/assignment/<aid>/review', ['POST'], 'review')
    def review(aid):
        ctx.require_person('record a review')
        store = ctx.store()
        ev = evaluate(store, aid, datetime.now(timezone.utc).date())
        now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        rec = store.create('crt_review', {
            'crt_assignment_id': aid, 'state': ev['state'], 'label': ev['label'], 'variance_codes': ev['codes'],
            'accepted_codes': [a['code'] for a in ev['accepted']], 'rule_version': RULE_VERSION,
            'requirement_set_id': ev['requirement_set_id'], 'requirement_set_version': ev['requirement_set_version'],
            'confirmation_ids': ev['confirmation_ids'], 'disclaimer_text': DISCLAIMER,
            'hold_suggested': ev['hold_suggested'], 'reviewed_by': ctx.by(), 'reviewed_at': now,
            'evaluation': ev}, by=ctx.by(), agent=False, via_pack=True)
        return jsonify({'review': rec, 'evaluation': ev}), 201

    @route('/assignment/<aid>/chase-sent', ['POST'], 'chase_sent')
    def chase_sent(aid):
        ctx.require_person('mark a chase as sent')
        body = ctx.body()
        store = ctx.store()
        store.get('crt_assignment', aid)
        stage = body.get('stage')
        if stage not in ('T-30', 'T-15', 'T-0', 'variance'):
            raise _err(422, 'bad_request', 'stage is T-30, T-15, T-0 or variance')
        store.note_activity(collection='crt_assignment', record_id=aid, type_='crt_chase_marked_sent', by=ctx.by(),
                            diff={'stage': stage, 'policy_id': body.get('policy_id'),
                                  'note': str(body.get('note') or '')[:500] or None})
        return jsonify({'ok': True, 'stage': stage}), 201

    @route('/project/<pid>/matrix', ['GET'], 'matrix')
    def matrix(pid):
        store = ctx.store()
        project = store.get('crt_project', pid)
        today = today_arg()
        rows = []
        for a in _all(store, 'crt_assignment', {'crt_project_id': pid}):
            ev = evaluate(store, a['id'], today)
            party = _get(store, 'contact', a['data'].get('party_contact_id'))
            rows.append({'assignment_id': a['id'], 'party_contact_id': a['data'].get('party_contact_id'),
                         'party_name': (party or {}).get('data', {}).get('company_name')
                         or (party or {}).get('data', {}).get('name'),
                         'trade_category': a['data'].get('trade_category'), 'state': ev['state'],
                         'label': ev['label'], 'codes': ev['codes'], 'accepted': ev['accepted'],
                         'items': ev['items'], 'hold_suggested': ev['hold_suggested'], 'hold': ev['hold'],
                         'expiring': ev['expiring'], 'policies_read': ev['policies_read']})
        return jsonify({'project': project, 'rows': rows, 'as_of': today.isoformat(), 'disclaimer': DISCLAIMER,
                        'rule_version': RULE_VERSION})

    def _rollup(store, account_id, today):
        projects = _all(store, 'crt_project', {'crt_account_id': account_id})
        counts = {s: 0 for s in LABELS}
        subs, expiring30 = set(), 0
        for p in projects:
            for a in _all(store, 'crt_assignment', {'crt_project_id': p['id']}):
                ev = evaluate(store, a['id'], today)
                counts[ev['state']] += 1
                subs.add(a['data'].get('party_contact_id'))
                if ev['expiring'] and ev['expiring']['days'] <= 30:
                    expiring30 += 1
        total = sum(counts.values())
        unmet = sum(counts[s] for s in ('missing', 'variance', 'expired'))
        return {'projects': len(projects), 'subs_tracked': len(subs), 'assignments': total, 'states': counts,
                'expiring_30': expiring30,
                'gap_rate': (round(unmet / (total - counts['cant_tell']), 3)
                             if total - counts['cant_tell'] > 0 else None)}

    @route('/book', ['GET'], 'book')
    def book():
        store = ctx.store()
        today = today_arg()
        acct_ids = []
        for ln in _all(store, 'crt_account_party'):
            if ln['data'].get('crt_account_id') not in acct_ids:
                acct_ids.append(ln['data'].get('crt_account_id'))
        for p in _all(store, 'crt_project'):
            if p['data'].get('crt_account_id') not in acct_ids:
                acct_ids.append(p['data'].get('crt_account_id'))
        out = []
        for aid in acct_ids:
            acct = _get(store, 'contact', aid)
            if not acct:
                continue
            out.append({'account_id': aid, 'name': acct['data'].get('company_name') or acct['data'].get('name'),
                        **_rollup(store, aid, today)})
        return jsonify({'accounts': out, 'as_of': today.isoformat(), 'disclaimer': DISCLAIMER})

    @route('/account/<aid>', ['GET'], 'account')
    def account(aid):
        store = ctx.store()
        today = today_arg()
        acct = store.get('contact', aid)
        projects = _all(store, 'crt_project', {'crt_account_id': aid})
        links = _all(store, 'crt_account_party', {'crt_account_id': aid})
        subs = []
        for ln in links:
            c = _get(store, 'contact', ln['data'].get('party_contact_id'))
            subs.append({'link': ln, 'party': c})
        return jsonify({'account': acct, 'projects': projects, 'subs': subs,
                        'rollup': _rollup(store, aid, today), 'as_of': today.isoformat()})

    @route('/party/<pid>', ['GET'], 'party')
    def party(pid):
        """One sub as seen by ONE account: only records received for that account."""
        store = ctx.store()
        account_id = request.args.get('account')
        if not account_id:
            raise _err(400, 'bad_query', 'account=<contractor account id> is required: a sub is always seen '
                                         'through one account')
        store.get('contact', account_id)
        c = store.get('contact', pid)
        if not store.list('crt_account_party', where={'crt_account_id': account_id, 'party_contact_id': pid},
                          limit=1)['records']:
            raise _err(404, 'not_linked', 'This sub is not linked to that account')
        pols = _all(store, 'crt_policy', {'party_contact_id': pid, 'crt_account_id': account_id})
        ends = [e for e in _all(store, 'crt_endorsement', {'crt_account_id': account_id})
                if e['data'].get('crt_policy_id') in {p['id'] for p in pols}]
        docs = _all(store, 'document', {'crt_account_id': account_id, 'contact_id': pid})
        asgs = [a for a in _all(store, 'crt_assignment', {'party_contact_id': pid})
                if (_get(store, 'crt_project', a['data'].get('crt_project_id')) or {}).get('data', {})
                .get('crt_account_id') == account_id]
        reviews, waivers = [], []
        for a in asgs:
            reviews += _all(store, 'crt_review', {'crt_assignment_id': a['id']})
            waivers += _all(store, 'crt_waiver', {'crt_assignment_id': a['id']})
        return jsonify({'party': c, 'account_id': account_id, 'policies': pols, 'endorsements': ends,
                        'documents': docs, 'assignments': asgs, 'reviews': reviews, 'waivers': waivers})

    @route('/renewals', ['GET'], 'renewals')
    def renewals():
        store = ctx.store()
        today = today_arg()
        cols = {'T-30': [], 'T-15': [], 'T-0': []}
        for a in _all(store, 'crt_assignment'):
            ev = evaluate(store, a['id'], today)
            pol_ids = ev['policies_read']
            for pid in pol_ids:
                pol = store.get('crt_policy', pid)
                exp = _d(pol['data'].get('expiration'))
                if not exp:
                    continue
                days = (exp - today).days
                stage = 'T-0' if days <= 0 else 'T-15' if days <= 15 else 'T-30' if days <= 30 else None
                if not stage:
                    continue
                party = _get(store, 'contact', a['data'].get('party_contact_id'))
                project = _get(store, 'crt_project', a['data'].get('crt_project_id'))
                pname = (party or {}).get('data', {}).get('company_name') or (party or {}).get('data', {}).get('name')
                proj_name = (project or {}).get('data', {}).get('name')
                line = (pol['data'].get('line') or '').upper()
                draft = (f"Hi {pname or 'there'}, the {line} policy {pol['data'].get('policy_number') or ''} we have on "
                         f"file for {proj_name or 'the project'} "
                         + (f"expired on {exp.isoformat()}" if days <= 0 else f"expires on {exp.isoformat()}")
                         + ". Please have your agent send the renewal certificate and the additional insured "
                           "endorsements (CG 20 10 / CG 20 37 / CG 20 01 or equivalents) naming this project. Thank you.")
                cols[stage].append({'assignment_id': a['id'], 'policy_id': pid, 'party_name': pname,
                                    'project_name': proj_name, 'line': pol['data'].get('line'),
                                    'expiration': exp.isoformat(), 'days': days,
                                    'producer': {k: pol['data'].get(k) for k in PRODUCER_FIELDS},
                                    'draft': draft})
        return jsonify({'columns': cols, 'as_of': today.isoformat(),
                        'note': 'Drafts only. A person sends them and marks them sent; nothing is sent automatically.'})
