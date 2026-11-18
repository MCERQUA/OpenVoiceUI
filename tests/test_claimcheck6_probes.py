"""Gate-4 re-check #6 probes (non-author, opus, 2026-11-17), written from the spec (plan 4.3/4.4, the 1.5.0 notes,
ruling F "staff approve what the page showed them -- the full diff against the version it replaces") and the threat
model (a prompt-injected agent working for a sub: agent key + bridged canvas JWT + any HTTP client), plus honest-but-
wrong data. NOT derived from the 1.5.0 fixes.

HOLE probes assert the BAD outcome: PASSED means the hole is real. CONTROL probes assert the good outcome.
Each HOLE prints the watcher's verdict ("WATCH <probe> <verdict>") after a staff review freezes the state.
"""
import json
from datetime import timedelta

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, DOC_FIELDS, END_FIELDS, M, POLICY_FIELDS, SBX, STAFF_1, TODAY, _ends, codes, confirm, dirs, h, iso, pdf, world,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed, _staff_confirm_wording


def _no_completed():
    e = _ends()
    e.pop('ai_completed')
    return e


def _review_and_watch(h, dirs, w, tag):
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']
    v, rows, _ = _watch(dirs)
    print(f'WATCH {tag}', v, rv['state'], json.dumps(rows)[:700])
    return v, rv


def _repin(h, w, new):
    h.staff()
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    return h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': new['id'],
                                                                'requirement_set_version': new['version']}},
                        a['version']))


def _approve_as_page(h, sid):
    """What cca-crt-requirements.html does: GET the diff, render it, POST approve with the diff's sha256."""
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    rec = h.ok(h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']}))['record']
    return d, rec


def _page_visible_change(d):
    """The columns the approval page renders per item (cca-crt-requirements.html renderDiff): anything else that
    changed shows only as a bare 'changed' chip."""
    cols = ('function', 'required', 'forms', 'edition', 'trade_categories', 'classes', 'min')
    out = []
    for it in d['items']:
        if it['change'] in ('added', 'removed'):
            out.append((it['id'], it['change']))
        elif it['change'] == 'changed':
            out.append((it['id'], 'changed', [k for k in it['changed_fields'] if k in cols]))
    return out


# ───────────── D: the approval diff is against an agent-chosen base (ruling F guarantee) ─────────────
def test_D0_control_agent_revision_dropping_completed_ops_shows_as_removed(h):
    w = world(h, endorsements=_no_completed(), party_name='D Zero Inc')
    assert codes(h.state(w['asg']['id'])) == ['MISSING:ai_completed']
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.agent()
    a = h.ok(h.post(f'/crt/requirement-sets/{s["id"]}/revise',
                    {'items': [it for it in s['data']['items'] if it['id'] != 'ai_completed']}), 201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{a["id"]}/diff'))
    assert ('ai_completed', 'removed') in _page_visible_change(d), d['items']


def test_D1_HOLE_two_step_revision_launders_the_diff_and_reaches_plain_meets(h, dirs):
    """The sub has NO completed-operations endorsement (MISSING:ai_completed). The agent revises the approved, pinned
    set X into A (drops the completed-ops item), then revises A into B with no change. B's approval page shows every
    item 'same' against "the version in use" -- which is A, an unapproved agent draft, not X. Staff approve exactly
    what the page showed; the assignment then says "newer requirement set available"; staff re-pin."""
    w = world(h, endorsements=_no_completed(), party_name='D One Inc')
    aid = w['asg']['id']
    ev0 = h.state(aid)
    assert codes(ev0) == ['MISSING:ai_completed'], ev0                        # control: honest state
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.agent()
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise',
                    {'items': [it for it in x['data']['items'] if it['id'] != 'ai_completed']}), 201)['record']
    b = h.ok(h.post(f'/crt/requirement-sets/{a["id"]}/revise', {}), 201)['record']
    d, b = _approve_as_page(h, b['id'])
    print('D1 diff against', d['against'], 'visible changes', _page_visible_change(d),
          'table changes', {k: d['form_functions'][k] for k in ('added', 'removed', 'changed')})
    assert d['against']['id'] == a['id'] and d['against']['kind'] == 'set'
    assert _page_visible_change(d) == [] and not d['floor_violations']
    assert not any(d['form_functions'][k] for k in ('added', 'removed', 'changed'))
    ev1 = h.state(aid)
    print('D1 label after approval', ev1['label'], ev1['newer_requirement_set'])
    assert ev1['label'].endswith('; newer requirement set available') and ev1['newer_requirement_set']['id'] == b['id']
    _repin(h, w, b)
    ev = h.state(aid)
    print('D1', ev0['state'], codes(ev0), '->', ev['state'], codes(ev), ev['items'])
    assert ev['state'] == 'meets', ev
    _review_and_watch(h, dirs, w, 'D1')


def test_D2_HOLE_generic_create_decoy_base_reads_as_the_version_in_use(h):
    """Variant: the agent's decoy base is a generic DRAFT create carrying the pinned set's name and set_version, so
    the approval header reads 'against the version in use (v1)' while the base is the decoy."""
    w = world(h, party_name='D Two Inc')
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.agent()
    loose = [it for it in x['data']['items'] if it['id'] != 'ai_completed']
    r = h.post('/crt_requirement_set', {'data': {'name': x['data']['name'], 'trade_category': x['data']['trade_category'],
                                                 'items': loose, 'form_functions': x['data']['form_functions'],
                                                 'set_version': x['data']['set_version']}})
    print('D2 generic create', r.status_code, (r.get_json() or {}).get('code'))
    decoy = h.ok(r, 201)
    b = h.ok(h.post(f'/crt/requirement-sets/{decoy["id"]}/revise', {}), 201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{b["id"]}/diff'))
    print('D2 against', d['against'], 'visible', _page_visible_change(d))
    assert d['against']['set_version'] == x['data']['set_version'] and d['against']['id'] != x['id']
    assert _page_visible_change(d) == []


# ───────────── M: verdict-relevant item fields the approval page never renders ─────────────
def _low_gl_world(h, tag):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 250_000, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols, party_name=tag)
    assert codes(h.state(w['asg']['id'])) == ['LIMIT_LOW:gl']                 # control
    return w


def _revise_item(h, w, iid, change):
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [dict(it, **change) if it['id'] == iid else it for it in x['data']['items']]
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items})
    print('revise', iid, change, r.status_code, (r.get_json() or {}).get('code'))
    return h.ok(r, 201)['record']


def test_M1_HOLE_min_null_reads_as_zero_not_the_floor(h, dirs):
    """Plan 4.3: min_* = the floor unless staff raise it. An explicit null min makes the minimum $0 (engine AND
    watcher read it.get('min', floor) or 0). The page shows Minimum 'was 1000000 / now -' and the floor is unchanged."""
    w = _low_gl_world(h, 'M One Inc')
    a = _revise_item(h, w, 'gl_occ', {'min': None})
    stored = [it for it in a['data']['items'] if it['id'] == 'gl_occ'][0]
    print('M1 stored item', stored)
    d, a = _approve_as_page(h, a['id'])
    print('M1 visible', _page_visible_change(d))
    _repin(h, w, a)
    ev = h.state(w['asg']['id'])
    print('M1', ev['state'], codes(ev), ev['items'].get('gl_occ'))
    assert ev['state'] == 'meets', ev
    _review_and_watch(h, dirs, w, 'M1')


def test_M2_HOLE_limit_key_swap_is_invisible_on_the_approval_table(h, dirs):
    """The agent's revision points the GL each-occurrence item at the aggregate (2M). The approval table shows the
    item 'changed' with no changed column (function/required/forms/edition/trades/classes/min all equal)."""
    w = _low_gl_world(h, 'M Two Inc')
    a = _revise_item(h, w, 'gl_occ', {'limit': 'general_aggregate'})
    d, a = _approve_as_page(h, a['id'])
    print('M2 visible', _page_visible_change(d))
    assert _page_visible_change(d) == [('gl_occ', 'changed', [])]
    _repin(h, w, a)
    ev = h.state(w['asg']['id'])
    print('M2', ev['state'], codes(ev))
    assert ev['state'] == 'meets', ev
    _review_and_watch(h, dirs, w, 'M2')


# ───────────── U: the combined GL + umbrella test reads an umbrella nobody checks ─────────────
def test_U1_HOLE_combined_test_accepts_another_companys_umbrella(h, dirs):
    """High severity allows "$1M/$2M with adequate umbrella". Staff drop the separate umbrella item (a sub with the
    combined path only). The umbrella the combined test reads is another legal entity's: WRONG_INSURED (never
    waivable) is never computed for it, and its expiry never reaches 'expiring'."""
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 6 * M},
                         'named_insured': 'Unrelated Holdings LLC', 'expiration': iso(TODAY + timedelta(days=5))},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='steel_erection', policies=pols, party_name='U One Inc')
    ev0 = h.state(w['asg']['id'])
    print('U1 control', ev0['state'], codes(ev0))
    assert 'WRONG_INSURED' in codes(ev0)                                       # control: umbrella item present
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    h.staff()                                                                  # staff themselves revise (honest path)
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise',
                    {'items': [it for it in x['data']['items'] if it['id'] != 'umb']}), 201)['record']
    d, a = _approve_as_page(h, a['id'])
    _repin(h, w, a)
    ev = h.state(w['asg']['id'])
    print('U1', ev['state'], codes(ev), ev['expiring'], ev['notes'])
    assert ev['state'] == 'meets' and 'WRONG_INSURED' not in codes(ev), ev
    _review_and_watch(h, dirs, w, 'U1')


# ───────────── G: CG 20 26 counts for completed ops through the manuscript path ─────────────
def test_G0_control_cg2026_not_manuscript_is_not_completed_ops(h):
    w = world(h, endorsements=_completed(form='CG 20 26'), party_name='G Zero Inc')
    ev = h.state(w['asg']['id'])
    print('G0', ev['state'], codes(ev))
    assert ev['state'] != 'meets', ev


def test_G1_HOLE_cg2026_as_manuscript_with_wording_meets_completed_ops(h, dirs):
    """Ruling F floor: CG 20 26 never covers completed operations (it is not in a set's table or an item's forms).
    Confirmed as manuscript with completed-ops wording, it satisfies ai_completed. R3 closed this route for CG 20 10
    only ('a LISTED ISO number is never manuscript'); CG 20 26 / CG 20 11 are ISO numbers but not 'listed'."""
    w = world(h, endorsements=_completed(form='CG 20 26', manuscript=True), party_name='G One Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    print('G1', ev['state'], codes(ev), ev['items'].get('ai_completed'))
    assert ev['state'] == 'meets' and ev['items']['ai_completed'] == 'OK', ev
    _review_and_watch(h, dirs, w, 'G1')


# ───────────── W: a waiver is per code, not per paper or function ─────────────
def _waive(h, w, code):
    h.staff()
    return h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': code,
                               'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                               'acceptance_document_id': w['docs']['waiver_ok']['id'],
                               'recorded_by': 'clerk:' + STAFF_1, 'reason': 'ongoing-ops paper names the old job name',
                               'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=300))})


def test_W1_HOLE_ongoing_ops_waiver_silently_covers_a_later_completed_ops_miss(h, dirs):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='W One Inc')
    assert codes(h.state(w['asg']['id'])) == ['WRONG_PROJECT']
    assert h.state(w['asg']['id'])['items']['ai_completed'] == 'OK'          # completed ops right when accepted
    _waive(h, w, 'WRONG_PROJECT')
    assert h.state(w['asg']['id'])['state'] == 'meets_accepted'
    # later: the sub's completed-ops paper is replaced by one naming another job; staff file and confirm it honestly
    h.staff()
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_completed"]["id"]}'))
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'scheduled_projects': ['Tempe Warehouse Retrofit']}},
                     e['version']))
    confirm(h, 'crt_endorsement', e, END_FIELDS, e['data']['document_id'])
    ev = h.state(w['asg']['id'])
    print('W1', ev['state'], codes(ev), ev['items'], ev['accepted'])
    assert ev['state'] == 'meets_accepted' and ev['items']['ai_completed'] != 'OK', ev
    assert len(ev['accepted']) == 1
    _review_and_watch(h, dirs, w, 'W1')


# ───────────── K: blanket flag beside a schedule naming someone else ─────────────
def test_K1_HOLE_blanket_flag_skips_a_schedule_that_names_another_gc(h, dirs):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=['Zenith General Contractors Inc'],
                                    scheduled_projects=['Tempe Warehouse Retrofit']), party_name='K One Inc')
    ev = h.state(w['asg']['id'])
    print('K1', ev['state'], codes(ev))
    assert ev['state'] == 'meets', ev
    _review_and_watch(h, dirs, w, 'K1')


# ───────────── records the matrix does not list (agent side): controls ─────────────
def test_X1_control_agent_unarchive_of_a_staff_archived_waiver_ok(h):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='X One Inc')
    _waive(h, w, 'WRONG_PROJECT')
    assert h.state(w['asg']['id'])['state'] == 'meets_accepted'
    h.staff()
    h.ok(h.post(f'/document/{w["docs"]["waiver_ok"]["id"]}/archive'))       # the contractor withdrew the OK
    assert h.state(w['asg']['id'])['state'] == 'variance'
    h.agent()
    r = h.post(f'/document/{w["docs"]["waiver_ok"]["id"]}/unarchive')
    ev = h.state(w['asg']['id'])
    print('X1', r.status_code, ev['state'], codes(ev))
    assert ev['state'] not in ('meets', 'meets_accepted'), ev


def test_X2_control_agent_repoints_the_contract_document_file(h):
    w = world(h, party_name='X Two Inc')
    h.agent()
    d = h.ok(h.get(f'/document/{w["docs"]["contract"]["id"]}'))
    r = h.patch(f'/document/{d["id"]}', {'data': {'file_url': w['files']['cert2']['url']}}, d['version'])
    ev = h.state(w['asg']['id'])
    print('X2', r.status_code, ev['state'], codes(ev))
    assert ev['state'] not in ('meets', 'meets_accepted'), ev


def test_X3_control_agent_edits_waiver_ok_identity(h):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='X Three Inc')
    _waive(h, w, 'WRONG_PROJECT')
    h.agent()
    d = h.ok(h.get(f'/document/{w["docs"]["waiver_ok"]["id"]}'))
    r = h.patch(f'/document/{d["id"]}', {'data': {'file_url': w['files']['cert2']['url']}}, d['version'])
    ev = h.state(w['asg']['id'])
    print('X3', r.status_code, ev['state'], codes(ev))
    assert ev['state'] != 'meets_accepted', ev


def test_X4_approve_without_diff_sha_is_accepted(h):
    """Comment in set_approve: 'a set changed since (or a diff never fetched) is refused'. Staff-surface only."""
    w = world(h, approve=False, party_name='X Four Inc')
    h.staff()
    r = h.post(f'/crt/requirement-sets/{w["set"]["id"]}/approve', {})
    print('X4 approve with no diff_sha256', r.status_code, (r.get_json() or {}).get('code'))
    assert r.status_code == 200


def test_X5_list_state_changing_endpoints(h):
    """Every state-changing rule in the url_map (all prefixes), to diff against the matrix's ROUTES."""
    rows = sorted((r.rule, ','.join(sorted(set(r.methods) - {'GET', 'HEAD', 'OPTIONS'})), r.endpoint)
                  for r in h.app.url_map.iter_rules() if set(r.methods) - {'GET', 'HEAD', 'OPTIONS'})
    biz = [x for x in rows if x[0].startswith('/api/biz') or x[2].startswith('biz.')]
    print('X5 biz state-changing', len(biz))
    for x in biz:
        print('X5', *x)
    print('X5 all state-changing', len(rows))
