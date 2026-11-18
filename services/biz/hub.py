"""services/biz/hub.py — the JamBot Hub summary: GET /api/biz/v1/hub/summary.

One call gives the Hub (JamDash) a tile per section of the business: counts, the last time
anything happened there, and a verdict the tile can colour without doing arithmetic:

    verdict  ok | warn | bad | unknown        reason  one plain sentence for the owner
    state    live | empty | unreachable | not_built | sandbox

Two kinds of section:
  * local: read from this tenant's own business.db (the jail cell): customers, jobs, money,
    team, inventory. Counts are computed by the server over every record (never a page of rows).
  * read-through: leads and SEO live in the platform service (:6350, the same one JamDash's
    fixture keys point at). The container calls it on its own bridge gateway; that service binds
    the caller to its tenant by source address and refuses any other tenant (403). We send the
    store's own tenant, never anything from the request. If it cannot be reached, the section is
    verdict unknown with a plain reason: never a guessed or cached-forever number.

Nothing here returns a row: counts, sums of money, timestamps and verdicts only. The tenant
is the store's (meta.tenant, verified against JAMBOT_TENANT when the store opened). A dev
sandbox holds fake data, so its read-through sections say so instead of showing the dev
tenant's real leads.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HUB_VERSION = 1
SECTIONS = ('customers', 'jobs', 'money', 'team', 'inventory', 'leads', 'seo', 'site')
LABELS = {
    'customers': 'Customers', 'jobs': 'Jobs', 'money': 'Estimates, invoices and payments',
    'team': 'Team', 'inventory': 'Inventory', 'leads': 'Leads', 'seo': 'Get found (SEO)',
    'site': 'Website',
}
_RANK = {'unknown': 0, 'ok': 1, 'warn': 2, 'bad': 3}

JOB_OPEN = ('lead', 'quoted', 'scheduled', 'in_progress', 'on_hold')
JOB_DONE = ('completed', 'invoiced', 'paid')
EST_OPEN = ('issued', 'sent')
INV_UNPAID = ('sent', 'partial', 'overdue')
SEO_STALE_DAYS = 45
OVERDUE_BAD_DAYS = 30

PLATFORM_PORT = 6350
PLATFORM_TIMEOUT_S = 3.0
PLATFORM_CACHE_S = 30.0
_cache_lock = threading.Lock()
_cache: dict = {}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _parse_ts(v):
    if not v or not isinstance(v, str):
        return None
    s = v.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(v[:10], '%Y-%m-%d')
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _newest(*vals):
    best = None
    for v in vals:
        dt = _parse_ts(v)
        if dt and (best is None or dt > best[0]):
            best = (dt, v)
    return _iso(best[0]) if best else None


def section(name, *, verdict, reason, state, source, counts=None, last_activity=None, **extra):
    out = {'label': LABELS[name], 'verdict': verdict, 'reason': reason, 'state': state, 'source': source,
           'counts': counts or {}, 'last_activity': last_activity}
    out.update(extra)
    return out


# ── local sections (business.db) ─────────────────────────────────────────────
def _q(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def _live(conn, collection):
    r = _q(conn, 'SELECT COUNT(*), MAX(updated_at) FROM records WHERE collection = ? AND archived_at IS NULL',
           (collection,))
    return r[0] or 0, r[1]


def _flagged(conn, collections):
    marks = ','.join('?' * len(collections))
    return _q(conn, f"SELECT COUNT(*) FROM records WHERE collection IN ({marks}) AND archived_at IS NULL "
                    f"AND json_array_length(COALESCE(source, '{{}}'), '$.flags') > 0", tuple(collections))[0]


def _count_where(conn, collection, clause, params=()):
    return _q(conn, f'SELECT COUNT(*) FROM records WHERE collection = ? AND archived_at IS NULL AND ({clause})',
              (collection, *params))[0]


def _sum_where(conn, collection, field, clause='1', params=()):
    return round(_q(conn, f"SELECT TOTAL(json_extract(data, '$.{field}')) FROM records WHERE collection = ? "
                          f"AND archived_at IS NULL AND ({clause})", (collection, *params))[0] or 0.0, 2)


def _in(field, values):
    return f"json_extract(data, '$.{field}') IN ({','.join('?' * len(values))})", tuple(values)


def _needs_check(n):
    return f'; {n} value{"s" if n != 1 else ""} an agent wrote wait for you to confirm' if n else ''


def _customers(conn, now):
    n, last = _live(conn, 'contact')
    flagged = _flagged(conn, ['contact'])
    counts = {'customers': n, 'needs_check': flagged}
    if not n:
        return section('customers', verdict='unknown', state='empty', source='business',
                       reason='No customers recorded yet', counts=counts)
    v = 'warn' if flagged else 'ok'
    return section('customers', verdict=v, state='live', source='business', counts=counts, last_activity=last,
                   reason=f'{n} customer{"s" if n != 1 else ""} on file' + _needs_check(flagged))


def _jobs(conn, now):
    n, last = _live(conn, 'job')
    c_open, p_open = _in('status', JOB_OPEN)
    c_done, p_done = _in('status', JOB_DONE)
    counts = {
        'jobs': n,
        'open': _count_where(conn, 'job', c_open, p_open),
        'scheduled': _count_where(conn, 'job', "json_extract(data, '$.status') = 'scheduled'"),
        'in_progress': _count_where(conn, 'job', "json_extract(data, '$.status') = 'in_progress'"),
        'done': _count_where(conn, 'job', c_done, p_done),
        'late': _count_where(conn, 'job', "json_extract(data, '$.status') = 'scheduled' AND "
                                          "json_extract(data, '$.scheduled_at') < ?", (_iso(now),)),
        'needs_check': _flagged(conn, ['job']),
    }
    if not n:
        return section('jobs', verdict='unknown', state='empty', source='business', reason='No jobs recorded yet',
                       counts=counts)
    if counts['late']:
        v, why = 'warn', f'{counts["late"]} scheduled job{"s are" if counts["late"] != 1 else " is"} past its date'
    else:
        v, why = 'ok', f'{counts["open"]} open, {counts["done"]} done'
    if counts['needs_check'] and v == 'ok':
        v = 'warn'
    return section('jobs', verdict=v, state='live', source='business', counts=counts, last_activity=last,
                   reason=why + _needs_check(counts['needs_check']))


def _money(conn, now):
    today = now.date().isoformat()
    n_est, last_est = _live(conn, 'estimate')
    n_inv, last_inv = _live(conn, 'invoice')
    n_pay, last_pay = _live(conn, 'payment')
    c_eo, p_eo = _in('status', EST_OPEN)
    c_iu, p_iu = _in('status', INV_UNPAID)
    overdue_clause = (f"({c_iu}) AND (json_extract(data, '$.status') = 'overdue' OR "
                      f"json_extract(data, '$.due_on') < ?)")
    bad_cut = (now - timedelta(days=OVERDUE_BAD_DAYS)).date().isoformat()
    since30 = _iso(now - timedelta(days=30))
    counts = {
        'estimates': n_est,
        'estimates_open': _count_where(conn, 'estimate', c_eo, p_eo),
        'estimates_open_value': _sum_where(conn, 'estimate', 'total', c_eo, p_eo),
        'invoices': n_inv,
        'invoices_unpaid': _count_where(conn, 'invoice', c_iu, p_iu),
        'invoices_unpaid_value': _sum_where(conn, 'invoice', 'amount', c_iu, p_iu),
        'invoices_overdue': _count_where(conn, 'invoice', overdue_clause, (*p_iu, today)),
        'invoices_overdue_value': _sum_where(conn, 'invoice', 'amount', overdue_clause, (*p_iu, today)),
        'payments': n_pay,
        'payments_30d': _count_where(conn, 'payment', "json_extract(data, '$.paid_at') >= ?", (since30,)),
        'payments_30d_value': _sum_where(conn, 'payment', 'amount', "json_extract(data, '$.paid_at') >= ?",
                                         (since30,)),
        'needs_check': _flagged(conn, ['estimate', 'invoice', 'payment']),
    }
    last = _newest(last_est, last_inv, last_pay)
    if not (n_est or n_inv or n_pay):
        return section('money', verdict='unknown', state='empty', source='business',
                       reason='No estimates, invoices or payments recorded yet', counts=counts)
    very_late = _count_where(conn, 'invoice', f"({c_iu}) AND json_extract(data, '$.due_on') < ?", (*p_iu, bad_cut))
    od = counts['invoices_overdue']
    if very_late:
        v, why = 'bad', (f'{very_late} invoice{"s are" if very_late != 1 else " is"} more than '
                         f'{OVERDUE_BAD_DAYS} days past due')
    elif od:
        v, why = 'warn', f'{od} invoice{"s are" if od != 1 else " is"} past due'
    else:
        v, why = 'ok', (f'{counts["estimates_open"]} open estimate{"s" if counts["estimates_open"] != 1 else ""}, '
                        f'{counts["invoices_unpaid"]} unpaid invoice{"s" if counts["invoices_unpaid"] != 1 else ""}')
    if counts['needs_check'] and v == 'ok':
        v = 'warn'
    return section('money', verdict=v, state='live', source='business', counts=counts, last_activity=last,
                   reason=why + _needs_check(counts['needs_check']))


def _team(conn, now):
    n, last = _live(conn, 'person')
    # unknown is never counted as measured: only active === true is active; a missing/null field is `active_unknown`
    active = _count_where(conn, 'person', "json_extract(data, '$.active') = 1")
    unknown = _count_where(conn, 'person', "json_extract(data, '$.active') IS NULL")
    counts = {'people': n, 'active': active, 'active_unknown': unknown}
    if not n:
        return section('team', verdict='unknown', state='empty', source='business',
                       reason='No team members recorded yet', counts=counts)
    return section('team', verdict='ok', state='live', source='business', counts=counts, last_activity=last,
                   reason=f'{active} active team member{"s" if active != 1 else ""}'
                          + (f', {unknown} not set' if unknown else ''))


def _inventory(conn, now):
    n_prod, last_prod = _live(conn, 'product')
    n_mv, last_mv = _live(conn, 'stock_move')
    # unknown is never counted as measured: only active === true is active; a missing/null field is `products_unknown`
    active = _count_where(conn, 'product', "json_extract(data, '$.active') = 1")
    unknown = _count_where(conn, 'product', "json_extract(data, '$.active') IS NULL")
    since30 = (now - timedelta(days=30)).date().isoformat()
    counts = {'products': n_prod, 'products_active': active, 'products_unknown': unknown, 'stock_moves': n_mv,
              'stock_moves_30d': _count_where(conn, 'stock_move', "COALESCE(json_extract(data, '$.date'), "
                                                                  "substr(created_at, 1, 10)) >= ?", (since30,))}
    if not (n_prod or n_mv):
        return section('inventory', verdict='unknown', state='empty', source='business',
                       reason='No products or stock moves recorded yet', counts=counts)
    return section('inventory', verdict='ok', state='live', source='business', counts=counts,
                   last_activity=_newest(last_prod, last_mv),
                   reason=f'{active} active product{"s" if active != 1 else ""}'
                          + (f', {unknown} not set' if unknown else '') + f', {counts["stock_moves_30d"]} stock '
                          f'move{"s" if counts["stock_moves_30d"] != 1 else ""} in 30 days '
                          '(low-stock is not computed yet)')


LOCAL = (('customers', _customers), ('jobs', _jobs), ('money', _money), ('team', _team), ('inventory', _inventory))


# ── read-through sections (the platform service) ─────────────────────────────
def platform_base():
    """Where the platform service answers for this container, or None when it is switched off.

    BIZ_PLATFORM_API_URL overrides (tests, a non-docker install); 'off' disables. Otherwise the
    container's own default-route gateway (read at call time, never a fixed bridge address).
    """
    raw = os.getenv('BIZ_PLATFORM_API_URL', '').strip()
    if raw.lower() in ('off', 'none', '0'):
        return None
    if raw:
        return raw.rstrip('/')
    from services.host_broker import default_gateway
    gw = default_gateway()
    return f'http://{gw}:{PLATFORM_PORT}' if gw else None


class Unreachable(Exception):
    def __init__(self, why):
        super().__init__(why)
        self.why = why


def _get(base, path, tenant):
    req = urllib.request.Request(base + path, headers={'X-Tenant': tenant, 'User-Agent': 'jambot-biz-hub/1',
                                                       'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=PLATFORM_TIMEOUT_S) as r:      # noqa: S310 (fixed base)
            body = r.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Unreachable(f'the platform service refused this server ({e.code})')
        raise Unreachable(f'the platform service answered {e.code}')
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, 'reason', e)
        raise Unreachable('the platform service could not be reached from this server'
                          + (' (timed out)' if 'timed out' in str(reason) else ''))
    try:
        return json.loads(body)
    except ValueError:
        raise Unreachable('the platform service answered something that is not JSON')


def _cached(key, fn):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < PLATFORM_CACHE_S:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (now, val)
    return val


def clear_cache():
    with _cache_lock:
        _cache.clear()


def _unknown(name, why, state='unreachable'):
    return section(name, verdict='unknown', state=state, source='platform', reason=why)


def _leads(base, tenant, now):
    try:
        st = _get(base, '/api/leads/stats?exclude_test=true', tenant)
        newest = _get(base, '/api/leads?limit=1', tenant)
    except Unreachable as e:
        return _unknown('leads', f'Leads unknown: {e.why}')
    s = st.get('stats') if isinstance(st, dict) else None
    if not isinstance(s, dict) or not isinstance(s.get('total'), int):
        return _unknown('leads', 'Leads unknown: the lead system answered without a count')
    counts = {k: s.get(k) for k in ('total', 'new_count', 'contacted', 'quoted', 'won', 'lost', 'spam')
              if isinstance(s.get(k), int)}
    counts['new'] = counts.pop('new_count', 0)
    rows = newest.get('leads') if isinstance(newest, dict) else None
    last = _newest(*[(rows[0] or {}).get(k) for k in ('created_at', 'received_at', 'updated_at')]) \
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    real = counts['total'] - counts.get('spam', 0)
    if not real:
        return section('leads', verdict='unknown', state='empty', source='platform', counts=counts,
                       last_activity=last, reason='The lead system has no leads for this business yet')
    if counts['new']:
        return section('leads', verdict='warn', state='live', source='platform', counts=counts, last_activity=last,
                       reason=f'{counts["new"]} new lead{"s" if counts["new"] != 1 else ""} not contacted yet')
    return section('leads', verdict='ok', state='live', source='platform', counts=counts, last_activity=last,
                   reason=f'{real} lead{"s" if real != 1 else ""}, none waiting')


def _seo_and_site(base, tenant, now):
    try:
        proj = _get(base, '/api/seo/projects', tenant)
        rep = _get(base, '/api/seo/reports', tenant)
    except Unreachable as e:
        return (_unknown('seo', f'SEO unknown: {e.why}'),
                _unknown('site', f'Website unknown: {e.why}'))
    projects = [p for p in (proj.get('projects') if isinstance(proj, dict) else None) or [] if isinstance(p, dict)]
    reports = [r for r in (rep.get('reports') if isinstance(rep, dict) else None) or [] if isinstance(r, dict)]
    domains = sorted({str(x.get('domain')).strip().lower() for x in projects + reports
                      if isinstance(x.get('domain'), str) and x.get('domain').strip()})
    last = _newest(*[r.get('generatedAt') for r in reports])
    counts = {'projects': len(projects), 'reports': len(reports), 'domains': len(domains)}
    if not projects and not reports:
        seo = section('seo', verdict='unknown', state='empty', source='platform', counts=counts,
                      reason='No SEO project set up yet')
    elif not reports:
        seo = section('seo', verdict='warn', state='live', source='platform', counts=counts,
                      reason='An SEO project exists but no report has been made yet')
    else:
        age = (now - _parse_ts(last)).days if last else None
        if age is not None and age > SEO_STALE_DAYS:
            seo = section('seo', verdict='warn', state='live', source='platform', counts=counts, last_activity=last,
                          reason=f'Newest SEO report is {age} days old')
        else:
            seo = section('seo', verdict='ok', state='live', source='platform', counts=counts, last_activity=last,
                          reason=f'{len(reports)} SEO report{"s" if len(reports) != 1 else ""}, newest '
                                 f'{age if age is not None else "?"} days old')
    if domains:
        site = section('site', verdict='unknown', state='not_built', source='platform',
                       counts={'domains': len(domains)}, domains=domains,
                       reason='Website on file; whether it is up is not measured by this backend yet')
    else:
        site = section('site', verdict='unknown', state='empty', source='platform', counts={'domains': 0},
                       domains=[], reason='No website on file yet')
    return seo, site


def read_through(tenant, now, *, sandbox=None):
    if sandbox:
        why = 'Not shown in a dev sandbox: sandbox data is fake and the real {} belong to the dev server'
        return {'leads': _unknown('leads', why.format('leads'), 'sandbox'),
                'seo': _unknown('seo', why.format('SEO reports'), 'sandbox'),
                'site': _unknown('site', why.format('site records'), 'sandbox')}
    base = platform_base()
    if not base:
        why = 'the platform service is not connected to this server'
        return {'leads': _unknown('leads', f'Leads unknown: {why}'), 'seo': _unknown('seo', f'SEO unknown: {why}'),
                'site': _unknown('site', f'Website unknown: {why}')}

    def run():
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_leads = ex.submit(_leads, base, tenant, now)
            f_seo = ex.submit(_seo_and_site, base, tenant, now)
            seo, site = f_seo.result()
            return {'leads': f_leads.result(), 'seo': seo, 'site': site}

    return _cached((base, tenant), run)


# ── the summary ──────────────────────────────────────────────────────────────
def summary(store, *, sandbox=None, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    sections = {}
    with store._read() as conn:
        for name, fn in LOCAL:
            sections[name] = fn(conn, now)
    sections.update(read_through(store.tenant, now, sandbox=sandbox))
    judged = [(n, s) for n, s in sections.items() if s['verdict'] != 'unknown']
    if judged:
        worst = max(judged, key=lambda ns: _RANK[ns[1]['verdict']])
        overall = {'verdict': worst[1]['verdict'],
                   'reason': ('Everything measured looks fine' if worst[1]['verdict'] == 'ok'
                              else f'{worst[1]["label"]}: {worst[1]["reason"]}')}
    else:
        overall = {'verdict': 'unknown', 'reason': 'Nothing measured yet'}
    overall['unknown_sections'] = [n for n in SECTIONS if sections[n]['verdict'] == 'unknown']
    from services.biz import BIZ_API, BIZ_ENGINE_VERSION
    return {
        'hub': HUB_VERSION, 'api': BIZ_API, 'engine': BIZ_ENGINE_VERSION,
        'tenant': store.tenant, 'sandbox': sandbox, 'generated_at': _iso(now),
        'mode': 'read-only' if store.read_only else 'read-write',
        'overall': overall, 'order': list(SECTIONS), 'sections': {n: sections[n] for n in SECTIONS},
    }
