"""Business-data importer: a tenant's existing ad-hoc business files -> its own business.db.

Design: client-backend design section 10 (migration) and the `POST /import?dry_run=1` endpoint.

Rules this module keeps (they are the point of it):
  * One tenant only. The store is opened for one tenant (env / meta.tenant wall in BizStore);
    a caller that names a tenant (the host CLI does) must match it, or the run is refused.
  * Writes go through BizStore.create / adopt_library_field only, so every validation,
    tenant trigger, card-number refusal and activity row applies exactly as for any write.
  * Additive and idempotent. Each imported record gets an import key
    (`import:<source path>#<item>`) stored as the `ref` of its append-only `created`
    activity row. A key that already has a created row is never written again, even if
    the record was later edited or archived, so a re-run adds 0 rows.
  * Never invents data. Only fields present in the source are mapped; anything the
    adapter cannot place is reported under `unmapped`, never guessed.
  * Never touches the source files (they arrive as parsed JSON; nothing is written back).
  * dry_run=True runs the exact same apply path against a temporary copy of the db and
    overlay, then reports what it would add; the live db is not written.

Sources are keyed by their path relative to the tenant root (/mnt/clients/<tenant>/):
  openclaw/workspace/business/jobs.json              -> contact, job, cost_entry, document, invoice
  openclaw/workspace/field/field-data.json           -> person (roster)
  openvoiceui/canvas-pages/_data/field-dock/inventory.json   -> product (+ stock_move counts)
  openvoiceui/canvas-pages/_data/field-dock/extractions.json -> document (extracted fields)
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
from pathlib import Path

IMPORTER_VERSION = '1.1.0'
REF_PREFIX = 'import:'
BY = 'import:biz-importer'
MAX_KEY = 500                   # activity.ref / source.ref limit: every stored key fits, never truncated
TOTAL_TOLERANCE = 0.05          # dollars: sub-lines must sum to the confirmed total within this

SRC_JOBS = 'openclaw/workspace/business/jobs.json'
SRC_FIELD = 'openclaw/workspace/field/field-data.json'
SRC_INVENTORY = 'openvoiceui/canvas-pages/_data/field-dock/inventory.json'
SRC_EXTRACTIONS = 'openvoiceui/canvas-pages/_data/field-dock/extractions.json'
KNOWN_SOURCES = (SRC_JOBS, SRC_FIELD, SRC_INVENTORY, SRC_EXTRACTIONS)


class ImportRefused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def norm_key(key: str) -> str:
    """The stored form of an import key: the key itself, or a stable hashed form if it is too long.

    source.ref holds at most MAX_KEY characters. A longer key (e.g. a very long image path) is
    replaced by `<import:source>#<item type>:sha256:<hex of the full key>`, so the same item always
    maps to the same stored key and a re-run finds it. Never truncated (a truncated key is not stable).
    """
    if len(key) <= MAX_KEY:
        return key
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    head, _, tail = key.partition('#')
    short = f'{head}#{tail.split(":", 1)[0][:40]}:sha256:{digest}'
    return short if len(short) <= MAX_KEY else f'{REF_PREFIX}sha256:{digest}'


class Ref:
    """A reference to another import item, resolved to its record id at write time."""
    __slots__ = ('key',)

    def __init__(self, key):
        self.key = norm_key(key)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


def _digits(v):
    return re.sub(r'\D', '', v) if isinstance(v, str) else ''


def _slug(v):
    return re.sub(r'[^a-z0-9]+', '-', (v or '').lower()).strip('-')[:80]


def _clip(v, n):
    return v if v is None or len(v) <= n else v[:n - 1] + '…'


class Plan:
    def __init__(self):
        self.items = []          # (key, collection, data, words)
        self.unmapped = []       # {source, what, count, why}
        self.source_rows = {}    # source -> rows seen
        self.checks = []

    def add(self, key, collection, data, words=None):
        self.items.append((norm_key(key), collection, {k: v for k, v in data.items() if v is not None}, words))

    def gap(self, source, what, count, why):
        if count:
            self.unmapped.append({'source': source, 'what': what, 'count': count, 'why': why})


# ── adapter: workspace/business/jobs.json ────────────────────────────────────
_FUEL = re.compile(r'^(diesel(_\d+)?|gas|fuel|fuel_gas_plus_diesel)$')
_LABOR = re.compile(r'^(labor_cost|labor_written_dollars|manpower)$')
_MATERIAL = re.compile(r'^(material|cc_material|oc_material|batt_material\w*|cellulose_bags|wall_plugs|plugs|staples|'
                       r'plastic|r\d+_bags|bags|oc_clicks|cc_clicks)$')
_OTHER = re.compile(r'^(dump_fee|dump|leg_\w+)$')
# Keys that describe the cost sheet rather than being a cost line. Read, never imported as money.
_COST_META = re.compile(r'^(known_subtotal_excl_labor|written_total|sublines\w*|labor_hours|labor_rate|lump_sum_cost|'
                        r'receipts|\w*_note|sub_read|pending_additional|misc_struck_out)$')
_JOB_STATUS = {'lead', 'quoted', 'scheduled', 'in_progress', 'completed', 'invoiced', 'paid', 'cancelled', 'on_hold'}


def _cost_kind(key):
    for kind, rx in (('fuel', _FUEL), ('labor', _LABOR), ('material', _MATERIAL), ('other', _OTHER)):
        if rx.match(key):
            return kind
    return None


def _cost_line(key, val):
    """(amount, qty, unit, extra-words) for one cost-sheet key, or None if it carries no dollar amount."""
    if _num(val) is not None:
        return float(val), None, None, None
    if isinstance(val, dict):
        for k in ('cost', 'cost_read', 'cost_at_face'):
            if _num(val.get(k)) is not None:
                qty = _num(val.get('qty')) if _num(val.get('qty')) is not None else _num(val.get('hours'))
                unit = 'hours' if _num(val.get('hours')) is not None and _num(val.get('qty')) is None else None
                extra = {kk: vv for kk, vv in val.items() if kk not in (k, 'qty', 'hours')}
                return float(val[k]), qty, unit, (f'{k}; ' + json.dumps(extra, ensure_ascii=False)) if extra or k != 'cost' else None
    return None


def _confirmed_total(job, costs):
    pf = job.get('profit_final') if isinstance(job.get('profit_final'), dict) else {}
    for label, v in (('profit_final.total_costs', pf.get('total_costs')), ('costs.written_total', costs.get('written_total')),
                     ('costs.lump_sum_cost', costs.get('lump_sum_cost'))):
        if _num(v) is not None:
            return float(v), label
    return None, None


def adapt_jobs(src, doc, plan: Plan):
    jobs = doc.get('jobs') if isinstance(doc, dict) else None
    if not isinstance(jobs, list):
        plan.gap(src, 'whole file', 1, 'no "jobs" list in this file (shape not recognised)')
        return
    plan.source_rows[src] = len(jobs)
    unknown_cost_keys = {}
    for job in jobs:
        jid, customer = (_text(job.get(k)) if isinstance(job, dict) else None for k in ('id', 'customer'))
        if not jid or not customer:
            plan.gap(src, 'job row without id or customer', 1, 'a job needs an id and a customer name to be placed')
            continue
        base = f'{REF_PREFIX}{src}#'
        phone = _text(job.get('customer_phone'))
        ckey = base + 'contact:' + (_digits(phone) or _slug(customer))
        cnotes = [n for n in (_text(job.get('customer_name_note')),
                              ('Customer type as recorded: ' + job['customer_type']) if _text(job.get('customer_type')) else None) if n]
        plan.add(ckey, 'contact', {'name': _clip(customer, 1000), 'phone': phone if phone and len(phone) <= 40 else None,
                                   'address': _clip(_text(job.get('address')), 1000),
                                   'notes': '\n'.join(cnotes) or None},
                 words=f'customer on {jid}')

        costs = job.get('costs') if isinstance(job.get('costs'), dict) else {}
        status = job.get('status') if job.get('status') in _JOB_STATUS else None
        if job.get('status') and not status:
            plan.gap(src, f'{jid} status', 1, f'status {job.get("status")!r} is not a job status; left unset')
        st = _text(job.get('service_type'))
        notes = []
        if _text(job.get('purpose')):
            notes.append('Purpose: ' + job['purpose'])
        if isinstance(job.get('notes'), list):
            notes += [str(n) for n in job['notes'] if n]
        elif _text(job.get('notes')):
            notes.append(job['notes'])
        if _text(job.get('revenue_billed_note')):
            notes.append('Revenue note: ' + job['revenue_billed_note'])
        for k in sorted(costs):
            if k == 'receipts' and isinstance(costs[k], list):
                notes += ['Receipt: ' + str(r) for r in costs[k]]
            elif k.endswith('_note') or k == 'sub_read':
                notes.append(f'Cost sheet ({k}): {costs[k]}')
            elif k == 'pending_additional' and isinstance(costs[k], dict):
                notes.append('Pending additional spend (not imported as a cost): ' + json.dumps(costs[k], ensure_ascii=False))
            elif k == 'misc_struck_out':
                notes.append(f'Struck out on the sheet (not imported as a cost): {costs[k]}')
        if _text(job.get('source')):
            notes.append('Recorded from: ' + job['source'])
        notes.append(f'Imported from {src} ({jid}).')
        jkey = base + 'job:' + jid
        worked = job.get('worked_dates') if isinstance(job.get('worked_dates'), list) else None
        plan.add(jkey, 'job', {
            'title': _clip(customer + (' - ' + st if st else ''), 1000),
            'contact_id': Ref(ckey),
            'address': _clip(_text(job.get('address')) or _text(job.get('location')), 1000),
            'service_type': _clip(st, 1000),
            'status': status,
            'completed_at': job.get('completed_date') if isinstance(job.get('completed_date'), str) else None,
            'worked_dates': [d for d in worked if isinstance(d, str)] if worked else None,
            'work_order_no': str(job['work_order']) if job.get('work_order') not in (None, '') else None,
            'invoice_no': str(job['invoice']) if job.get('invoice') not in (None, '') else None,
            'gen_contract_price': _num(job.get('revenue_billed')),
            'notes': _clip('\n'.join(notes), 50000),
        }, words=f'job {jid}')

        # costs: itemised lines only when they reconcile with the confirmed total, else one roll-up line
        lines = []
        for k, v in costs.items():
            if v is None or _COST_META.match(k):
                continue
            kind = _cost_kind(k)
            line = _cost_line(k, v)
            if kind is None:
                if line is not None:
                    unknown_cost_keys[k] = unknown_cost_keys.get(k, 0) + 1
                continue
            if line is None or line[0] == 0:
                continue
            lines.append((k, kind) + line)
        total, total_from = _confirmed_total(job, costs)
        line_sum = round(sum(l[2] for l in lines), 2)
        if lines and (total is None or abs(line_sum - total) <= TOTAL_TOLERANCE):
            for k, kind, amount, qty, unit, extra in lines:
                plan.add(f'{base}cost:{jid}:{k}', 'cost_entry', {
                    'job_id': Ref(jkey), 'kind': kind, 'description': k.replace('_', ' '),
                    'amount': round(amount, 2), 'qty': qty, 'unit': unit,
                    'notes': f'From the {jid} cost sheet' + (f' ({extra})' if extra else '')}, words=f'cost {k} on {jid}')
        elif total is not None:
            why = ('lump sum, no itemised lines' if not lines else
                   f'itemised lines as read sum to {line_sum}, which does not match the confirmed {total}')
            plan.add(f'{base}cost:{jid}:total', 'cost_entry', {
                'job_id': Ref(jkey), 'kind': 'other', 'description': 'job cost total',
                'amount': round(total, 2),
                'notes': _clip(f'Confirmed total from {total_from} on {jid}; {why}.'
                               + (' Lines as read: ' + json.dumps({l[0]: l[2] for l in lines}) if lines else ''), 50000)},
                words=f'cost total on {jid}')
        else:
            plan.gap(src, f'{jid} costs', 1, 'no dollar total and no itemised cost lines')

        photo = _text(job.get('photo_url')) or _text(job.get('photo'))
        if photo:
            plan.add(f'{base}photo:{jid}', 'document', {
                'file_url': _clip(photo, 1000), 'kind': 'photo', 'title': _clip(f'{jid} photo - {customer}', 1000),
                'job_id': Ref(jkey), 'contact_id': Ref(ckey)}, words=f'photo on {jid}')

        inv = job.get('invoice')
        if inv not in (None, ''):
            plan.add(f'{base}invoice:{jid}', 'invoice', {
                'number': _clip(str(inv), 1000), 'job_id': Ref(jkey), 'contact_id': Ref(ckey),
                'amount': _num(job.get('revenue_billed')),
                'notes': f'Invoice number and billed amount as recorded on {jid}; '
                         'the issue date and payment status were not recorded, so they are left unset.'},
                words=f'invoice on {jid}')

    for k, n in sorted(unknown_cost_keys.items()):
        plan.gap(src, f'cost key {k!r}', n, 'a dollar amount under a key the importer does not know how to classify')
    rt = doc.get('running_totals')
    if isinstance(rt, dict):
        plan.checks.append({'source': src, 'kind': 'running_totals', 'recorded': {
            k: rt.get(k) for k in ('jobs', 'revenue', 'net_profit', 'net_margin_pct') if k in rt}})


# ── adapter: workspace/field/field-data.json ─────────────────────────────────
def adapt_field(src, doc, plan: Plan):
    if not isinstance(doc, dict):
        plan.gap(src, 'whole file', 1, 'not an object (shape not recognised)')
        return
    roster = doc.get('roster') if isinstance(doc.get('roster'), list) else []
    plan.source_rows[src] = len(roster)
    for p in roster:
        name = _text(p.get('name')) if isinstance(p, dict) else None
        if not name:
            plan.gap(src, 'roster row without a name', 1, 'a person needs a name')
            continue
        phone = _text(p.get('phone'))
        plan.add(f'{REF_PREFIX}{src}#person:' + (_digits(phone) or _slug(name)), 'person', {
            'name': _clip(name, 1000), 'phone': phone if phone and len(phone) <= 40 else None,
            'role': _clip(_text(p.get('role')), 1000)}, words=f'roster {name}')
    for key, why in (('material', 'job/material log rows; several repeat jobs already in business/jobs.json, so they '
                                  'need a dedupe decision before they become jobs'),
                     ('submissions', 'raw SMS submissions; their photos are already covered by field-dock extractions'),
                     ('inventory_log', 'mirror of the inventory page log (handled from the inventory source)')):
        if isinstance(doc.get(key), list):
            plan.gap(src, key, len(doc[key]), why)


# ── adapter: canvas-pages/_data/field-dock/inventory.json ────────────────────
def adapt_inventory(src, doc, plan: Plan):
    items = doc.get('items') if isinstance(doc, dict) else None
    if not isinstance(items, list):
        plan.gap(src, 'whole file', 1, 'no "items" list (shape not recognised)')
        return
    log = doc.get('log') if isinstance(doc.get('log'), list) else []
    voided = {r.get('voids') for r in log if isinstance(r, dict) and r.get('action') == 'void'}
    real = [r for r in log if isinstance(r, dict) and r.get('action') != 'void' and r.get('id') not in voided]
    plan.source_rows[src] = len(items)
    for it in items:
        name = _text(it.get('name')) if isinstance(it, dict) else None
        touched = [r for r in real if r.get('item') == name]
        if not name or not touched:
            # an item nobody has counted (or whose only count was voided) is page seed/demo data,
            # not the business's stock: never imported as fact
            plan.gap(src, f'item {name or "?"}', 1, 'no real (non-voided) count in the log; page seed values '
                                                   'are not business data, the owner has to give a count')
            continue
        pkey = f'{REF_PREFIX}{src}#product:{it.get("id", _slug(name))}'
        plan.add(pkey, 'product', {'name': _clip(name, 1000), 'category': _clip(_text(it.get('cat')), 1000),
                                   'unit': _clip(_text(it.get('unit')), 1000), 'unit_cost': _num(it.get('cost'))},
                 words=f'inventory item {name}')
        for r in touched:
            if r.get('action') == 'count' and _num(r.get('qty')) is not None:
                plan.add(f'{REF_PREFIX}{src}#stock:{r.get("id")}', 'stock_move', {
                    'product_id': Ref(pkey), 'type': 'count', 'qty': r['qty'], 'counted': r['qty'],
                    'date': r['ts'][:10] if isinstance(r.get('ts'), str) and len(r['ts']) >= 10 else None,
                    'reason': _clip(f'count by {r.get("who") or "unknown"}', 1000)}, words=f'stock count {r.get("id")}')
            else:
                plan.gap(src, f'log action {r.get("action")!r}', 1, 'only counts are imported as stock moves')


# ── adapter: canvas-pages/_data/field-dock/extractions.json ──────────────────
_DOC_KIND = {'work order': 'work_order', 'receipt': 'receipt', 'invoice': 'other', 'photo': 'photo', 'plan': 'plan',
             'contract': 'contract'}


def adapt_extractions(src, doc, plan: Plan):
    images = doc.get('images') if isinstance(doc, dict) else None
    if isinstance(images, dict):
        images = list(images.values())
    if not isinstance(images, list):
        plan.gap(src, 'whole file', 1, 'no "images" collection (shape not recognised)')
        return
    plan.source_rows[src] = len(images)
    for im in images:
        url = _text(im.get('image')) if isinstance(im, dict) else None
        if not url:
            plan.gap(src, 'extraction without an image path', 1, 'a document needs its file')
            continue
        dt = (_text(im.get('doc_type')) or '').lower()
        notes = [n for n in (('Job (as labelled on the field dock): ' + im['job']) if _text(im.get('job')) else None,
                             ('Summary: ' + im['summary']) if _text(im.get('summary')) else None,
                             ('Legibility: ' + im['legibility']) if _text(im.get('legibility')) else None,
                             f'Extracted by {im.get("extracted_by")} at {im.get("extracted_at")}'
                             if im.get('extracted_by') else None,
                             f'Imported from {src}.') if n]
        # readers = who read the page (text); the second reader's per-field verdict rides on each extracted field
        readers = []
        if _text(im.get('extracted_by')):
            readers.append(_clip(f'A: {im["extracted_by"]} at {im.get("extracted_at")}', 1000))
        b = im.get('second_reader') if isinstance(im.get('second_reader'), dict) else None
        if b and b.get('model'):
            readers.append(_clip(f'B: {b.get("model")} ({b.get("status")}) at {b.get("at")}', 1000))
        comp = im.get('comparison') if isinstance(im.get('comparison'), dict) else {}
        verdicts = comp.get('fields') if isinstance(comp.get('fields'), dict) else {}
        extracted = None
        if isinstance(im.get('fields'), list):
            extracted = []
            for i, f in enumerate(im['fields']):
                if isinstance(f, dict):
                    f = dict(f)
                    if str(i) in verdicts:
                        f['second_reader'] = verdicts[str(i)]
                    extracted.append(f)
        if isinstance(comp.get('counts'), dict):
            notes.insert(0, 'Second-reader comparison: ' + json.dumps(comp['counts'], ensure_ascii=False))
        for k in ('conflicts_with_filed_data', 'missing', 'calculations'):
            if isinstance(im.get(k), list) and im[k]:
                notes.insert(-1, f'{k.replace("_", " ").capitalize()}: ' + json.dumps(im[k], ensure_ascii=False))
        plan.add(f'{REF_PREFIX}{src}#doc:{url}', 'document', {
            'file_url': _clip(url, 1000), 'kind': _DOC_KIND.get(dt, 'other'),
            'title': _clip(_text(im.get('title')) or url.rsplit('/', 1)[-1], 1000),
            'extracted': extracted,
            'line_items': [f for f in im['line_items'] if isinstance(f, dict)] if isinstance(im.get('line_items'), list) else None,
            'transcription': _clip(_text(im.get('transcription')), 50000),
            'readers': readers or None,
            'notes': _clip('\n'.join(notes), 50000)}, words=f'field-dock extraction {url.rsplit("/", 1)[-1]}')
    if doc.get('failed'):
        plan.gap(src, 'failed extractions', len(doc['failed']) if isinstance(doc['failed'], (list, dict)) else 1,
                 'the extraction pass failed on these images; nothing to import')


ADAPTERS = {SRC_JOBS: adapt_jobs, SRC_FIELD: adapt_field, SRC_INVENTORY: adapt_inventory,
            SRC_EXTRACTIONS: adapt_extractions}


def build_plan(sources: dict) -> Plan:
    plan = Plan()
    for name in sources:
        if name not in ADAPTERS:
            plan.gap(name, 'whole file', 1, 'no importer adapter for this source')
    for name in KNOWN_SOURCES:                # fixed order: contacts/jobs before things that point at them
        if name in sources:
            ADAPTERS[name](name, sources[name], plan)
    return plan


# ── apply ────────────────────────────────────────────────────────────────────
def existing_keys(store) -> dict:
    """import key -> (collection, record id), from append-only `created` activity rows.

    Only rows written by the importer itself count (channel 'import'). The tenant's agent can put any
    text in source.ref, but it can never write channel 'import' (AGENT_CHANNELS excludes it), so a
    forged `import:` ref cannot make the importer skip, or link to, a record it did not create.
    """
    with store._read() as conn:
        rows = conn.execute("SELECT ref, collection, record_id FROM activity WHERE type = 'created' "
                            "AND channel = 'import' AND ref LIKE ?", (REF_PREFIX + '%',)).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _resolve(data, ids, dropped):
    out = {}
    for k, v in data.items():
        if isinstance(v, Ref):
            if v.key in ids:
                out[k] = ids[v.key]
            else:
                dropped.append(k)
        else:
            out[k] = v
    return out


def apply_plan(store, plan: Plan, *, run_by: str | None = None) -> dict:
    from services.biz.schema import SchemaError
    have = existing_keys(store)
    ids = {k: rid for k, (_c, rid) in have.items()}
    per = {}
    rejects = []
    if any(c == 'job' and 'gen_contract_price' in d for _k, c, d, _w in plan.items):
        job_spec = store.registry.get('job') or {}
        if 'gen_contract_price' not in (job_spec.get('fields') or {}):
            store.adopt_library_field('job', 'gen_contract_price', by=BY,
                                      source={'channel': 'import', 'ref': REF_PREFIX + 'adopt:job.gen_contract_price'})
    for key, coll, data, words in plan.items:
        row = per.setdefault(coll, {'planned': 0, 'created': 0, 'already_imported': 0, 'merged_in_run': 0,
                                    'rejected': 0})
        row['planned'] += 1
        if key in have and have[key][0] == coll:
            row['already_imported'] += 1
            continue
        if key in ids:
            # the same item planned again in this run (a repeat customer, a shared phone, a repeated id):
            # it already exists, so this copy is merged into it, never created twice
            row['merged_in_run'] += 1
            continue
        dropped = []
        clean = _resolve(data, ids, dropped)
        try:
            rec = store.create(coll, clean, by=BY,
                               source={'channel': 'import', 'ref': key,
                                       'words': _clip(f'importer {IMPORTER_VERSION}'
                                                      + (f' run by {run_by}' if run_by else '')
                                                      + f': {words or key}', 4000)})
        except SchemaError as exc:
            row['rejected'] += 1
            rejects.append({'key': key, 'collection': coll, 'code': exc.code,
                            'fields': [f.get('field') for f in exc.extra.get('fields', [])][:10],
                            'dropped_refs': dropped})
            continue
        ids[key] = rec['id']
        row['created'] += 1
        if dropped:
            rejects.append({'key': key, 'collection': coll, 'code': 'ref_dropped', 'fields': dropped,
                            'note': 'created without these links: the record they point at was not imported'})
    return {'collections': per, 'rejects': rejects}


def recompute_checks(store, plan: Plan) -> list:
    """Running totals recomputed from the imported records (never imported), compared to the recorded ones."""
    out = []
    for chk in plan.checks:
        if chk['kind'] != 'running_totals':
            continue
        prefix = f'{REF_PREFIX}{chk["source"]}#job:'
        with store._read() as conn:
            jobs = conn.execute("SELECT r.id, r.data FROM records r JOIN activity a ON a.record_id = r.id "
                                "AND a.type = 'created' AND a.channel = 'import' AND r.collection = 'job' "
                                "WHERE a.ref LIKE ? AND r.archived_at IS NULL",
                                (prefix + '%',)).fetchall()
            job_ids = [j[0] for j in jobs]
            revenue = round(sum(json.loads(j[1]).get('gen_contract_price') or 0 for j in jobs), 2)
            cost = 0.0
            if job_ids:
                marks = ','.join('?' * len(job_ids))
                cost = conn.execute(f"SELECT TOTAL(json_extract(data, '$.amount')) FROM records WHERE "
                                    f"collection = 'cost_entry' AND archived_at IS NULL AND "
                                    f"json_extract(data, '$.job_id') IN ({marks})", job_ids).fetchone()[0] or 0.0
        net = round(revenue - cost, 2)
        rec = chk['recorded']
        out.append({'source': chk['source'], 'recorded': rec,
                    'recomputed': {'jobs': len(jobs), 'revenue': revenue, 'cost': round(cost, 2), 'net_profit': net,
                                   'net_margin_pct': round(100 * net / revenue, 1) if revenue else None},
                    'delta': {'jobs': len(jobs) - rec['jobs'] if _num(rec.get('jobs')) is not None else None,
                              'revenue': round(revenue - rec['revenue'], 2) if _num(rec.get('revenue')) is not None else None,
                              'net_profit': round(net - rec['net_profit'], 2) if _num(rec.get('net_profit')) is not None else None}})
    return out


_RUN_LOCKS = {}
_RUN_LOCKS_GUARD = threading.Lock()


def _run_lock(store):
    """One live import at a time per database file in this process (two concurrent runs could both miss a key)."""
    with _RUN_LOCKS_GUARD:
        return _RUN_LOCKS.setdefault(str(Path(store.path).resolve()), threading.Lock())


def run(store, sources: dict, *, dry_run: bool = True, expect_tenant: str | None = None,
        run_by: str | None = None) -> dict:
    """Import `sources` ({relative source path: parsed JSON}) into this tenant's store.

    dry_run: run the identical apply path against a temporary copy and report; the live db is untouched.
    expect_tenant: the tenant the caller believes it is importing for; a mismatch is refused.
    """
    if expect_tenant is not None and expect_tenant != store.tenant:
        raise ImportRefused('tenant_mismatch', f'import is for tenant {expect_tenant!r} but this business database '
                                               f'belongs to {store.tenant!r}; refused')
    if not isinstance(sources, dict) or not sources:
        raise ImportRefused('no_sources', 'nothing to import')
    plan = build_plan(sources)
    report = {'importer': IMPORTER_VERSION, 'tenant': store.tenant, 'dry_run': bool(dry_run),
              'source_rows': plan.source_rows, 'unmapped': plan.unmapped}
    if not dry_run:
        with _run_lock(store):
            report.update(apply_plan(store, plan, run_by=run_by))
            report['checks'] = recompute_checks(store, plan)
            report['counts_after'] = store.collection_counts()
        return report
    from services.biz.store import BizStore
    with tempfile.TemporaryDirectory(prefix='biz-import-dry-') as tmp:
        tmp = Path(tmp)
        store.backup_to(tmp / 'business.db')
        overlay = store.db_dir / 'schema' / 'custom'
        if overlay.is_dir():
            shutil.copytree(overlay, tmp / 'schema' / 'custom')
        copy = BizStore(tmp, store.tenant)
        try:
            report.update(apply_plan(copy, plan, run_by=run_by))
            report['checks'] = recompute_checks(copy, plan)
            report['counts_after'] = copy.collection_counts()
        finally:
            copy.close()
    report['counts_before'] = store.collection_counts()
    return report


def main(argv=None):
    """In-container entry: `python3 -m services.biz.importer [--apply] [--expect-tenant T] < bundle.json`.

    The bundle is {"sources": {path: json}}. The store is this container's own (env), never a path.
    """
    import argparse
    import sys
    from services.biz.schema import SchemaError
    from services.biz.store import BizStore, resolve_location
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write to the live db (default: dry run)')
    ap.add_argument('--expect-tenant', required=True)
    ap.add_argument('--backup-to', help='file name inside the db dir for a verified pre-import backup')
    a = ap.parse_args(argv)
    bundle = json.load(sys.stdin)
    try:
        tenant, db_dir = resolve_location(hosted=True)
        store = BizStore(db_dir, tenant)
    except SchemaError as exc:
        print(json.dumps({'refused': exc.code, 'message': exc.message}))
        return 3
    try:
        if a.apply and a.backup_to:
            if '/' in a.backup_to or not a.backup_to.startswith('business.db.bak-pre-import-'):
                print(json.dumps({'refused': 'bad_backup_name'}))
                return 2
            b = store.backup_to(store.db_dir / a.backup_to)
            if not b.get('verified'):
                print(json.dumps({'refused': 'backup_not_verified', 'backup': b}))
                return 4
        rep = run(store, bundle.get('sources') or {}, dry_run=not a.apply, expect_tenant=a.expect_tenant)
        print(json.dumps(rep, default=str))
        return 0
    except ImportRefused as exc:
        print(json.dumps({'refused': exc.code, 'message': exc.message}))
        return 3
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
