"""0003 files: the backend-private file store index (design amendment 2026-11-17, CCA plan B1).

Bytes live at <db dir>/files/<sha256>, outside UPLOADS_DIR, so the public /uploads route
can never serve them. They are served only by GET /api/biz/v1/files/<id> (a person's
session). The id IS the content hash: no account, project, customer or file name is ever
part of a path. A row is immutable and never deleted.
"""

from services.biz.migrations import script

VERSION = 3
NAME = 'files'


def up(conn):
    script(conn, """
    CREATE TABLE IF NOT EXISTS files (
        id           TEXT PRIMARY KEY CHECK (length(id) = 64),
        tenant       TEXT NOT NULL,
        content_type TEXT NOT NULL,
        size         INTEGER NOT NULL,
        name         TEXT,
        created_at   TEXT NOT NULL,
        created_by   TEXT NOT NULL,
        source       TEXT CHECK (source IS NULL OR json_valid(source))
    );
    CREATE TRIGGER IF NOT EXISTS files_no_delete BEFORE DELETE ON files
    BEGIN SELECT RAISE(ABORT, 'biz: stored files are kept forever, never deleted'); END;
    CREATE TRIGGER IF NOT EXISTS files_no_update BEFORE UPDATE ON files
    BEGIN SELECT RAISE(ABORT, 'biz: a stored file is immutable (its id is its content hash)'); END;
    CREATE TRIGGER IF NOT EXISTS files_tenant_ins BEFORE INSERT ON files
    WHEN NEW.tenant IS NOT (SELECT value FROM meta WHERE key = 'tenant')
    BEGIN SELECT RAISE(ABORT, 'biz: row tenant does not match this database'); END;
    """)
