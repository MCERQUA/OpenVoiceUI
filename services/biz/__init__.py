"""JamBot client backend ("biz"): one engine, one SQLite file per tenant.

Design: the JamBot client-backend design (platform docs). Phase 1 = the engine:
per-tenant business.db, the schema registry (core collections + tenant overlay),
/api/biz/v1 CRUD with provenance and optimistic locking, counters, the activity
feed, and the daily backup. Served by routes/biz.py; the page SDK is jb/v1/jb.js.
Phase 2 = the company system: the app_pages registry (services/biz/pages.py) and the
shared menu shell jb/v1/shell.js, injected at serve time into registered pages.

Isolation: the tenant comes from the container env (JAMBOT_TENANT) and the db
directory from the container env (BIZ_DB_DIR, else the dir of OVUI_DB_PATH).
Nothing in a request can name a tenant or a path.
"""

BIZ_ENGINE_VERSION = '1.1.0'
BIZ_API = 'v1'
