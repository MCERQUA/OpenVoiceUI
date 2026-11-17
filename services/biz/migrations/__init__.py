"""Numbered, additive-only schema migrations for business.db (design section 11).

Each file NNNN_<name>.py defines VERSION (int == NNNN), NAME and up(conn).
A migration may add tables, columns, indexes, triggers or rows. It never drops,
renames or retypes anything: an older engine must still read a newer db.
"""
import importlib
import re
import sqlite3
from pathlib import Path

_FILE_RE = re.compile(r'^(\d{4})_([a-z0-9_]+)\.py$')


def discover():
    """Return [(version, name, module)] sorted by version; versions must be 1..N with no gaps."""
    found = []
    for f in sorted(Path(__file__).parent.iterdir()):
        m = _FILE_RE.match(f.name)
        if not m:
            continue
        mod = importlib.import_module(f'{__name__}.{f.stem}')
        if getattr(mod, 'VERSION', None) != int(m.group(1)):
            raise RuntimeError(f'migration {f.name}: VERSION does not match its file number')
        found.append((mod.VERSION, mod.NAME, mod))
    for i, (v, _, _) in enumerate(found, start=1):
        if v != i:
            raise RuntimeError(f'migration numbering has a gap at {i}')
    return found


def script(conn, sql: str):
    """Run a multi-statement script INSIDE the caller's transaction.

    sqlite3's executescript() commits first, which would break "all pending
    migrations in one transaction"; this runs statement by statement instead.
    """
    buf = ''
    for piece in sql.split(';'):
        buf += piece + ';'
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt.strip(';').strip():
                conn.execute(stmt)
            buf = ''
    if buf.strip(' \n\t;'):
        raise ValueError('incomplete SQL statement at end of migration script')
