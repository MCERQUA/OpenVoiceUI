"""0002 app_pages: the page/tool registry behind the shared menu shell (design section 9.2).

One row per canvas page that is part of this tenant's company system. The id is the
canvas page id (the file name without .html). Rows are archived, never deleted.
"""

from services.biz.migrations import script

VERSION = 2
NAME = 'app_pages'


def up(conn):
    script(conn, """
    CREATE TABLE IF NOT EXISTS app_pages (
        id              TEXT PRIMARY KEY,
        tenant          TEXT NOT NULL,
        file            TEXT NOT NULL,
        label           TEXT NOT NULL,
        section         TEXT NOT NULL,
        kind            TEXT NOT NULL DEFAULT 'tool',
        collections     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(collections)),
        icon            TEXT,
        ord             INTEGER NOT NULL DEFAULT 100,
        tab             INTEGER NOT NULL DEFAULT 0,
        verb            TEXT,
        verb_href       TEXT,
        shell           TEXT NOT NULL DEFAULT 'office',
        legacy_nav      TEXT,
        first_of_system INTEGER NOT NULL DEFAULT 0,
        version         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        created_by      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        updated_by      TEXT NOT NULL,
        archived_at     TEXT,
        source          TEXT CHECK (source IS NULL OR json_valid(source))
    );
    CREATE INDEX IF NOT EXISTS ix_app_pages_menu ON app_pages(archived_at, section, ord);

    CREATE TRIGGER IF NOT EXISTS app_pages_no_delete BEFORE DELETE ON app_pages
    BEGIN SELECT RAISE(ABORT, 'biz: registry pages are archived, never deleted'); END;
    CREATE TRIGGER IF NOT EXISTS app_pages_tenant_ins BEFORE INSERT ON app_pages
    WHEN NEW.tenant IS NOT (SELECT value FROM meta WHERE key = 'tenant')
    BEGIN SELECT RAISE(ABORT, 'biz: row tenant does not match this database'); END;
    CREATE TRIGGER IF NOT EXISTS app_pages_tenant_upd BEFORE UPDATE ON app_pages
    WHEN NEW.tenant IS NOT (SELECT value FROM meta WHERE key = 'tenant')
    BEGIN SELECT RAISE(ABORT, 'biz: row tenant does not match this database'); END;
    """)
