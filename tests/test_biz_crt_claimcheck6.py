"""Rules 1.6.0 regression set: gate-4 re-check #6 (docs/jambot/contractors-choice-claimcheck6-20261117.md) under
host rulings D, M, U, G, W, K and X4. Each case is written to FAIL on 9ba90ea (rules 1.5.0) and pass here; positive
controls say so in their name. The verbatim re-checker probes are tests/test_claimcheck6_probes{,_b}.py. Fake data only.

D  the approval diff base is fixed server-side: the nearest APPROVED version up the set's supersedes chain; a draft of
   a draft is compared with the approved version; supersedes / template_key / set_version are the draft and revise
   routes' only; the page names only an approved set "the version in use"; the watcher checks the recorded base.
M  a missing or null min is the floor, never $0; an item with neither, a min below the floor, an unknown key, or an
   item whose identity (kind/line/limit/function/code) changes under the same id is refused; the diff lists every
   key of every item, then and now.
U  the umbrella a combined GL + umbrella test leans on gets every policy check (named insured, producer, expiry).
G  an ISO number (CG 20 xx, the floor) is that ISO form whatever its manuscript flag; CG 20 26 / 20 11 never count as
   completed operations; the note never tells staff to confirm an ISO form as manuscript.
W  a waiver is bound (server-stamped) to the function + document id + file hash raising the code; other or changed
   paper raises it again.
K  blanket applies only when the schedule is empty; a schedule naming anyone is checked.
X4 approval without diff_sha256 is refused.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, DOC_FIELDS, END_FIELDS, M, POLICY_FIELDS, SBX, SBX_KEY, SRC, STAFF_1, STAFF_2, STAFF_ORIGIN, TODAY, _ends,
    codes, confirm, dirs, h, iso, pdf, world,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed, _staff_confirm_wording

ROOT = Path(__file__).resolve().parents[1]
MEETS = ('meets', 'meets_accepted', 'expiring_30', 'expiring_15')


def _store(h):
    return h.app.extensions['biz']['sandbox:josh']


def _set(h, sid):
    h.staff()
    return h.ok(h.get(f'/crt_requirement_set/{sid}'))


def _revise(h, sid, body, who='agent'):
    getattr(h, who)()
    return h.post(f'/crt/requirement-sets/{sid}/revise', body)


def _repin(h, w, new):
    h.staff()
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    return h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': new['id'],
                                                                'requirement_set_version': new['version']}},
                        a['version']))


def _approve(h, sid):
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    return d, h.ok(h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']}))


def _forge(h, w, state='meets', set_id=None):
    """A review claiming Meets written straight into the store (a bug or a forger): the watcher must re-derive."""
    asg = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return _store(h).create('crt_review', {
        'crt_assignment_id': w['asg']['id'], 'state': state, 'label': 'Meets requirements (documents reviewed)',
        'variance_codes': [], 'requirement_set_id': set_id or asg['data']['requirement_set_id'],
        'requirement_set_version': asg['data']['requirement_set_version'], 'confirmation_ids': [],
        'reviewed_by': 'clerk:' + STAFF_1, 'reviewed_at': now}, by='clerk:' + STAFF_1, via_pack=True)


def _watch_rows(dirs):
    v, rows, _ = _watch(dirs)
    return v, rows


def _review(h, w):
    h.staff()
    return h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']


def _waive(h, w, code, **extra):
    h.staff()
    return h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': code,
                               'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                               'acceptance_document_id': w['docs']['waiver_ok']['id'],
                               'recorded_by': 'clerk:' + STAFF_1, 'reason': 'accepted in writing',
                               'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=300)), **extra})


def _no_completed():
    e = _ends()
    e.pop('ai_completed')
    return e


# ═════════════════════════ D: the diff base is the approved version in use ═════════════════════════
def test_d_a_draft_of_a_draft_is_compared_with_the_approved_version(h):
    w = world(h, endorsements=_no_completed(), party_name='D Reg One Inc')
    x = _set(h, w['set']['id'])
    a = h.ok(_revise(h, x['id'], {'items': [it for it in x['data']['items'] if it['id'] != 'ai_completed']}),
             201)['record']
    b = h.ok(_revise(h, a['id'], {}), 201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{b["id"]}/diff'))
    assert d['against']['kind'] == 'set' and d['against']['id'] == x['id'] and d['against']['approved'] is True
    assert [u['id'] for u in d['unapproved_between']] == [a['id']]
    assert ('ai_completed', 'removed') in [(i['id'], i['change']) for i in d['items']]


def test_d_approval_records_the_approved_base(h):
    w = world(h, party_name='D Reg Two Inc')
    x = _set(h, w['set']['id'])
    a = h.ok(_revise(h, x['id'], {}), 201)['record']
    b = h.ok(_revise(h, a['id'], {}), 201)['record']
    _d, out = _approve(h, b['id'])
    ag = out['confirmation']['data']['approved_against']
    assert ag == {'kind': 'set', 'id': x['id'], 'record_version': x['version']}, ag


@pytest.mark.parametrize('field,value', [('supersedes', None), ('template_key', 'mep'), ('set_version', 1)])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_d_a_generic_create_never_names_the_base(h, field, value, who):
    w = world(h, party_name=f'D Gen {field} {who} Inc')
    x = _set(h, w['set']['id'])
    getattr(h, who)()
    data = {'name': x['data']['name'], 'trade_category': x['data']['trade_category'], 'items': x['data']['items'],
            'form_functions': x['data']['form_functions'], field: value if value is not None else x['id']}
    r = h.post('/crt_requirement_set', {'data': data})
    assert r.status_code == 409 and r.get_json()['code'] == 'set_field_server_only', r.get_json()


def test_d_a_staff_edit_never_moves_the_base(h):
    w = world(h, approve=False, party_name='D Edit Inc')
    other = h.ok(h.post('/crt/requirement-sets/from-template', {'template_key': 'mep'}), 201)['record']
    h.staff()
    s = _set(h, w['set']['id'])
    r = h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'supersedes': other['id']}}, s['version'])
    assert r.status_code == 409 and r.get_json()['code'] == 'set_field_server_only', r.get_json()


def test_d_a_decoy_draft_is_never_called_the_version_in_use(h):
    w = world(h, party_name='D Decoy Inc')
    x = _set(h, w['set']['id'])
    h.agent()
    decoy = h.ok(h.post('/crt_requirement_set', {'data': {
        'name': x['data']['name'], 'trade_category': x['data']['trade_category'],
        'items': [it for it in x['data']['items'] if it['id'] != 'ai_completed'],
        'form_functions': x['data']['form_functions']}}), 201)
    b = h.ok(_revise(h, decoy['id'], {}), 201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{b["id"]}/diff'))
    assert d['against'] is None and [u['id'] for u in d['unapproved_between']] == [decoy['id']]
    assert all(i['change'] == 'added' for i in d['items'])


def test_d_an_archived_approved_version_is_not_the_base(h):
    w = world(h, party_name='D Archived Inc')
    x = _set(h, w['set']['id'])
    y = h.ok(_revise(h, x['id'], {}, who='staff'), 201)['record']
    _approve(h, y['id'])
    z = h.ok(_revise(h, y['id'], {}), 201)['record']
    h.staff()
    h.ok(h.post(f'/crt_requirement_set/{y["id"]}/archive'))
    d = h.ok(h.get(f'/crt/requirement-sets/{z["id"]}/diff'))
    assert d['against']['id'] == x['id'], d['against']


def test_d_an_archived_set_is_never_the_base_even_with_a_current_approval(h):
    """Archiving bumps the version (so the approval no longer matches); this pins the archived check on its own: a
    confirmation written around the API at the archived version still never makes it the version in use."""
    w = world(h, party_name='D Archived Two Inc')
    x = _set(h, w['set']['id'])
    y = h.ok(_revise(h, x['id'], {}, who='staff'), 201)['record']
    _approve(h, y['id'])
    z = h.ok(_revise(h, y['id'], {}), 201)['record']
    h.staff()
    y = h.ok(h.post(f'/crt_requirement_set/{y["id"]}/archive'))
    _store(h).create('crt_confirmation', {'record_collection': 'crt_requirement_set', 'record_id': y['id'],
                                          'record_version': y['version'], 'fields': ['items', 'status', 'form_functions'],
                                          'confirmed_by': 'clerk:' + STAFF_1, 'on_staff_surface': True},
                     by='clerk:' + STAFF_1, via_pack=True, surface=True)
    d = h.ok(h.get(f'/crt/requirement-sets/{z["id"]}/diff'))
    assert d['against']['id'] == x['id'] and [u['id'] for u in d['unapproved_between']] == [y['id']], d['against']


def test_d_positive_control_watch_passes_an_honest_two_step_approval(h, dirs):
    """The D1 chain after the fix: the page showed 'ai_completed removed' against the approved set; staff approved it
    and re-pinned. Meets (by staff's decision); the watcher's RD finds the approved base and PASSes."""
    w = world(h, endorsements=_no_completed(), party_name='D Honest Inc')
    x = _set(h, w['set']['id'])
    a = h.ok(_revise(h, x['id'], {'items': [it for it in x['data']['items'] if it['id'] != 'ai_completed']}),
             201)['record']
    b = h.ok(_revise(h, a['id'], {}), 201)['record']
    _d, out = _approve(h, b['id'])
    _repin(h, w, out['record'])
    assert h.state(w['asg']['id'])['state'] == 'meets'
    assert _review(h, w)['state'] == 'meets'
    v, rows = _watch_rows(dirs)
    assert v == 'PASS', rows


def test_watch_d_an_approval_compared_with_a_draft_is_not_backed(h, dirs):
    """The 1.5.0 D1 chain written around the fixed route: B approved with its base recorded as the draft A."""
    w = world(h, endorsements=_no_completed(), party_name='D Watch Inc')
    x = _set(h, w['set']['id'])
    a = h.ok(_revise(h, x['id'], {'items': [it for it in x['data']['items'] if it['id'] != 'ai_completed']}),
             201)['record']
    b = h.ok(_revise(h, a['id'], {}), 201)['record']
    st = _store(h)
    b = st.update('crt_requirement_set', b['id'], {'status': 'APPROVED'}, expected_version=b['version'],
                  by='clerk:' + STAFF_1, via_pack=True, surface=True)
    st.create('crt_confirmation', {'record_collection': 'crt_requirement_set', 'record_id': b['id'],
                                   'record_version': b['version'], 'fields': ['items', 'status', 'form_functions'],
                                   'confirmed_by': 'clerk:' + STAFF_1, 'on_staff_surface': True,
                                   'approved_diff_sha256': 'a' * 64,
                                   'approved_against': {'kind': 'set', 'id': a['id'], 'record_version': a['version']}},
              by='clerk:' + STAFF_1, via_pack=True, surface=True)
    _repin(h, w, b)
    assert h.state(w['asg']['id'])['state'] == 'meets'        # the engine reads the stored approval as it is
    _review(h, w)
    v, rows = _watch_rows(dirs)
    assert v == 'FAIL' and any(r.startswith('RD') and 'draft' in r for row in rows for r in row['reasons']), rows


# ═════════════════════════ M: every verdict-relevant key, the floor never $0 ═════════════════════════
def _low_gl_world(h, tag):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 250_000, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}}}
    w = world(h, policies=pols, party_name=tag)
    assert codes(h.state(w['asg']['id'])) == ['LIMIT_LOW:gl']
    return w


def _items_with(x, iid, change, drop=()):
    out = []
    for it in x['data']['items']:
        if it['id'] == iid:
            it = {k: v for k, v in dict(it, **change).items() if k not in drop}
        out.append(it)
    return out


@pytest.mark.parametrize('how', ['null', 'missing'])
def test_m_a_null_or_missing_min_is_the_floor(h, how):
    w = _low_gl_world(h, f'M Null {how} Inc')
    x = _set(h, w['set']['id'])
    items = _items_with(x, 'gl_occ', {'min': None}) if how == 'null' else _items_with(x, 'gl_occ', {}, drop=('min',))
    a = h.ok(_revise(h, x['id'], {'items': items}), 201)['record']
    d, out = _approve(h, a['id'])
    row = {r['key']: r for r in next(i for i in d['items'] if i['id'] == 'gl_occ')['rows']}
    assert row['minimum_applied']['was'] == row['minimum_applied']['now'] == 1 * M, row
    _repin(h, w, out['record'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['LIMIT_LOW:gl'], ev


def test_m_a_limit_item_with_no_min_and_no_floor_is_refused(h):
    w = world(h, party_name='M None Inc')
    x = _set(h, w['set']['id'])
    r = _revise(h, x['id'], {'items': _items_with(x, 'gl_occ', {}, drop=('min', 'floor'))})
    assert r.status_code == 422 and r.get_json()['code'] == 'limit_floor_required', r.get_json()


def test_m_a_stored_limit_item_with_no_floor_reads_cant_tell(h):
    w = world(h, party_name='M Stored Inc')
    x = _set(h, w['set']['id'])
    from services.biz.packs import risk_transfer as rt
    data = dict(x['data'], items=_items_with(x, 'gl_occ', {}, drop=('min', 'floor')))
    ev = rt.evaluate_inputs(dict(rt.gather(_store(h), w['asg']['id']), set=dict(x, data=data)), TODAY)
    assert ev['state'] == 'cant_tell' and ev['items']['gl_occ'] == 'UNCONFIRMED', ev


def test_m_a_min_below_its_floor_is_refused(h):
    w = world(h, party_name='M Below Inc')
    x = _set(h, w['set']['id'])
    r = _revise(h, x['id'], {'items': _items_with(x, 'gl_occ', {'min': 500_000})})
    assert r.status_code == 422 and r.get_json()['code'] == 'safety_floor', r.get_json()


@pytest.mark.parametrize('key', ['minimum', 'Min', 'limit_key', 'x_note'])
def test_m_an_unknown_item_key_is_refused(h, key):
    w = world(h, party_name=f'M Key {key} Inc')
    x = _set(h, w['set']['id'])
    r = _revise(h, x['id'], {'items': _items_with(x, 'gl_occ', {key: 1})})
    assert r.status_code == 422 and r.get_json()['code'] == 'unknown_item_key', r.get_json()


@pytest.mark.parametrize('iid,change', [('gl_occ', {'limit': 'general_aggregate'}), ('gl_occ', {'line': 'umbrella'}),
                                        ('ai_completed', {'function': 'ai_ongoing', 'forms': ['CG 20 10']}),
                                        ('gl_occ', {'kind': 'ai'})])
@pytest.mark.parametrize('who', ['agent', 'staff'])
def test_m_an_item_never_changes_what_it_measures_under_the_same_id(h, iid, change, who):
    w = world(h, party_name=f'M Swap {iid} {sorted(change)[0]} {who} Inc')
    x = _set(h, w['set']['id'])
    r = _revise(h, x['id'], {'items': _items_with(x, iid, change)}, who=who)
    assert r.status_code == 422 and r.get_json()['code'] == 'item_identity_changed', r.get_json()


def test_m_a_staff_generic_edit_cannot_swap_a_key_either(h):
    w = world(h, approve=False, party_name='M Edit Inc')
    h.staff()
    s = _set(h, w['set']['id'])
    r = h.patch(f'/crt_requirement_set/{s["id"]}', {'data': {'items': _items_with(s, 'gl_occ', {'limit': 'general_aggregate'})}},
                s['version'])
    assert r.status_code == 422 and r.get_json()['code'] == 'item_identity_changed', r.get_json()


def test_m_the_diff_lists_every_key_of_every_item_then_and_now(h):
    w = world(h, party_name='M Rows Inc')
    x = _set(h, w['set']['id'])
    a = h.ok(_revise(h, x['id'], {'items': _items_with(x, 'gl_occ', {'ceiling': 3 * M, 'cite': 'staff raised'})}),
             201)['record']
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{a["id"]}/diff'))
    now = {it['id']: it for it in a['data']['items']}
    for it in d['items']:
        keys = {r['key'] for r in it['rows']}
        assert set(now[it['id']]) <= keys, (it['id'], set(now[it['id']]) - keys)
    gl = next(i for i in d['items'] if i['id'] == 'gl_occ')
    assert gl['change'] == 'changed' and sorted(gl['changed_fields']) == ['ceiling', 'cite']
    assert d['notes'] == {'was': x['data'].get('notes'), 'now': a['data'].get('notes')}


def test_watch_m_a_null_min_meets_is_not_backed(h, dirs):
    """A set stored around the API with min null over a $250k GL: the watcher reads the floor itself (RM)."""
    w = _low_gl_world(h, 'M Watch Inc')
    x = _set(h, w['set']['id'])
    # the stored set carries min null (as 1.5.0 accepted), approved and pinned, then a forged Meets
    import sqlite3
    items = _items_with(x, 'gl_occ', {'min': None})
    conn = sqlite3.connect(str(Path(dirs['a']) / 'sandboxes' / 'josh' / 'business.db'))
    conn.execute('UPDATE records SET data = ? WHERE id = ?', (json.dumps(dict(x['data'], items=items)), x['id']))
    conn.commit()
    conn.close()
    _forge(h, w)
    v, rows = _watch_rows(dirs)
    assert v == 'FAIL' and any('R3' in r and 'below' in r for row in rows for r in row['reasons']), rows


# ═════════════════════════ U: the combined test's umbrella gets every policy check ═════════════════════════
def _combined_world(h, tag, umbrella):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'limits': {'each_occurrence': 1 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 6 * M}, **umbrella},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='steel_erection', policies=pols, party_name=tag)
    x = _set(h, w['set']['id'])
    a = h.ok(_revise(h, x['id'], {'items': [it for it in x['data']['items'] if it['id'] != 'umb']}, who='staff'),
             201)['record']
    _d, out = _approve(h, a['id'])
    _repin(h, w, out['record'])
    return w


def test_u_the_combined_tests_umbrella_must_name_this_sub(h):
    w = _combined_world(h, 'U Reg One Inc', {'named_insured': 'Unrelated Holdings LLC'})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and 'WRONG_INSURED' in codes(ev), ev
    assert w['pol']['umbrella']['id'] in ev['policies_read']


def test_u_the_combined_tests_umbrella_expiry_is_reported(h):
    w = _combined_world(h, 'U Reg Two Inc', {'expiration': iso(TODAY + timedelta(days=5))})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'expiring_15' and ev['expiring']['line'] == 'umbrella', ev


def test_u_the_combined_tests_umbrella_producer_is_checked(h):
    w = _combined_world(h, 'U Reg Three Inc', {'producer_phone': None, 'producer_email': None})
    ev = h.state(w['asg']['id'])
    assert 'PRODUCER_INCOMPLETE' in codes(ev), ev


def test_u_positive_control_an_honest_umbrella_meets_the_combined_test(h):
    w = _combined_world(h, 'U Honest Inc', {})
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and any('combined' in n for n in ev['notes']), ev


def test_watch_u_a_combined_meets_on_another_companys_umbrella_is_not_backed(h, dirs):
    w = _combined_world(h, 'U Watch Inc', {'named_insured': 'Unrelated Holdings LLC'})
    _forge(h, w)
    v, rows = _watch_rows(dirs)
    assert v == 'FAIL' and any('limit is below' in r for row in rows for r in row['reasons']), rows


# ═════════════════════════ G: an ISO number is never a manuscript ═════════════════════════
@pytest.mark.parametrize('form', ['CG 20 26', 'CG 20 11'])
def test_g_cg2026_and_cg2011_confirmed_manuscript_never_cover_completed_ops(h, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'G {form} Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and ev['items']['ai_completed'] == 'MISSING:ai_completed', ev


@pytest.mark.parametrize('form', ['CG 20 26', 'CG 20 15'])
def test_g_the_note_never_asks_staff_to_confirm_an_iso_form_as_manuscript(h, form):
    e = _ends(form=form)
    w = world(h, endorsements=e, party_name=f'G Note {form} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev
    assert not [n for n in ev['notes'] if 'manuscript form' in n and 'never as a manuscript' not in n], ev['notes']


def test_g_a_manuscript_flag_on_an_unlisted_iso_number_is_read_as_that_iso_form(h):
    w = world(h, endorsements=_ends(form='CG 20 15', manuscript=True), party_name='G Unlisted Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_endorsement.form' in codes(ev), ev


def test_g_positive_control_a_non_iso_manuscript_still_counts_with_its_wording(h):
    w = world(h, endorsements=_completed(form='ACME COMP 7', manuscript=True), party_name='G Manuscript Inc')
    _staff_confirm_wording(h, w)
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_watch_g_a_cg2026_manuscript_meets_is_not_backed(h, dirs):
    w = world(h, endorsements=_completed(form='CG 20 26', manuscript=True), party_name='G Watch Inc')
    _staff_confirm_wording(h, w)
    _forge(h, w)
    v, rows = _watch_rows(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for row in rows for r in row['reasons']), rows


# ═════════════════════════ W: a waiver is bound to the paper it was given for ═════════════════════════
def test_w_the_server_stamps_the_binding_and_ignores_the_callers(h):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='W Stamp Inc')
    wv = _waive(h, w, 'WRONG_PROJECT', bound_evidence=[{'function': 'ai_completed', 'document_id': 'x', 'sha256': 'y'}])
    be = wv['data']['bound_evidence']
    doc = w['docs']['e_ongoing']
    assert be == [{'function': 'ai_ongoing', 'document_id': doc['id'], 'sha256': doc['data']['file_url'][len('biz-file:'):]}], be
    assert h.state(w['asg']['id'])['state'] == 'meets_accepted'


def test_w_a_later_miss_on_other_paper_is_raised_again(h):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='W Later Inc')
    _waive(h, w, 'WRONG_PROJECT')
    h.staff()
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_completed"]["id"]}'))
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'scheduled_projects': ['Tempe Warehouse Retrofit']}},
                     e['version']))
    confirm(h, 'crt_endorsement', e, END_FIELDS, e['data']['document_id'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_PROJECT'] and ev['accepted'] == [], ev


def test_w_a_changed_document_raises_it_again(h):
    """The same document record re-pointed at other bytes (staff did it, honestly): the hash moved."""
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='W Bytes Inc')
    _waive(h, w, 'WRONG_PROJECT')
    h.staff()
    up = h.upload(pdf('renewal-paper'), 'renew.pdf')
    d = h.ok(h.get(f'/document/{w["docs"]["e_ongoing"]["id"]}'))
    d = h.ok(h.patch(f'/document/{d["id"]}', {'data': {'file_url': up['url']}}, d['version']))
    confirm(h, 'document', d, DOC_FIELDS)
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_ongoing"]["id"]}'))
    confirm(h, 'crt_endorsement', e, END_FIELDS, d['id'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and codes(ev) == ['WRONG_PROJECT'], ev


def test_w_a_waiver_recorded_before_the_code_binds_nothing(h):
    w = world(h, party_name='W Early Inc')
    _waive(h, w, 'WRONG_PROJECT')
    h.staff()
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_ongoing"]["id"]}'))
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'scheduled_projects': ['Tempe Warehouse Retrofit']}},
                     e['version']))
    confirm(h, 'crt_endorsement', e, END_FIELDS, e['data']['document_id'])
    assert h.state(w['asg']['id'])['state'] == 'variance'


@pytest.mark.parametrize('field,value', [('variance_code', 'WRONG_PARTY'),
                                         ('bound_evidence', [{'function': 'ai_completed', 'document_id': 'd',
                                                              'sha256': 'x'}])])
def test_w_the_binding_never_changes_after_the_fact(h, field, value):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name=f'W Fixed {field} Inc')
    wv = _waive(h, w, 'WRONG_PROJECT')
    r = h.patch(f'/crt_waiver/{wv["id"]}', {'data': {field: value}}, wv['version'])
    assert r.status_code == 409 and r.get_json()['code'] == 'waiver_binding_fixed', r.get_json()


def test_w_positive_control_the_waived_paper_itself_stays_accepted(h):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='W Same Inc')
    _waive(h, w, 'WRONG_PROJECT')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets_accepted' and [a['code'] for a in ev['accepted']] == ['WRONG_PROJECT'], ev


def test_watch_w_a_waiver_of_other_paper_never_backs_the_meets(h, dirs):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='W Watch Inc')
    _waive(h, w, 'WRONG_PROJECT')
    h.staff()
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_completed"]["id"]}'))
    e = h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'scheduled_projects': ['Tempe Warehouse Retrofit']}},
                     e['version']))
    confirm(h, 'crt_endorsement', e, END_FIELDS, e['data']['document_id'])
    _forge(h, w, state='meets_accepted')
    v, rows = _watch_rows(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for row in rows for r in row['reasons']), rows


def test_watch_w_positive_control_the_bound_waiver_passes(h, dirs):
    w = world(h, endorsements=_ends(scheduled_projects=['Mesa Clinic Phase 1']), party_name='W Watch Ok Inc')
    _waive(h, w, 'WRONG_PROJECT')
    assert _review(h, w)['state'] == 'meets_accepted'
    v, rows = _watch_rows(dirs)
    assert v == 'PASS', rows


# ═════════════════════════ K: blanket never skips a schedule ═════════════════════════
def test_k_a_blanket_flag_beside_a_schedule_naming_another_gc_is_checked(h):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=['Zenith General Contractors Inc'],
                                    scheduled_projects=['Tempe Warehouse Retrofit']), party_name='K Reg Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and {'WRONG_PARTY', 'WRONG_PROJECT'} <= set(codes(ev)), ev


def test_k_positive_control_blanket_with_no_schedule_and_the_contract_tick(h):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=[], scheduled_projects=[]), party_name='K Ok Inc')
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_watch_k_a_blanket_meets_beside_a_wrong_schedule_is_not_backed(h, dirs):
    w = world(h, endorsements=_ends(blanket=True, scheduled_names=['Zenith General Contractors Inc'],
                                    scheduled_projects=['Tempe Warehouse Retrofit']), party_name='K Watch Inc')
    _forge(h, w)
    v, rows = _watch_rows(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') for row in rows for r in row['reasons']), rows


# ═════════════════════════ X4 / version ═════════════════════════
@pytest.mark.parametrize('body', [{}, {'diff_sha256': None}, {'diff_sha256': ''}])
def test_x4_an_approval_without_the_diff_hash_is_refused(h, body):
    w = world(h, approve=False, party_name='X4 Reg Inc')
    h.staff()
    r = h.post(f'/crt/requirement-sets/{w["set"]["id"]}/approve', body)
    assert r.status_code == 422 and r.get_json()['code'] == 'diff_sha256_required', r.get_json()


def test_watch_x4_an_approval_that_names_no_diff_is_not_backed(h, dirs):
    w = world(h, party_name='X4 Watch Inc')
    x = _set(h, w['set']['id'])
    b = h.ok(_revise(h, x['id'], {}, who='staff'), 201)['record']
    st = _store(h)
    b = st.update('crt_requirement_set', b['id'], {'status': 'APPROVED'}, expected_version=b['version'],
                  by='clerk:' + STAFF_1, via_pack=True, surface=True)
    st.create('crt_confirmation', {'record_collection': 'crt_requirement_set', 'record_id': b['id'],
                                   'record_version': b['version'], 'fields': ['items', 'status', 'form_functions'],
                                   'confirmed_by': 'clerk:' + STAFF_1, 'on_staff_surface': True,
                                   'approved_against': {'kind': 'set', 'id': x['id'], 'record_version': x['version']}},
              by='clerk:' + STAFF_1, via_pack=True, surface=True)
    _repin(h, w, b)
    assert _review(h, w)['state'] == 'meets'
    v, rows = _watch_rows(dirs)
    assert v == 'FAIL' and any('X4' in r for row in rows for r in row['reasons']), rows


def test_rules_version_is_1_6_0():
    from services.biz.packs import risk_transfer as rt
    assert rt.RULE_VERSION == 'crt-rules-1.10.2'         # rules 1.7.0-1.10.2 (re-checks #7-#12) keep every 1.6.0 rule
