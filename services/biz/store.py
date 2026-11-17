"""BizStore: one tenant's business.db (the company record).

Four walls (design section 8):
  physical  one file per tenant, in the tenant's own bind-mounted db dir
  identity  tenant from the container env only; refuse to open a db whose
            meta.tenant differs (a copied or misrouted file)
  row       every row carries tenant; triggers abort a row whose tenant is not
            meta.tenant
  auth      enforced by routes/biz.py (Clerk session or this tenant's agent key)

Records are JSON in one `records` table, validated against the registry.
Hard delete does not exist (archive only), activity is append-only, a stale
If-Match version is a 409 (never last-write-wins), and a PATCH never blanks a
populated field with an empty value (the _ovu_merge_preserve rule); clearing a
field is an explicit `clear` list.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from services.biz import BIZ_API, BIZ_ENGINE_VERSION
from services.biz import field_gate
from services.biz.pages import PageRegistryMixin
from services.biz.migrations import discover, script
from services.biz.schema import (COLLECTION_RE, FIELD_RE, ID_RE, Registry, SchemaError,
                                 mask_secret)
from services.db_pool import SQLitePool

logger = logging.getLogger(__name__)

TENANT_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
COUNTER_RE = re.compile(r'^[a-z][a-z0-9_]{0,40}$')
PREFIX_RE = re.compile(r'^[A-Za-z0-9-]{0,12}$')
SOURCE_CHANNELS = {'sms', 'voice', 'email', 'photo', 'web', 'manual', 'import'}
AGENT_CHANNELS = {'sms', 'voice', 'email', 'photo', 'web'}
MAX_RECORD_BYTES = 256 * 1024
TABLES = ('records', 'activity', 'counters', 'field_proposals', 'app_pages', 'files')
# Backend-private file store (migration 0003): <db dir>/files/<sha256>, served only by
# GET /api/biz/v1/files/<id>. Type is decided from the bytes, never from the client's claim.
FILE_MAX_BYTES = 15 * 1024 * 1024
FILE_ID_RE = re.compile(r'^[0-9a-f]{64}$')
FILE_URL_PREFIX = 'biz-file:'
FILE_TYPES = ((b'%PDF-', 'application/pdf', '.pdf'),
              (b'\x89PNG\r\n\x1a\n', 'image/png', '.png'),
              (b'\xff\xd8\xff', 'image/jpeg', '.jpg'))
META_FIELDS = {'id', 'created_at', 'updated_at', 'created_by', 'updated_by', 'version'}
_EMPTY = (None, '', [], {})


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _err(status, code, message, **extra):
    return SchemaError(status, code, message, **extra)


# ── where the store lives: env only, never a request ─────────────────────────
def resolve_location(env=None, *, hosted: bool):
    """(tenant, db_dir) from the container env. Raises SchemaError(503) when unsafe.

    hosted = Clerk auth is on or CANVAS_REQUIRE_AUTH=true (a JamBot tenant).
    """
    env = os.environ if env is None else env
    tenant = (env.get('JAMBOT_TENANT') or '').strip()
    if not tenant:
        if hosted:
            raise _err(503, 'biz_tenant_unset', 'Business backend unavailable: JAMBOT_TENANT is not set')
        tenant = 'local'
    if not TENANT_RE.match(tenant):
        raise _err(503, 'biz_tenant_invalid', 'Business backend unavailable: JAMBOT_TENANT is not a valid id')
    db_dir = (env.get('BIZ_DB_DIR') or '').strip()
    if not db_dir and (env.get('OVUI_DB_PATH') or '').strip():
        db_dir = os.path.dirname(env['OVUI_DB_PATH'].strip())
    if not db_dir:
        if hosted:
            # The container's writable layer is wiped on recreate: never keep the company record there.
            raise _err(503, 'biz_storage_not_durable',
                       'Business backend unavailable: no durable db dir (BIZ_DB_DIR / OVUI_DB_PATH)')
        from services.paths import RUNTIME_DIR
        db_dir = str(RUNTIME_DIR / 'db')
    return tenant, Path(db_dir).resolve()


class _Pool(SQLitePool):
    def _create_connection(self):
        conn = super()._create_connection()
        conn.isolation_level = None          # explicit BEGIN/COMMIT only
        conn.row_factory = sqlite3.Row
        return conn


class BizStore(PageRegistryMixin):
    def __init__(self, db_dir: Path, tenant: str, *, core_dir=None, library_path=None, packs_dir=None):
        if not TENANT_RE.match(tenant or ''):
            raise _err(503, 'biz_tenant_invalid', 'invalid tenant id')
        self.tenant = tenant
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.db_dir / 'business.db'
        self.backup_dir = self.db_dir / 'backups'
        self.files_dir = self.db_dir / 'files'
        kw = {}
        if core_dir is not None:
            kw['core_dir'] = core_dir
        if library_path is not None:
            kw['library_path'] = library_path
        if packs_dir is not None:
            kw['packs_dir'] = packs_dir
        self.registry = Registry(self.db_dir / 'schema' / 'custom', **kw)
        self.read_only = False
        self.mode_reason = None
        self.engine_too_old = False
        self.last_backup = None
        self._backup_lock = threading.Lock()
        self._migrations = discover()
        self.latest_schema = self._migrations[-1][0]
        self.pool = _Pool(self.path, pool_size=4)
        try:
            self._bootstrap()
        except Exception:
            self.pool.close()
            raise
        self._migrate()
        self.registry.set_packs(self.packs_enabled())

    # ── plumbing ─────────────────────────────────────────────────────────
    @contextmanager
    def _tx(self):
        with self.pool.get_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise

    @contextmanager
    def _read(self):
        with self.pool.get_connection() as conn:
            yield conn

    def close(self):
        self.pool.close()

    def _meta(self, conn, key):
        row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        return row[0] if row else None

    def _bootstrap(self):
        with self._tx() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            have = self._meta(conn, 'tenant')
            if have is None:
                conn.execute("INSERT INTO meta(key, value) VALUES ('tenant', ?)", (self.tenant,))
                conn.execute("INSERT INTO meta(key, value) VALUES ('created_at', ?)", (utcnow(),))
                conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '0')")
                script(conn, """
                CREATE TRIGGER IF NOT EXISTS meta_tenant_immutable BEFORE UPDATE ON meta
                WHEN OLD.key = 'tenant' BEGIN SELECT RAISE(ABORT, 'biz: meta.tenant is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS meta_tenant_undeletable BEFORE DELETE ON meta
                WHEN OLD.key = 'tenant' BEGIN SELECT RAISE(ABORT, 'biz: meta.tenant is immutable'); END;
                """)
            elif have != self.tenant:
                logger.error('biz: REFUSING db %s: meta.tenant=%r but this container is %r',
                             self.path, have, self.tenant)
                raise _err(503, 'biz_tenant_mismatch',
                           'Business backend refused to open: this database belongs to another tenant')

    def _counts(self, conn) -> dict:
        out = {}
        for t in TABLES:
            try:
                out[t] = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None
        return out

    def _migrate(self):
        with self._read() as conn:
            current = int(self._meta(conn, 'schema_version') or 0)
        self.schema_version = current
        if current > self.latest_schema:
            self.engine_too_old = True      # forward-compatible read; unknown writes refused
            return
        if current == self.latest_schema:
            return
        if current > 0:
            name = f'pre-migrate-{current}-{self.latest_schema}-{utcnow().replace(":", "")}.db'
            result = self.backup_to(self.backup_dir / name)
            if not result['verified']:
                self.read_only = True
                self.mode_reason = 'pre-migration backup did not verify; migration not applied'
                logger.error('biz: %s (%s)', self.mode_reason, result)
                return
        try:
            with self._tx() as conn:
                applied = json.loads(self._meta(conn, 'migrations') or '[]')
                for version, name, mod in self._migrations:
                    if version <= current:
                        continue
                    mod.up(conn)
                    applied.append({'version': version, 'name': name, 'at': utcnow(),
                                    'engine': BIZ_ENGINE_VERSION})
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('migrations', ?)",
                             (json.dumps(applied),))
                conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                             (str(self.latest_schema),))
                conn.execute('INSERT INTO activity(tenant, at, type, by, diff) VALUES (?, ?, ?, ?, ?)',
                             (self.tenant, utcnow(), 'schema_migrated', 'system:engine',
                              json.dumps({'from': current, 'to': self.latest_schema})))
            self.schema_version = self.latest_schema
        except Exception as exc:
            self.read_only = True
            self.mode_reason = f'migration failed: {type(exc).__name__}'
            logger.exception('biz: migration %s -> %s failed; serving read-only', current, self.latest_schema)

    def _writable(self):
        if self.read_only:
            raise _err(503, 'biz_read_only', 'Business backend is read-only: ' + (self.mode_reason or ''))

    # ── provenance ───────────────────────────────────────────────────────
    @staticmethod
    def check_source(source, *, agent: bool):
        if source is None:
            if agent:
                raise _err(422, 'source_required',
                           'Agent writes must say where the data came from: '
                           'source = {channel: sms|voice|email|photo|web, ref, confidence, words}')
            return None
        if not isinstance(source, dict):
            raise _err(422, 'bad_source', '"source" must be an object')
        allowed = {'channel', 'ref', 'confidence', 'words', 'flags'}
        extra = set(source) - allowed
        if extra:
            raise _err(422, 'bad_source', 'Unknown keys in source', keys=sorted(extra))
        channel = source.get('channel')
        channels = AGENT_CHANNELS if agent else SOURCE_CHANNELS
        if channel not in channels:
            raise _err(422, 'bad_source', 'source.channel must be one of: ' + ', '.join(sorted(channels)))
        conf = source.get('confidence')
        if conf is not None and (isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1):
            raise _err(422, 'bad_source', 'source.confidence must be a number from 0 to 1')
        for k, lim in (('ref', 500), ('words', 4000)):
            if source.get(k) is not None and (not isinstance(source[k], str) or len(source[k]) > lim):
                raise _err(422, 'bad_source', f'source.{k} must be text up to {lim} characters')
        flags = source.get('flags')
        if flags is not None and (not isinstance(flags, list) or not all(isinstance(f, str) for f in flags)):
            raise _err(422, 'bad_source', 'source.flags must be a list of field names needing a check')
        return source

    # ── records ──────────────────────────────────────────────────────────
    def _row_to_record(self, row, collection=None) -> dict:
        data = json.loads(row['data'])
        for f in self.registry.secret_fields(row['collection']):
            if f in data:
                data[f] = mask_secret(data[f])
        return {
            'id': row['id'], 'collection': row['collection'], 'version': row['version'],
            'created_at': row['created_at'], 'created_by': row['created_by'],
            'updated_at': row['updated_at'], 'updated_by': row['updated_by'],
            'archived_at': row['archived_at'],
            'source': json.loads(row['source']) if row['source'] else None,
            'data': data,
        }

    def _masked(self, collection, data: dict) -> dict:
        secrets = self.registry.secret_fields(collection)
        return {k: (mask_secret(v) if k in secrets else v) for k, v in data.items()}

    def _spec_for_write(self, collection):
        spec = self.registry.require(collection)
        if spec.get('read_through'):
            raise _err(501, 'read_through_not_built',
                       f'{collection} records come from the platform lead system (read-through, a later phase); '
                       'they are never copied into this database')
        return spec

    def _check_refs(self, conn, refs):
        for field, target, rid in refs:
            row = conn.execute('SELECT 1 FROM records WHERE id = ? AND collection = ?', (rid, target)).fetchone()
            if row is None:
                raise _err(422, 'ref_not_found', 'A referenced record does not exist in this business',
                           field=field, collection=target)

    @staticmethod
    def _staff_guard(spec, collection, *, agent, op, keys=()):
        """Refuse an agent write the schema reserves for a person (design amendment 2026-11-17).

        A collection with "staff_only": true is written (created, edited, archived, unarchived)
        only by a person's session. A field with "staff_only": true is set or cleared only by a
        person, on any collection. The agent key gets 403, never a silent drop.
        """
        if not agent:
            return
        if spec.get('staff_only'):
            raise _err(403, 'staff_only', f'Only a person (staff session) may {op} {collection} records; '
                                          'an agent key cannot', collection=collection, op=op)
        fields = spec.get('fields') or {}
        hit = sorted(k for k in keys if (fields.get(k) or {}).get('staff_only'))
        if hit:
            raise _err(403, 'staff_only_field', 'Only a person (staff session) may set these fields; '
                                                'an agent key cannot', collection=collection, fields=hit)

    def _pack_hooks(self, op, collection, data, current, *, by, agent, via_pack):
        """Run every enabled pack's check_write for this collection (contract: services/biz/packs)."""
        from services.biz import packs as _packs
        for pack in self.registry.pack_of(collection):
            mod = _packs.hooks_for(pack)
            fn = getattr(mod, 'check_write', None) if mod is not None else None
            if fn is not None:
                fn(self, op, collection, data, current, by=by, agent=agent, via_pack=via_pack)

    # ── packs ────────────────────────────────────────────────────────────
    def packs_enabled(self) -> list:
        with self._read() as conn:
            raw = self._meta(conn, 'packs')
        try:
            names = json.loads(raw) if raw else []
        except ValueError:
            names = []
        return [n for n in names if isinstance(n, str)]

    def pack_enabled(self, name: str) -> bool:
        return name in self.registry.enabled_packs

    def enable_pack(self, name, *, by, source=None, agent=False):
        """Turn a shipped pack on for this business. Additive and permanent (never turned off)."""
        self._writable()
        source = self.check_source(source, agent=agent)
        if not isinstance(name, str) or name not in self.registry.available_packs:
            raise _err(404, 'unknown_pack', 'No such pack ships with this engine', pack=str(name)[:40])
        with self._tx() as conn:
            raw = self._meta(conn, 'packs')
            names = json.loads(raw) if raw else []
            already = name in names
            if not already:
                names.append(name)
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('packs', ?)", (json.dumps(names),))
                self._activity(conn, collection=None, record_id=None, type_='pack_enabled', by=by, source=source,
                               diff={'pack': name, 'version': self.registry.available_packs[name].get('version')})
        self.registry.set_packs(names)
        return {'pack': name, 'enabled': True, 'already': already, 'enabled_packs': list(self.registry.enabled_packs)}

    def note_activity(self, *, collection, record_id, type_, by, source=None, diff=None):
        """Append one activity row (pack routes: e.g. a person marked a drafted chase as sent)."""
        self._writable()
        if not isinstance(type_, str) or not re.match(r'^[a-z][a-z0-9_]{0,40}$', type_):
            raise _err(422, 'bad_request', 'activity type is lower-case letters, digits and _')
        with self._tx() as conn:
            self._activity(conn, collection=collection, record_id=record_id, type_=type_, by=by,
                           source=source, diff=diff)

    def _activity(self, conn, *, collection, record_id, type_, by, source=None, diff=None):
        conn.execute(
            'INSERT INTO activity(tenant, at, collection, record_id, type, by, channel, ref, diff) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (self.tenant, utcnow(), collection, record_id, type_, by,
             (source or {}).get('channel'), (source or {}).get('ref'),
             json.dumps(diff) if diff is not None else None))

    def create(self, collection, data, *, by, source=None, agent=False, via_pack=False):
        self._writable()
        spec = self._spec_for_write(collection)
        self._staff_guard(spec, collection, agent=agent, op='create',
                          keys=data.keys() if isinstance(data, dict) else ())
        source = self.check_source(source, agent=agent)
        clean, refs = self.registry.validate(collection, data, partial=False, engine_too_old=self.engine_too_old)
        clean = {k: v for k, v in clean.items() if v is not None}
        self._pack_hooks('create', collection, clean, None, by=by, agent=agent, via_pack=via_pack)
        blob = json.dumps(clean, separators=(',', ':'))
        if len(blob) > MAX_RECORD_BYTES:
            raise _err(413, 'record_too_large', 'Record is larger than 256 KB')
        rid, now = str(uuid.uuid4()), utcnow()
        with self._tx() as conn:
            if spec.get('singleton'):
                if conn.execute('SELECT 1 FROM records WHERE collection = ? AND archived_at IS NULL',
                                (collection,)).fetchone():
                    raise _err(409, 'singleton_exists', f'There is already a {collection} record; update it instead')
            self._check_refs(conn, refs)
            conn.execute(
                'INSERT INTO records(id, tenant, collection, data, version, created_at, created_by, '
                'updated_at, updated_by, source) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)',
                (rid, self.tenant, collection, blob, now, by, now, by,
                 json.dumps(source) if source else None))
            self._activity(conn, collection=collection, record_id=rid, type_='created', by=by, source=source,
                           diff={'set': self._masked(collection, clean)})
            row = conn.execute('SELECT * FROM records WHERE id = ?', (rid,)).fetchone()
        return self._row_to_record(row)

    def get(self, collection, rid):
        self.registry.require(collection)
        if not isinstance(rid, str) or not ID_RE.match(rid):
            raise _err(404, 'not_found', 'No such record')
        with self._read() as conn:
            row = conn.execute('SELECT * FROM records WHERE id = ? AND collection = ?', (rid, collection)).fetchone()
        if row is None:
            raise _err(404, 'not_found', 'No such record')
        return self._row_to_record(row)

    def update(self, collection, rid, patch, *, expected_version, by, source=None, agent=False, clear=None,
               via_pack=False):
        self._writable()
        spec = self._spec_for_write(collection)
        self._staff_guard(spec, collection, agent=agent, op='edit',
                          keys=list((patch or {}).keys() if isinstance(patch, dict) else ())
                          + [c for c in (clear or []) if isinstance(c, str)])
        if spec.get('append_only'):
            raise _err(409, 'append_only', f'{collection} is an append-only ledger: add a correcting entry '
                                           '(reverses_id) instead of editing')
        source = self.check_source(source, agent=agent)
        clean, refs = self.registry.validate(collection, patch or {}, partial=True,
                                             engine_too_old=self.engine_too_old)
        clear = clear or []
        if not isinstance(clear, list) or not all(isinstance(c, str) for c in clear):
            raise _err(422, 'bad_request', '"clear" must be a list of field names')
        fields = spec.get('fields') or {}
        for c in clear:
            if c not in fields:
                raise _err(422, 'validation_failed', 'Cannot clear an unknown field', field=c[:60])
            if c in (spec.get('required') or []):
                raise _err(422, 'validation_failed', 'A required field cannot be cleared', field=c)
        if not isinstance(expected_version, int):
            raise _err(428, 'if_match_required', 'Send If-Match: <version> (the version you read)')
        current = self.get(collection, rid)
        if current['archived_at']:
            raise _err(409, 'archived', 'This record is archived; unarchive it first')
        if self.registry.pack_of(collection):
            after = dict(current['data'])
            for k, v in clean.items():
                if v in _EMPTY and after.get(k) not in _EMPTY:
                    continue
                if v is None:
                    after.pop(k, None)
                else:
                    after[k] = v
            for c in clear:
                after.pop(c, None)
            self._pack_hooks('edit', collection, after, current, by=by, agent=agent, via_pack=via_pack)
        with self._tx() as conn:
            row = conn.execute('SELECT * FROM records WHERE id = ? AND collection = ?', (rid, collection)).fetchone()
            if row is None:
                raise _err(404, 'not_found', 'No such record')
            if row['version'] != expected_version:
                raise _err(409, 'version_conflict',
                           'Someone else saved this record first. Reload to see their changes.',
                           current=self._row_to_record(row))
            data = json.loads(row['data'])
            changes, preserved = {}, []
            for k, v in clean.items():
                old = data.get(k)
                if v in _EMPTY and old not in _EMPTY:
                    preserved.append(k)      # never blank a populated field by accident
                    continue
                if old != v:
                    changes[k] = [old, v]
                    if v is None:
                        data.pop(k, None)
                    else:
                        data[k] = v
            for c in clear:
                if c in data:
                    changes[c] = [data.pop(c), None]
            if not changes:
                rec = self._row_to_record(row)
                rec['unchanged'] = True
                rec['preserved'] = preserved
                return rec
            self._check_refs(conn, refs)
            blob = json.dumps(data, separators=(',', ':'))
            if len(blob) > MAX_RECORD_BYTES:
                raise _err(413, 'record_too_large', 'Record is larger than 256 KB')
            now = utcnow()
            cur = conn.execute(
                'UPDATE records SET data = ?, version = version + 1, updated_at = ?, updated_by = ?, '
                'source = COALESCE(?, source) WHERE id = ? AND version = ?',
                (blob, now, by, json.dumps(source) if source else None, rid, expected_version))
            if cur.rowcount != 1:
                raise _err(409, 'version_conflict', 'Someone else saved this record first. Reload to see their changes.')
            secrets = self.registry.secret_fields(collection)
            diff = {k: ([mask_secret(a), mask_secret(b)] if k in secrets else [a, b]) for k, (a, b) in changes.items()}
            self._activity(conn, collection=collection, record_id=rid, type_='updated', by=by, source=source,
                           diff={'changed': diff, 'preserved': preserved} if preserved else {'changed': diff})
            row = conn.execute('SELECT * FROM records WHERE id = ?', (rid,)).fetchone()
        rec = self._row_to_record(row)
        if preserved:
            rec['preserved'] = preserved
        return rec

    def set_archived(self, collection, rid, archived: bool, *, by, source=None, agent=False):
        self._writable()
        spec = self._spec_for_write(collection)
        self._staff_guard(spec, collection, agent=agent, op='archive' if archived else 'unarchive')
        source = self.check_source(source, agent=agent)
        current = self.get(collection, rid)
        if self.registry.pack_of(collection) and bool(current['archived_at']) != archived:
            self._pack_hooks('archive' if archived else 'unarchive', collection, current['data'], current,
                             by=by, agent=agent, via_pack=False)
        with self._tx() as conn:
            row = conn.execute('SELECT * FROM records WHERE id = ? AND collection = ?', (rid, collection)).fetchone()
            if bool(row['archived_at']) == archived:
                return self._row_to_record(row)
            now = utcnow()
            conn.execute('UPDATE records SET archived_at = ?, version = version + 1, updated_at = ?, '
                         'updated_by = ? WHERE id = ?', (now if archived else None, now, by, rid))
            self._activity(conn, collection=collection, record_id=rid,
                           type_='archived' if archived else 'unarchived', by=by, source=source)
            row = conn.execute('SELECT * FROM records WHERE id = ?', (rid,)).fetchone()
        return self._row_to_record(row)

    def _field_expr(self, spec, field):
        if field in META_FIELDS:
            return field
        if not isinstance(field, str) or not FIELD_RE.match(field) or field not in (spec.get('fields') or {}):
            raise _err(400, 'bad_query', 'Unknown field in query', field=str(field)[:60])
        # field is validated against the regex AND the schema, so it is safe to inline
        return f"json_extract(data, '$.{field}')"

    def _where_sql(self, spec, collection, where, archived):
        """WHERE clauses for list/aggregate. Fields are checked against the schema before inlining."""
        clauses, params = ['collection = ?'], [collection]
        if not archived:
            clauses.append('archived_at IS NULL')
        ops = {'$eq': '=', '$ne': '!=', '$gt': '>', '$gte': '>=', '$lt': '<', '$lte': '<='}
        for field, cond in (where or {}).items():
            expr = self._field_expr(spec, field)
            if isinstance(cond, dict):
                for op, val in cond.items():
                    if op == '$in':
                        if not isinstance(val, list) or not val or len(val) > 200:
                            raise _err(400, 'bad_query', '$in needs a list of 1-200 values')
                        clauses.append(f'{expr} IN ({",".join("?" * len(val))})')
                        params.extend(val)
                    elif op in ops:
                        clauses.append(f'{expr} {ops[op]} ?')
                        params.append(val)
                    else:
                        raise _err(400, 'bad_query', 'Unknown operator', op=str(op)[:20])
            elif isinstance(cond, (str, int, float, bool)) or cond is None:
                if cond is None:
                    clauses.append(f'{expr} IS NULL')
                else:
                    clauses.append(f'{expr} = ?')
                    params.append(cond)
            else:
                raise _err(400, 'bad_query', 'A where value must be text, a number, true/false, null or {op: value}')
        return clauses, params

    def list(self, collection, *, where=None, sort=None, limit=50, cursor=None, include=None, archived=False,
             flagged=False):
        spec = self.registry.require(collection)
        if spec.get('read_through'):
            raise _err(501, 'read_through_not_built',
                       f'{collection} is served from the platform lead system (read-through, a later phase)')
        clauses, params = self._where_sql(spec, collection, where, archived)
        if flagged:     # the "needs check" queue: agent-written values a person has not confirmed yet
            clauses.append("json_array_length(COALESCE(source, '{}'), '$.flags') > 0")
        order = '-updated_at' if not sort else sort
        desc = order.startswith('-')
        order_expr = self._field_expr(spec, order.lstrip('-'))
        try:
            limit = max(1, min(int(limit or 50), 500))
            offset = max(0, int(cursor or 0))
        except (TypeError, ValueError):
            raise _err(400, 'bad_query', 'limit and cursor must be numbers')
        sql = (f'SELECT * FROM records WHERE {" AND ".join(clauses)} '
               f'ORDER BY {order_expr} {"DESC" if desc else "ASC"}, id LIMIT ? OFFSET ?')
        with self._read() as conn:
            rows = conn.execute(sql, (*params, limit + 1, offset)).fetchall()
            more = len(rows) > limit
            records = [self._row_to_record(r) for r in rows[:limit]]
            include = [c for c in (include or []) if isinstance(c, str) and COLLECTION_RE.match(c)]
            if include:
                fields = spec.get('fields') or {}
                ref_fields = {f: s['ref'] for f, s in fields.items() if s.get('type') == 'ref' and s.get('ref') in include}
                for rec in records:
                    inc = {}
                    for f, target in ref_fields.items():
                        rid = rec['data'].get(f)
                        if rid:
                            r = conn.execute('SELECT * FROM records WHERE id = ? AND collection = ?',
                                             (rid, target)).fetchone()
                            if r is not None:
                                inc[f] = self._row_to_record(r)
                    if inc:
                        rec['included'] = inc
        return {'records': records, 'next_cursor': str(offset + limit) if more else None}

    # ── aggregates: counts and sums computed by the server, never from a page of rows ──
    AGG_NUMERIC = ('number', 'integer', 'money')

    def aggregate(self, collection, *, where=None, sum_fields=None, group_by=None, archived=False, flagged=False):
        """{count, sum: {field: total}, groups: [{key, count, sum}], flagged_only}.

        Counts every matching record (not one page). sum_fields must be numeric fields;
        group_by any scalar field (at most 200 groups, largest first).
        """
        spec = self.registry.require(collection)
        if spec.get('read_through'):
            raise _err(501, 'read_through_not_built', f'{collection} is read-through (a later phase)')
        clauses, params = self._where_sql(spec, collection, where, archived)
        if flagged:
            clauses.append("json_array_length(COALESCE(source, '{}'), '$.flags') > 0")
        fields = spec.get('fields') or {}
        sum_fields = list(dict.fromkeys(sum_fields or []))
        if len(sum_fields) > 10:
            raise _err(400, 'bad_query', 'sum takes at most 10 fields')
        sums = []
        for f in sum_fields:
            expr = self._field_expr(spec, f)
            if (fields.get(f) or {}).get('type') not in self.AGG_NUMERIC:
                raise _err(400, 'bad_query', 'sum needs a number or money field', field=f[:60])
            sums.append((f, expr))
        sel = ', '.join(['COUNT(*) AS n'] + [f'TOTAL({e}) AS s{i}' for i, (_, e) in enumerate(sums)])
        where_sql = ' AND '.join(clauses)
        out = {'collection': collection, 'flagged_only': bool(flagged)}
        with self._read() as conn:
            row = conn.execute(f'SELECT {sel} FROM records WHERE {where_sql}', params).fetchone()
            out['count'] = row['n']
            out['sum'] = {f: row[f's{i}'] for i, (f, _) in enumerate(sums)}
            if group_by:
                gexpr = self._field_expr(spec, group_by)
                if (fields.get(group_by) or {}).get('type') in ('list', 'object'):
                    raise _err(400, 'bad_query', 'group_by needs a single-value field', field=group_by[:60])
                rows = conn.execute(f'SELECT {gexpr} AS k, {sel} FROM records WHERE {where_sql} '
                                    f'GROUP BY k ORDER BY n DESC, k LIMIT 201', params).fetchall()
                out['groups'] = [{'key': r['k'], 'count': r['n'],
                                  'sum': {f: r[f's{i}'] for i, (f, _) in enumerate(sums)}} for r in rows[:200]]
                out['groups_truncated'] = len(rows) > 200
        return out

    # ── "needs check": a person confirms or clears agent-flagged values ─────
    def confirm_flags(self, collection, rid, fields, *, by, note=None):
        """Remove names from source.flags after a person checked them. Owner action; logged."""
        self._writable()
        self._spec_for_write(collection)
        if not isinstance(fields, list) or not fields or not all(isinstance(f, str) for f in fields) or len(fields) > 50:
            raise _err(422, 'bad_request', '"fields" must be a list of the flagged field names you checked')
        if note is not None and (not isinstance(note, str) or len(note) > 500):
            raise _err(422, 'bad_request', '"note" must be text up to 500 characters')
        self.get(collection, rid)
        with self._tx() as conn:
            row = conn.execute('SELECT * FROM records WHERE id = ? AND collection = ?', (rid, collection)).fetchone()
            source = json.loads(row['source']) if row['source'] else {}
            flags = list(source.get('flags') or [])
            unknown = [f for f in fields if f not in flags]
            if unknown:
                raise _err(409, 'not_flagged', 'These fields are not flagged on this record (reload it)',
                           fields=unknown[:20], flagged=flags)
            source['flags'] = [f for f in flags if f not in fields]
            now = utcnow()
            conn.execute('UPDATE records SET source = ?, version = version + 1, updated_at = ?, updated_by = ? '
                         'WHERE id = ?', (json.dumps(source), now, by, rid))
            self._activity(conn, collection=collection, record_id=rid, type_='flags_confirmed', by=by,
                           diff={'confirmed': fields, 'remaining': source['flags'], 'note': note})
            row = conn.execute('SELECT * FROM records WHERE id = ?', (rid,)).fetchone()
        return self._row_to_record(row)

    def timeline(self, collection, rid):
        self.get(collection, rid)
        with self._read() as conn:
            rows = conn.execute('SELECT * FROM activity WHERE record_id = ? ORDER BY id', (rid,)).fetchall()
        return [self._activity_row(r) for r in rows]

    @staticmethod
    def _activity_row(r):
        return {'id': r['id'], 'at': r['at'], 'collection': r['collection'], 'record_id': r['record_id'],
                'type': r['type'], 'by': r['by'], 'channel': r['channel'], 'ref': r['ref'],
                'diff': json.loads(r['diff']) if r['diff'] else None}

    def events(self, since=0, limit=200):
        """Activity after `since`. since=-1 returns no events, only the latest id (a page's starting point)."""
        try:
            if int(since or 0) == -1:
                with self._read() as conn:
                    last = conn.execute('SELECT COALESCE(MAX(id), 0) FROM activity').fetchone()[0]
                return {'events': [], 'last_id': last}
            since = max(0, int(since or 0))
            limit = max(1, min(int(limit or 200), 1000))
        except (TypeError, ValueError):
            raise _err(400, 'bad_query', 'since and limit must be numbers')
        with self._read() as conn:
            rows = conn.execute('SELECT * FROM activity WHERE id > ? ORDER BY id LIMIT ?', (since, limit)).fetchall()
        events = [self._activity_row(r) for r in rows]
        return {'events': events, 'last_id': events[-1]['id'] if events else since}

    # ── counters ─────────────────────────────────────────────────────────
    def counter_next(self, name, *, prefix='', pad=0, by='system'):
        self._writable()
        if not isinstance(name, str) or not COUNTER_RE.match(name):
            raise _err(422, 'bad_counter', 'Counter names are lower-case letters, digits and _')
        if not isinstance(prefix, str) or not PREFIX_RE.match(prefix):
            raise _err(422, 'bad_counter', 'prefix is up to 12 letters, digits or -')
        if isinstance(pad, bool) or not isinstance(pad, int) or not 0 <= pad <= 9:
            raise _err(422, 'bad_counter', 'pad is a whole number 0-9')
        with self._tx() as conn:
            conn.execute('INSERT OR IGNORE INTO counters(name, tenant, prefix, pad, last) VALUES (?, ?, ?, ?, 0)',
                         (name, self.tenant, prefix, pad))
            conn.execute('UPDATE counters SET last = last + 1 WHERE name = ?', (name,))
            row = conn.execute('SELECT * FROM counters WHERE name = ?', (name,)).fetchone()
            value = row['last']
            formatted = f"{row['prefix']}{str(value).zfill(row['pad'])}"
            self._activity(conn, collection=None, record_id=None, type_='counter_next', by=by,
                           diff={'counter': name, 'value': value, 'formatted': formatted})
        return {'name': name, 'value': value, 'formatted': formatted}

    # ── schema changes and the shared field library ─────────────────────
    def _gate_context(self, conn):
        company = conn.execute(
            "SELECT data FROM records WHERE collection = 'company' AND archived_at IS NULL LIMIT 1").fetchone()
        names = [r[0] for r in conn.execute(
            "SELECT json_extract(data, '$.name') FROM records WHERE collection = 'contact'").fetchall() if r[0]]
        return {
            'brand_terms': field_gate.brand_terms_for(self.tenant, json.loads(company[0]) if company else None),
            'customer_names': names,
            'vocabulary': field_gate.vocabulary_from(self.registry),
        }

    @staticmethod
    def candidate_definition(collection, field, spec):
        key = field[2:] if field.startswith('x_') else field
        out = {'key': key, 'collection': collection, 'type': spec.get('type')}
        for k in ('label', 'unit', 'min', 'max', 'values', 'items', 'description'):
            if spec.get(k) is not None:
                out[k] = spec[k]
        return out

    def add_custom_schema(self, payload, *, by, source=None, agent=False):
        self._writable()
        source = self.check_source(source, agent=agent)
        spec, new_fields = self.registry.add_custom(payload)
        name = spec['collection']
        proposals = []
        with self._tx() as conn:
            ctx = self._gate_context(conn) if new_fields else None
            for f in new_fields:
                defn = self.candidate_definition(name, f, spec['fields'][f])
                gate = field_gate.vet_definition(defn, **ctx)
                conn.execute(
                    'INSERT OR IGNORE INTO field_proposals(tenant, at, by, collection, field, definition, gate) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (self.tenant, utcnow(), by, name, f, json.dumps(defn), json.dumps(gate)))
                proposals.append({'field': f, 'definition': defn, 'gate': gate})
            self._activity(conn, collection=name, record_id=None, type_='schema_changed', by=by, source=source,
                           diff={'fields': sorted((payload.get('fields') or {}).keys())})
        return spec, proposals

    def adopt_library_field(self, collection, key, *, by, source=None, agent=False):
        self._writable()
        source = self.check_source(source, agent=agent)
        spec = self.registry.adopt(collection, key)
        with self._tx() as conn:
            self._activity(conn, collection=collection, record_id=None, type_='field_adopted', by=by,
                           source=source, diff={'library_key': key,
                                                'library_version': self.registry.library.get('library_version')})
        return spec

    def proposals(self):
        with self._read() as conn:
            rows = conn.execute('SELECT * FROM field_proposals ORDER BY id').fetchall()
        return [{'id': r['id'], 'at': r['at'], 'by': r['by'], 'collection': r['collection'], 'field': r['field'],
                 'definition': json.loads(r['definition']), 'gate': json.loads(r['gate']) if r['gate'] else None,
                 'status': r['status']} for r in rows]

    # ── backend-private file store ───────────────────────────────────────
    @staticmethod
    def sniff_type(blob: bytes):
        for magic, ctype, ext in FILE_TYPES:
            if blob.startswith(magic):
                return ctype, ext
        return None, None

    def put_file(self, blob, *, name=None, by, source=None, agent=False):
        """Store bytes by content hash. Returns the file's metadata (+ existed). Idempotent."""
        self._writable()
        source = self.check_source(source, agent=agent)
        if not isinstance(blob, (bytes, bytearray)) or not blob:
            raise _err(422, 'file_empty', 'Send the file as multipart form field "file"')
        if len(blob) > FILE_MAX_BYTES:
            raise _err(413, 'file_too_large', f'A stored file is at most {FILE_MAX_BYTES // (1024 * 1024)} MB')
        ctype, _ = self.sniff_type(bytes(blob[:16]))
        if ctype is None:
            raise _err(415, 'file_type_refused', 'Only PDF, PNG and JPEG files are stored (checked from the bytes)')
        if name is not None:
            if not isinstance(name, str):
                raise _err(422, 'bad_request', 'name must be text')
            name = re.split(r'[\\/]', name)[-1].strip()[:200] or None
        fid = hashlib.sha256(blob).hexdigest()
        self.files_dir.mkdir(parents=True, exist_ok=True)
        dest = self.files_dir / fid
        if not dest.is_file() or self._sha_of(dest) != fid:
            tmp = self.files_dir / f'.{fid}.partial'
            with open(tmp, 'wb') as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o640)
            os.replace(tmp, dest)
        with self._tx() as conn:
            row = conn.execute('SELECT * FROM files WHERE id = ?', (fid,)).fetchone()
            existed = row is not None
            if not existed:
                conn.execute('INSERT INTO files(id, tenant, content_type, size, name, created_at, created_by, source) '
                             'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                             (fid, self.tenant, ctype, len(blob), name, utcnow(), by,
                              json.dumps(source) if source else None))
                self._activity(conn, collection=None, record_id=None, type_='file_stored', by=by, source=source,
                               diff={'file': fid, 'size': len(blob), 'content_type': ctype})
                row = conn.execute('SELECT * FROM files WHERE id = ?', (fid,)).fetchone()
        out = self._file_row(row)
        out['existed'] = existed
        return out

    @staticmethod
    def _file_row(r):
        return {'id': r['id'], 'sha256': r['id'], 'url': FILE_URL_PREFIX + r['id'],
                'content_type': r['content_type'], 'size': r['size'], 'name': r['name'],
                'created_at': r['created_at'], 'created_by': r['created_by']}

    @staticmethod
    def _sha_of(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest()

    def file_meta(self, fid):
        if not isinstance(fid, str) or not FILE_ID_RE.match(fid):
            raise _err(404, 'not_found', 'No such file')
        with self._read() as conn:
            row = conn.execute('SELECT * FROM files WHERE id = ?', (fid,)).fetchone()
        if row is None:
            raise _err(404, 'not_found', 'No such file')
        return self._file_row(row)

    def file_verified(self, fid) -> bool:
        """True when the file is indexed AND its bytes on disk still hash to its id."""
        try:
            self.file_meta(fid)
        except SchemaError:
            return False
        path = self.files_dir / fid
        return path.is_file() and self._sha_of(path) == fid

    def file_open(self, fid):
        """(metadata, path) for a stored file whose bytes still match its hash; else refused."""
        meta = self.file_meta(fid)
        path = self.files_dir / fid
        if not path.is_file() or self._sha_of(path) != fid:
            logger.error('biz: stored file %s is missing or does not match its hash', fid[:12])
            raise _err(500, 'file_corrupt', 'The stored file is missing or does not match its hash')
        return meta, path

    def backup_files(self) -> dict:
        """Mirror the file store into backups/files/ (copy what is missing, verify each copy's hash).

        Never deletes. Same disk as the store: an off-box copy is a separate, platform job.
        """
        mirror = self.backup_dir / 'files'
        with self._read() as conn:
            ids = [r[0] for r in conn.execute('SELECT id FROM files').fetchall()]
        copied, bad = 0, []
        for fid in ids:
            src, dst = self.files_dir / fid, mirror / fid
            if dst.is_file() and dst.stat().st_size == (src.stat().st_size if src.is_file() else -1):
                continue
            if not src.is_file():
                bad.append(fid[:12])
                continue
            mirror.mkdir(parents=True, exist_ok=True)
            tmp = mirror / f'.{fid}.partial'
            shutil.copy2(src, tmp)
            if self._sha_of(tmp) != fid:
                bad.append(fid[:12])
                continue
            os.replace(tmp, dst)
            copied += 1
        present = sum(1 for fid in ids if (mirror / fid).is_file())
        return {'indexed': len(ids), 'mirrored': present, 'copied_now': copied, 'problems': bad,
                'verified': not bad and present == len(ids)}

    def files_list(self):
        try:
            with self._read() as conn:
                rows = conn.execute('SELECT * FROM files ORDER BY created_at').fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._file_row(r) for r in rows]

    # ── health, export, backup ───────────────────────────────────────────
    def collection_counts(self):
        with self._read() as conn:
            rows = conn.execute('SELECT collection, COUNT(*) AS n, SUM(archived_at IS NULL) AS live '
                                'FROM records GROUP BY collection').fetchall()
        return {r['collection']: {'total': r['n'], 'live': r['live']} for r in rows}

    def health(self):
        return {
            'engine': BIZ_ENGINE_VERSION, 'api': BIZ_API, 'schema': self.schema_version,
            'schema_latest': self.latest_schema, 'tenant': self.tenant,
            'mode': 'read-only' if self.read_only else 'read-write', 'mode_reason': self.mode_reason,
            'engine_too_old': self.engine_too_old,
            'library_version': self.registry.library.get('library_version'),
            'collections': self.collection_counts(), 'last_backup': self.last_backup,
            'shell': BIZ_API, 'registered_pages': self._registered_count(),
            'packs': list(self.registry.enabled_packs), 'files': self._files_count(),
        }

    def _files_count(self):
        try:
            with self._read() as conn:
                return conn.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        except sqlite3.OperationalError:
            return None

    def _registered_count(self):
        try:
            with self._read() as conn:
                return conn.execute('SELECT COUNT(*) FROM app_pages WHERE archived_at IS NULL').fetchone()[0]
        except sqlite3.OperationalError:      # an older schema (read-only mode): no registry yet
            return None

    def export(self):
        with self._read() as conn:
            rows = conn.execute('SELECT * FROM records ORDER BY collection, created_at').fetchall()
            counters = [dict(r) for r in conn.execute('SELECT name, prefix, pad, last FROM counters').fetchall()]
            n_activity = conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0]
        return {'tenant': self.tenant, 'exported_at': utcnow(), 'engine': BIZ_ENGINE_VERSION,
                'schema': self.schema_version, 'records': [self._row_to_record(r) for r in rows],
                'counters': counters, 'activity_count': n_activity, 'proposals': self.proposals(),
                'pages': self.pages_list(archived=True) if self._registered_count() is not None else [],
                'files': self.files_list(),
                'schema_overlay': {n: s for n, s in self.registry.collections().items() if s.get('layer') == 'custom'}}

    def backup_to(self, dest: Path) -> dict:
        """Consistent copy of business.db at dest, verified by per-table counts + integrity check."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + '.partial')
        with self._backup_lock, self.pool.get_connection() as lock_conn:
            lock_conn.execute('BEGIN IMMEDIATE')     # hold writers off so counts and copy agree
            try:
                live = self._counts(lock_conn)
                src = sqlite3.connect(str(self.path))
                dst = sqlite3.connect(str(tmp))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
            finally:
                lock_conn.execute('ROLLBACK')
        os.replace(tmp, dest)
        check = verify_backup(dest)
        result = {'path': dest.name, 'at': utcnow(), 'live_counts': live, 'backup_counts': check['counts'],
                  'integrity': check['integrity'],
                  'verified': check['integrity'] == 'ok' and check['counts'] == live}
        return result

    def maybe_daily_backup(self):
        """At most one backup per UTC day (business-YYYYMMDD.db). Never deletes an old one."""
        day = datetime.now(timezone.utc).strftime('%Y%m%d')
        dest = self.backup_dir / f'business-{day}.db'
        if (self.last_backup or {}).get('day') == day or dest.exists():
            return None
        try:
            result = self.backup_to(dest)
        except Exception as exc:
            logger.exception('biz: daily backup failed')
            self.last_backup = {'day': day, 'verified': False, 'error': type(exc).__name__}
            return self.last_backup
        result['day'] = day
        try:
            result['files'] = self.backup_files()
        except Exception as exc:
            logger.exception('biz: file store backup failed')
            result['files'] = {'verified': False, 'error': type(exc).__name__}
        self.last_backup = result
        if not result['verified']:
            logger.error('biz: daily backup did NOT verify: %s', result)
        return result


def verify_backup(path: Path) -> dict:
    """Open a backup read-only: per-table counts and PRAGMA integrity_check."""
    uri = f'file:{Path(path)}?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    try:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        counts = {}
        for t in TABLES:
            try:
                counts[t] = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            except sqlite3.OperationalError:
                counts[t] = None
        tenant = conn.execute("SELECT value FROM meta WHERE key = 'tenant'").fetchone()
    finally:
        conn.close()
    return {'integrity': integrity, 'counts': counts, 'tenant': tenant[0] if tenant else None}
