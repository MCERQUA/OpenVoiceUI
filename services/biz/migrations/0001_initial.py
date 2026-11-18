"""0001 initial: meta, records, activity, counters, field_proposals + the jail-cell triggers."""

from services.biz.migrations import script

VERSION = 1
NAME = 'initial'

_TENANT_GUARD = """
CREATE TRIGGER IF NOT EXISTS {t}_tenant_ins BEFORE INSERT ON {t}
WHEN NEW.tenant IS NOT (SELECT value FROM meta WHERE key = 'tenant')
BEGIN SELECT RAISE(ABORT, 'biz: row tenant does not match this database'); END;
CREATE TRIGGER IF NOT EXISTS {t}_tenant_upd BEFORE UPDATE ON {t}
WHEN NEW.tenant IS NOT (SELECT value FROM meta WHERE key = 'tenant')
BEGIN SELECT RAISE(ABORT, 'biz: row tenant does not match this database'); END;
"""


def up(conn):
    script(conn, """
    CREATE TABLE IF NOT EXISTS records (
        id          TEXT PRIMARY KEY,
        tenant      TEXT NOT NULL,
        collection  TEXT NOT NULL,
        data        TEXT NOT NULL CHECK (json_valid(data)),
        version     INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL,
        created_by  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        updated_by  TEXT NOT NULL,
        archived_at TEXT,
        source      TEXT CHECK (source IS NULL OR json_valid(source))
    );
    CREATE INDEX IF NOT EXISTS ix_records_coll ON records(collection, archived_at, updated_at);
    CREATE INDEX IF NOT EXISTS ix_records_job_number ON records(collection, json_extract(data, '$.job_number'));
    CREATE INDEX IF NOT EXISTS ix_records_contact ON records(collection, json_extract(data, '$.contact_id'));
    CREATE INDEX IF NOT EXISTS ix_records_job ON records(collection, json_extract(data, '$.job_id'));
    CREATE INDEX IF NOT EXISTS ix_records_status ON records(collection, json_extract(data, '$.status'));
    CREATE INDEX IF NOT EXISTS ix_records_date ON records(collection, json_extract(data, '$.date'));

    CREATE TABLE IF NOT EXISTS activity (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant      TEXT NOT NULL,
        at          TEXT NOT NULL,
        collection  TEXT,
        record_id   TEXT,
        type        TEXT NOT NULL,
        by          TEXT NOT NULL,
        channel     TEXT,
        ref         TEXT,
        diff        TEXT CHECK (diff IS NULL OR json_valid(diff))
    );
    CREATE INDEX IF NOT EXISTS ix_activity_record ON activity(record_id, id);

    CREATE TABLE IF NOT EXISTS counters (
        name    TEXT PRIMARY KEY,
        tenant  TEXT NOT NULL,
        prefix  TEXT NOT NULL DEFAULT '',
        pad     INTEGER NOT NULL DEFAULT 0,
        last    INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS field_proposals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant      TEXT NOT NULL,
        at          TEXT NOT NULL,
        by          TEXT NOT NULL,
        collection  TEXT NOT NULL,
        field       TEXT NOT NULL,
        definition  TEXT NOT NULL CHECK (json_valid(definition)),
        gate        TEXT CHECK (gate IS NULL OR json_valid(gate)),
        status      TEXT NOT NULL DEFAULT 'candidate',
        UNIQUE (collection, field)
    );

    -- Append-only logs: no UPDATE, no DELETE, ever.
    CREATE TRIGGER IF NOT EXISTS activity_no_update BEFORE UPDATE ON activity
    BEGIN SELECT RAISE(ABORT, 'biz: activity is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS activity_no_delete BEFORE DELETE ON activity
    BEGIN SELECT RAISE(ABORT, 'biz: activity is append-only'); END;
    -- Hard delete does not exist: archived_at is the only removal.
    CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
    BEGIN SELECT RAISE(ABORT, 'biz: records are archived, never deleted'); END;
    CREATE TRIGGER IF NOT EXISTS field_proposals_no_delete BEFORE DELETE ON field_proposals
    BEGIN SELECT RAISE(ABORT, 'biz: proposals are never deleted'); END;
    """)
    for table in ('records', 'activity', 'counters', 'field_proposals'):
        script(conn, _TENANT_GUARD.format(t=table))
