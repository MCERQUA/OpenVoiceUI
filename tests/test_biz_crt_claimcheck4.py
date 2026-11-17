"""Rules 1.4.0: regression set for gate-4 re-check #4 (host rulings R1, R3, R4, LOW) and the staff-write guard
review (narrow the 403 to crt, record provenance for contact / document / company, close its 3 mutants).

R1 is tested from the agent's side: the agent holds the staff Clerk JWT the canvas bridge handed one of its
pages (azp = the tenant origin) and talks to the backend as a plain HTTP client, forging any header it likes,
Host included. Every case goes through the real API. Fake data only.
"""
import os
import re
import stat
import time
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_biz_crt_pack import (  # noqa: F401  (fixtures)
    B, CANVAS_ORIGIN, DOC_FIELDS, END_FIELDS, M, POLICY_FIELDS, SBX, SBX_KEY, SRC, STAFF_1, STAFF_2, STAFF_ORIGIN,
    TODAY, _ends, codes, confirm, dirs, h, iso, world,
)
from tests.test_biz_crt_tools import _forge_meets, _watch

ROOT = Path(__file__).resolve().parents[1]
NAV = {'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Site': 'none'}
STAFF_HOST = STAFF_ORIGIN.split('//', 1)[1]


def _agent_client(h):
    """The agent's own HTTP client: a fresh cookie jar (it never holds the httpOnly staff cookie)."""
    return h.app.test_client()


def _forged(jwt='tok', **extra):
    """Everything a non-browser client can put on a request: the stolen bearer, forged fetch metadata."""
    return {'Authorization': f'Bearer {jwt}', 'X-Biz-Sandbox': SBX, 'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Site': 'same-origin', 'Origin': STAFF_ORIGIN,
            'X-JB-Staff-Signin': '1', **extra}


def _person_writes(w):
    pol = w['pol']['gl']
    return [
        ('POST', '/crt/confirm', {'record_collection': 'crt_policy', 'record_id': pol['id'], 'fields': ['line'],
                                  'source_document_id': w['docs']['cert']['id']}),
        ('POST', f'/crt/requirement-sets/{w["set"]["id"]}/approve', {}),
        ('POST', f'/crt/assignment/{w["asg"]["id"]}/review', {}),
        ('POST', f'/crt/assignment/{w["asg"]["id"]}/chase-sent', {'stage': 'T-30'}),
        ('PATCH', f'/crt_assignment/{w["asg"]["id"]}', {'data': {'trade_category': 'roofing'}}),
        ('POST', '/crt_waiver', {'data': {'crt_assignment_id': w['asg']['id']}}),
        ('POST', f'/crt_policy/{pol["id"]}/archive', None),
    ]


# ───────────────────────── R1: the staff surface is its own origin ─────────────────────────
def test_r1_an_exfiltrated_canvas_jwt_with_forged_headers_is_refused_at_every_person_write(h):
    """The re-check #4 attack, end to end: no sign-in, no page token, no person-write, no Meets."""
    w = world(h, confirm_all=False)
    h.staff(STAFF_1)                                   # the mock verifier: 'tok' is the bridge-handed JWT
    a = _agent_client(h)
    # 1. sign in on the staff origin with it: refused, its azp is the tenant origin
    r = a.post(f'{B}/crt/staff/session', json={'token': 'tok'}, headers=_forged(), base_url=STAFF_ORIGIN)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_origin_token_refused', r.get_json()
    assert 'Set-Cookie' not in r.headers
    # 2. mint a page token: on the tenant origin no page is served (a redirect, nothing minted) ...
    r = a.get(f'{B}/crt/staff/cca-crt-intake.html?sandbox={SBX}', headers=_forged())
    assert r.status_code == 302 and r.headers['Location'].startswith(STAFF_ORIGIN)
    assert b'__JB_SURFACE__' not in r.data and b'"token"' not in r.data
    # ... and on the staff origin (Host forged too) only the sign-in redirect, without the cookie
    r = a.get(f'{B}/crt/staff/cca-crt-intake.html?sandbox={SBX}', headers=_forged(), base_url=STAFF_ORIGIN)
    assert r.status_code == 302 and '/crt/staff/signin?next=' in r.headers['Location']
    assert b'__JB_SURFACE__' not in r.data
    # 3. every person-write, from the tenant origin and from the staff origin, with and without a forged token
    forged_tok = 'v2.%d.%s.%s' % (int(time.time()) + 3600, 'a' * 24, 'b' * 64)
    for method, path, body in _person_writes(w):
        for base, extra, want in ((None, {}, (403, 'staff_surface_required')),
                                  (None, {'X-JB-Staff-Surface': forged_tok}, (403, 'staff_surface_invalid')),
                                  (STAFF_ORIGIN, {}, (401, 'staff_session_required')),
                                  (STAFF_ORIGIN, {'X-JB-Staff-Surface': forged_tok}, (401, 'staff_session_required'))):
            kw = {'base_url': base} if base else {}
            r = a.open(B + path, method=method, json=body, headers={**_forged(), **extra, 'If-Match': '1'}, **kw)
            assert (r.status_code, (r.get_json() or {}).get('code')) == want, (method, path, base, extra, r.get_json())
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.line' in codes(ev)


@pytest.mark.parametrize('claims,origin,signin,code', [
    ({'sub': STAFF_1, 'azp': CANVAS_ORIGIN}, STAFF_ORIGIN, '1', 'staff_origin_token_refused'),   # canvas JWT
    ({'sub': STAFF_1}, STAFF_ORIGIN, '1', 'staff_origin_token_refused'),                          # no azp
    ({'sub': STAFF_1, 'azp': STAFF_ORIGIN + '.evil.example'}, STAFF_ORIGIN, '1', 'staff_origin_token_refused'),
    ({'sub': STAFF_1, 'azp': STAFF_ORIGIN.replace('https', 'http')}, STAFF_ORIGIN, '1', 'staff_origin_token_refused'),
    ({'sub': STAFF_1, 'azp': STAFF_ORIGIN}, CANVAS_ORIGIN, '1', 'staff_signin_refused'),         # another page
    ({'sub': STAFF_1, 'azp': STAFF_ORIGIN}, 'null', '1', 'staff_signin_refused'),
    ({'sub': STAFF_1, 'azp': STAFF_ORIGIN}, STAFF_ORIGIN, None, 'staff_signin_refused'),          # a form post
])
def test_r1_staff_sign_in_needs_a_jwt_minted_for_the_staff_origin(h, monkeypatch, claims, origin, signin, code):
    import services.auth as auth
    monkeypatch.setattr(auth, 'verify_clerk_claims', lambda t: dict(claims))
    a = _agent_client(h)
    hdr = {'Origin': origin, 'Sec-Fetch-Site': 'same-origin', **({'X-JB-Staff-Signin': signin} if signin else {})}
    r = a.post(f'{B}/crt/staff/session', json={'token': 'x'}, headers=hdr, base_url=STAFF_ORIGIN)
    assert r.status_code == 403 and r.get_json()['code'] == code, r.get_json()
    assert 'Set-Cookie' not in r.headers


def test_r1_the_staff_cookie_is_httponly_secure_strict_and_host_only(h):
    a = _agent_client(h)
    r = a.post(f'{B}/crt/staff/session', json={'token': 'staffjwt:' + STAFF_1}, base_url=STAFF_ORIGIN,
               headers={'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin', 'X-JB-Staff-Signin': '1'})
    assert r.status_code == 200 and r.get_json()['by'] == 'clerk:' + STAFF_1
    cookie = r.headers['Set-Cookie']
    assert cookie.startswith('__Host-jb_staff=')
    attrs = [x.strip().lower() for x in cookie.split(';')[1:]]
    assert 'httponly' in attrs and 'secure' in attrs and 'samesite=strict' in attrs and 'path=/' in attrs
    assert not any(x.startswith('domain=') for x in attrs)                     # host-only: never the tenant origin
    # the cookie is never sent to the tenant origin: a person-write there is still refused
    assert a.get_cookie('__Host-jb_staff', domain='localhost') is None
    r = a.post(f'{B}/crt/confirm', json={}, headers={'X-Biz-Sandbox': SBX})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_required'


def test_r1_a_staff_cookie_alone_never_writes(h):
    """CSRF from the same SITE (the tenant origin is same-site, so SameSite=Strict still sends the cookie): a form
    post or a no-cors fetch carries the cookie but never the page token. Refused, even for core collections."""
    w = world(h, confirm_all=False)
    h.signin(STAFF_1)
    pol = w['pol']['gl']
    for path, body in (('/crt/confirm', {'record_collection': 'crt_policy', 'record_id': pol['id'], 'fields': ['line'],
                                         'source_document_id': w['docs']['cert']['id']}),
                       ('/contact', {'data': {'name': 'Planted Inc', 'type': 'commercial'}})):
        r = h.c.post(B + path, json=body, base_url=STAFF_ORIGIN,
                     headers={'X-Biz-Sandbox': SBX, 'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin'})
        assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_required', (path, r.get_json())
        r = h.c.post(B + path, data={'x': '1'}, base_url=STAFF_ORIGIN,
                     headers={'X-Biz-Sandbox': SBX, 'Origin': CANVAS_ORIGIN, 'Sec-Fetch-Site': 'same-site'})
        assert r.status_code == 403, (path, r.status_code)


def test_r1_the_staff_origin_serves_only_the_staff_surface(h):
    h.signin(STAFF_1)
    for path in ('/', '/pages/x.html', f'/pages/sandbox/{SBX}/x.html', '/openclaw-ui/', '/openclaw-ui/..%2F..%2Fx',
                 '/openclaw-ui', '/uploads/a.png', '/sw.js', '/manifest.json', '/api/canvas/manifest',
                 '/api/upload', '/website-dev/', '/canvas-proxy/x', '/ws/clawdbot', '/admin', '/src/app.js',
                 '/jb/v1/shell.js', f'{B}/dev/pages', '/api/auth/check'):
        r = h.c.get(path, base_url=STAFF_ORIGIN, headers=NAV)
        assert r.status_code == 404 and r.get_json()['code'] == 'not_on_staff_origin', (path, r.status_code)
    assert h.c.get('/jb/v1/jb.js', base_url=STAFF_ORIGIN).status_code == 200
    # no CORS grant, ever, even to an origin the app otherwise allows
    r = h.c.open(f'{B}/crt/confirm', method='OPTIONS', base_url=STAFF_ORIGIN,
                 headers={'Origin': 'chrome-extension://abc', 'Access-Control-Request-Method': 'POST',
                          'Access-Control-Request-Headers': 'x-jb-staff-surface'})
    assert r.status_code == 403 and not [k for k in r.headers.keys() if k.lower().startswith('access-control-')]
    r = h.c.get(f'{B}/health', base_url=STAFF_ORIGIN, headers={'Origin': 'chrome-extension://abc', 'X-Biz-Sandbox': SBX})
    assert r.status_code == 200 and not [k for k in r.headers.keys() if k.lower().startswith('access-control-')]
    # an agent key is never accepted there
    r = h.c.get(f'{B}/health', base_url=STAFF_ORIGIN, headers={'X-Agent-Key': SBX_KEY})
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only'


def test_r1_the_staff_origin_never_takes_a_bearer_or_session_jwt(h):
    a = _agent_client(h)
    for hdr in ({'Authorization': 'Bearer tok'}, {'Authorization': 'Bearer staffjwt:' + STAFF_1}):
        r = a.get(f'{B}/health', base_url=STAFF_ORIGIN, headers=hdr)
        assert r.status_code == 401 and r.get_json()['code'] == 'staff_session_required'
    a.set_cookie('__session', 'staffjwt:' + STAFF_1, domain=STAFF_HOST)
    r = a.get(f'{B}/health', base_url=STAFF_ORIGIN)
    assert r.status_code == 401 and r.get_json()['code'] == 'staff_session_required'


def test_r1_staff_page_and_api_headers_are_pinned(h):
    h.signin(STAFF_1)
    r = h.c.get(f'{B}/crt/staff/cca-crt-intake.html?sandbox={SBX}', base_url=STAFF_ORIGIN, headers=NAV)
    assert r.status_code == 200
    csp = r.headers['Content-Security-Policy']
    script_src = csp.split('script-src')[1].split(';')[0]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    assert script_src.strip().startswith("'nonce-") and "'unsafe-inline'" not in script_src and 'https:' not in script_src
    assert "connect-src 'self'" in csp
    for k, v in (('X-Frame-Options', 'DENY'), ('Cross-Origin-Opener-Policy', 'same-origin'),
                 ('Cross-Origin-Resource-Policy', 'same-origin'), ('Referrer-Policy', 'no-referrer'),
                 ('X-Content-Type-Options', 'nosniff')):
        assert r.headers[k] == v, k
    assert 'no-store' in r.headers['Cache-Control']
    assert 'id="jb-surface-boot"' in r.get_data(as_text=True)                  # jb.js removes this node (M2)
    js = (ROOT / 'jb' / 'v1' / 'jb.js').read_text(encoding='utf-8')
    assert "getElementById('jb-surface-boot')" in js and 'removeChild(boot)' in js
    r = h.c.get(f'{B}/crt_policy', base_url=STAFF_ORIGIN, headers={'X-Biz-Sandbox': SBX})
    assert r.status_code == 200
    assert r.headers['Content-Security-Policy'].startswith("default-src 'none'")
    assert r.headers['X-Frame-Options'] == 'DENY' and r.headers['Cross-Origin-Opener-Policy'] == 'same-origin'
    # the tenant origin's canvas pages carry no COOP, so the two never share a browsing-context group anyway
    h.agent()
    h.c.put(f'{B}/dev/pages/plain.html', json={'html': '<p>x</p>', 'source': SRC}, headers=h.h)
    h.staff()
    r = h.c.get(f'/pages/sandbox/{SBX}/plain.html', headers={'Authorization': 'Bearer tok'})
    assert r.status_code == 200 and 'Cross-Origin-Opener-Policy' not in r.headers


def test_r1_an_expired_staff_session_goes_to_sign_in_never_raw_json(h, monkeypatch):
    import routes.biz as biz
    h.signin(STAFF_1)
    real = time.time
    monkeypatch.setattr(biz.time, 'time', lambda: real() + biz.STAFF_SURFACE_TTL_S + 60)
    r = h.c.get(f'{B}/crt/staff/cca-crt-party.html?sandbox={SBX}&party=p1', base_url=STAFF_ORIGIN, headers=NAV)
    assert r.status_code == 302, r.data[:200]
    loc = r.headers['Location']
    assert '/crt/staff/signin?next=' in loc and 'cca-crt-party.html' in loc
    r = h.c.get(f'{B}/crt_policy', base_url=STAFF_ORIGIN, headers={'X-Biz-Sandbox': SBX})
    assert r.status_code == 401 and r.get_json()['code'] == 'staff_session_required'
    # the tenant origin's old staff links send the person to the staff origin (not a JSON 401 either)
    monkeypatch.setattr(biz.time, 'time', real)
    r = h.c.get(f'{B}/crt/staff/cca-crt-book.html?sandbox={SBX}', headers={'Authorization': 'Bearer tok', **NAV})
    assert r.status_code == 302 and r.headers['Location'] == f'{STAFF_ORIGIN}{B}/crt/staff/cca-crt-book.html?sandbox={SBX}'


def test_r1_the_sign_in_page_holds_no_credential_and_loads_only_clerk(h):
    r = _agent_client(h).get(f'{B}/crt/staff/signin?next=https://evil.example/x', base_url=STAFF_ORIGIN)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'evil.example' not in body and '"next": "/api/biz/v1/crt/staff/cca-crt-book.html"' in body
    assert 'tok' not in re.findall(r'"token":\s*"([^"]*)"', body)
    csp = r.headers['Content-Security-Policy']
    assert "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp.split('script-src')[1].split(';')[0]
    assert r.headers['X-Frame-Options'] == 'DENY'


def test_r1_page_tokens_and_sessions_expire(h, monkeypatch):
    import routes.biz as biz
    w = world(h, confirm_all=False)
    tok = h.surface_token(STAFF_1)
    pol = w['pol']['gl']
    cur = h.ok(h.get(f'/crt_policy/{pol["id"]}'))
    body = {'record_collection': 'crt_policy', 'record_id': pol['id'], 'record_version': cur['version'],
            'fields': ['line'], 'source_document_id': w['docs']['cert']['id']}
    hdr = {'X-Biz-Sandbox': SBX, 'X-JB-Staff-Surface': tok, 'Origin': STAFF_ORIGIN, 'Sec-Fetch-Site': 'same-origin'}
    # a fresh session cookie (re-signed in later) with an EXPIRED page token: refused
    real = time.time
    monkeypatch.setattr(biz.time, 'time', lambda: real() + biz.STAFF_SURFACE_TTL_S + 5)
    with h.app.app_context():
        assert biz.staff_surface_token_ok(tok, 'clerk:' + STAFF_1, SBX) is False
    h.signed_in = None
    h.signin(STAFF_1)                                       # a new session minted "now" (8 h later)
    r = h.c.post(f'{B}/crt/confirm', json=body, headers=hdr, base_url=STAFF_ORIGIN)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_invalid', r.get_json()
    monkeypatch.setattr(biz.time, 'time', real)
    with h.app.app_context():
        sess = biz.mint_staff_session('clerk:' + STAFF_1)
        assert biz.staff_session_by(sess) == 'clerk:' + STAFF_1
        monkeypatch.setattr(biz.time, 'time', lambda: real() + biz.STAFF_SURFACE_TTL_S + 5)
        assert biz.staff_session_by(sess) is None


def test_r1_the_secret_is_one_per_tenant_persisted_and_shared_by_every_worker(h, dirs):
    import routes.biz as biz
    tok = h.surface_token(STAFF_1)
    key = Path(dirs['a']) / 'staff-surface.key'
    assert key.is_file() and len(key.read_bytes()) == 32
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
    with h.app.app_context():
        biz._secrets.clear()                                # a second worker / a restart: nothing in memory
        assert biz.staff_surface_token_ok(tok, 'clerk:' + STAFF_1, SBX) is True
    sess = h.c.get_cookie('__Host-jb_staff', domain=STAFF_HOST)
    biz._secrets.clear()
    with h.app.app_context():
        assert biz.staff_session_by(sess.value) == 'clerk:' + STAFF_1
    src = (ROOT / 'routes' / 'biz.py').read_text(encoding='utf-8')
    assert '_SURFACE_SECRET = secrets.token_bytes' not in src


# ───────────────────────── guard redesign (review H1/H3): crt only, provenance for the rest ─────────────
def test_guard_contact_document_company_stay_writable_from_any_staff_page(h):
    w = world(h)
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Origin': 'http://localhost',
            'Sec-Fetch-Site': 'same-origin'}
    h.staff()
    r = h.c.post(f'{B}/contact', json={'data': {'name': 'Walk-in Lead', 'type': 'commercial'}}, headers=page)
    assert r.status_code == 201
    c = r.get_json()
    r = h.c.patch(f'{B}/contact/{c["id"]}', json={'data': {'notes': 'called back'}},
                  headers={**page, 'If-Match': str(c['version'])})
    assert r.status_code == 200
    assert h.c.post(f'{B}/contact/{c["id"]}/archive', json={}, headers=page).status_code == 200
    co = h.c.get(f'{B}/company', headers=page).get_json()['records'][0]
    r = h.c.patch(f'{B}/company/{co["id"]}', json={'data': {'tagline': 'Insurance done right'}},
                  headers={**page, 'If-Match': str(co['version'])})
    assert r.status_code in (200, 422), r.get_json()          # never staff_surface_required
    assert (r.get_json() or {}).get('code') != 'staff_surface_required'
    # the provenance is stamped server-side: off the staff surface, no staff_surface field list
    tl = h.c.get(f'{B}/contact/{c["id"]}/timeline', headers=page).get_json()['events']
    assert all('staff_surface' not in (e['diff'] or {}) for e in tl)
    tl = h.ok(h.get(f'/contact/{w["party"]["id"]}/timeline'))['events']
    assert tl[0]['diff']['staff_surface'] == sorted(tl[0]['diff']['set'])  # the world's staff-surface create


def test_guard_a_client_cannot_claim_the_staff_surface_in_the_body(h):
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Origin': 'http://localhost'}
    h.staff()
    r = h.c.post(f'{B}/contact', json={'data': {'name': 'X Inc', 'type': 'commercial'}, 'surface': True,
                                       'staff_surface': ['name']}, headers=page)
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        tl = h.c.get(f'{B}/contact/{r.get_json()["id"]}/timeline', headers=page).get_json()['events']
        assert 'staff_surface' not in (tl[0]['diff'] or {})


@pytest.mark.parametrize('who,field,bad,good,code', [
    ('party', 'name', 'Sparky Electric, Inc.', 'Sparky Electric Inc', 'UNCONFIRMED:contact.name'),
    ('account', 'company_name', 'Acme Builders, L.L.C.', 'Acme Builders LLC', 'UNCONFIRMED:contact.company_name'),
])
def test_guard_an_off_surface_person_write_to_a_name_the_verdict_reads_is_cant_tell(h, who, field, bad, good, code):
    """The same legal name (it would read Meets if believed), written by a canvas page: Can't tell."""
    w = world(h)
    assert h.state(w['asg']['id'])['state'] == 'meets'
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Origin': 'http://localhost'}
    h.staff()
    rec = h.c.get(f'{B}/contact/{w[who]["id"]}', headers=page).get_json()
    r = h.c.patch(f'{B}/contact/{rec["id"]}', json={'data': {field: bad}},
                  headers={**page, 'If-Match': str(rec['version'])})
    assert r.status_code == 200
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and code in codes(ev), ev
    # the same edit made on the staff surface is a person's again
    rec = h.ok(h.get(f'/contact/{rec["id"]}'))
    h.ok(h.patch(f'/contact/{rec["id"]}', {'data': {field: good}}, rec['version']))
    assert h.state(w['asg']['id'])['state'] == 'meets'


@pytest.mark.parametrize('surface', [False, True])
def test_guard_an_off_surface_write_to_a_document_identity_is_cant_tell(h, surface):
    """Every identity field of the endorsement's paper re-written (to the same final values): by a canvas page it
    is nobody's word (Can't tell); the same writes on the staff surface are a person's (Meets, control)."""
    w = world(h)
    assert h.state(w['asg']['id'])['state'] == 'meets'
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Origin': 'http://localhost'}
    h.staff()
    d = h.ok(h.get(f'/document/{w["docs"]["e_ongoing"]["id"]}'))
    orig = {k: d['data'][k] for k in ('kind', 'crt_account_id', 'contact_id', 'file_url')}
    other = {'kind': 'other', 'crt_account_id': orig['contact_id'], 'contact_id': orig['crt_account_id'],
             'file_url': w['files']['e_other']['url']}
    for i, data in enumerate((other, orig)):
        if surface:
            h.ok(h.patch(f'/document/{d["id"]}', {'data': data}, d['version'] + i))
        else:
            r = h.c.patch(f'{B}/document/{d["id"]}', json={'data': data},
                          headers={**page, 'If-Match': str(d['version'] + i)})
            assert r.status_code == 200, r.get_json()
    ev = h.state(w['asg']['id'])
    assert ev['state'] == ('meets' if surface else 'cant_tell'), ev


def test_guard_the_agency_name_counts_only_when_written_on_the_staff_surface(h):
    """company (review H3 mutant 'company dropped'): the agency's own policies need two people; an off-surface
    rename of the agency must not quietly turn that rule off."""
    co_name = 'Contractors Choice Agency Sandbox'
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'producer_name': co_name,
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False,
                     'limits': {'combined_single_limit': M}}}
    w = world(h, policies=pols)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:crt_policy.line' in codes(ev)    # one person only: not enough
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Origin': 'http://localhost'}
    h.staff()
    co = h.c.get(f'{B}/company', headers=page).get_json()['records'][0]
    r = h.c.patch(f'{B}/company/{co["id"]}', json={'data': {'name': 'Some Other Agency'}},
                  headers={**page, 'If-Match': str(co['version'])})
    assert r.status_code == 200
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and 'UNCONFIRMED:company.name' in codes(ev), ev


def test_guard_fails_closed_when_the_pack_state_cannot_be_read(h, monkeypatch):
    from services.biz.store import BizStore
    w = world(h, confirm_all=False)

    def boom(self, name):
        raise RuntimeError('registry unreadable')
    monkeypatch.setattr(BizStore, 'pack_enabled', boom)
    h.staff()
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX}
    r = h.c.post(f'{B}/crt_account_party', json={'data': {'crt_account_id': w['account']['id'],
                                                           'party_contact_id': w['party']['id'], 'status': 'active'}},
                 headers=page)
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_surface_required', r.get_json()


def test_guard_reserved_platform_page_names_on_the_main_canvas_routes(h):
    import routes.canvas as canvas
    h.staff()
    names = sorted(p.name for p in (ROOT / 'default-schemas' / 'packs' / 'risk-transfer' / 'pages').glob('*.html'))
    assert names
    for n in names:
        r = h.c.post('/api/canvas/pages', json={'filename': n, 'html': '<p>look-alike</p>', 'title': 'x'},
                     headers={'Authorization': 'Bearer tok'})
        assert r.status_code == 409 and r.get_json()['code'] == 'platform_page_reserved', (n, r.status_code)
        assert not (Path(canvas.CANVAS_PAGES_DIR) / n).exists()
    # a file the agent dropped there directly is never served as a canvas page
    (Path(canvas.CANVAS_PAGES_DIR) / names[0]).write_text('<p>look-alike</p>', encoding='utf-8')
    r = h.c.get(f'/pages/{names[0]}', headers={'Authorization': 'Bearer tok'})
    assert r.status_code == 404 and b'look-alike' not in r.data
    assert canvas._platform_page_reserved('my-crt-view.html') is False       # control: any other name is free
    assert canvas._platform_page_reserved('CCA-CRT-INTAKE.HTML') is True


# ───────────────────────── R3: manuscript and CG 20 10 ─────────────────────────
def _completed(**kw):
    e = _ends()
    e['ai_completed'] = {**e['ai_completed'], **kw}
    return e


def _staff_confirm_wording(h, w):
    h.staff()
    e = w['end']['ai_completed']
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, cur['version']))
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])


@pytest.mark.parametrize('form', ['CG 20 10', 'cg2010', 'CG 20 33', 'CG 20 38', 'CG 20 01'])
def test_r3_a_listed_iso_form_is_never_read_as_manuscript(h, form):
    w = world(h, endorsements=_completed(form=form, manuscript=True), party_name=f'Ms {form} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev


def test_r3_a_manuscript_cg2037_reads_as_cg2037(h):
    w = world(h, endorsements=_completed(form='CG 20 37', manuscript=True))
    assert h.state(w['asg']['id'])['state'] == 'meets'


def test_r3_cg2010_never_satisfies_completed_ops_by_any_route(h):
    # (1) even with the manuscript flag AND the completed-ops wording confirmed by staff
    w = world(h, endorsements=_completed(form='CG 20 10', manuscript=True))
    _staff_confirm_wording(h, w)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'missing' and 'MISSING:ai_completed' in codes(ev), ev
    # (2) a requirement set can neither list it for completed ops nor map it there
    h.staff()
    s = w['set']
    items = [dict(it, forms=['CG 20 37', 'CG 20 10']) if it['id'] == 'ai_completed' else it for it in s['data']['items']]
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items})
    assert r.status_code == 422 and r.get_json()['code'] == 'cg2010_never_completed_ops'
    ff = dict(s['data']['form_functions'], **{'CG 20 10': 'ai_completed'})
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'form_functions': ff})
    assert r.status_code == 422 and r.get_json()['code'] == 'cg2010_never_completed_ops'
    # (3) MISSING is never waivable
    from services.biz.packs import risk_transfer as rt
    assert not 'MISSING:ai_completed'.startswith(rt.WAIVABLE_PREFIXES)


def test_r3_a_manuscript_counts_for_completed_ops_only_with_staff_confirmed_wording(h):
    w = world(h, endorsements=_completed(form='ACME-COMP-OPS 7', manuscript=True))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_endorsement.completed_ops_wording'], ev
    # the agent cannot set it
    h.agent()
    e = h.ok(h.get(f'/crt_endorsement/{w["end"]["ai_completed"]["id"]}'))
    r = h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, e['version'])
    assert r.status_code == 403 and r.get_json()['code'] == 'staff_only_field'
    # staff set it, and confirmed every other field, but nobody confirmed the wording: still Can't tell
    h.staff()
    h.ok(h.patch(f'/crt_endorsement/{e["id"]}', {'data': {'completed_ops_wording': True}}, e['version']))
    cur = h.ok(h.get(f'/crt_endorsement/{e["id"]}'))
    confirm(h, 'crt_endorsement', cur, END_FIELDS, cur['data']['document_id'])
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell' and codes(ev) == ['UNCONFIRMED:crt_endorsement.completed_ops_wording'], ev
    confirm(h, 'crt_endorsement', cur, END_FIELDS + ['completed_ops_wording'], cur['data']['document_id'])
    assert h.state(w['asg']['id'])['state'] == 'meets'
    # the ongoing-ops item is not touched by this rule (a manuscript ongoing form rests on staff, as before)
    w2 = world(h, endorsements={**_ends(), 'ai_ongoing': {**_ends()['ai_ongoing'], 'form': 'ACME-ONG 2',
                                                          'manuscript': True}}, party_name='Ongoing Sub Inc')
    assert h.state(w2['asg']['id'])['state'] == 'meets'


def test_watch_r3_fails_a_meets_on_cg2010_or_an_unworded_manuscript(h, dirs):
    w1 = world(h, endorsements=_completed(form='CG 20 10', manuscript=True), party_name='Ms One Inc')
    _forge_meets(h, w1)
    w2 = world(h, endorsements=_completed(form='ACME-COMP-OPS 7', manuscript=True), party_name='Ms Two Inc')
    _forge_meets(h, w2)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and [r['watch'] for r in rows] == ['FAIL', 'FAIL'], rows
    assert all(any(x.startswith('R4') for x in r['reasons']) for r in rows), rows


def test_watch_r3_fails_a_meets_on_cg2010_even_worded_as_manuscript(h, dirs):
    """The CG 20 10 rule alone (mutant watch-r3-cg2010): manuscript AND its completed-ops wording confirmed."""
    w = world(h, endorsements=_completed(form='CG 20 10', manuscript=True), party_name='Ms Worded Inc')
    _staff_confirm_wording(h, w)
    assert 'MISSING:ai_completed' in codes(h.state(w['asg']['id']))
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in rows[0]['reasons']), rows


def test_watch_fails_a_meets_on_an_unlisted_form(h, dirs):
    """The listed-form check alone (mutant watch-med-form-off): a carrier form is no form of the completed-ops item
    (rules 1.6.0: CG 20 26 now reads MISSING by ruling G, so the unlisted non-ISO form carries this check)."""
    w = world(h, endorsements=_completed(form='ACME 12 34'), party_name='Unlisted Form Inc')
    assert h.state(w['asg']['id'])['state'] == 'cant_tell'
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(r.startswith('R4') and 'ai_completed' in r for r in rows[0]['reasons']), rows


def test_watch_r3_passes_a_worded_manuscript_control(h, dirs):
    w = world(h, endorsements=_completed(form='ACME-COMP-OPS 7', manuscript=True))
    _staff_confirm_wording(h, w)
    rv = h.ok(h.post(f'/crt/assignment/{w["asg"]["id"]}/review'), 201)
    assert rv['review']['data']['state'] == 'meets'
    v, rows, _ = _watch(dirs)
    assert v == 'PASS', rows


def test_watch_r1_fails_a_meets_on_an_off_surface_person_write(h, dirs):
    w = world(h)
    page = {'Authorization': 'Bearer tok', 'X-Biz-Sandbox': SBX, 'Origin': 'http://localhost'}
    h.staff()
    rec = h.c.get(f'{B}/contact/{w["party"]["id"]}', headers=page).get_json()
    assert h.c.patch(f'{B}/contact/{rec["id"]}', json={'data': {'name': 'Sparky Electric Inc.'}},
                     headers={**page, 'If-Match': str(rec['version'])}).status_code == 200
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any('R5 sub name' in x for x in rows[0]['reasons']), rows


# ───────────────────────── R4: CG 20 38 and the set's own equivalences ─────────────────────────
def test_r4_cg2038_stays_ongoing_and_equivalences_are_part_of_the_approved_set(h):
    w = world(h, endorsements=_ends(form='CG 20 38', blanket=True, scheduled_names=[], scheduled_projects=[]))
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'meets' and ev['items']['ai_ongoing'] == 'OK'          # the conservative reading
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    assert s['data']['form_functions']['CG 20 38'] == 'ai_ongoing'
    assert s['data']['form_functions']['CG 20 10'] == 'ai_ongoing'
    confs = h.ok(h.get('/crt_confirmation', query_string={'where': '{"record_id": "%s"}' % s['id']}))['records']
    assert any('form_functions' in c['data']['fields'] for c in confs)          # approved WITH the set
    # Rules 1.5.0 (host ruling F, safety floor): a built-in ISO number keeps its ISO category in EVERY set, so CG 20 38
    # can no longer be re-mapped to completed operations by a set version (the R4 per-set re-map is superseded).
    h.staff()
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise',
               {'form_functions': dict(s['data']['form_functions'], **{'CG 20 38': 'ai_completed'})})
    assert r.status_code == 422 and r.get_json()['code'] == 'iso_form_floor', r.get_json()
    items = []
    for it in s['data']['items']:
        if it['id'] == 'ai_completed':
            it = dict(it, forms=['CG 20 37', 'CG 20 38'])
        items.append(it)
    r = h.post(f'/crt/requirement-sets/{s["id"]}/revise', {'items': items})
    assert r.status_code == 422 and r.get_json()['code'] == 'safety_floor', r.get_json()
    w2 = world(h, endorsements=_completed(form='CG 20 38', blanket=True, scheduled_names=[], scheduled_projects=[]),
               party_name='Remap Sub Inc')
    assert 'MISSING:ai_completed' in codes(h.state(w2['asg']['id']))            # ongoing only, in every set


def test_r4_an_unapproved_change_to_the_equivalences_is_cant_tell(h):
    w = world(h)
    h.staff()
    s = h.ok(h.get(f'/crt_requirement_set/{w["set"]["id"]}'))
    from services.biz.packs import risk_transfer as rt
    store = h.app.extensions['biz']['sandbox:josh']
    # rules 1.5.0 (ruling F): the agent no longer edits a set at all, and CG 20 33 -> pnc is below the floor; the
    # unapproved change is a staff edit of a non-ISO equivalence, made around the approval route
    import pytest as _pt
    with _pt.raises(rt.SchemaError) as ex:
        store.update('crt_requirement_set', s['id'], {'form_functions': dict(s['data']['form_functions'], **{'ACME 99': 'pnc'})},
                     expected_version=s['version'], by='agent:test-dev', agent=True, source=SRC, via_pack=True)
    assert ex.value.status == 403
    store.update('crt_requirement_set', s['id'], {'form_functions': dict(s['data']['form_functions'], **{'ACME 99': 'pnc'})},
                 expected_version=s['version'], by='clerk:' + STAFF_1, agent=False, via_pack=True, surface=True)
    assert rt.RULE_VERSION == 'crt-rules-1.7.0'          # rules 1.7.0 (re-check #7) keeps every 1.4.0 rule
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'cant_tell', ev


def test_r4_the_plan_carries_the_gate5_spec_note():
    tpl = (ROOT / 'default-schemas' / 'packs' / 'risk-transfer' / 'templates.json').read_text(encoding='utf-8')
    assert 'gate-5 pilot' in tpl and 'CG 20 38' in tpl


# ───────────────────────── LOW: edition, heavy civil ─────────────────────────
@pytest.mark.parametrize('ed,state,code', [('07 04', 'variance', 'WRONG_EDITION:ai_ongoing'),
                                           ('04 2013', 'meets', None), ('(04/13)', 'meets', None),
                                           ('12 19', 'variance', 'WRONG_EDITION:ai_ongoing')])
def test_low_the_item_edition_is_compared(h, ed, state, code):
    w = world(h, endorsements=_ends(edition=ed), party_name=f'Ed {ed.replace("/", " ")} Inc')
    ev = h.state(w['asg']['id'])
    assert ev['state'] == state and codes(ev) == ([code] if code else []), ev


def test_watch_low_fails_a_meets_on_another_edition(h, dirs):
    w = world(h, endorsements=_ends(edition='07 04'))
    _forge_meets(h, w)
    v, rows, _ = _watch(dirs)
    assert v == 'FAIL' and any(x.startswith('R4') for x in rows[0]['reasons']), rows


def test_low_heavy_civil_is_an_xcu_trade(h):
    pols = {'gl': {'line': 'gl', 'policy_number': 'GL-1', 'exclusions': ['xcu'],
                   'limits': {'each_occurrence': 2 * M, 'general_aggregate': 2 * M}},
            'umbrella': {'line': 'umbrella', 'policy_number': 'UM-1', 'limits': {'each_occurrence': 5 * M}},
            'auto': {'line': 'auto', 'policy_number': 'AU-1', 'addl_insd': False, 'limits': {'combined_single_limit': M}}}
    w = world(h, template='high_severity', trade='heavy_civil', policies=pols)
    ev = h.state(w['asg']['id'])
    assert ev['state'] == 'variance' and 'EXCLUSION:xcu' in codes(ev), ev
