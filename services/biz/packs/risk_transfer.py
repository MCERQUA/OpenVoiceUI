"""pack:risk-transfer — the certificate and endorsement tracker (CCA Risk Transfer Tracker).

Plan: docs/jambot/contractors-choice-risk-transfer-app-plan.md (rev 2), section 4.4. The guide it
follows is cited per value in default-schemas/packs/risk-transfer/templates.json.

The verdict reads ONLY confirmed inputs (rule 1, ruling b):
  - evidence fields (crt_policy, crt_endorsement, and the contract fields of crt_project) count
    only when a crt_confirmation covers that field at the record's CURRENT version and names a
    source document whose private file still hashes to the confirmation's source_sha256, and that
    document was received for THIS account and THIS sub (host rulings L3/N3/N4, 2026-11-17);
  - set-up fields a person types (account and sub names, project name / account / dates, the
    assignment's project, sub, set, trade and scope) count PER FIELD: the last logged write that set
    or changed THAT field was a person's, or a person confirmed that field at the record's current
    version (host ruling N1). A staff touch to another field, a hold, an archive or a note never
    turns an agent-written field into a person-written one;
  - a backing document (certificate, endorsement, contractor's written OK) counts only when its
    kind, account, sub and file were written or confirmed by a person; an endorsement document whose
    file is a certificate's file is never the endorsement (host ruling N2);
  - a requirement set counts only when APPROVED and confirmed by staff at its current version;
  - when we (the agency) are the sub's producer, evidence needs two different staff confirmers.
Anything else is "Can't tell (needs staff check)", code UNCONFIRMED:<collection>.<field>.

Nothing here sends, holds or pays anything. hold_suggested is advice; the stored hold is staff-set.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from services.biz.schema import SchemaError
from services.biz.store import FILE_ID_RE, FILE_URL_PREFIX, SURFACE_KEY

PACK = 'risk-transfer'
RULE_VERSION = 'crt-rules-1.6.0'
DISCLAIMER = ('Evidence check against the requirement set you approved. '
              'Not a coverage opinion or legal advice.')
LABELS = {
    'meets': 'Meets requirements (documents reviewed)',
    'meets_accepted': 'Meets requirements except contractor-accepted variances (documents reviewed)',
    'expiring_30': 'Meets requirements (documents reviewed), expiring within 30 days',
    'expiring_15': 'Meets requirements (documents reviewed), expiring within 15 days',
    'cant_tell': "Can't tell (needs staff check)",
    'missing': 'Missing documents',
    'variance': 'Variance',
    'expired': 'Expired',
}
# host ruling N9: an accepted variance never hides that the evidence is about to expire
ACCEPTED_EXPIRING_LABELS = {
    15: 'Meets requirements except contractor-accepted variances (documents reviewed), expiring within 15 days',
    30: 'Meets requirements except contractor-accepted variances (documents reviewed), expiring within 30 days',
}
UNMET_STATES = ('cant_tell', 'missing', 'variance', 'expired')
PERSON_PREFIXES = ('clerk:', 'local:')
REVIEW_STALE_DAYS = 30
TEMPLATES_PATH = Path(__file__).resolve().parents[3] / 'default-schemas' / 'packs' / 'risk-transfer' / 'templates.json'
PAGES_DIR = TEMPLATES_PATH.parent / 'pages'

EVIDENCE = {'crt_policy', 'crt_endorsement'}
PROJECT_CONTRACT_FIELDS = ('contract_requires_ai', 'contract_ai_parties')
CONFIRMABLE = {'crt_policy', 'crt_endorsement', 'crt_project', 'contact', 'crt_account_party', 'crt_assignment',
               'document', 'company'}      # company (rules 1.5.0, ruling P/C): staff re-vouch the agency record
# what makes a document "this sub's paper": a person wrote or confirmed each of these (rulings N2, L3)
DOC_IDENTITY_FIELDS = ('kind', 'crt_account_id', 'contact_id', 'file_url')
POLICY_BASE_FIELDS = ('line', 'policy_number', 'effective', 'expiration', 'limits', 'named_insured', 'addl_insd',
                      'source_document_id', 'producer_is_cca')
PRODUCER_FIELDS = ('producer_name', 'producer_phone', 'producer_email')
ENDORSEMENT_FIELDS = ('function', 'form', 'edition', 'scheduled_names', 'scheduled_projects', 'blanket',
                      'manuscript', 'document_id', 'crt_policy_id')   # N4: staff name the policy it attaches to
AI_FUNCTIONS = ('ai_ongoing', 'ai_completed')
# Host ruling H4 (2026-11-17): a waiver never clears a MISSING document; only variances are waivable.
WAIVABLE_PREFIXES = ('NOT_AI', 'LIMIT_LOW', 'WRONG_', 'EXCLUSION', 'PRODUCER_INCOMPLETE', 'PL_NO_TAIL',
                     'COMPLETED_OPS_LAPSED', 'SCOPE_CHANGED', 'BLANKET_UNBACKED')
# Host ruling N10: a certificate for a different company is never waivable, same as MISSING.
NEVER_WAIVABLE = ('WRONG_INSURED',)
# Assignment fields the verdict reads: they count only when a person wrote the assignment's current
# version, or a person confirmed them (claim-check H1).
ASSIGNMENT_FIELDS = ('crt_project_id', 'party_contact_id', 'requirement_set_id', 'requirement_set_version',
                     'trade_category', 'scope_changed_at')
LINK_FIELDS = ('crt_account_id', 'party_contact_id', 'status')
CONTACT_NAME_FIELDS = ('name', 'company_name')
_ANI_RE = re.compile(r'additional\s+named\s+insured', re.I)

# Host ruling H2 (2026-11-17): the legal-entity suffix is part of the name (LLC != Inc != Corp); only
# spellings of the SAME suffix are equal. Every other letter is kept, non-ASCII included (Moller != Muller).
_SUFFIX_CANON = {'inc': 'inc', 'incorporated': 'inc', 'llc': 'llc', 'corp': 'corp', 'corporation': 'corp',
                 'co': 'co', 'company': 'co', 'ltd': 'ltd', 'limited': 'ltd', 'lp': 'lp', 'llp': 'llp',
                 'lllp': 'lllp', 'pllc': 'pllc', 'pc': 'pc', 'pa': 'pa', 'plc': 'plc'}
_SUFFIX_PHRASES = ((('professional', 'limited', 'liability', 'company'), 'pllc'),
                   (('limited', 'liability', 'company'), 'llc'),
                   (('limited', 'liability', 'partnership'), 'llp'),
                   (('limited', 'partnership'), 'lp'),
                   (('professional', 'corporation'), 'pc'))

# Host ruling H1 (2026-11-17): the trade is a structured category staff choose, never a substring; the
# requirement set says which categories an exclusion item applies to (trade_categories) and which
# exclusion classes it disallows (classes). Exclusions on a policy are staff-mapped classes; wording
# that is not a class reads "Can't tell". 'none' is staff saying "policy exclusions reviewed: none".
TRADE_CATEGORIES = (
    'excavation', 'trenching', 'earthwork', 'grading', 'demolition', 'blasting', 'shoring',
    'underground_utilities', 'pile_driving', 'tunneling', 'structural_steel', 'steel_erection', 'crane_rigging',
    'heavy_civil', 'roofing', 'waterproofing', 'windows_glazing', 'curtain_wall', 'siding_cladding', 'hvac',
    'electrical', 'plumbing', 'fire_protection', 'concrete', 'framing', 'masonry', 'painting', 'drywall',
    'flooring', 'tile', 'landscaping', 'signage', 'architect', 'engineer', 'other')
EXCLUSION_NONE = 'none'
EXCLUSION_CLASSES = (
    EXCLUSION_NONE, 'xcu', 'explosion', 'collapse', 'underground', 'water_intrusion', 'moisture',
    'water_damage_sublimit', 'insured_vs_insured', 'residential', 'eifs', 'subsidence', 'mold', 'silica',
    'pollution', 'action_over', 'designated_ongoing_operations', 'classification_limitation',
    'height_limitation', 'other_reviewed')
# what an exclusion item disallows when the set names no classes of its own
EXCLUSION_FAMILIES = {'xcu': ('xcu', 'explosion', 'collapse', 'underground'),
                      'water': ('water_intrusion', 'moisture', 'water_damage_sublimit')}
# Host ruling H3: a non-manuscript form's number says what it is. CG 20 10 is never completed operations.
# Host ruling R4 (rules 1.4.0): this is only the DEFAULT. A requirement set carries its own `form_functions`
# (copied from templates.json at draft time, approved with the set, versioned with it), so CCA's licensed staff
# change an equivalence per set version. CG 20 38 stays ongoing ops (safe direction: a wrong call reads
# MISSING, never a false Meets) until they rule; see the plan's gate-5 spec note.
ISO_FORM_FUNCTION = {'CG2010': 'ai_ongoing', 'CG2033': 'ai_ongoing', 'CG2038': 'ai_ongoing',
                     'CG2037': 'ai_completed', 'CG2001': 'pnc', 'CG2503': 'project_aggregate',
                     'CG2545': 'completed_ops_aggregate', 'CG2404': 'waiver_of_subrogation'}
# Host ruling R3: CG 20 10 never satisfies completed operations, by any route (set, manuscript flag, waiver).
NEVER_COMPLETED_OPS = ('CG2010',)
# Host ruling F (rules 1.5.0): a built-in safety floor no requirement set can loosen. A built-in ISO number keeps its
# own ISO category (CG 20 10 / 20 33 / 20 38 are ongoing operations only; completed operations needs CG 20 37 or a
# form the set's table lists as completed operations), and an endorsement item's form list is never empty. Checked
# at every set write, at approval, and again at every verdict (a stored set that breaks it reads Can't tell).
ISO_FLOOR = dict(ISO_FORM_FUNCTION)
# Other ISO additional-insured numbers a set might list, that are never completed operations (re-check #5 F2):
# CG 20 26 (designated person or organization) and CG 20 11 (managers or lessors of premises).
ISO_NOT_COMPLETED = ('CG2026', 'CG2011')
# Host ruling G (rules 1.6.0): a floored or otherwise ISO additional-insured number (the CG 20 xx series, every number
# above) is evaluated as that ISO form whatever its manuscript flag says. A manuscript is a non-ISO form only.
_ISO_AI_RE = re.compile(r'^CG20\d\d$')


def is_iso_number(fk) -> bool:
    """An ISO number (built-in floor, ISO_NOT_COMPLETED, or any CG 20 xx): never read as a manuscript (ruling G)."""
    return bool(fk) and (fk in ISO_FLOOR or fk in ISO_NOT_COMPLETED or bool(_ISO_AI_RE.match(fk)))


# Host ruling M (rules 1.6.0): the keys an item may carry. Anything else (a typo, a renamed key) is refused on write,
# so every key that can change a verdict is one the approval page names. An item's identity keys never change under
# the same id: a revision that wants another limit / line / coverage removes the item and adds a new one.
ITEM_KEYS = ('id', 'kind', 'line', 'required', 'cite', 'limit', 'floor', 'ceiling', 'min', 'function', 'forms',
             'edition', 'names_parties', 'suggested_when', 'code', 'classes', 'combined', 'trade_categories',
             'tail_required', 'when_trade')
ITEM_IDENTITY_KEYS = ('kind', 'line', 'limit', 'function', 'code')
COMBINED_KEYS = ('gl_occ_floor', 'gl_occ_plus_umbrella_min', 'note')
# Host ruling D (rules 1.6.0): fields of a requirement set only the pack's own routes set (never a generic write)
SET_SERVER_FIELDS = ('supersedes', 'template_key', 'set_version')
FORM_FUNCTIONS = ('ai_ongoing', 'ai_completed', 'pnc', 'project_aggregate', 'completed_ops_aggregate',
                  'waiver_of_subrogation')
_MONTHS = {m: i for i, m in enumerate(('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT',
                                       'NOV', 'DEC'), start=1)}


# ── small helpers ───────────────────────────────────────────────────────────
def _err(status, code, message, **extra):
    return SchemaError(status, code, message, **extra)


def norm_name(s) -> str:
    """NFKC + casefold, punctuation and spacing collapsed, every letter kept, the entity suffix kept in one
    spelling per kind (host ruling H2): 'Acme Builders, L.L.C.' == 'ACME BUILDERS LLC' != 'Acme Builders Inc'."""
    if not isinstance(s, str):
        return ''
    s = unicodedata.normalize('NFKC', s).casefold().replace('&', ' and ')
    toks, run = [], []
    for t in re.split(r'[\W_]+', s):
        if not t:
            continue
        if len(t) == 1 and t.isalpha():          # L.L.C. / P.C.: initials read as one word
            run.append(t)
            continue
        if run:
            toks.append(''.join(run))
            run = []
        toks.append(t)
    if run:
        toks.append(''.join(run))
    out, i = [], 0
    while i < len(toks):
        for phrase, canon in _SUFFIX_PHRASES:
            if tuple(toks[i:i + len(phrase)]) == phrase:
                out.append(canon)
                i += len(phrase)
                break
        else:
            out.append(_SUFFIX_CANON.get(toks[i], toks[i]))
            i += 1
    return ' '.join(out)


def category_key(s):
    """A trade category as staff picked it ('Steel erection' -> 'steel_erection'); None when it is not one
    of TRADE_CATEGORIES (free text is never matched by substring: host ruling H1)."""
    k = re.sub(r'[\W_]+', '_', unicodedata.normalize('NFKC', str(s or '')).casefold()).strip('_')
    return k if k in TRADE_CATEGORIES else None


def exclusion_classes(values):
    """(classes, unmapped) for a policy's confirmed exclusions. A blank list is NOT 'no exclusions' (H1)."""
    classes, unmapped = [], []
    for v in values if isinstance(values, list) else ([] if values in (None, '') else [values]):
        k = re.sub(r'[\W_]+', '_', unicodedata.normalize('NFKC', str(v)).casefold()).strip('_')
        if k in EXCLUSION_CLASSES:
            if k not in classes:
                classes.append(k)
        else:
            unmapped.append(str(v)[:120])
    return classes, unmapped


def form_key(s) -> str:
    return re.sub(r'[^A-Z0-9]', '', unicodedata.normalize('NFKC', str(s or '')).upper())


def is_person(by) -> bool:
    return str(by or '').startswith(PERSON_PREFIXES)


def names_equal(wanted, candidates) -> bool:
    """True when one candidate IS the wanted name after normalising (host ruling N8: no containment)."""
    w = norm_name(wanted)
    return bool(w) and any(norm_name(c) == w for c in (candidates or []))


def field_writers(events, rec) -> dict:
    """Who last set or changed each field of rec, from its append-only activity log (host ruling N1).

    {field: by}, plus '' -> the record's creator. A field whose current value is not the value its
    last logged write left (a write the log does not explain) maps to None: nobody vouches for it.
    A PERSON's write counts as the person only for the fields the store stamped as written on the platform
    staff surface (R1 guard redesign); any other person-write (a canvas page, which may be the agent's) maps
    to 'unvouched:<by>', which is not a person: the verdict reads it as Can't tell, the same as an agent write.
    """
    who, val, creator = {}, {}, None

    def vouched(by, field, d):
        if not is_person(by):
            return by
        return by if field in (d.get(SURFACE_KEY) or ()) else f'unvouched:{by}'
    for ev in events or []:
        d = ev.get('diff') or {}
        if ev.get('type') == 'created':
            by = ev.get('by')
            creator = by if (not is_person(by) or SURFACE_KEY in d) else f'unvouched:{by}'
            for k, v in (d.get('set') or {}).items():
                who[k], val[k] = vouched(by, k, d), v
        elif ev.get('type') == 'updated':
            for k, ch in (d.get('changed') or {}).items():
                if isinstance(ch, list) and len(ch) == 2:
                    who[k], val[k] = vouched(ev.get('by'), k, d), ch[1]
    data = (rec or {}).get('data') or {}
    out = {k: (who[k] if val[k] == data.get(k) else None) for k in who}
    for k in data:
        out.setdefault(k, None)
    out[''] = creator
    return out


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
    """The year of an ISO edition date written the ways a form footer prints it: '04 13', '11-85', '(11/85)',
    '1185', '11/1985', 'Nov 1985', 'CG 20 10 11 85'. None when it is empty or not a date (MED ruling)."""
    k = _edition_key(edition)
    return k[1] if k else None


def _edition_key(edition):
    """(month, year) of an ISO edition date as printed (see _edition_year), or None."""
    s = unicodedata.normalize('NFKC', str(edition or '')).upper().strip()
    s = re.sub(r'^[A-Z]{2}\s*\d{2}\s*\d{2}(?=\s|\(|$)', ' ', s)      # the form number printed before it
    s = re.sub(r'[()\[\]]', ' ', s).strip().rstrip('.').strip()
    m = re.match(r'^([A-Z]{3})[A-Z]*\.?\s*,?\s*(\d{2}|\d{4})$', s)
    if m and m.group(1) in _MONTHS:
        month, year = _MONTHS[m.group(1)], m.group(2)
    else:
        m = re.match(r'^(\d{1,2})\s*[-/.\s]?\s*(\d{2}|\d{4})$', s)
        if not m:
            return None
        month, year = int(m.group(1)), m.group(2)
    if not 1 <= month <= 12:
        return None
    y = int(year)
    if len(year) == 2:
        y = 1900 + y if y >= 50 else 2000 + y
    return (month, y) if 1950 <= y <= 2100 else None


def validate_form_functions(ff):
    """A set's form equivalences: {form number: function}. CG 20 10 is never completed operations (R3)."""
    if ff is None:
        return
    if not isinstance(ff, dict) or not ff:
        raise _err(422, 'bad_form_functions', 'form_functions maps a form number to its function, '
                                              'e.g. {"CG 20 37": "ai_completed"}')
    for k, v in ff.items():
        if not isinstance(k, str) or not form_key(k) or v not in FORM_FUNCTIONS:
            raise _err(422, 'bad_form_functions', 'each form_functions entry is "<form number>": one of '
                       + ', '.join(FORM_FUNCTIONS), form=str(k)[:30])
        if form_key(k) in NEVER_COMPLETED_OPS and v == 'ai_completed':
            raise _err(422, 'cg2010_never_completed_ops', 'CG 20 10 covers ongoing operations only; it never '
                                                          'satisfies completed operations (host ruling R3)')
        if form_key(k) in ISO_NOT_COMPLETED and v == 'ai_completed':
            raise _err(422, 'iso_form_floor', f'{k} is an ISO form that never covers completed operations (host '
                                              'ruling F safety floor)', form=str(k)[:30])
        if form_key(k) in ISO_FLOOR and v != ISO_FLOOR[form_key(k)]:
            raise _err(422, 'iso_form_floor', f'{k} is an ISO {ISO_FLOOR[form_key(k)]} form; no requirement set can '
                                              'map it to another kind of coverage (host ruling F safety floor)',
                       form=str(k)[:30], iso_function=ISO_FLOOR[form_key(k)])


def set_form_functions(set_data) -> dict:
    """{form key: function} the set uses: its own approved table, else the built-in default. A built-in ISO number
    always keeps its ISO category (host ruling F floor), whatever a stored table says."""
    ff = (set_data or {}).get('form_functions')
    out = {form_key(k): v for k, v in ff.items() if isinstance(k, str)} if isinstance(ff, dict) and ff else {}
    out.update(ISO_FLOOR)
    return out


def floor_violations(set_data) -> list:
    """Why a set breaks the built-in safety floor (host ruling F), [] when it does not. Pure; also used at verdict
    time, so a set stored before rules 1.5.0 (or written around the API) reads Can't tell, never loosens the floor."""
    d = set_data or {}
    out = []
    ff = d.get('form_functions')
    table = {}
    if isinstance(ff, dict):
        for k, v in ff.items():
            fk = form_key(k)
            if fk in ISO_FLOOR and v != ISO_FLOOR[fk]:
                out.append(f'form_functions maps {k} to {v}; it is ISO {ISO_FLOOR[fk]}')
            if fk in ISO_NOT_COMPLETED and v == 'ai_completed':
                out.append(f'form_functions maps {k} to completed operations; it never is')
            table[fk] = v
    elif ff is not None:
        out.append('form_functions is not a table')
    for it in d.get('items') or []:
        if isinstance(it, dict) and it.get('kind') == 'limit':
            # plan S2 / ruling M (rules 1.6.0): min_* is the floor unless staff RAISE it; never below it, never absent
            mn, fl = it.get('min'), it.get('floor')
            if limit_need(it) is None:
                out.append(f"item {it.get('id')}: a limit item with no minimum and no floor")
            elif isinstance(mn, (int, float)) and isinstance(fl, (int, float)) and not isinstance(mn, bool) and \
                    not isinstance(fl, bool) and mn < fl:
                out.append(f"item {it.get('id')}: minimum {mn} is below its floor {fl}")
            continue
        if not isinstance(it, dict) or it.get('kind') != 'endorsement':
            continue
        fn, forms = it.get('function'), it.get('forms')
        if not isinstance(forms, list) or not [f for f in forms if isinstance(f, str) and form_key(f)]:
            out.append(f"item {it.get('id')}: its form list is empty")
            continue
        for f in forms:
            fk = form_key(f)
            if fk in ISO_FLOOR and ISO_FLOOR[fk] != fn:
                out.append(f"item {it.get('id')} ({fn}) lists {f}, an ISO {ISO_FLOOR[fk]} form")
            elif fn == 'ai_completed' and fk not in ISO_FLOOR and table.get(fk) != 'ai_completed':
                out.append(f"item {it.get('id')} (completed operations) lists {f}, which the set's table does not "
                           'list as a completed-operations form')
    return out


def validate_floor(items, form_functions):
    """422 when a set's items + table would loosen the built-in safety floor (host ruling F)."""
    bad = floor_violations({'items': items, 'form_functions': form_functions})
    if bad:
        raise _err(422, 'safety_floor', 'This requirement set would loosen the built-in safety floor: '
                   + '; '.join(bad[:5]) + ' (host ruling F)', reasons=bad[:20])


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
        unknown = sorted(str(k)[:40] for k in it if k not in ITEM_KEYS)
        if unknown:                     # host ruling M: a key the approval page cannot name never reaches a verdict
            raise _err(422, 'unknown_item_key', 'Requirement items carry only these keys: ' + ', '.join(ITEM_KEYS)
                       + ' (host ruling M)', item=iid, keys=unknown[:10])
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
            forms = it.get('forms')
            if forms is not None and (not isinstance(forms, list) or not all(isinstance(f, str) and form_key(f)
                                                                             for f in forms)):
                raise _err(422, 'bad_requirement_items', 'forms lists the accepted form numbers, e.g. ["CG 20 37"]',
                           item=iid)
            if not forms:                       # host ruling F floor: never an empty form list
                raise _err(422, 'forms_required', 'An endorsement item lists the form numbers it accepts, e.g. '
                                                  '["CG 20 37"]; an empty list is never allowed (host ruling F)',
                           item=iid)
            if it.get('function') == 'ai_completed' and any(form_key(f) in NEVER_COMPLETED_OPS for f in forms or []):
                raise _err(422, 'cg2010_never_completed_ops', 'CG 20 10 covers ongoing operations only; it never '
                                                              'satisfies completed operations (host ruling R3)',
                           item=iid)
        if kind == 'disallowed_exclusion':
            if it.get('when_trade') is not None:
                raise _err(422, 'when_trade_retired', 'Trade applicability is a list of trade categories '
                                                      '(trade_categories), never words matched inside the trade '
                                                      '(host ruling H1)', item=iid)
            cats = it.get('trade_categories')
            if cats is not None and (not isinstance(cats, list) or not cats or
                                     any(category_key(c) != c for c in cats)):
                raise _err(422, 'bad_requirement_items', 'trade_categories must list categories from: '
                           + ', '.join(TRADE_CATEGORIES), item=iid)
            classes = it.get('classes')
            if classes is None:
                classes = EXCLUSION_FAMILIES.get(it.get('code'))
            if not isinstance(classes, (list, tuple)) or not classes or \
                    any(c not in EXCLUSION_CLASSES or c == EXCLUSION_NONE for c in classes):
                raise _err(422, 'bad_requirement_items', 'classes must list the exclusion classes this item '
                           'disallows, from: ' + ', '.join(c for c in EXCLUSION_CLASSES if c != EXCLUSION_NONE),
                           item=iid)
        if is_ai and line in ('pl', 'auto'):
            warnings.append({'item': iid, 'warning': f'Additional insured on {line.upper()} is cautioned against '
                                                     '(guide p.25, p.23). PL takes certificate holder only.'})
        if kind == 'limit':
            for k in ('min', 'floor'):
                v = it.get(k)
                if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                    raise _err(422, 'bad_requirement_items', f'{k} must be a dollar amount', item=iid)
            if not isinstance(it.get('limit'), str) or not it.get('limit'):
                raise _err(422, 'bad_requirement_items', 'a limit item names the policy limit it reads, e.g. '
                                                         '"each_occurrence"', item=iid)
            if limit_need(it) is None:      # host ruling M: no minimum and no floor is never $0
                raise _err(422, 'limit_floor_required', 'A limit item carries its floor (and a minimum at or above '
                                                        'it); a missing minimum means the floor, never $0 '
                                                        '(host ruling M)', item=iid)
            comb = it.get('combined')
            if comb is not None:
                if line != 'gl' or not isinstance(comb, dict) or any(k not in COMBINED_KEYS for k in comb) or \
                        any(isinstance(comb.get(k), bool) or not isinstance(comb.get(k), (int, float)) or comb[k] < 0
                            for k in ('gl_occ_floor', 'gl_occ_plus_umbrella_min')):
                    raise _err(422, 'bad_requirement_items', 'combined is {"gl_occ_floor": <$>, '
                                                             '"gl_occ_plus_umbrella_min": <$>} on a GL limit item',
                               item=iid)
            if trade_category == 'finish' and line == 'umbrella' and (it.get('min') or 0) > 2_000_000:
                warnings.append({'item': iid, 'warning': 'Umbrella above $2M on a finish trade over-demands '
                                                         '(guide p.18, p.25).'})
    return warnings


def limit_need(it):
    """The minimum a limit item asks for: its `min`, or its `floor` when `min` is missing or null (host ruling M:
    never $0 by omission). None when the item carries neither (refused on write; reads Can't tell)."""
    for k in ('min', 'floor'):
        v = (it or {}).get(k)
        if v is not None and not isinstance(v, bool) and isinstance(v, (int, float)):
            return v
    return None


def check_item_identity(old_items, new_items):
    """422 when a revision keeps an item's id but changes what the item IS (its kind, line, limit, function or
    exclusion code): host ruling M. Remove the item and add a new one instead, so the page shows both whole."""
    was = {it.get('id'): it for it in old_items or [] if isinstance(it, dict)}
    for it in new_items or []:
        if not isinstance(it, dict) or it.get('id') not in was:
            continue
        moved = [k for k in ITEM_IDENTITY_KEYS if was[it['id']].get(k) != it.get(k)]
        if moved:
            raise _err(422, 'item_identity_changed', f"Item {it['id']} would change its {', '.join(moved)}. An "
                       'item keeps what it measures; remove it and add a new item with its own id (host ruling M)',
                       item=it['id'], keys=moved)


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


def _writers(store, collection, rec):
    try:
        return field_writers(store.timeline(collection, rec['id']), rec)
    except SchemaError:
        return {}


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
        'company': company[0] if company else None,
        'links': [r for r in _all(store, 'crt_account_party', {'party_contact_id': party_id})
                  if r['data'].get('crt_account_id') == account_id] if account_id and party_id else [],
        'policies': [], 'endorsements': [], 'documents': {}, 'files_ok': {}, 'confirmations': [],
        'waivers': _all(store, 'crt_waiver', {'crt_assignment_id': assignment_id}),
        'reviews': _all(store, 'crt_review', {'crt_assignment_id': assignment_id}),
        'account_id': account_id, 'party_id': party_id, 'writers': {}, 'cert_files': set(),
        'newer_sets': [],
    }
    # LOW ruling: later revisions of the pinned set (the verdict stays pinned; a newer APPROVED one is noted)
    if inputs['set']:
        sets = _all(store, 'crt_requirement_set')
        parent = {r['id']: r['data'].get('supersedes') for r in sets}
        for r in sets:
            cur, seen = parent.get(r['id']), set()
            while cur and cur not in seen and len(seen) < 100:
                if cur == inputs['set']['id']:
                    inputs['newer_sets'].append(r)
                    break
                seen.add(cur)
                cur = parent.get(cur)
    # every record of the verdict's scope, archived ones too: an archive or unarchive is a provenance event
    # (host ruling P, rules 1.5.0): one not made by a person on the staff surface reads Can't tell until staff
    # vouch for the record again on the staff surface
    scope = {}                                   # id -> (collection, record)
    if account_id and party_id:
        all_pols = _all(store, 'crt_policy', {'party_contact_id': party_id, 'crt_account_id': account_id},
                        archived=True)
        inputs['policies'] = [p for p in all_pols if not p['archived_at']]
        all_pids = [p['id'] for p in all_pols]
        pids = [p['id'] for p in inputs['policies']]
        all_ends = [e for e in _all(store, 'crt_endorsement', {'crt_account_id': account_id}, archived=True)
                    if e['data'].get('crt_policy_id') in all_pids] if all_pids else []
        inputs['endorsements'] = [e for e in all_ends if not e['archived_at'] and e['data'].get('crt_policy_id') in pids]
        for coll, recs in (('crt_policy', all_pols), ('crt_endorsement', all_ends),
                           ('crt_account_party', [r for r in _all(store, 'crt_account_party',
                                                                   {'party_contact_id': party_id}, archived=True)
                                                  if r['data'].get('crt_account_id') == account_id])):
            for r in recs:
                scope[r['id']] = (coll, r)
    for coll, r in (('crt_assignment', asg), ('crt_project', project), ('contact', inputs['account']),
                    ('contact', inputs['party']), ('crt_requirement_set', inputs['set']), ('company', inputs['company'])):
        if r:
            scope[r['id']] = (coll, r)
    record_ids = [r['id'] for r in inputs['policies'] + inputs['endorsements']]
    for k in ('project', 'account', 'party', 'set', 'assignment'):
        if inputs[k]:
            record_ids.append(inputs[k]['id'])
    record_ids += [r['id'] for r in inputs['links']]
    record_ids += [r['id'] for r in inputs['newer_sets']]
    record_ids += [rid for rid in scope if rid not in record_ids]
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
        if doc:
            scope[doc['id']] = ('document', doc)
        if doc and not doc['archived_at']:
            inputs['documents'][did] = doc
            fid = _file_id(doc)
            if fid and fid not in inputs['files_ok']:
                inputs['files_ok'][fid] = store.file_verified(fid)
    # a person's confirmation of a document's kind / account / sub / file (rulings N2, L3), and of any document
    # in scope (an archived one staff vouch for again, ruling P)
    dids = sorted(set(inputs['documents']) | {rid for rid, (c, _r) in scope.items() if c == 'document'})
    for i in range(0, len(dids), 200):
        inputs['confirmations'] += _all(store, 'crt_confirmation', {'record_id': {'$in': dids[i:i + 200]}})
    # every certificate file on this sub-for-this-account: an endorsement document is never one of them (N2)
    if account_id and party_id:
        for d in _all(store, 'document', {'crt_account_id': account_id, 'contact_id': party_id}, archived=True):
            if d['data'].get('kind') == 'coi_acord25' and _file_id(d):
                inputs['cert_files'].add(_file_id(d))
    for p in inputs['policies']:
        src = _get(store, 'document', p['data'].get('source_document_id'))
        if _file_id(src):
            inputs['cert_files'].add(_file_id(src))
    # per-field provenance of every set-up record and document the verdict reads (ruling N1)
    for coll, recs in (('crt_assignment', [asg]), ('crt_project', [project]), ('company', [inputs['company']]),
                       ('contact', [inputs['account'], inputs['party']]),
                       ('crt_account_party', inputs['links']), ('document', list(inputs['documents'].values())),
                       ('crt_policy', inputs['policies'])):
        for r in recs:
            if r:
                inputs['writers'][r['id']] = _writers(store, coll, r)
    # ruling P: the last archive / unarchive of each record in scope, with who made it and where
    inputs['toggles'] = {}
    for rid, (coll, r) in scope.items():
        try:
            evs = [ev for ev in store.timeline(coll, rid) if ev.get('type') in ('archived', 'unarchived')]
        except SchemaError:
            continue
        if evs:
            inputs['toggles'][rid] = {'collection': coll, 'record': r, 'event': evs[-1]}
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
        self.doc_codes = {}
        for c in inputs['confirmations']:
            if c['archived_at']:
                continue
            self.by_record.setdefault(c['data'].get('record_id'), []).append(c)

    def person_wrote(self, rec, field) -> bool:
        """The last logged write that set or changed THIS field was a person's (ruling N1)."""
        ws = (self.i.get('writers') or {}).get(rec['id'])
        if not ws:
            return False
        if field in ws:
            return is_person(ws[field])
        return is_person(ws.get(''))           # never written: whoever created the record left it blank

    def doc_check(self, did, *, kind=None, party=True) -> str:
        """'' when the document backs THIS sub's verdict for THIS account; else why not:
        'missing' (no such live, hash-verified paper of that kind), 'foreign' (another account's or
        sub's paper, rulings L3/N3), 'unconfirmed' (its kind / account / sub / file is agent-written)."""
        doc = self.i['documents'].get(did)
        fid = _file_id(doc)
        if not doc or not fid or not self.i['files_ok'].get(fid):
            return 'missing'
        dd = doc['data']
        if dd.get('crt_account_id') != self.i.get('account_id') or \
                (party and dd.get('contact_id') != self.i.get('party_id')):
            return 'foreign'
        if kind is not None and dd.get('kind') != kind:
            return 'missing'
        fields = [f for f in DOC_IDENTITY_FIELDS if (f != 'kind' or kind is not None) and (f != 'contact_id' or party)]
        bad = [f for f in fields if not self.ok(doc, 'document', f, need_source=False, person_ok=True)]
        if bad:
            self.doc_codes[did] = [f'UNCONFIRMED:document.{f}' for f in bad]
            return 'unconfirmed'
        return ''

    def _valid(self, c, rec, collection, field, need_source):
        d = c['data']
        if d.get('record_collection') != collection or d.get('record_version') != rec['version']:
            return False
        # host ruling C (rules 1.5.0): a confirmation counts only when a person made it on the staff surface
        if d.get('on_staff_surface') is not True or not is_person(d.get('confirmed_by')):
            return False
        if field not in (d.get('fields') or []):
            return False
        if need_source:
            doc = self.i['documents'].get(d.get('source_document_id'))
            fid = _file_id(doc)
            if not fid or fid != d.get('source_sha256') or not self.i['files_ok'].get(fid):
                return False
            if self.doc_check(d.get('source_document_id'), party=collection in EVIDENCE) != '':
                return False                  # read from another account's / sub's paper (L3)
        return True

    def ok(self, rec, collection, field, *, need_source=True, two=False, person_ok=False) -> bool:
        if rec is None:
            return False
        if person_ok and self.person_wrote(rec, field):
            return True
        hits = [c for c in self.by_record.get(rec['id'], []) if self._valid(c, rec, collection, field, need_source)]
        people = {c['data'].get('confirmed_by') for c in hits}
        if not hits or (two and len(people) < 2):
            return False
        self.used.update(c['id'] for c in hits)
        return True

    def revouched(self, rec) -> bool:
        """Staff vouched for this record on the staff surface at its CURRENT version (any confirmation)."""
        return any(c['data'].get('record_version') == rec['version'] and c['data'].get('on_staff_surface') is True
                   and is_person(c['data'].get('confirmed_by')) for c in self.by_record.get(rec['id'], []))


def evaluate_inputs(inputs: dict, today: date) -> dict:
    """The verdict for one sub on one project. Pure: same inputs + same today -> same answer."""
    conf = _Confirmed(inputs)
    asg, project, s_rec = inputs['assignment'], inputs['project'], inputs['set']
    codes, missing, variances, expired, suggestions, notes = [], [], [], [], [], []
    unconfirmed = []
    cells = {}
    expiring = None
    # host ruling W (rules 1.6.0): every waivable code is raised by one piece of paper for one function; a waiver
    # binds to exactly those (function, document id, file hash) triples, so other or changed paper re-raises it
    raised = {}

    def occur(code, function, doc_id):
        b = {'function': function, 'document_id': doc_id,
             'sha256': _file_id(inputs['documents'].get(doc_id)) if doc_id else None}
        lst = raised.setdefault(code, [])
        if b not in lst:
            lst.append(b)

    def unc(coll, field):
        code = f'UNCONFIRMED:{coll}.{field}'
        if code not in unconfirmed:
            unconfirmed.append(code)
        return code

    # the assignment itself: a person wrote (or confirmed) EACH of these fields -- claim-check H1, ruling N1
    for f in ASSIGNMENT_FIELDS:
        if not conf.ok(asg, 'crt_assignment', f, need_source=False, person_ok=True):
            unc('crt_assignment', f)
    # the set: approved by staff at its current version, and the version the assignment pinned
    items = []
    if not s_rec or s_rec['archived_at']:
        unc('crt_assignment', 'requirement_set_id')
    else:
        if asg['data'].get('requirement_set_version') != s_rec['version']:
            unc('crt_assignment', 'requirement_set_version')
        approved = s_rec['data'].get('status') == 'APPROVED' and \
            conf.ok(s_rec, 'crt_requirement_set', 'status', need_source=False) and \
            conf.ok(s_rec, 'crt_requirement_set', 'items', need_source=False) and \
            (not s_rec['data'].get('form_functions') or
             conf.ok(s_rec, 'crt_requirement_set', 'form_functions', need_source=False))
        if not approved:
            unc('crt_requirement_set', 'status')
        items = s_rec['data'].get('items') or []
    form_fn = set_form_functions((s_rec or {}).get('data'))
    # host ruling F: a stored set that breaks the safety floor (written before rules 1.5.0, or around the API) is
    # never read as loosening it
    for why in (floor_violations(s_rec['data']) if s_rec else []):
        unc('crt_requirement_set', 'items')
        notes.append(f'requirement set below the safety floor: {why}')
    if not project:
        unc('crt_assignment', 'crt_project_id')
    account, party = inputs['account'], inputs['party']
    active = [ln for ln in inputs['links'] if ln['data'].get('status', 'active') == 'active']
    if not active:
        unc('crt_account_party', 'status')
    else:
        # the link itself: each field a person wrote or confirmed (ruling N1); report the closest link's gaps
        # rules 1.5.0: EVERY active link for this pair is read, so an extra link nobody vouched for reads Can't tell
        for ln in active:
            for f in LINK_FIELDS:
                if not conf.ok(ln, 'crt_account_party', f, need_source=False, person_ok=True):
                    unc('crt_account_party', f)
    # host ruling P (rules 1.5.0): archiving or unarchiving a record the verdict reads is a provenance event. One not
    # made by a person on the staff surface (the agent key, a canvas page) reads Can't tell -- never a skipped
    # check, never the record silently counting again -- until staff vouch for the record again on the staff
    # surface (a staff-surface archive / unarchive, or a staff confirmation at its current version).
    for rid, t in sorted((inputs.get('toggles') or {}).items()):
        ev = t['event']
        if is_person(ev.get('by')) and SURFACE_KEY in (ev.get('diff') or {}):
            continue
        if conf.revouched(t['record']):
            continue
        unc(t['collection'], 'archived_at')
        notes.append(f"{t['collection']} {rid} was {ev.get('type')} off the staff surface; staff check it again")

    pd = (project or {}).get('data', {})
    # set-up fields a person types: each one person-written, or confirmed (ruling N1)
    acct_name = party_name = None

    def contact_name(c):
        bad = [f for f in CONTACT_NAME_FIELDS if not conf.ok(c, 'contact', f, need_source=False, person_ok=True)]
        for f in bad:
            unc('contact', f)
        return None if bad else (c['data'].get('company_name') or c['data'].get('name'))
    if account:
        acct_name = contact_name(account)
    if party:
        party_name = contact_name(party)

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
    company_rec = inputs.get('company')
    # Host ruling C (rules 1.5.0): the two-person rule fails CLOSED. The agency's own record must exist, be live and
    # carry a name a person wrote (or confirmed) on the staff surface; the policy's producer_is_cca must be present
    # and written by a person on the staff surface (it is a staff-only field). Anything else is Can't tell, and the
    # policy is read as the agency's own (two confirmers), never silently as someone else's.
    company_vouched = bool(company_rec and not company_rec['archived_at'] and company_name and
                           conf.ok(company_rec, 'company', 'name', need_source=False, person_ok=True))

    def is_cca(pol):
        if not company_vouched:
            unc('company', 'name')
            return True
        flag = pol['data'].get('producer_is_cca')
        if not isinstance(flag, bool) or not conf.person_wrote(pol, 'producer_is_cca'):
            unc('crt_policy', 'producer_is_cca')
            return True
        if flag:
            return True
        return names_match(company_name, [pol['data'].get('producer_name')])

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
        src_state = conf.doc_check(pol['data'].get('source_document_id'))
        if src_state == 'unconfirmed':
            for c in conf.doc_codes.get(pol['data'].get('source_document_id'), []):
                _add(unconfirmed, c)
            cells[item_id] = 'UNCONFIRMED'
            return None
        if src_state:
            code = f'MISSING:{line}_certificate'
            if code not in missing:
                missing.append(code)
            cells[item_id] = code
            return None
        eff, exp = _d(pol['data'].get('effective')), _d(pol['data'].get('expiration'))
        if eff is None or exp is None:                       # claim-check H3: no dates, no verdict
            unc('crt_policy', 'effective' if eff is None else 'expiration')
            cells[item_id] = 'UNCONFIRMED'
            return None
        if eff > today:                                      # next term's certificate, sent early
            _add(missing, f'NOT_IN_FORCE:{line}')
            cells[item_id] = f'NOT_IN_FORCE:{line}'
            return None
        checked_policies[pol['id']] = pol
        return pol

    def umbrella_for_combined():
        """The umbrella the combined GL + umbrella test may lean on: the current one, passing the same checks
        policy_for runs (every base field confirmed, this sub's paper, dates, in force today), or None."""
        umb = current('umbrella')
        if umb is None or not all(pol_ok(umb, f) for f in POLICY_BASE_FIELDS) or \
                conf.doc_check(umb['data'].get('source_document_id')) != '':
            return None
        u_eff, u_exp = _d(umb['data'].get('effective')), _d(umb['data'].get('expiration'))
        return umb if u_eff and u_exp and u_eff <= today < u_exp else None

    def endorsements_for(pol, function):
        return [e for e in inputs['endorsements'] if e['data'].get('crt_policy_id') == pol['id']
                and e['data'].get('function') == function]

    if project:
        proj('crt_account_id')       # which account's evidence is read at all (ruling N1)
    sc, sc_ok = (proj('substantial_completion') if project else (None, False))
    sc_d = _d(sc) if sc_ok else None
    trade_ok = conf.ok(asg, 'crt_assignment', 'trade_category', need_source=False, person_ok=True)
    trade = category_key(asg['data'].get('trade_category')) if trade_ok else None
    size = pd.get('size_flag')

    for it in items:
        iid, kind, line = it.get('id'), it.get('kind'), it.get('line')
        required = it.get('required', True)
        if kind == 'limit':
            need = limit_need(it)                # ruling M: a missing / null min is the floor, never $0
            if need is None:
                unc('crt_requirement_set', 'items')
                cells[iid] = 'UNCONFIRMED'
                continue
            pol = policy_for(line, iid)
            if pol is None:
                continue
            val = (pol['data'].get('limits') or {}).get(it.get('limit'))
            ok = isinstance(val, (int, float)) and not isinstance(val, bool) and val >= need
            comb = it.get('combined')
            if not ok and isinstance(comb, dict) and line == 'gl' and isinstance(val, (int, float)) and \
                    not isinstance(val, bool) and isinstance(comb.get('gl_occ_floor'), (int, float)) and \
                    isinstance(comb.get('gl_occ_plus_umbrella_min'), (int, float)) and val >= comb['gl_occ_floor']:
                umb = umbrella_for_combined()
                if umb is not None:
                    uv = (umb['data'].get('limits') or {}).get('each_occurrence')
                    if isinstance(uv, (int, float)) and not isinstance(uv, bool) and \
                            val + uv >= comb['gl_occ_plus_umbrella_min']:
                        ok = True
                        # host ruling U (rules 1.6.0): the umbrella this test leans on gets every check a policy
                        # gets (named insured, producer, expiry window), whether or not the set has an umbrella item
                        checked_policies[umb['id']] = umb
                        notes.append(f'{iid}: met by the combined GL + umbrella test')
            if not ok:
                code = f'LIMIT_LOW:{line}'
                if code not in variances:
                    variances.append(code)
                occur(code, iid, pol['data'].get('source_document_id'))
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
                occur(f'NOT_AI:{line}', iid, pol['data'].get('source_document_id'))
                cells[iid] = f'NOT_AI:{line}'
        elif kind == 'cert_holder':
            pol = policy_for(line, iid)
            if pol is None:
                continue
            if not pol_ok(pol, 'certificate_holder'):
                unc('crt_policy', 'certificate_holder')
                cells[iid] = 'UNCONFIRMED'
            elif acct_name and names_equal(acct_name, [pol['data'].get('certificate_holder')]):
                cells[iid] = 'OK'
            else:
                _add(variances, 'WRONG_HOLDER')
                occur('WRONG_HOLDER', iid, pol['data'].get('source_document_id'))
                cells[iid] = 'WRONG_HOLDER'
        elif kind == 'tail':
            pol = policy_for(line, iid)
            if pol is None:
                continue
            if not (pol_ok(pol, 'occurrence_or_claims_made') and pol_ok(pol, 'tail')):
                unc('crt_policy', 'tail')
                cells[iid] = 'UNCONFIRMED'
            elif pol['data'].get('occurrence_or_claims_made') == 'claims_made' and not pol['data'].get('tail'):
                _add(variances, 'PL_NO_TAIL')
                occur('PL_NO_TAIL', iid, pol['data'].get('source_document_id'))
                cells[iid] = 'PL_NO_TAIL'
            else:
                cells[iid] = 'OK'
        elif kind == 'disallowed_exclusion':
            # host ruling H1: which trades the item covers is a list of staff-chosen categories, never words
            # found inside the trade; a set still carrying the old substring rule is not read at all
            if it.get('when_trade') is not None:
                unc('crt_requirement_set', 'items')
                cells[iid] = 'UNCONFIRMED'
                continue
            cats = it.get('trade_categories')
            if cats:
                if not trade_ok:
                    cells[iid] = 'UNCONFIRMED'           # UNCONFIRMED:crt_assignment.trade_category is recorded
                    continue
                if trade is None:                       # not one of the categories: nobody can say it applies
                    unc('crt_assignment', 'trade_category')
                    notes.append(f'{iid}: the trade is not one of the listed trade categories; staff pick one')
                    cells[iid] = 'UNCONFIRMED'
                    continue
                if trade not in cats:
                    cells[iid] = 'N/A'
                    continue
            pol = policy_for(line, iid)
            if pol is None:
                continue
            if not pol_ok(pol, 'exclusions'):
                unc('crt_policy', 'exclusions')
                cells[iid] = 'UNCONFIRMED'
                continue
            have, unmapped = exclusion_classes(pol['data'].get('exclusions'))
            if unmapped or not have or (EXCLUSION_NONE in have and len(have) > 1):
                # wording not mapped to a class, a blank list, or "none" beside a class: nobody has said what
                # the policy excludes (host ruling H1: blank is "none" only when staff confirmed "none")
                unc('crt_policy', 'exclusions')
                notes.append(f'{iid}: policy exclusions need a staff check (map each to a class, or confirm '
                             '"policy exclusions reviewed: none")')
                cells[iid] = 'UNCONFIRMED'
                continue
            banned = it.get('classes') or EXCLUSION_FAMILIES.get(it.get('code')) or ()
            code = f"EXCLUSION:{it.get('code')}"
            if any(c in banned for c in have):
                _add(variances, code)
                occur(code, iid, pol['data'].get('source_document_id'))
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
                _add(variances if lapsed_rule else missing, code)
                if lapsed_rule:
                    occur(code, fn, None)
                cells[iid] = code
                continue
            two = is_cca(pol)
            # Host ruling E (rules 1.5.0): EVERY check runs on EVERY endorsement and each one's codes are kept in
            # full. No finding (an edition, a pre-2004 form, a naming miss, anything) stops the later checks, so a
            # waiver of one code can never stand in for another check that was never computed.
            per_end = []
            for e in ends:
                ecodes = []

                def ec(code, _ecodes=ecodes):
                    if code not in _ecodes:
                        _ecodes.append(code)
                for f in ENDORSEMENT_FIELDS:
                    if not conf.ok(e, 'crt_endorsement', f, two=two):
                        ec(f'UNCONFIRMED:crt_endorsement.{f}')
                e_doc = e['data'].get('document_id')
                e_state = conf.doc_check(e_doc, kind='endorsement')
                if e_state == 'unconfirmed':                # an agent's "endorsement" label alone never counts (N2)
                    for c in conf.doc_codes.get(e_doc, []):
                        ec(c)
                elif e_state or e_doc == pol['data'].get('source_document_id') or \
                        _file_id(inputs['documents'].get(e_doc)) in inputs.get('cert_files', ()):
                    ec(f'MISSING:{fn}')                     # the certificate is a summary; the endorsement is the proof
                # host ruling H3: the form number staff confirmed must be one the item accepts; a known ISO form
                # for another purpose is not this endorsement at all.
                # R3 (rules 1.4.0): CG 20 10 never satisfies completed operations, by any route; a LISTED ISO form
                # number (the set's table or the built-in one) is never read as manuscript, whatever the flag says;
                # a manuscript form satisfies completed ops only when staff confirmed its explicit completed-
                # operations wording (a person's confirmation, made only on the staff surface).
                # F (rules 1.5.0): the item's own form list is the only list (never empty); a built-in ISO number
                # always keeps its ISO category.
                # G (rules 1.6.0): an ISO number (the floor, CG 20 26 / 20 11, any CG 20 xx) is that ISO form whatever
                # its manuscript flag says; CG 20 26 / 20 11 are never completed operations, like CG 20 10.
                fk = form_key(e['data'].get('form'))
                iso = is_iso_number(fk)
                if fn == 'ai_completed' and (fk in NEVER_COMPLETED_OPS or fk in ISO_NOT_COMPLETED):
                    ec(f'MISSING:{fn}')
                elif e['data'].get('manuscript') is not True or fk in form_fn or iso:
                    accepted = {form_key(f) for f in it.get('forms') or []}
                    if fk not in accepted:
                        if form_fn.get(fk) not in (None, fn):
                            ec(f'MISSING:{fn}')
                        else:
                            ec('UNCONFIRMED:crt_endorsement.form')
                            notes.append(f"{iid}: form {e['data'].get('form') or '(blank)'} is not a listed form "
                                         'for this item; ' + (
                                             'it is an ISO form, read as that form (never as a manuscript); list '
                                             'it in the requirement set only if your licensed staff accept it for '
                                             'this coverage' if iso else
                                             'list the equivalent in the requirement set or confirm it as a '
                                             'manuscript form'))
                elif fn == 'ai_completed' and not (e['data'].get('completed_ops_wording') is True and
                                                   conf.ok(e, 'crt_endorsement', 'completed_ops_wording', two=two)):
                    ec('UNCONFIRMED:crt_endorsement.completed_ops_wording')
                    notes.append(f'{iid}: a manuscript endorsement counts for completed operations only when staff '
                                 'confirm its explicit completed-operations wording')
                ed_year = _edition_year(e['data'].get('edition'))
                want_ed = _edition_key(it.get('edition')) if it.get('edition') else None
                if ed_year is None:                     # MED ruling: an empty or unreadable edition is no pass
                    ec('UNCONFIRMED:crt_endorsement.edition')
                else:
                    if ed_year < 2004:
                        ec(f'WRONG_FORM:{fn}')
                    # LOW ruling (rules 1.4.0): the set's edition for this item is a requirement, not a note
                    if want_ed and _edition_key(e['data'].get('edition')) != want_ed:
                        ec(f'WRONG_EDITION:{fn}')
                if fn in AI_FUNCTIONS or it.get('names_parties'):
                    # K (rules 1.6.0): blanket applies only when there is no schedule; a schedule that names anyone
                    # (a party or a project) is checked like any other, whatever the blanket flag says
                    if e['data'].get('blanket') and not (e['data'].get('scheduled_names') or
                                                         e['data'].get('scheduled_projects')):
                        cr, cr_ok = (pd.get('contract_requires_ai'),
                                     project and conf.ok(project, 'crt_project', 'contract_requires_ai'))
                        if not cr_ok:
                            ec('UNCONFIRMED:crt_project.contract_requires_ai')
                        elif cr is not True:
                            ec('BLANKET_UNBACKED')
                    else:
                        if not (project and conf.ok(project, 'crt_project', 'contract_ai_parties')):
                            ec('UNCONFIRMED:crt_project.contract_ai_parties')
                        pname = pd.get('name')
                        if not (project and conf.ok(project, 'crt_project', 'name', need_source=False,
                                                    person_ok=True)):
                            ec('UNCONFIRMED:crt_project.name')
                        if not acct_name:
                            ec('UNCONFIRMED:contact.name')
                        sched = e['data'].get('scheduled_names') or []
                        wanted = [acct_name] + list(pd.get('contract_ai_parties') or [])
                        if not all(names_equal(w, sched) for w in wanted if w):     # N8: exact, like M3
                            ec('WRONG_PARTY')
                        if not names_equal(pname, e['data'].get('scheduled_projects') or []):   # ruling (a): exact
                            ec('WRONG_PROJECT')
                per_end.append((e, ecodes))
            if any(not c for _e, c in per_end):
                # one endorsement carries no code at all; an endorsement on the same item that nobody has checked
                # still reads Can't tell (rules 1.5.0: every check runs, Can't tell is never hidden). Variances on
                # another paper (another project's endorsement for this sub) do not count against this one.
                pending = [c for _e, cs in per_end for c in cs if c.startswith('UNCONFIRMED')]
                if pending:
                    for c in pending:
                        _add(unconfirmed, c)
                    cells[iid] = 'UNCONFIRMED'
                else:
                    cells[iid] = 'OK'
                continue
            first = None
            for e, cs in per_end:
                for c in cs:
                    if c.startswith('UNCONFIRMED'):
                        _add(unconfirmed, c)
                    else:
                        _add(missing if c == f'MISSING:{fn}' else variances, c)
                        occur(c, fn, e['data'].get('document_id'))
                        first = first or c
            cells[iid] = 'UNCONFIRMED' if any(c.startswith('UNCONFIRMED') for _e, cs in per_end for c in cs) else first
        else:
            cells[iid] = 'N/A'

    # named insured + producer on every policy the verdict read
    for pol in checked_policies.values():
        if party_name and norm_name(party_name) != norm_name(pol['data'].get('named_insured')):
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
                occur('PRODUCER_INCOMPLETE', 'producer:' + str(pdd.get('line')), pdd.get('source_document_id'))
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
            occur('SCOPE_CHANGED', 'scope@' + str(asg['data'].get('scope_changed_at')), None)

    # contractor-accepted variances (written OK on file, unexpired)
    accepted = []
    live_waivers = {}
    for w in inputs['waivers']:
        wd = w['data']
        exp_w = _d(wd.get('expires'))
        if w['archived_at'] or not exp_w or exp_w <= today or \
                conf.doc_check(wd.get('acceptance_document_id'), kind='contractor_waiver_ok', party=False) != '':
            continue                      # N3: the written OK of THIS assignment's contractor account only
        live_waivers.setdefault(wd.get('variance_code'), []).append(w)
    for lst in (missing, variances):
        for code in list(lst):
            if code not in live_waivers or not code.startswith(WAIVABLE_PREFIXES) or code in NEVER_WAIVABLE:
                continue
            # host ruling W (rules 1.6.0): the waiver covers the code only on the paper it was given for: every
            # place the code is raised now (function + document id + file hash) is one a waiver was bound to
            bound = {(b.get('function'), b.get('document_id'), b.get('sha256'))
                     for w in live_waivers[code] for b in (w['data'].get('bound_evidence') or [])
                     if isinstance(b, dict)}
            need = [(o['function'], o['document_id'], o['sha256']) for o in raised.get(code) or []]
            uncovered = [o for o in need if o not in bound]
            if not need or uncovered:
                notes.append(f'{code}: the contractor accepted this on other paper; it is raised again here '
                             '(' + ', '.join(f'{fn_} on document {d_ or "-"}' for fn_, d_, _s in uncovered[:3])
                             + ')')
                continue
            w0 = live_waivers[code][0]
            lst.remove(code)
            accepted.append({'code': code, 'waiver_id': w0['id'],
                             'accepted_by_contractor': w0['data'].get('accepted_by_contractor'),
                             'expires': w0['data'].get('expires')})

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
    if accepted and state in ('meets', 'expiring_15', 'expiring_30'):
        state = 'meets_accepted'           # host ruling H4: never plain Meets when a variance was accepted
    label = LABELS[state]
    if state == 'meets_accepted' and expiring and expiring['days'] <= 30:
        label = ACCEPTED_EXPIRING_LABELS[15 if expiring['days'] <= 15 else 30]
    # LOW ruling: a newer APPROVED revision of the pinned set is said on the label; the verdict stays pinned
    newer = [r for r in inputs.get('newer_sets') or [] if not r['archived_at']
             and r['data'].get('status') == 'APPROVED'
             and conf.ok(r, 'crt_requirement_set', 'status', need_source=False)
             and conf.ok(r, 'crt_requirement_set', 'items', need_source=False)]
    newer_set = None
    if newer:
        n = max(newer, key=lambda r: int(r['data'].get('set_version') or 0))
        newer_set = {'id': n['id'], 'set_version': n['data'].get('set_version'), 'name': n['data'].get('name')}
        label += '; newer requirement set available'
        notes.append(f"newer requirement set available: {n['data'].get('name') or n['id']} version "
                     f"{n['data'].get('set_version')}; this verdict stays on the pinned set until staff re-pin it")
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
        'assignment_id': asg['id'], 'state': state, 'label': label, 'codes': codes,
        'accepted': accepted, 'suggestions': suggestions, 'notes': notes, 'items': cells,
        'expiring': expiring, 'hold_suggested': hold_suggested, 'hold_suggested_because': why,
        'hold': bool(asg['data'].get('hold')), 'hold_reason': asg['data'].get('hold_reason'),
        'last_review_at': (last_review['data'].get('reviewed_at') if last_review else None),
        'requirement_set_id': s_rec['id'] if s_rec else None,
        'requirement_set_version': s_rec['data'].get('set_version') if s_rec else None,
        'newer_requirement_set': newer_set,
        'policies_read': sorted(checked_policies), 'confirmation_ids': sorted(conf.used),
        'raised': raised,
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
        if op in ('create', 'edit') and not via_pack:
            # host ruling D (rules 1.6.0): which set a set replaces, the template it came from and its version number
            # are the pack's own routes' to set (draft, revise): an agent or a generic write never names the base
            # an approval is compared against
            was_d = (current or {}).get('data', {})
            moved = [k for k in SET_SERVER_FIELDS if data.get(k) != was_d.get(k)]
            if moved:
                raise _err(409, 'set_field_server_only', 'supersedes, template_key and set_version are set by the '
                                                         'draft and revise routes only (host ruling D)',
                           fields=moved)
        if op == 'edit' and current:
            check_item_identity(current['data'].get('items'), data.get('items'))
        if agent and op in ('edit', 'archive', 'unarchive'):
            raise _err(403, 'set_change_staff_only', 'The agent only drafts requirement sets (from a template or as '
                                                     'a revision, POST /api/biz/v1/crt/requirement-sets/<id>/revise); '
                                                     'editing, archiving and unarchiving a set happen on the staff '
                                                     'pages (host ruling F)')
        if op in ('create', 'edit'):
            validate_items(data.get('items'), data.get('trade_category'))
            validate_form_functions(data.get('form_functions'))
            validate_floor(data.get('items'), data.get('form_functions'))
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
        sid = data.get('requirement_set_id')
        if op == 'create' or sid != (current or {}).get('data', {}).get('requirement_set_id') or \
                data.get('requirement_set_version') != (current or {}).get('data', {}).get('requirement_set_version'):
            s_rec = store.get('crt_requirement_set', sid)
            if data.get('requirement_set_version') != s_rec['version']:
                raise _err(422, 'set_version_mismatch', 'Pin the requirement set version you read '
                                                        '(requirement_set_version = the set record\'s version)',
                           field='requirement_set_version', current_version=s_rec['version'])
        if agent and current:
            old = current['data'].get('scope_changed_at')
            new = data.get('scope_changed_at')
            if old and (not new or new < old):
                raise _err(403, 'scope_change_needs_person', 'Only a person may clear or back-date a scope change',
                           field='scope_changed_at')
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
        doc = _private_doc(store, data.get('acceptance_document_id'), 'acceptance_document_id')
        if doc['data'].get('kind') != 'contractor_waiver_ok':
            raise _err(422, 'not_a_waiver_document', "The acceptance must be the contractor's written OK "
                                                     "(a document of kind contractor_waiver_ok)",
                       field='acceptance_document_id')
        # host ruling W (rules 1.6.0): a waiver binds to the code AND the paper it was given for. The server stamps,
        # at the moment staff record it, every (function, document id, file hash) that raises the code today; later
        # different or changed paper raises the code again. The binding is never the caller's to write or change.
        if op == 'create':
            ev = evaluate(store, data.get('crt_assignment_id'), datetime.now(timezone.utc).date())
            data['bound_evidence'] = list((ev.get('raised') or {}).get(data.get('variance_code')) or [])
        else:
            was_d = current['data']
            moved = [k for k in ('crt_assignment_id', 'variance_code', 'bound_evidence')
                     if data.get(k) != was_d.get(k)]
            if moved:
                raise _err(409, 'waiver_binding_fixed', 'A waiver stays bound to the code and paper it was given '
                                                        'for; record a new waiver instead (host ruling W)',
                           fields=moved)
        return


# ── requirement-set diff (host ruling F: the approval page shows all of it) ─
def set_approved(store, rec) -> bool:
    """The set is APPROVED, live, and a person approved it on the staff surface at its CURRENT version (the same
    test the verdict uses). A draft, an archived set, or one edited since its approval is not."""
    if not rec or rec['archived_at'] or rec['data'].get('status') != 'APPROVED':
        return False
    need = {'status', 'items'} | ({'form_functions'} if rec['data'].get('form_functions') else set())
    return any(c['data'].get('record_collection') == 'crt_requirement_set'
               and c['data'].get('record_version') == rec['version'] and c['data'].get('on_staff_surface') is True
               and is_person(c['data'].get('confirmed_by')) and need <= set(c['data'].get('fields') or [])
               for c in _all(store, 'crt_confirmation', {'record_id': rec['id']}))


def diff_base(store, rec):
    """Host ruling D (rules 1.6.0): what a set is compared against at approval, fixed server-side: the nearest
    APPROVED set up its `supersedes` chain (the version in use), never the immediate parent when that is a draft.
    Returns (approved base record or None, [the unapproved drafts in between, nearest first])."""
    hops, seen = [], {rec['id']}
    cur = rec['data'].get('supersedes')
    while cur and cur not in seen and len(seen) <= 100:
        seen.add(cur)
        prev = _get(store, 'crt_requirement_set', cur)
        if prev is None:
            break
        if set_approved(store, prev):
            return prev, hops
        hops.append(prev)
        cur = prev['data'].get('supersedes')
    return None, hops


def _item_view(it):
    """Every key of an item (host ruling M: the page shows all of them), plus the minimum the verdict applies."""
    v = {k: it.get(k) for k in sorted(it, key=str)}
    if it.get('kind') == 'limit':
        v['minimum_applied'] = limit_need(it)          # a missing / null min reads as the floor (ruling M)
    return v


def set_diff(store, rec) -> dict:
    """The full difference between a requirement set and the version in use: the nearest APPROVED set up its
    `supersedes` chain (host ruling D; drafts in between are listed, never used as the base), else the template it
    was drafted from (server data), else nothing. Every key of every item is listed then and now (ruling M); the
    form-equivalence table is listed whole, then and now. Pure read; `sha256` binds an approval to exactly what the
    page showed."""
    d = rec['data']
    base, against = None, None
    prev, hops = diff_base(store, rec)
    if prev is not None:
        base = prev['data']
        against = {'kind': 'set', 'id': prev['id'], 'set_version': prev['data'].get('set_version'),
                   'record_version': prev['version'], 'status': prev['data'].get('status'), 'approved': True,
                   'name': prev['data'].get('name')}
    elif d.get('template_key'):
        tpls = load_templates()
        tpl = next((t for t in tpls['templates'] if t['key'] == d['template_key']), None)
        if tpl:
            base = {'items': tpl['items'], 'trade_category': tpl['trade_category'], 'name': tpl.get('name'),
                    'form_functions': tpls.get('form_functions')}
            against = {'kind': 'template', 'template_key': d['template_key'], 'name': tpl.get('name')}
    base = base or {}
    was = {it.get('id'): it for it in base.get('items') or [] if isinstance(it, dict)}
    now = {it.get('id'): it for it in d.get('items') or [] if isinstance(it, dict)}
    items = []
    for iid in list(now) + [i for i in was if i not in now]:
        a, b = was.get(iid), now.get(iid)
        va, vb = (_item_view(a) if a else None), (_item_view(b) if b else None)
        change = 'added' if a is None else 'removed' if b is None else ('same' if va == vb else 'changed')
        keys = sorted(set(va or {}) | set(vb or {}), key=str)
        rows = [{'key': k, 'was': (va or {}).get(k), 'now': (vb or {}).get(k),
                 'changed': change != 'same' and (va or {}).get(k) != (vb or {}).get(k)} for k in keys]
        items.append({'id': iid, 'change': change, 'was': va, 'now': vb, 'rows': rows,
                      'changed_fields': [r['key'] for r in rows if r['changed']] if change == 'changed' else []})
    ffa = base.get('form_functions') if isinstance(base.get('form_functions'), dict) else {}
    ffb = d.get('form_functions') if isinstance(d.get('form_functions'), dict) else {}
    table = {'was': ffa, 'now': ffb,
             'added': {k: ffb[k] for k in ffb if k not in ffa},
             'removed': {k: ffa[k] for k in ffa if k not in ffb},
             'changed': {k: [ffa[k], ffb[k]] for k in ffb if k in ffa and ffa[k] != ffb[k]}}
    out = {'set_id': rec['id'], 'record_version': rec['version'], 'set_version': d.get('set_version'),
           'against': against,
           'unapproved_between': [{'id': r['id'], 'set_version': r['data'].get('set_version'),
                                   'record_version': r['version'], 'status': r['data'].get('status') or 'DRAFT'}
                                  for r in hops],
           'items': items, 'form_functions': table,
           'trade_category': {'was': base.get('trade_category'), 'now': d.get('trade_category')},
           'name': {'was': base.get('name'), 'now': d.get('name')},
           'notes': {'was': base.get('notes'), 'now': d.get('notes')},
           'floor_violations': floor_violations(d)}
    out['sha256'] = hashlib.sha256(json.dumps(out, sort_keys=True, default=str).encode()).hexdigest()
    return out


# ── routes (contract: services/biz/packs/__init__.py) ──────────────────────
def register_routes(bp, api, ctx):
    from flask import jsonify, request

    base = f'{api}/crt'

    def today_arg():
        raw = request.args.get('today')
        if raw:
            if ctx.is_agent():          # LOW ruling: an as-of date other than today is a staff what-if only
                raise _err(403, 'today_staff_only', 'Only a person (staff session) may ask for the state as of '
                                                    'another date; an agent key reads today only')
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
        return jsonify({**load_templates(), 'trade_categories': list(TRADE_CATEGORIES),
                        'exclusion_classes': list(EXCLUSION_CLASSES)})

    @route('/staff/<page>', ['GET'], 'staff_surface')
    def staff_surface(page):
        """Host ruling H4: the staff pages (confirm, approve, review, waiver, hold, set-up) ship in the OVU image
        and are served here, never from the tenant's canvas-pages (which the agent can write). Each render
        carries a per-render token bound to this staff session; only requests carrying it may write."""
        ctx.require_person('open the staff pages')
        return ctx.staff_surface_page(PAGES_DIR, page)

    @route('/requirement-sets/from-template', ['POST'], 'set_from_template')
    def set_from_template():
        body = ctx.body()
        key = body.get('template_key')
        tpl = next((t for t in load_templates()['templates'] if t['key'] == key), None)
        if tpl is None:
            raise _err(404, 'unknown_template', 'No such template', template_key=str(key)[:40])
        warnings = validate_items(tpl['items'], tpl['trade_category'])
        validate_floor(tpl['items'], load_templates().get('form_functions'))
        rec = ctx.store().create('crt_requirement_set', {
            'name': str(body.get('name') or tpl['name'])[:200], 'trade_category': tpl['trade_category'],
            'template_key': key, 'items': tpl['items'], 'guide_cites': tpl.get('guide_cites') or [],
            'notes': ' '.join(filter(None, [tpl.get('conflict_note'), tpl.get('single_source')])) or None,
            'form_functions': dict(load_templates().get('form_functions') or {}) or None,
            'set_version': 1},                      # no status = DRAFT; only staff approve
            by=ctx.by(), agent=ctx.is_agent(), source=body.get('source'), via_pack=True,
            surface=ctx.surface())
        return jsonify({'record': rec, 'warnings': warnings}), 201

    @route('/requirement-sets/<sid>/approve', ['POST'], 'set_approve')
    def set_approve(sid):
        ctx.require_person('approve a requirement set')
        ctx.require_staff_surface()
        store = ctx.store()
        rec = store.get('crt_requirement_set', sid)
        warnings = validate_items(rec['data'].get('items'), rec['data'].get('trade_category'))
        validate_form_functions(rec['data'].get('form_functions'))
        validate_floor(rec['data'].get('items'), rec['data'].get('form_functions'))
        # host ruling F: staff approve what the page showed them -- the full diff against the version it replaces.
        # The page sends the diff's sha256; a set that changed since (or a diff never fetched) is refused.
        diff = set_diff(store, rec)
        shown = ctx.body().get('diff_sha256')
        if not isinstance(shown, str) or not shown:      # X4 (rules 1.6.0): an approval names the diff it saw
            raise _err(422, 'diff_sha256_required', 'Approve from the requirements page: it sends the sha256 of the '
                                                    'changes it showed you (GET .../diff)', diff=diff)
        if shown != diff['sha256']:
            raise _err(409, 'diff_changed', 'The requirement set changed since the page showed its changes; reload '
                                            'and review the changes again', diff=diff)
        if rec['data'].get('status') != 'APPROVED':
            rec = store.update('crt_requirement_set', sid, {'status': 'APPROVED'}, expected_version=rec['version'],
                               by=ctx.by(), agent=False, via_pack=True, surface=ctx.surface())
        conf = store.create('crt_confirmation', {
            'record_collection': 'crt_requirement_set', 'record_id': sid, 'record_version': rec['version'],
            'fields': ['items', 'status'] + (['form_functions'] if rec['data'].get('form_functions') else []),
            'confirmed_by': ctx.by(), 'on_staff_surface': ctx.surface(), 'approved_diff_sha256': diff['sha256'],
            # ruling D: what the approval was compared against, so the watcher can check it was an approved version
            'approved_against': {k: v for k, v in (diff['against'] or {'kind': 'none'}).items()
                                 if k in ('kind', 'id', 'record_version', 'template_key')},
            'note': str(ctx.body().get('note') or 'approved for use')[:500]},
            by=ctx.by(), agent=False, via_pack=True, surface=ctx.surface())
        return jsonify({'record': rec, 'confirmation': conf, 'warnings': warnings, 'diff': diff})

    @route('/requirement-sets/<sid>/diff', ['GET'], 'set_diff')
    def set_diff_route(sid):
        return jsonify(set_diff(ctx.store(), ctx.store().get('crt_requirement_set', sid)))

    @route('/requirement-sets/<sid>/revise', ['POST'], 'set_revise')
    def set_revise(sid):
        body = ctx.body()
        store = ctx.store()
        old = store.get('crt_requirement_set', sid)
        d = dict(old['data'])
        if body.get('items') is not None:
            d['items'] = body['items']
            check_item_identity(old['data'].get('items'), d['items'])     # ruling M: no key swap under one id
        if body.get('form_functions') is not None:          # R4: equivalences change per set version
            validate_form_functions(body['form_functions'])
            d['form_functions'] = body['form_functions']
        if body.get('name'):
            d['name'] = str(body['name'])[:200]
        warnings = validate_items(d.get('items'), d.get('trade_category'))
        validate_floor(d.get('items'), d.get('form_functions'))
        d.pop('status', None)                   # a new version is a DRAFT until staff approve it
        d.update({'set_version': int(old['data'].get('set_version') or 1) + 1, 'supersedes': sid})
        rec = store.create('crt_requirement_set', d, by=ctx.by(), agent=ctx.is_agent(),
                           source=body.get('source'), via_pack=True, surface=ctx.surface())
        return jsonify({'record': rec, 'warnings': warnings}), 201

    @route('/confirm', ['POST'], 'confirm')
    def confirm():
        ctx.require_person('confirm a reading')
        ctx.require_staff_surface()
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
        if coll == 'document' and 'kind' in fields and rec['data'].get('kind') == 'endorsement':
            fid = _file_id(rec)
            acct, party = rec['data'].get('crt_account_id'), rec['data'].get('contact_id')
            certs = {_file_id(d) for d in _all(store, 'document', {'crt_account_id': acct, 'contact_id': party},
                                               archived=True) if d['data'].get('kind') == 'coi_acord25'}
            for p in _all(store, 'crt_policy', {'crt_account_id': acct, 'party_contact_id': party}):
                certs.add(_file_id(_get(store, 'document', p['data'].get('source_document_id'))))
            if fid and fid in certs:
                raise _err(422, 'endorsement_is_the_certificate',
                           "This file is the sub's certificate. The certificate is a summary; the endorsement "
                           'is its own document (host ruling N2)', field='kind')
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
            'on_staff_surface': ctx.surface(),
            'note': str(body.get('note'))[:500] if body.get('note') else None},
            by=ctx.by(), agent=False, via_pack=True, surface=ctx.surface())
        return jsonify(rec2), 201

    @route('/assignment/<aid>/state', ['GET'], 'state')
    def state(aid):
        return jsonify(evaluate(ctx.store(), aid, today_arg()))

    @route('/assignment/<aid>/review', ['POST'], 'review')
    def review(aid):
        ctx.require_person('record a review')
        ctx.require_staff_surface()
        store = ctx.store()
        ev = evaluate(store, aid, datetime.now(timezone.utc).date())
        now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        rec = store.create('crt_review', {
            'crt_assignment_id': aid, 'state': ev['state'], 'label': ev['label'], 'variance_codes': ev['codes'],
            'accepted_codes': [a['code'] for a in ev['accepted']], 'rule_version': RULE_VERSION,
            'requirement_set_id': ev['requirement_set_id'], 'requirement_set_version': ev['requirement_set_version'],
            'confirmation_ids': ev['confirmation_ids'], 'disclaimer_text': DISCLAIMER,
            'hold_suggested': ev['hold_suggested'], 'reviewed_by': ctx.by(), 'reviewed_at': now,
            'evaluation': ev}, by=ctx.by(), agent=False, via_pack=True, surface=ctx.surface())
        return jsonify({'review': rec, 'evaluation': ev}), 201

    @route('/assignment/<aid>/chase-sent', ['POST'], 'chase_sent')
    def chase_sent(aid):
        ctx.require_person('mark a chase as sent')
        ctx.require_staff_surface()
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
