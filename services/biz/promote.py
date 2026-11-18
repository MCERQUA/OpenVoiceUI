"""Platform promotion step for the shared field library (read-only on every tenant db).

    python3 -m services.biz.promote --db /path/to/<tenant>/db/business.db [--db ...] --out review.json

For every candidate field definition in each tenant's field_proposals table, this
re-runs the promotion gate (services/biz/field_gate.py) with THAT tenant's own
brand terms and customer names, then groups the definitions that pass by
(type, normalised key) across tenants. It writes a review file and nothing else:
it never writes a tenant db and never edits the library. A vetted definition
reaches other tenants only when a person adds it to default-schemas/fields/
library.json in a release.

The review file holds passing definitions, verdicts and tenant COUNTS. It never
holds record data; a refused candidate is listed by proposal id and reasons only
(its key may be the very text that failed), and reasons never echo matched text.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from services.biz import field_gate
from services.biz.schema import Registry


def _read_tenant(db_path: Path):
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        tenant = conn.execute("SELECT value FROM meta WHERE key = 'tenant'").fetchone()
        company = conn.execute("SELECT data FROM records WHERE collection = 'company' "
                               "AND archived_at IS NULL LIMIT 1").fetchone()
        names = [r[0] for r in conn.execute(
            "SELECT json_extract(data, '$.name') FROM records WHERE collection = 'contact'").fetchall() if r[0]]
        rows = conn.execute("SELECT id, collection, field, definition FROM field_proposals "
                            "WHERE status = 'candidate' ORDER BY id").fetchall()
    finally:
        conn.close()
    return (tenant[0] if tenant else None, json.loads(company[0]) if company else None, names,
            [(i, c, f, json.loads(d)) for i, c, f, d in rows])


def review(db_paths, library_keys, vocabulary):
    passed, refused, errors = {}, [], []
    for p in db_paths:
        try:
            tenant, company, names, candidates = _read_tenant(Path(p))
        except sqlite3.Error as exc:
            errors.append({'db': str(p), 'error': type(exc).__name__})
            continue
        terms = field_gate.brand_terms_for(tenant, company)
        for proposal_id, collection, field, defn in candidates:
            verdict = field_gate.vet_definition(defn, brand_terms=terms, customer_names=names,
                                                vocabulary=vocabulary)
            if verdict['verdict'] != 'pass':
                # the field name itself may hold the matched text: report the proposal id, never the name
                refused.append({'tenant': tenant, 'collection': collection, 'proposal_id': proposal_id,
                                'reasons': verdict['reasons']})
                continue
            group = (defn.get('type'), defn.get('key'))
            entry = passed.setdefault(group, {'key': defn.get('key'), 'type': defn.get('type'),
                                              'definitions': [], 'tenants': set(),
                                              'already_in_library': False})
            entry['definitions'].append({k: v for k, v in defn.items() if k != 'collection'})
            entry['tenants'].add(tenant)
            entry['already_in_library'] = any(lk.endswith('_' + str(defn.get('key'))) for lk in library_keys)
    candidates = []
    for entry in passed.values():
        candidates.append({'key': entry['key'], 'type': entry['type'], 'tenant_count': len(entry['tenants']),
                           'already_in_library': entry['already_in_library'],
                           'definition': entry['definitions'][0],
                           'variants': len({json.dumps(d, sort_keys=True) for d in entry['definitions']})})
    candidates.sort(key=lambda c: (-c['tenant_count'], c['key'] or ''))
    return {'candidates_passing_gate': candidates, 'refused': refused, 'errors': errors,
            'note': 'Promotion = a person adds a generic candidate to default-schemas/fields/library.json '
                    'in a release. 2+ tenants inventing the same field is the strongest signal.'}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--db', action='append', required=True, help='a tenant business.db (opened read-only)')
    ap.add_argument('--out', required=True, help='review file to write')
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as empty:      # core + library only, no tenant overlay
        reg = Registry(overlay_dir=Path(empty))
    result = review(args.db, set(reg.library['fields']), field_gate.vocabulary_from(reg))
    Path(args.out).write_text(json.dumps(result, indent=2, default=sorted) + '\n')
    print(f"passing={len(result['candidates_passing_gate'])} refused={len(result['refused'])} "
          f"errors={len(result['errors'])} -> {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
