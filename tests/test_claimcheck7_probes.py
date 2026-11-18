"""Gate-4 re-check #7 probes (non-author, opus, 2026-11-17), on the COMBINED tree (df962ce + PR #597 44fd830).

Written from the spec (plan 4.3 "items[] {... required/suggested ...}", 4.4 rules 2/3/5, the 1.6.0 notes M "every key,
the floor never $0", W "other paper, a changed document or a renewal raises it again", G "an ISO number is evaluated as
that ISO form whatever its manuscript flag says; the note on an unlisted ISO form never tells staff to confirm it as a
manuscript") and from the shipped approval page (cca-crt-requirements.html itemText: `it.required === false ?
'Suggested: ' : ''`). Threat model: a prompt-injected agent with the agent key and its own HTTP client, plus honest
staff who approve exactly what the page shows and re-pin when the label says a newer set exists. NOT derived from the
1.6.0 fixes.

HOLE probes assert the SPEC outcome, so FAILED means the hole is real (the host's round-7 convention; round 6 used the
opposite). CONTROL probes assert the good outcome and pass. Each HOLE prints the engine state it actually reached and,
where it reaches a Meets, the watcher's verdict ("WATCH <probe> <verdict>") after a staff review freezes that state.
"""
import json
from datetime import timedelta

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, DOC_FIELDS, END_FIELDS, M, NAMED, POLICY_FIELDS, SBX, STAFF_1, TODAY, _ends, codes, confirm, dirs, h, iso, pdf,
    world,
)
from tests.test_biz_crt_tools import _watch


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


def _approve_as_page(h, sid):
    """What cca-crt-requirements.html does: GET the diff, render it, POST approve with the diff's sha256."""
    h.staff()
    d = h.ok(h.get(f'/crt/requirement-sets/{sid}/diff'))
    rec = h.ok(h.post(f'/crt/requirement-sets/{sid}/approve', {'diff_sha256': d['sha256']}))['record']
    return d, rec


def _repin_if_prompted(h, w, rec):
    """Staff re-pin only when the assignment's label says this set is the newer requirement set (the D1/matrix rule)."""
    ev = h.state(w['asg']['id'])
    if (ev.get('newer_requirement_set') or {}).get('id') != rec['id']:
        return False
    h.staff()
    a = h.ok(h.get(f'/crt_assignment/{w["asg"]["id"]}'))
    h.ok(h.patch(f'/crt_assignment/{a["id"]}', {'data': {'requirement_set_id': rec['id'],
                                                         'requirement_set_version': rec['version']}}, a['version']))
    return True


def _page_item_text_required(it):
    """cca-crt-requirements.html itemText for an endorsement item: 'Suggested: ' only when required === false."""
    return 'Suggested' if it.get('required') is False else 'required'


def _page_val(v):
    """cca-crt-requirements.html val(): undefined / null render as '-'."""
    if v is None:
        return '-'
    if isinstance(v, list):
        return ', '.join(map(str, v)) if v else '(empty)'
    return str(v).lower() if isinstance(v, bool) else str(v)


# ───────────── R7-A: a null `required` reads "required" on the page and "not required" in the engine ─────────────
@pytest.mark.parametrize('value', [None, 0, ''], ids=['null', 'zero', 'empty'])
def test_R7_A1_HOLE_required_falsy_never_drops_a_required_item(h, dirs, value):
    """The sub has NO completed-operations endorsement (MISSING:ai_completed). The agent revises the approved, pinned
    set with one key changed on ai_completed: required true -> null (or 0 / ""). The approval page's item list still
    shows the item as required (itemText only says 'Suggested:' for `=== false`); the diff row reads 'true -> -'.
    Staff approve exactly what the page showed, the label says a newer set exists, staff re-pin."""
    w = world(h, endorsements=_no_completed(), party_name=f'A One {value!r} Inc')
    aid = w['asg']['id']
    ev0 = h.state(aid)
    assert codes(ev0) == ['MISSING:ai_completed'], ev0                          # control: honest state
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [dict(it, required=value) if it['id'] == 'ai_completed' else it for it in x['data']['items']]
    h.agent()
    r = h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items})
    print('A1 agent revise', r.status_code, (r.get_json() or {}).get('code'))
    a = h.ok(r, 201)['record']
    d, rec = _approve_as_page(h, a['id'])
    item = next(i for i in d['items'] if i['id'] == 'ai_completed')
    shown = [(row['key'], _page_val(row['was']), _page_val(row['now']), row['changed']) for row in item['rows']
             if row['changed']]
    now_it = next(it for it in rec['data']['items'] if it['id'] == 'ai_completed')
    print('A1 against', d['against'], '| changed rows', shown, '| page item list says', _page_item_text_required(now_it),
          '| floor_violations', d['floor_violations'])
    assert d['against']['id'] == x['id'] and d['against']['approved'] is True      # the right base (D holds)
    assert _page_item_text_required(now_it) == 'required'                           # the page still calls it required
    assert _repin_if_prompted(h, w, rec)
    ev = h.state(aid)
    print('A1', ev0['state'], codes(ev0), '->', ev['state'], codes(ev), ev['items'].get('ai_completed'))
    if ev['state'] in ('meets', 'meets_accepted'):
        _review_and_watch(h, dirs, w, f'A1[{value!r}]')
    # spec: the page approved a set it showed as still REQUIRING CG 20 37; the sub has none -> never a Meets
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev


def test_R7_A0_control_required_false_is_shown_as_suggested_by_the_page(h):
    """Control: an honest 'make it suggested' (required: false) is what the page labels 'Suggested:'."""
    w = world(h, endorsements=_no_completed(), party_name='A Zero Inc')
    x = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    items = [dict(it, required=False) if it['id'] == 'ai_completed' else it for it in x['data']['items']]
    h.staff()
    a = h.ok(h.post(f'/crt/requirement-sets/{x["id"]}/revise', {'items': items}), 201)['record']
    it = next(i for i in a['data']['items'] if i['id'] == 'ai_completed')
    assert _page_item_text_required(it) == 'Suggested'


# ───────────── R7-B: a COMPLETED_OPS_LAPSED waiver is bound to no paper, so a renewal never re-raises it ─────────────
def _lapsed_world(h, tag, policies=None):
    """Substantial completion was 30 days ago, repose 8 years: completed ops must be renewed; the sub has no CG 20 37
    on its GL (COMPLETED_OPS_LAPSED). Ongoing ops are over (N/A)."""
    return world(h, endorsements=_no_completed(), party_name=tag, policies=policies,
                 project={'substantial_completion': iso(TODAY - timedelta(days=30))})


def _waive(h, w, code):
    h.staff()
    return h.mk('crt_waiver', {'crt_assignment_id': w['asg']['id'], 'variance_code': code,
                               'accepted_by_contractor': 'Acme Builders PM (written OK on file)',
                               'acceptance_document_id': w['docs']['waiver_ok']['id'],
                               'recorded_by': 'clerk:' + STAFF_1, 'reason': 'accepted in writing for this term',
                               'compensating_control': 'none', 'expires': iso(TODAY + timedelta(days=700))})


def _renew(h, w, tag, limits=None):
    """The next policy term: new certificate (a new document), new policy records for every line, each person-confirmed
    on the staff surface against the new certificate; the P&NC endorsement re-issued on the new GL policy."""
    h.agent()
    up = h.upload(pdf(f'renewal-cert-{tag}'), 'renewal-cert.pdf')
    cert = h.mk('document', {'title': 'renewal cert', 'file_url': up['url'], 'kind': 'coi_acord25',
                             'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']})
    up2 = h.upload(pdf(f'renewal-pnc-{tag}'), 'renewal-pnc.pdf')
    epnc = h.mk('document', {'title': 'renewal pnc', 'file_url': up2['url'], 'kind': 'endorsement',
                             'crt_account_id': w['account']['id'], 'contact_id': w['party']['id']})
    eff, exp = TODAY + timedelta(days=195), TODAY + timedelta(days=560)
    new = {}
    for k, p in w['pol'].items():
        d = {f: v for f, v in p['data'].items() if f not in ('producer_is_cca',)}
        d.update(effective=iso(eff), expiration=iso(exp), source_document_id=cert['id'],
                 policy_number=str(d.get('policy_number')) + '-R')
        if limits and k in limits:
            d['limits'] = limits[k]
        new[k] = h.mk('crt_policy', d)
    e = h.mk('crt_endorsement', {'crt_account_id': w['account']['id'], 'crt_policy_id': new['gl']['id'],
                                 'function': 'pnc', 'form': 'CG 20 01', 'edition': '04 13', 'document_id': epnc['id'],
                                 **NAMED})
    h.staff()
    for k, p in list(new.items()):
        cur = h.ok(h.get(f'/crt_policy/{p["id"]}'))
        new[k] = h.ok(h.patch(f'/crt_policy/{p["id"]}', {'data': {'producer_is_cca': False}}, cur['version']))
    for dd in (cert, epnc):
        confirm(h, 'document', dd, DOC_FIELDS)
    for p in new.values():
        confirm(h, 'crt_policy', p, POLICY_FIELDS, cert['id'])
    confirm(h, 'crt_endorsement', e, END_FIELDS, epnc['id'])
    h.staff()
    return new, TODAY + timedelta(days=250)          # the old term expired at TODAY+200; the renewal is in force


def test_R7_B1_HOLE_completed_ops_lapsed_is_raised_again_on_a_renewal(h, dirs):
    """The contractor accepted, in writing, the missing CG 20 37 on THIS policy term. The sub renews (new certificate,
    new policies) and still sends no CG 20 37. Ruling W: 'a renewal raises it again'. Here the code was bound to
    (ai_completed, None, None) -- no paper at all -- so every later term is covered by the old acceptance."""
    w = _lapsed_world(h, 'B One Inc')
    aid = w['asg']['id']
    ev0 = h.state(aid)
    assert codes(ev0) == ['COMPLETED_OPS_LAPSED'], ev0                          # control: honest state
    wv = _waive(h, w, 'COMPLETED_OPS_LAPSED')
    print('B1 bound_evidence', wv['data'].get('bound_evidence'))
    ev1 = h.state(aid)
    assert ev1['state'] == 'meets_accepted', ev1                                # this term: accepted (as ruled)
    _new, later = _renew(h, w, 'b1')
    ev = h.state(aid, today=later)
    print('B1 after renewal', ev['state'], codes(ev), ev['accepted'], ev['policies_read'], ev['notes'][-2:])
    assert ev['policies_read'] and set(ev['policies_read']) <= {p['id'] for p in _new.values()}   # new term only
    if ev['state'] in ('meets', 'meets_accepted'):
        _review_and_watch(h, dirs, w, 'B1')
    # spec W: a renewal raises the code again; the old term's acceptance does not cover the new term
    assert ev['state'] == 'variance' and 'COMPLETED_OPS_LAPSED' in codes(ev), ev


def test_R7_B0_control_limit_low_waiver_is_raised_again_on_a_renewal(h):
    """Control for the binding: the same shape with LIMIT_LOW (bound to the certificate) is raised again on renewal."""
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-B0', 'limits': {'each_occurrence': 900_000,
                                                                      'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-B0', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-B0', 'addl_insd': False,
                     'limits': {'combined_single_limit': 1 * M}}}
    w = world(h, policies=pols, party_name='B Zero Inc')
    aid = w['asg']['id']
    assert codes(h.state(aid)) == ['LIMIT_LOW:gl']
    _waive(h, w, 'LIMIT_LOW:gl')
    assert h.state(aid)['state'] == 'meets_accepted'
    _new, later = _renew(h, w, 'b0')
    ev = h.state(aid, today=later)
    print('B0 after renewal', ev['state'], codes(ev))
    assert ev['state'] == 'variance' and 'LIMIT_LOW:gl' in codes(ev), ev


# ───────────── R7-C: an ISO number printed with its edition is not recognised as ISO (ruling G) ─────────────
def _staff_confirm_wording(h, w):
    h.staff()
    e = w['end']['ai_completed']
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])


def _completed(**kw):
    e = _ends()
    e['ai_completed'] = {**e['ai_completed'], **kw}
    return e


@pytest.mark.parametrize('form', ['CG 20 26', 'CG 20 26 04 13'], ids=['bare', 'with-edition'])
def test_R7_C0_HOLE_iso_form_with_edition_is_read_as_iso_not_invited_as_manuscript(h, form):
    """Ruling G: 'The note on an unlisted ISO form never tells staff to confirm it as a manuscript.' The form field as
    the footer prints it ('CG 20 26 04 13'); not manuscript; on the completed-operations item. No staff error yet:
    this is what the engine tells staff to do next. The bare number is the control."""
    w = world(h, endorsements=_completed(form=form), party_name=f'C Zero {form} Inc')
    ev = h.state(w['asg']['id'])
    note = [n for n in ev['notes'] if n.startswith('ai_completed:')]
    print('C0', form, ev['state'], codes(ev), note)
    if form == 'CG 20 26':
        assert 'MISSING:ai_completed' in codes(ev)                     # control: ISO 20 26 is never completed ops
    else:
        # spec G: an ISO number is read as that ISO form (CG 20 26 never completed ops) and the note never invites a
        # manuscript confirmation
        assert not any('manuscript form' in n and 'never as a manuscript' not in n for n in note), note
        assert 'MISSING:ai_completed' in codes(ev), ev


@pytest.mark.parametrize('form', ['CG 20 10 04 13', 'CG 20 26 04 13', 'ISO CG 20 10'])
def test_R7_C1_HOLE_iso_number_with_edition_never_counts_for_completed_ops(h, dirs, form):
    """Ruling R3/G: CG 20 10 (and CG 20 26 / 20 11) never satisfy completed operations by any route, 'not as
    manuscript'. The same number written the way the form footer prints it (with its edition) or with an 'ISO' prefix,
    confirmed manuscript with completed-operations wording (the step the C0 note invites), reaches plain Meets.
    Control: the bare 'CG 20 10' in the same world is MISSING (test_r3_a_listed_iso_form_is_never_read_as_manuscript)."""
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'C One {form} Inc')
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    print('C1', form, ev['state'], codes(ev), ev['items'].get('ai_completed'))
    if ev['state'] in ('meets', 'meets_accepted'):
        _review_and_watch(h, dirs, w, f'C1[{form}]')
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev    # spec R3/G: never completed ops


# ───────────── R7-D: the dev-page publish route follows a symlink the agent planted in its canvas folder ─────────────
def _plant_and_publish(h, dirs, target, name='planted-view.html'):
    """The dev-sandbox agent (it writes its canvas folder, canvas-pages/sandbox/<sandbox>/, from its own container)
    plants `name` as a symlink to `target`, then republishes `name` through PUT /dev/pages/<name> with its sandbox key.
    Returns (status, history copy path or None)."""
    from pathlib import Path
    pdir = Path(dirs['pages']) / 'sandbox' / SBX
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / name).symlink_to(target)
    h.agent()
    r = h.c.put(f'{B}/dev/pages/{name}', json={'html': '<!doctype html><title>v</title><p>v</p>',
                                               'source': {'channel': 'web', 'ref': 'r7d', 'confidence': 1.0}},
                headers=h.h, base_url=h.base)
    kept = (r.get_json() or {}).get('previous_kept_as')
    print('R7-D put', r.status_code, (r.get_json() or {}).get('code'), 'kept', kept)
    return r.status_code, (pdir / '.history' / kept) if kept else None


def test_R7_D1_HOLE_dev_page_publish_copies_the_staff_surface_key_into_the_agents_canvas_folder(h, dirs):
    """PR #597's guard (tests/test_agent_writable_io_guard.py) flags routes/biz.py dev_page_put / dev_page_get on the
    combined tree. This drives it: the history copy (shutil.copy2 follows the link) lands in the agent's own canvas
    folder holding the bytes of <db dir>/staff-surface.key -- the secret every staff session and page token is
    MACed with (T2 shows a forged staff session from those bytes reaches plain Meets). Spec (1.4.0 R1 / #597): the
    agent never reads the staff secret by any route."""
    from pathlib import Path
    world(h, party_name='D One Inc')                       # the pack is on: the staff secret exists
    key = Path(dirs['a']) / 'staff-surface.key'
    assert key.is_file() and len(key.read_bytes()) == 32
    st, copy = _plant_and_publish(h, dirs, key)
    leaked = bool(copy and copy.is_file() and copy.read_bytes() == key.read_bytes())
    print('R7-D1 history copy', copy, 'holds the staff key:', leaked)
    assert not leaked, f'the staff-surface key was copied to {copy} (agent-readable canvas folder)'


def test_R7_D0_control_dev_page_publish_of_a_plain_page_keeps_the_previous_version(h, dirs):
    """Control: a plain republish (no symlink) keeps the previous version in .history (the route's purpose)."""
    from pathlib import Path
    world(h, party_name='D Zero Inc')
    pdir = Path(dirs['pages']) / 'sandbox' / SBX
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / 'plain-view.html').write_text('<!doctype html><p>old</p>', encoding='utf-8')
    h.agent()
    r = h.c.put(f'{B}/dev/pages/plain-view.html', json={'html': '<!doctype html><p>new</p>',
                                                         'source': {'channel': 'web', 'ref': 'r7d0', 'confidence': 1.0}},
                headers=h.h, base_url=h.base)
    kept = r.get_json().get('previous_kept_as')
    assert r.status_code == 200 and kept and (pdir / '.history' / kept).read_text() == '<!doctype html><p>old</p>'
