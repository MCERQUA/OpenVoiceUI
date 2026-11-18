"""Promotion gate for shared-library candidates (services/biz/field_gate.py) and the
platform promotion step (services/biz/promote.py).

A candidate definition may reach other tenants only if it carries no data values,
no customer names, phone numbers, emails, addresses, prices, and none of the
tenant's own brand terms. Positive controls: generic definitions and every field
in the shipped library pass. Negative controls: each forbidden kind is refused,
and a refusal never echoes the matched text.
"""
import json
import os
import sys
from pathlib import Path

import pytest

from services.biz import field_gate
from services.biz.schema import LIBRARY_PATH, CORE_DIR, Registry, load_library

ROOT = Path(__file__).resolve().parent.parent
TERMS = field_gate.brand_terms_for('tenant-a', {'name': 'Desert Sky Insulation LLC',
                                                'website': 'https://www.desertskyfoam.com'})
NAMES = ['Bartholomew Quist', 'Maria Delgado-Ruiz', 'Attic Pros LLC']


@pytest.fixture(scope='module')
def vocab(tmp_path_factory):
    reg = Registry(tmp_path_factory.mktemp('overlay'))
    return field_gate.vocabulary_from(reg)


def vet(defn, vocab):
    return field_gate.vet_definition(defn, brand_terms=TERMS, customer_names=NAMES, vocabulary=vocab)


# ── positive controls ───────────────────────────────────────────────────────
@pytest.mark.parametrize('defn', [
    {'key': 'gate_code', 'type': 'string', 'label': 'Gate code'},
    {'key': 'attic_depth_in', 'type': 'number', 'label': 'Attic depth', 'unit': 'in',
     'description': 'Depth of existing insulation measured at the hatch'},
    {'key': 'roof_pitch', 'type': 'enum', 'label': 'Roof pitch', 'values': ['low', 'medium', 'steep']},
    {'key': 'drum_lot', 'type': 'string', 'label': 'Drum lot number', 'collection': 'job'},
    {'key': 'r_target', 'type': 'integer', 'label': 'R-value target', 'min': 11, 'max': 60},
])
def test_generic_definitions_pass(defn, vocab):
    v = vet(defn, vocab)
    assert v == {'verdict': 'pass', 'reasons': []}, v


def test_every_shipped_library_field_passes_the_gate(vocab):
    lib = load_library(LIBRARY_PATH)
    assert len(lib['fields']) >= 100
    failures = {}
    for key, f in lib['fields'].items():
        defn = {'key': key, 'type': f['type'], 'label': f['label']}
        for k in ('unit', 'min', 'max', 'values', 'description'):
            if f.get(k) is not None:
                defn[k] = f[k]
        v = field_gate.vet_definition(defn, vocabulary=vocab)
        if v['verdict'] != 'pass':
            failures[key] = v['reasons']
    assert failures == {}


# ── negative controls ───────────────────────────────────────────────────────
@pytest.mark.parametrize('defn,reason', [
    ({'key': 'callback', 'type': 'string', 'label': 'Call back at (602) 555-0144'}, 'contains a phone number'),
    ({'key': 'callback', 'type': 'string', 'label': 'Callback', 'description': 'ring 480.555.0199'},
     'contains a phone number'),
    ({'key': 'contact', 'type': 'string', 'label': 'Contact', 'description': 'send to bq@example.com'},
     'contains an email address'),
    ({'key': 'site', 'type': 'string', 'label': 'Job at 4417 E Camelback Road'}, 'contains a street address'),
    ({'key': 'site', 'type': 'string', 'label': 'Site', 'description': 'Phoenix, AZ 85018'},
     'contains a street address'),
    ({'key': 'bag_cost', 'type': 'number', 'label': 'Bag cost', 'description': 'we charge $45 a bag'},
     'contains a price'),
    ({'key': 'set_cost', 'type': 'number', 'label': 'Set cost 1850 dollars'}, 'contains a price'),
    ({'key': 'rate', 'type': 'number', 'label': 'Rate', 'unit': 'per sqft at 1.35'}, 'contains a price'),
    ({'key': 'desertsky_rating', 'type': 'integer', 'label': 'Rating'}, "contains the tenant's own brand term"),
    ({'key': 'rating', 'type': 'integer', 'label': 'Desert Sky rating'}, "contains the tenant's own brand term"),
    ({'key': 'promo', 'type': 'string', 'label': 'Promo', 'description': 'from desertskyfoam ads'},
     "contains the tenant's own brand term"),
    ({'key': 'quist_notes', 'type': 'text', 'label': 'Notes'}, 'contains a customer name'),
    ({'key': 'notes', 'type': 'text', 'label': 'Notes for Bartholomew Quist'}, 'contains a customer name'),
    ({'key': 'crew_lead', 'type': 'enum', 'label': 'Crew lead', 'values': ['Delgado', 'Other']},
     'contains a customer name'),
    ({'key': 'gate_code', 'type': 'string', 'label': 'Gate code', 'example': '4411'},
     'carries keys beyond a definition'),
    ({'key': 'gate_code', 'type': 'string', 'label': 'Gate code', 'data': {'job-1': '4411'}},
     'carries keys beyond a definition'),
])
def test_forbidden_content_is_refused(defn, reason, vocab):
    v = vet(defn, vocab)
    assert v['verdict'] == 'refuse'
    assert any(r.startswith(reason) for r in v['reasons']), v


def test_trade_word_in_a_customer_name_does_not_block_generic_fields(vocab):
    # "Attic Pros LLC" is a customer, but "attic" is ordinary trade vocabulary
    v = vet({'key': 'attic_access', 'type': 'string', 'label': 'Attic access'}, vocab)
    assert v['verdict'] == 'pass', v


def test_refusal_reasons_never_echo_the_matched_text(vocab):
    v = vet({'key': 'quist', 'type': 'string', 'label': 'Quist at 4417 E Camelback Road, (602) 555-0144',
             'description': 'Desert Sky, $45'}, vocab)
    blob = json.dumps(v).lower()
    for leaked in ('quist', 'camelback', '555', 'desert', '45'):
        assert leaked not in blob


def test_brand_terms_derive_from_company_record():
    assert 'tenant-a' in TERMS and 'Desert Sky Insulation LLC' in TERMS
    assert 'desert sky insulation' in TERMS and 'desertskyfoam' in TERMS


# ── library integrity ───────────────────────────────────────────────────────
def test_library_keys_never_collide_with_core_fields():
    lib = load_library(LIBRARY_PATH)
    raw = json.loads(LIBRARY_PATH.read_text())
    assert len(lib['fields']) == len(raw['fields'])          # no entry was dropped as malformed
    core_fields = set()
    for f in CORE_DIR.glob('*.json'):
        core_fields.update(json.loads(f.read_text())['fields'])
    assert not (set(lib['fields']) & core_fields)
    for key, f in lib['fields'].items():
        assert f['source']['id'] in lib['sources'], key
        assert f['trade'] in ('spray-foam', 'blow-in', 'fiberglass', 'general'), key
        if f.get('min') is not None or f.get('max') is not None:
            assert f['type'] in ('number', 'integer', 'money'), key


def test_library_covers_the_requested_spray_foam_set():
    lib = load_library(LIBRARY_PATH)['fields']
    for key in ('sf_yield_per_set', 'sf_ab_ratio', 'sf_a_side_pressure', 'sf_b_side_pressure',
                'sf_a_side_temperature', 'sf_b_side_temperature', 'sf_hose_temperature',
                'sf_ambient_temperature', 'sf_substrate_temperature', 'sf_humidity', 'sf_pass_thickness_in',
                'sf_total_thickness_in', 'sf_board_feet', 'sf_sets_used', 'sf_a_batch_number',
                'sf_b_batch_number', 'sf_stroke_count', 'sf_foam_type', 'sf_r_value_target'):
        assert key in lib, key
    assert lib['sf_foam_type']['values'] == ['open', 'closed']


# ── the platform promotion step ─────────────────────────────────────────────
def test_promotion_review_across_tenants(tmp_path):
    from services.biz.store import BizStore
    dbs = []
    for tenant, company, customer in (('tenant-a', 'Desert Sky Insulation', 'Bartholomew Quist'),
                                      ('tenant-b', 'Northwind Foam Co', 'Ingrid Vasquez')):
        d = tmp_path / tenant / 'db'
        s = BizStore(d, tenant)
        s.create('company', {'name': company}, by='test')
        s.create('contact', {'name': customer}, by='test')
        s.add_custom_schema({'collection': 'job', 'fields': {
            'x_gate_code': {'type': 'string', 'label': 'Gate code'},
            'x_' + customer.split()[1].lower() + '_notes': {'type': 'text', 'label': 'Notes'}}}, by='test')
        s.close()
        dbs.append(d / 'business.db')
    before = [os.path.getmtime(p) for p in dbs]
    from services.biz import promote
    reg = Registry(tmp_path / 'no-overlay')
    result = promote.review(dbs, set(reg.library['fields']), field_gate.vocabulary_from(reg))
    passing = {c['key']: c for c in result['candidates_passing_gate']}
    assert passing['gate_code']['tenant_count'] == 2
    assert sorted((r['tenant'], r['reasons'][0]) for r in result['refused']) == [
        ('tenant-a', 'contains a customer name'), ('tenant-b', 'contains a customer name')]
    out = json.dumps(result, default=sorted).lower()
    for leaked in ('bartholomew', 'quist', 'ingrid', 'vasquez', 'desert sky', 'northwind'):
        assert leaked not in out
    assert [os.path.getmtime(p) for p in dbs] == before      # read-only on every tenant db
