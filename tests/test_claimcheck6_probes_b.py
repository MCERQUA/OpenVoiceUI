"""Gate-4 re-check #6 probes, part b: the agent key's shell in the OVU container (/ws/terminal) reaches the db dir.

T1 reads the shipped code (the websocket cannot be driven by the Flask test client). T2 shows what a shell running as
the OVU user gets from one `cat <db dir>/staff-surface.key`: full staff powers on the staff origin, no Clerk.
HOLE probes assert the BAD outcome: PASSED means the hole is real.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, END_FIELDS, SBX, STAFF_ORIGIN, _ends, codes, dirs, h, world,
)
from tests.test_biz_crt_tools import _watch

ROOT = Path(__file__).resolve().parents[1]


def test_T1_HOLE_agent_key_opens_a_login_shell_in_the_ovu_container():
    src = (ROOT / 'server.py').read_text(encoding='utf-8')
    m = re.search(r'@sock\.route\("/ws/terminal"\)\ndef terminal_websocket\(ws\):(.*?)\n# ----', src, re.S)
    body = m.group(1)
    assert 'has_agent_key = bool(agent_key) and request.headers.get("X-Agent-Key") == agent_key' in body
    assert 'if not user_id and not has_agent_key:' in body          # agent key alone is enough
    assert 'pty.fork()' in body and 'os.execvp(shell, [shell, "-l"])' in body
    assert not re.search(r'setuid|setgid|chroot|unshare|seccomp', body)   # same uid as the app: reads the db dir
    app = (ROOT / 'app.py').read_text(encoding='utf-8')
    assert "'/ws/terminal'," in app                                # app gate lets the handler self-authenticate
    # the R1 design rests on the agent never reaching the db dir (routes/biz.py _surface_secret docstring)
    assert "The agent's container does not mount the db dir" in (ROOT / 'routes' / 'biz.py').read_text(encoding='utf-8')
    print('T1 /ws/terminal: X-Agent-Key -> pty.fork + login shell as the app uid; no uid drop / chroot')


def _mac(key, *parts):
    return hmac.new(key, '|'.join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()


def test_T2_HOLE_one_cat_of_the_db_dir_key_gives_the_agent_full_staff_powers(h, dirs):
    """World: the ongoing-ops endorsement names another project (WRONG_PROJECT). The agent reads the staff-surface
    key the way a shell in the OVU container would (same uid, 0600 file in the db dir), forges a staff cookie and a
    page token for any clerk id, rewrites the reading on the staff surface, confirms it and freezes a review."""
    w = world(h, endorsements=_ends(scheduled_projects=['Tempe Warehouse Retrofit']), party_name='T Two Inc')
    aid = w['asg']['id']
    assert codes(h.state(aid)) == ['WRONG_PROJECT']
    key = (Path(dirs['a']) / 'staff-surface.key').read_bytes()            # `cat /app/runtime/db/staff-surface.key`
    assert len(key) == 32
    by = 'clerk:user_forged_by_agent'
    exp = int(time.time()) + 3600
    rnd = secrets.token_hex(12)
    cookie = f's2.{exp}.{rnd}.{by.encode().hex()}.{_mac(key, "session", "s2", STAFF_ORIGIN, by, exp, rnd)}'
    rnd2 = secrets.token_hex(12)
    page = f'v2.{exp}.{rnd2}.{_mac(key, "page", "v2", STAFF_ORIGIN, by, SBX, exp, rnd2)}'
    c = h.app.test_client()                                                # the agent's own HTTP client, no Clerk
    c.set_cookie('__Host-jb_staff', cookie, domain=STAFF_ORIGIN.split('//')[1])
    hdr = {'X-Biz-Sandbox': SBX, 'X-JB-Staff-Surface': page, 'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin'}
    e = c.get(f'{B}/crt_endorsement/{w["end"]["ai_ongoing"]["id"]}', headers=hdr, base_url=STAFF_ORIGIN)
    assert e.status_code == 200, e.get_json()
    e = e.get_json()
    r = c.patch(f'{B}/crt_endorsement/{e["id"]}', json={'data': {'scheduled_projects': ['Mesa Clinic Expansion']}},
                headers={**hdr, 'If-Match': str(e['version'])}, base_url=STAFF_ORIGIN)
    print('T2 forged-staff patch', r.status_code, (r.get_json() or {}).get('code'))
    e = r.get_json()
    r2 = c.post(f'{B}/crt/confirm', json={'record_collection': 'crt_endorsement', 'record_id': e['id'],
                                          'record_version': e['version'], 'fields': END_FIELDS,
                                          'source_document_id': e['data']['document_id']},
                headers=hdr, base_url=STAFF_ORIGIN)
    print('T2 forged-staff confirm', r2.status_code, (r2.get_json() or {}).get('code'))
    rv = c.post(f'{B}/crt/assignment/{aid}/review', json={}, headers=hdr, base_url=STAFF_ORIGIN)
    print('T2 forged-staff review', rv.status_code, (rv.get_json() or {}).get('review', {}).get('data', {}).get('state'))
    ev = h.state(aid)
    print('T2', ev['state'], codes(ev))
    assert r.status_code == 200 and r2.status_code == 201 and rv.status_code == 201 and ev['state'] == 'meets', ev
    v, rows, _ = _watch(dirs)
    print('WATCH T2', v, json.dumps(rows)[:500])
