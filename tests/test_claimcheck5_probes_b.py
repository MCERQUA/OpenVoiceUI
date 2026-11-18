"""Re-check #5, part b: the watcher on each HOLE world, the control reasons, and the pre-2004 twin of E1."""
import json
from datetime import timedelta

from tests.test_biz_crt_pack import (  # noqa: F401
    B, SBX, TODAY, codes, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _watch
from tests.test_biz_crt_claimcheck4 import _completed
from tests.test_recheck5_probes import _cca_policies, _staff_owned_endorsement_doc, _waive


def _review_and_watch(h, dirs, w, tag):
    h.staff()
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)['review']['data']
    v, rows, _ = _watch(dirs)
    mine = [r for r in rows if r.get('assignment_id') == w['asg']['id']]
    print(f'WATCH {tag} review={rv["state"]} watch={v}', json.dumps(mine)[:500])
    return rv['state'], v, mine


def test_C0_reason(h):
    w = world(h, policies=_cca_policies(), party_name='C Zero R Inc')
    ev = h.state(w['asg']['id'])
    print('C0 reason', ev['state'], codes(ev))


def test_C1_watch(h, dirs):
    w = world(h, policies=_cca_policies(), party_name='C One W Inc')
    h.agent()
    assert h.post(f'/company/{w["company"]["id"]}/archive').status_code == 200
    st, v, mine = _review_and_watch(h, dirs, w, 'C1')
    assert st == 'meets' and v == 'PASS'            # HOLE: the watcher agrees with the false Meets


def test_P1_watch(h, dirs):
    w = world(h, party_name='P One W Inc')
    d2 = _staff_owned_endorsement_doc(h, w)
    h.staff()
    h.ok(h.post(f'/document/{d2["id"]}/archive'))
    h.agent()
    assert h.post(f'/document/{d2["id"]}/unarchive').status_code == 200
    st, v, mine = _review_and_watch(h, dirs, w, 'P1')
    assert st == 'meets' and v == 'PASS'


def test_E3_watch(h, dirs):
    w = world(h, endorsements=_completed(edition='12 19'), party_name='E Three W Inc',
              project={'substantial_completion': iso(TODAY - timedelta(days=30))})
    h.agent()
    p = h.ok(h.get(f'/crt_project/{w["project"]["id"]}'))
    h.ok(h.patch(f'/crt_project/{p["id"]}', {'data': {'contract_ai_parties': ['Mesa Health District', 'Anyone LLC']}},
                 p['version']))
    _waive(h, w, 'WRONG_EDITION:ai_completed')
    st, v, mine = _review_and_watch(h, dirs, w, 'E3')
    assert st == 'meets_accepted'
    print('E3 watch verdict', v)


def test_E4_HOLE_pre2004_edition_hides_wrong_project_too(h):
    w = world(h, endorsements=_completed(edition='11 85', scheduled_projects=['Tempe Warehouse Retrofit']),
              party_name='E Four Inc')
    ev = h.state(w['asg']['id'])
    print('E4', ev['state'], codes(ev))
    assert codes(ev) == ['WRONG_FORM:ai_completed'], ev
