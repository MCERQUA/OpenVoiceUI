#!/usr/bin/env python3
"""isolation-probe.py — the biz backend's jail-cell watcher (design section 8; CCA plan P0).

Proves, from OUTSIDE the store, through the public API only, that two business stores never
cross: what one store holds (records, pack records, private files) cannot be read, listed or
fetched with the other store's credentials; a key bound to one store cannot open another;
anonymous callers get 401; and an agent key cannot reach any staff-only action.

Verdict: PASS (exit 0) / FAIL (exit 1) / CANNOT-TELL (exit 3). A check that could not be made
(network error, 5xx, a target that is not a dev sandbox) is CANNOT-TELL, never PASS.

It WRITES test records (contacts, a link, a policy, one small PDF) into each target store, so
against a live server it runs ONLY on dev sandboxes: a target whose /health does not report a
sandbox is refused (CANNOT-TELL), and nothing is written to it. It never prints a key.

  Live (dev server sandboxes; keys from env, never argv):
    BIZ_PROBE_KEY_JOSH=... BIZ_PROBE_KEY_FOAM=... \\
      tools/biz/isolation-probe.py --url https://test-dev.jam-bot.com --target josh --target foam
  Controls (in-process, a throwaway app + temp dirs; run from the OVU repo root or the image):
    tools/biz/isolation-probe.py --self-test positive     # expect PASS
    tools/biz/isolation-probe.py --self-test negative     # a foreign row planted -> expect FAIL
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid

API = '/api/biz/v1'
PASS, FAIL, CANNOT = 'PASS', 'FAIL', 'CANNOT-TELL'
EXIT = {PASS: 0, FAIL: 1, CANNOT: 3}
SRC = {'channel': 'web', 'ref': 'isolation-probe', 'confidence': 1.0, 'words': 'isolation probe test record'}


# ── transports ──────────────────────────────────────────────────────────────
class HttpTransport:
    """The real thing: HTTPS to a running server."""

    def __init__(self, base_url, timeout=20):
        self.base = base_url.rstrip('/')
        self.timeout = timeout

    def call(self, method, path, headers=None, body=None, file=None):
        import urllib.error
        import urllib.request
        headers = dict(headers or {})
        data = None
        if file is not None:
            boundary = 'probe' + secrets.token_hex(8)
            name, blob = file
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="source"\r\n\r\n{json.dumps(SRC)}\r\n'.encode(),
                     f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                     'Content-Type: application/octet-stream\r\n\r\n'.encode() + blob + b'\r\n',
                     f'--{boundary}--\r\n'.encode()]
            data = b''.join(parts)
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        elif body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, _json(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _json(e.read())
        except Exception as e:                      # network: the probe cannot tell
            return None, {'error': type(e).__name__}


class FlaskTransport:
    """In-process: a Flask test client (self-test controls only)."""

    def __init__(self, client):
        self.c = client

    def call(self, method, path, headers=None, body=None, file=None):
        import io
        kw = {'headers': headers or {}}
        if file is not None:
            kw['data'] = {'file': (io.BytesIO(file[1]), file[0]), 'source': json.dumps(SRC)}
            kw['content_type'] = 'multipart/form-data'
        elif body is not None:
            kw['json'] = body
        r = self.c.open(path, method=method, **kw)
        return r.status_code, _json(r.data)


def _json(raw):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return {'_bytes': len(raw)}


# ── the probe ───────────────────────────────────────────────────────────────
class Probe:
    def __init__(self, transport, targets):
        """targets: [{'name': sandbox name, 'key': agent key}, ...] (at least two)."""
        self.t = transport
        self.targets = targets
        self.checks = []

    def _h(self, tgt, **extra):
        return {'X-Agent-Key': tgt['key'], **extra}

    def record(self, name, verdict, detail):
        self.checks.append({'check': name, 'verdict': verdict, 'detail': detail})

    def expect(self, name, got, allowed):
        if got is None or got >= 500:
            self.record(name, CANNOT, f'status {got}')
        elif got in allowed:
            self.record(name, PASS, f'status {got}')
        else:
            self.record(name, FAIL, f'status {got}, expected one of {sorted(allowed)}')

    def plant(self, tgt):
        """Write a small, recognisable world into one target store. Returns ids, or None (CANNOT-TELL)."""
        h = self._h(tgt)
        st, body = self.t.call('GET', API + '/health', h)
        if st != 200 or not isinstance(body, dict):
            self.record(f'{tgt["name"]}: health', CANNOT, f'status {st}')
            return None
        if not body.get('sandbox'):
            self.record(f'{tgt["name"]}: is a dev sandbox', CANNOT,
                        'target is not a dev sandbox; the probe never writes to a business store')
            return None
        tgt['tenant'] = body.get('tenant')
        nonce = f'{tgt["name"]}-{uuid.uuid4().hex[:10]}'
        ids = {'nonce': nonce}
        steps = [('enable', 'POST', API + '/packs/risk-transfer/enable', {'source': SRC})]
        for label, method, path, b in steps:
            st, _ = self.t.call(method, path, h, b)
            if st != 200:
                self.record(f'{tgt["name"]}: plant {label}', CANNOT, f'status {st}')
                return None

        def mk(coll, data):
            st, rec = self.t.call('POST', f'{API}/{coll}', h, {'data': data, 'source': SRC})
            if st != 201:
                self.record(f'{tgt["name"]}: plant {coll}', CANNOT,
                            f'status {st} {(rec or {}).get("code", "") if isinstance(rec, dict) else ""}'.strip())
                return None
            return rec['id']
        ids['account'] = mk('contact', {'name': f'Probe Account {nonce}', 'type': 'commercial'})
        ids['party'] = mk('contact', {'name': f'Probe Sub {nonce}', 'type': 'subcontractor'})
        if not ids['account'] or not ids['party']:
            return None
        ids['link'] = mk('crt_account_party', {'crt_account_id': ids['account'], 'party_contact_id': ids['party'],
                                               'status': 'active'})
        st, f = self.t.call('POST', API + '/files', h, file=('probe.pdf', b'%PDF-1.4\n% isolation probe ' +
                                                              nonce.encode() + b'\n%%EOF\n'))
        if st not in (200, 201):
            self.record(f'{tgt["name"]}: plant file', CANNOT, f'status {st}')
            return None
        ids['file'] = f['id']
        ids['document'] = mk('document', {'title': 'probe', 'file_url': f['url'], 'kind': 'coi_acord25',
                                          'crt_account_id': ids['account'], 'contact_id': ids['party']})
        ids['policy'] = mk('crt_policy', {'crt_account_id': ids['account'], 'party_contact_id': ids['party'],
                                          'line': 'gl', 'policy_number': 'PROBE-' + nonce,
                                          'source_document_id': ids['document']})
        st, s = self.t.call('POST', API + '/crt/requirement-sets/from-template', h, {'template_key': 'finish',
                                                                                    'source': SRC})
        ids['set'] = (s or {}).get('record', {}).get('id') if st == 201 else None
        ids['set_version'] = (s or {}).get('record', {}).get('version') if st == 201 else None
        if not all(ids.get(k) for k in ('link', 'document', 'policy', 'set')):
            return None
        return ids

    def cross(self, owner, other, ids):
        """other's credentials must never see owner's things."""
        h = self._h(other)
        tag = f'{other["name"]} key -> {owner["name"]} data'
        for coll, k in (('contact', 'account'), ('contact', 'party'), ('crt_account_party', 'link'),
                        ('document', 'document'), ('crt_policy', 'policy'), ('crt_requirement_set', 'set')):
            st, _ = self.t.call('GET', f'{API}/{coll}/{ids[k]}', h)
            self.expect(f'{tag}: read {coll}', st, {404})
        for coll, k in (('crt_policy', 'policy'), ('document', 'document'), ('contact', 'account')):
            st, body = self.t.call('GET', f'{API}/{coll}?limit=500', h)
            if st != 200 or not isinstance(body, dict):
                self.record(f'{tag}: list {coll}', CANNOT, f'status {st}')
                continue
            leaked = [r['id'] for r in body.get('records', []) if r.get('id') == ids[k]
                      or ids['nonce'] in json.dumps(r.get('data', {}))]
            self.record(f'{tag}: list {coll}', FAIL if leaked else PASS, f'{len(leaked)} foreign rows')
        st, _ = self.t.call('GET', f'{API}/files/{ids["file"]}/meta', h)
        self.expect(f'{tag}: file meta', st, {404})
        st, _ = self.t.call('GET', f'{API}/files/{ids["file"]}', h)
        self.expect(f'{tag}: file bytes', st, {403, 404})
        # a key bound to one store cannot ask for another
        st, _ = self.t.call('GET', f'{API}/crt_policy/{ids["policy"]}', {**h, 'X-Biz-Sandbox': owner['name']})
        self.expect(f'{tag}: key naming the other sandbox', st, {403})
        st, _ = self.t.call('GET', f'{API}/crt_policy/{ids["policy"]}?tenant={owner.get("tenant", "x")}', h)
        self.expect(f'{tag}: tenant parameter', st, {400})

    def anonymous(self, owner, ids):
        for path in (f'/files/{ids["file"]}', f'/files/{ids["file"]}/meta', '/crt_policy', f'/crt_policy/{ids["policy"]}',
                     '/crt/book'):
            st, _ = self.t.call('GET', API + path, {'X-Biz-Sandbox': owner['name']})
            self.expect(f'anonymous -> {owner["name"]} {path.split("/")[1]}', st, {401})

    def staff_only(self, owner, ids):
        h = self._h(owner)
        rid = str(uuid.uuid4())
        tries = [
            ('crt/confirm', 'POST', '/crt/confirm', {'record_collection': 'crt_policy', 'record_id': ids['policy'],
                                                    'fields': ['line'], 'source_document_id': ids['document']}),
            ('crt_confirmation create', 'POST', '/crt_confirmation',
             {'data': {'record_collection': 'crt_policy', 'record_id': ids['policy'], 'record_version': 1,
                       'fields': ['line']}}),
            ('crt_waiver create', 'POST', '/crt_waiver',
             {'data': {'crt_assignment_id': rid, 'variance_code': 'X', 'accepted_by_contractor': 'x',
                       'acceptance_document_id': ids['document'], 'expires': '2099-01-01'}}),
            ('crt_review create', 'POST', '/crt_review', {'data': {'crt_assignment_id': rid, 'state': 'meets'}}),
            ('approve a set', 'POST', f'/crt/requirement-sets/{ids["set"]}/approve', {}),
            ('record a review', 'POST', f'/crt/assignment/{rid}/review', {}),
            ('mark a chase sent', 'POST', f'/crt/assignment/{rid}/chase-sent', {'stage': 'T-30'}),
            ('core confirm', 'POST', f'/crt_policy/{ids["policy"]}/confirm', {'fields': ['line']}),
            ('export', 'GET', '/export', None),
        ]
        for name, method, path, body in tries:
            if body is not None:
                body = dict(body, source=SRC)
            st, _ = self.t.call(method, API + path, h, body)
            self.expect(f'{owner["name"]} agent key: {name}', st, {403})
        st, _ = self.t.call('PATCH', f'{API}/crt_requirement_set/{ids["set"]}', {**h, 'If-Match': str(ids['set_version'])},
                            {'data': {'status': 'APPROVED'}, 'source': SRC})
        self.expect(f'{owner["name"]} agent key: set status APPROVED', st, {403})

    def run(self, before_checks=None):
        if len(self.targets) < 2:
            self.record('targets', CANNOT, 'need at least two stores to prove they do not cross')
            return self.verdict()
        planted = {}
        for tgt in self.targets:
            ids = self.plant(tgt)
            if ids is None:
                return self.verdict()
            planted[tgt['name']] = ids
        tenants = [t.get('tenant') for t in self.targets]
        if len(set(tenants)) != len(tenants):
            self.record('targets are distinct stores', FAIL, 'two targets report the same tenant id')
        if before_checks:
            before_checks(planted)
        for owner in self.targets:
            for other in self.targets:
                if other is not owner:
                    self.cross(owner, other, planted[owner['name']])
            self.anonymous(owner, planted[owner['name']])
            self.staff_only(owner, planted[owner['name']])
        return self.verdict()

    def verdict(self):
        vs = [c['verdict'] for c in self.checks]
        if FAIL in vs:
            return FAIL
        if not vs or CANNOT in vs:
            return CANNOT
        return PASS


# ── self-test (controls) ────────────────────────────────────────────────────
def self_test(control):
    """Build a throwaway app (one dev container, sandboxes 'josh' and 'foam') and probe it.

    positive: expect PASS. negative: copy one of josh's policy rows into foam's database (the
    planted foreign-tenant row of design section 8) before the checks; expect FAIL.
    """
    import tempfile
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    tmp = tempfile.mkdtemp(prefix='biz-isolation-probe-')
    keys = {'josh': 'probe-josh-' + secrets.token_hex(20), 'foam': 'probe-foam-' + secrets.token_hex(20)}
    os.environ.update({'CLERK_PUBLISHABLE_KEY': 'pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk',
                       'AGENT_API_KEY': 'probe-main-' + secrets.token_hex(20), 'JAMBOT_TENANT': 'probe-dev',
                       'BIZ_DB_DIR': os.path.join(tmp, 'db'), 'BIZ_SANDBOXES': 'josh,foam',
                       'BIZ_SANDBOX_KEY_JOSH': keys['josh'], 'BIZ_SANDBOX_KEY_FOAM': keys['foam']})
    os.environ.pop('OVUI_DB_PATH', None)
    os.environ.pop('CANVAS_REQUIRE_AUTH', None)
    from app import create_app
    from routes.biz import biz_bp
    app, _ = create_app(config_override={'TESTING': True, 'RATELIMIT_ENABLED': False})
    if biz_bp.name not in app.blueprints:
        app.register_blueprint(biz_bp)
    probe = Probe(FlaskTransport(app.test_client()), [{'name': n, 'key': k} for n, k in keys.items()])

    def plant_foreign_row(planted):
        import sqlite3
        src = sqlite3.connect(os.path.join(tmp, 'db', 'sandboxes', 'josh', 'business.db'))
        row = src.execute('SELECT id, collection, data, created_at, created_by, updated_at, updated_by '
                          'FROM records WHERE id = ?', (planted['josh']['policy'],)).fetchone()
        src.close()
        dst = sqlite3.connect(os.path.join(tmp, 'db', 'sandboxes', 'foam', 'business.db'))
        tenant = dst.execute("SELECT value FROM meta WHERE key = 'tenant'").fetchone()[0]
        dst.execute('INSERT INTO records(id, tenant, collection, data, version, created_at, created_by, updated_at, '
                    'updated_by) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)', (row[0], tenant, *row[1:]))
        dst.commit()
        dst.close()

    try:
        return probe, probe.run(before_checks=plant_foreign_row if control == 'negative' else None)
    finally:
        for store in list(app.extensions.get('biz', {}).values()):
            if hasattr(store, 'close'):
                store.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--url', help='server base URL (dev server with BIZ_SANDBOXES)')
    ap.add_argument('--target', action='append', default=[],
                    help='sandbox name; its key is read from env BIZ_PROBE_KEY_<NAME>')
    ap.add_argument('--self-test', choices=('positive', 'negative'))
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.self_test:
        probe, verdict = self_test(a.self_test)
    else:
        if not a.url or len(a.target) < 2:
            ap.error('give --url and at least two --target sandboxes (or --self-test)')
        targets = []
        for name in a.target:
            key = os.environ.get('BIZ_PROBE_KEY_' + name.upper().replace('-', '_'), '').strip()
            if not key:
                print(f'CANNOT-TELL: no key in env for target {name}')
                return EXIT[CANNOT]
            targets.append({'name': name, 'key': key})
        probe = Probe(HttpTransport(a.url), targets)
        verdict = probe.run()
    if a.json:
        print(json.dumps({'verdict': verdict, 'checks': probe.checks}, indent=1))
    else:
        for c in probe.checks:
            if c['verdict'] != PASS:
                print(f"{c['verdict']:12s} {c['check']}: {c['detail']}")
        n = {v: sum(1 for c in probe.checks if c['verdict'] == v) for v in (PASS, FAIL, CANNOT)}
        print(f"isolation-probe: {verdict}  (checks pass={n[PASS]} fail={n[FAIL]} cannot-tell={n[CANNOT]})")
    return EXIT[verdict]


if __name__ == '__main__':
    sys.exit(main())
