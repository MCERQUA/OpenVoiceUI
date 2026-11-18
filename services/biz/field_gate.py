"""Promotion gate: may a tenant's candidate field DEFINITION enter the shared library?

A candidate is a definition only: key, label, type, unit, range, allowed values,
description. It must carry no data values and nothing that identifies a customer
or the tenant. The gate REFUSES a definition when any of its text (key, label,
description, unit, allowed values) contains:
  - a phone number or an email address
  - a street address (house number + street word, or a state + ZIP pair)
  - a price (a currency sign or word next to a number, or a cents amount)
  - one of the tenant's own brand terms (tenant id, company name, website name)
  - a customer name from the tenant's own contacts (full name, or any distinctive
    word of it that is not ordinary trade vocabulary)
  - any key outside the definition whitelist (values, defaults, examples, data)

Reasons never echo the matched text, so a refusal report is itself safe to share.
Refusing is the safe failure: a refused generic field just does not get promoted.

Used twice: in the tenant engine when a candidate is filed (advisory verdict
stored with it) and by the platform promotion step (services/biz/promote.py),
which re-runs it and is the authority.
"""
from __future__ import annotations

import re

DEFINITION_KEYS = {'key', 'label', 'type', 'unit', 'min', 'max', 'values', 'items', 'description', 'collection'}
GENERIC_SUFFIXES = {'llc', 'inc', 'co', 'corp', 'company', 'ltd', 'the', 'and', 'of'}

_EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[a-z]{2,}', re.I)
_PHONE = re.compile(r'(?<!\w)\+?\(?\d[\d\s().-]{5,}\d(?!\w)')
_STREET_WORDS = (r'st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|ct|court|pl|place|'
                 r'pkwy|parkway|hwy|highway|cir|circle|ter|terrace|trl|trail')
_ADDRESS = re.compile(r'\b\d{1,6}\s+(?:[a-z0-9.]+\s+){0,4}(?:' + _STREET_WORDS + r')\b\.?', re.I)
_STATE_ZIP = re.compile(r'\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b')
_PRICE = re.compile(r'(?:[$£€]\s?\d)|(?:\b\d[\d,]*(?:\.\d+)?\s?(?:usd|dollars?|bucks|cents)\b)'
                    r'|(?:\b\d+\.\d{2}\b)', re.I)


def _norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (text or '').lower())


def _words(text: str) -> list[str]:
    return [w for w in re.split(r'[^a-z0-9]+', (text or '').lower()) if w]


def brand_terms_for(tenant: str | None, company: dict | None) -> list[str]:
    """The tenant's own identifying terms: tenant id, company name, website name."""
    terms = []
    if tenant:
        terms.append(tenant)
    company = company or {}
    name = company.get('name')
    if isinstance(name, str) and name.strip():
        terms.append(name)
        core = [w for w in _words(name) if w not in GENERIC_SUFFIXES]
        if core:
            terms.append(' '.join(core))
        # every run of 2+ consecutive words, so 'desertsky_rating' matches "Desert Sky Insulation"
        for i in range(len(core)):
            for j in range(i + 2, len(core) + 1):
                terms.append(' '.join(core[i:j]))
    site = company.get('website')
    if isinstance(site, str):
        m = re.search(r'(?:https?://)?(?:www\.)?([a-z0-9-]+)\.', site.lower())
        if m:
            terms.append(m.group(1))
    extra = (company.get('brand') or {}).get('terms') if isinstance(company.get('brand'), dict) else None
    if isinstance(extra, list):
        terms.extend(t for t in extra if isinstance(t, str))
    return [t for t in terms if len(_norm(t)) >= 3]


def _definition_texts(defn: dict) -> list[str]:
    out = []
    for k in ('key', 'label', 'description', 'unit'):
        v = defn.get(k)
        if isinstance(v, str):
            out.append(v.replace('_', ' ') if k == 'key' else v)
    for v in defn.get('values') or []:
        if isinstance(v, str):
            out.append(v)
    return out


def vet_definition(defn: dict, *, brand_terms=(), customer_names=(), vocabulary=frozenset()) -> dict:
    """Return {'verdict': 'pass'|'refuse', 'reasons': [...]} for one candidate definition.

    vocabulary = ordinary trade/platform words (from the core schemas and the
    library) that a customer-name word may collide with without being a name.
    """
    reasons = []
    if not isinstance(defn, dict):
        return {'verdict': 'refuse', 'reasons': ['not a definition object']}
    extra = set(defn) - DEFINITION_KEYS
    if extra:
        reasons.append('carries keys beyond a definition (data values are never promoted): '
                       + ', '.join(sorted(extra)))
    if not isinstance(defn.get('key'), str) or not isinstance(defn.get('type'), str):
        reasons.append('missing key or type')
    values = defn.get('values')
    if values is not None and (not isinstance(values, list) or len(values) > 50
                               or any(not isinstance(v, str) or len(v) > 60 for v in values)):
        reasons.append('allowed values must be a short list of short text')

    texts = _definition_texts(defn)
    joined = ' \n '.join(texts)
    if _EMAIL.search(joined):
        reasons.append('contains an email address')
    for t in texts:
        for m in _PHONE.finditer(t):
            if len(re.sub(r'\D', '', m.group(0))) >= 7:
                reasons.append('contains a phone number')
                break
    if _ADDRESS.search(joined) or _STATE_ZIP.search(joined):
        reasons.append('contains a street address')
    if _PRICE.search(joined):
        reasons.append('contains a price')

    norm_texts = [_norm(t) for t in texts]
    words = set()
    for t in texts:
        words.update(_words(t))
    vocab = {w.lower() for w in vocabulary}

    # brand: the whole term anywhere (spacing ignored), or any distinctive word of it
    # that is not ordinary trade vocabulary ("Desert Sky Insulation" -> desert, sky)
    for term in brand_terms:
        nt = _norm(term)
        distinctive = [w for w in _words(term) if len(w) >= 3 and w not in vocab and w not in GENERIC_SUFFIXES]
        if (len(nt) >= 3 and any(nt in x for x in norm_texts)) or any(w in words for w in distinctive):
            reasons.append("contains the tenant's own brand term")
            break
    hit = False
    for name in customer_names:
        if not isinstance(name, str):
            continue
        nn = _norm(name)
        if len(nn) >= 5 and any(nn in x for x in norm_texts):
            hit = True
        for w in _words(name):
            if len(w) >= 3 and w not in vocab and w not in GENERIC_SUFFIXES and w in words:
                hit = True
        if hit:
            break
    if hit:
        reasons.append('contains a customer name')

    # de-duplicate, keep order
    seen, uniq = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return {'verdict': 'refuse' if uniq else 'pass', 'reasons': uniq}


def vocabulary_from(registry) -> frozenset:
    """Ordinary words: every word in core-schema and library labels/descriptions/keys."""
    words = set()
    for spec in registry.collections().values():
        if spec.get('layer') != 'core':
            continue
        for k in ('label', 'label_plural', 'description', 'collection'):
            words.update(_words(str(spec.get(k) or '')))
        for fname, f in (spec.get('fields') or {}).items():
            if f.get('layer') in ('custom',):
                continue
            words.update(_words(fname))
            words.update(_words(str(f.get('label') or '')))
    for key, f in registry.library['fields'].items():
        words.update(_words(key))
        words.update(_words(str(f.get('label') or '')))
        words.update(_words(str(f.get('description') or '')))
    return frozenset(words)
