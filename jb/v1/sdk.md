# JamBot client backend — SDK v1 (for agents and page builders)

Served at `/jb/v1/sdk.md` on every OpenVoiceUI that runs the business backend. Contract `v1`.

**The rule.** A page that shows or saves business data (customers, leads, jobs, quotes, invoices,
costs, materials, crew, files) is *only layout*. Its data lives in this business's own backend and
goes through `/jb/v1/jb.js`. Never `/api/canvas/data`, never `_data/*.json`, never workspace JSON,
never `localStorage` / `sessionStorage` / IndexedDB. Every business has the same backend, its own
separate database; improve the backend once and every business gets it.

A company page has three parts:

| Part | Who makes it | Where |
|---|---|---|
| Layout and styling | you (the business's own look, unless the owner asks for another) | the page HTML |
| Menu | the platform | `/jb/v1/shell.js`, added automatically once the page is registered |
| Data | the platform | `/jb/v1/jb.js` calling `/api/biz/v1` |

---

## 1. Include

```html
<script src="/jb/v1/jb.js"></script>
```

Nothing else. Do not copy `jb.js` into the page folder: it is served from the platform so fixes reach
every page. The menu shell is added by the server to registered pages (section 4); you do not include it.

## 2. Auth

| Caller | Credential | Recorded as |
|---|---|---|
| The owner in a browser (canvas page) | their signed-in session, automatic (the page's `authFetch` bridge) | `clerk:<user id>` |
| The business's own agent | header `X-Agent-Key: <this business's AGENT_API_KEY>` | `agent:<business>` |

* No request ever names a business. The business is fixed by the server it runs on. Sending `tenant`
  (query, body or header) is refused with 400.
* A wrong agent key is 403, never retried as anything else.
* **Agent writes must say where the data came from**: `source = {channel, ref, confidence, words}`,
  `channel` one of `sms | voice | email | photo | web`. Missing source = 422 `source_required`.

## 3. Data calls

```js
// read
const page  = await jb.list('job', { where: { status: 'scheduled' }, sort: '-scheduled_at', limit: 50 });
//            -> { records: [...], next_cursor }      record = { id, version, data: {...}, created_at, ... }
const all   = await jb.listAll('contact');            // every page of a list
const one   = await jb.get('job', id);
const tl    = await jb.timeline('job', id);           // who changed what, when

// write
const job   = await jb.create('job', { title: 'Attic re-insulation', address: '100 Example St' });
await jb.update('job', job.id, { status: 'scheduled' }, job.version);   // the version you read
await jb.archive('job', job.id);                       // nothing is ever hard-deleted
const n     = await jb.nextNumber('job_number', { prefix: '26', pad: 3 });   // one counter per business

// numbers on tiles: counted by the SERVER over every matching record (a page of jb.list undercounts)
const open  = await jb.count('job', { where: { status: { $in: ['scheduled', 'in_progress'] } } });
const money = await jb.aggregate('cost_entry', { where: { job_id: j.id }, sum: ['amount'], group_by: 'kind' });
//            -> { count, sum: { amount: 1234.5 }, groups: [{ key: 'material', count: 4, sum: { amount: 900 } }] }

// "needs check": values an agent wrote with source.flags, until a person confirms them
const queue = await jb.list('cost_entry', { flagged: true });            // or jb.count(..., { flagged: true })
await jb.confirm('cost_entry', rec.id, ['amount'], 'matches the receipt'); // owner sessions only (agents get 403)

// deep links: a text or a tile opens the record, not the home page
const url   = await jb.recordUrl('job', j.id);       // '/pages/<page that lists jobs>?record=job:<id>'
const url2  = await jb.recordUrl('job', null, { job_number: '26107' });
const rec   = jb.currentRecord();                     // on the opened page: { collection: 'job', id } or null

// live pages
const stop  = jb.on('job', ev => reload());           // change feed (polling, pauses when hidden)

// shape
const schema = await jb.schema();                      // every collection and field this business has
await jb.addFields('job', { x_gate_code: { type: 'string', label: 'Gate code' } });  // additive only
await jb.adopt('job', '<library key>');                // a shared-library field (jb.library())
const brand  = await jb.brand();                       // { name, logo, colors, fonts } from the company record
```

Errors are `jb.JbError` with `status`, `code`, `message`:

| code | status | what to do |
|---|---|---|
| `version_conflict` | 409 | someone saved first; `err.current` is the saved record: show it, let the user redo |
| `if_match_required` | 428 | pass the `version` you read |
| `validation_failed`, `unknown_collection`, `ref_not_found` | 422 / 404 | fix the data or call `jb.schema()` |
| `source_required`, `bad_source` | 422 | agent writes need `source` |
| `auth_required` | 401 | the page is not signed in |
| `biz_read_only` | 503 | the backend is migrating or protecting data; show a notice, retry later |

**Deep-link convention.** A record's link is `/pages/<file>?record=<collection>:<id>`, where `<file>` is the
registered page whose `collections` include that collection (kind list, then tool, form, dashboard, report).
For a text to the owner, put the business's domain in front: `https://<business domain>/pages/...`.
A page that can show one record reads `jb.currentRecord()` on load and opens it.

**Counts and money are never `0` on failure.** A failed call is "could not load", never an empty list.

Core collections: `company` (one record), `contact`, `lead` (read-through, a later phase), `job`,
`estimate`, `line_item`, `invoice`, `payment`, `cost_entry`, `time_entry`, `product`, `stock_move`
(append-only: correct with a reversing entry), `document`, `event`, `task`, `person`.
Custom fields are named `x_<name>`, custom collections `c_<name>`.

## 4. The company system: register the page, get the menu

When a page is a business tool (quotes, invoices, jobs, schedule, costs, materials, customers, crew,
reports), register it **after the page file exists**. From then on every registered page shows one
shared menu, and the menu grows as the business adds tools.

```js
const page = await jb.pages.register({
  file: 'quote-tool.html',        // the page file in this business's canvas folder (no folders, no URLs)
  label: 'Quotes',                // the owner's own word for it, up to 60 characters
  section: 'sales',               // home customers sales jobs money stock team files reports
  kind: 'tool',                   // tool dashboard list form report
  collections: ['estimate', 'contact'],   // what it reads/writes (must exist in jb.schema())
  icon: 'document',               // optional; one of the names in the 400 message, default = the section's
  order: 10,                      // optional, 0-9999, lower first inside its section
  tab: true,                      // optional, wanted on the phone bottom bar (max 5 tabs)
  verb: 'New quote',              // optional quick-action button in the top bar
  verb_href: 'quote-tool.html#new',
  shell: 'office',                // office (default) | field (crew phone pages) | off (no menu on this page)
  legacy_nav: '#old-nav'          // optional: the page's own old menu, hidden while the shell is on
}, { source: { channel: 'voice', ref: 'owner asked for a quote tool' } });  // source: agents only
// -> 201 { id: 'quote-tool', version: 1, first_of_system: true|false, url: '/pages/quote-tool.html', ... }
```

* `first_of_system: true` means this is the business's **first** company tool. Tell the owner, in your own
  words: this is the first piece of their company system; it has its own menu now, and each new tool
  (quotes, jobs, costs) joins that menu and shares the same customer and job list.
* Change it: `jb.pages.update(id, { label: 'Estimates' }, page.version)` (409 on a stale version).
* Remove it from the menu: `jb.pages.archive(id)` (the page file is untouched; `unarchive` brings it back).
* Already registered: 409 `page_registered` with `current`. Missing page file: 422 `page_not_found`.
* List: `jb.pages.list()`. What the menu will draw: `jb.shell(pageId)`.

**Menu sections are fixed** so every business's menu reads the same way:
Home · Customers · Sales · Jobs · Money · Stock · Team · Files · Reports. Only sections with a page show.

**Variants.** `office`: left rail grouped by section and a top bar (section, page title, quick action) on
desktop; top bar plus a bottom tab bar (up to 5) and a "More" drawer on phones. `field`: a slim top bar
(back, title, menu) and the whole screen for the task, for crews with gloves on.

**Look.** The menu uses the business's canvas style and the colours in the `company` record
(`brand.colors.primary`, `secondary`, `background`, `surface`, `text`; `brand.fonts.body`, `display`;
`brand.logo` as a `/uploads/...` path). Your page CSS always wins over the platform base.
The page keeps its own padding: the shell only adds room for itself (top, left rail, bottom bar).

**Do not** draw your own copy of the menu, and do not paste nav code into pages. A page that includes
`/jb/v1/shell.js` itself is left alone by the server (no double menu).

## 5. Raw HTTP (agents, scripts)

Base: `https://<business domain>/api/biz/v1` (inside the platform network the OpenVoiceUI service URL works too).

| Method + path | Body / query |
|---|---|
| `GET /health` | engine, api, schema, mode, counts, `registered_pages` |
| `GET /schema`, `GET /schema/<collection>` | |
| `GET /<collection>` | `where=<json>&sort=-field&limit=&cursor=&include=a,b&archived=1&flagged=1` |
| `GET /<collection>/aggregate` | `where=<json>&sum=a,b&group_by=field&flagged=1&archived=1` -> `{count, sum, groups}` |
| `POST /<collection>/<id>/confirm` | `{fields: [flagged names], note}` (owner only) |
| `POST /<collection>` | `{data, source}` |
| `GET /<collection>/<id>`, `GET /<collection>/<id>/timeline` | |
| `PATCH /<collection>/<id>` | header `If-Match: <version>`; `{data, clear:[field], source}` |
| `POST /<collection>/<id>/archive` / `unarchive` | `{source}` |
| `POST /counters/<name>/next` | `{prefix, pad}` |
| `GET /events?since=<id>` | change feed |
| `GET /registry/pages` | `?archived=1` |
| `POST /registry/pages` | the fields in section 4, plus `source` for agents |
| `GET /registry/pages/<id>` | |
| `PATCH /registry/pages/<id>` | header `If-Match: <version>` |
| `POST /registry/pages/<id>/archive` / `unarchive` | |
| `GET /registry/shell?page=<id>` | the menu model |
| `GET /registry/resolve?collection=&id=` (or `&job_number=`) | `{url}`: the deep link that opens the record |

```bash
curl -s -H "X-Agent-Key: $KEY" https://<business>/api/biz/v1/health
curl -s -H "X-Agent-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"data":{"name":"Sample Customer"},"source":{"channel":"sms","ref":"msg-123"}}' \
  https://<business>/api/biz/v1/contact
```

Never print or paste a key into a page, a chat or a file another business can read.

## 6. Dev sandboxes (dev server only)

A dev server can host **sandboxes**: separate databases for testing before a business's own server has
the backend. A sandbox key is used exactly like an agent key (`X-Agent-Key`) and reaches only
`/api/biz/` on that server, only its own sandbox database. Extra calls for sandboxes:

| Method + path | |
|---|---|
| `PUT /dev/pages/<file>` | `{html, source}` publishes a page into the sandbox (`/pages/sandbox/<name>/<file>`) |
| `GET /dev/pages` | the sandbox's page files and whether each is registered |
| `GET /dev/pages/<file>` | the file plus the menu tags the server injects when it is served |

Pages in a sandbox use the same `jb.js` and read the sandbox's own data automatically. On a business's
own server these calls do not exist (404), so nothing written for a sandbox needs changing later
except the base URL and key.

## Packs and the private file store (engine 1.2)

A **pack** is a set of collections that ships with the engine (`default-schemas/packs/<name>/`) and a
business turns on. It is never turned off; its collections and the fields it adds to core collections
are named `<prefix>_...`.

```js
await jb.packs.list();                       // {available: [...], enabled: [...]}
await jb.packs.enable('risk-transfer');      // a person, or an agent with a source
const book = await jb.pack('crt').get('book');            // GET  /api/biz/v1/crt/book
await jb.pack('crt').post('confirm', {...});              // POST /api/biz/v1/crt/confirm
```

Some collections and fields are **staff-only** (`"staff_only": true` in `jb.schema()`): only a person's
session creates, edits, archives or unarchives them. An agent key gets `403 staff_only` /
`403 staff_only_field`.

**Files are never public.** Store them in the backend-private file store, not `/uploads`:

```js
const f = await jb.files.upload(fileInput.files[0], { source });  // {id, url: 'biz-file:<sha256>', ...}
await jb.create('document', { title: 'COI', file_url: f.url });
const blobUrl = await jb.files.open(jb.files.idOf(doc.data.file_url));  // a person's session only
```

### Platform staff pages (engine 1.4, host rulings H4 + R1)

A pack whose verdict trusts what a *person* wrote (risk-transfer) ships its staff pages in the image and serves
them on the business's **own staff origin**, `BIZ_STAFF_ORIGIN` (e.g. `https://crt-staff-<tenant>.jam-bot.com`),
at `GET /api/biz/v1/crt/staff/<page>` (sandbox: `?sandbox=<name>`). The tenant origin never serves them (a
navigation there is redirected to the staff origin): it is where canvas pages, which the agent can write, run,
and OVU hands those pages the staff member's Clerk JWT.

* **Signing in** happens on the staff origin (`/api/biz/v1/crt/staff/signin`): Clerk mints a session token
  there whose `azp` is the staff origin, and `POST .../crt/staff/session` exchanges it for an httpOnly,
  Secure, `SameSite=Strict`, host-only `__Host-jb_staff` cookie (8 h). A JWT with any other `azp` (every
  token a canvas page can get) is `403 staff_origin_token_refused`. The staff origin accepts no bearer token,
  no `__session` cookie and no agent key, and serves nothing but the staff pages, their JSON API and
  `/jb/v1/jb.js` (`404 not_on_staff_origin`). It grants no CORS. An expired session sends a page to sign-in.
* **Every write** there also carries the page's per-render token: `jb.js` sends it (`X-JB-Staff-Surface`),
  drops it from `window` and removes its boot script node at load. No token: `403 staff_surface_required`;
  a token from another user, another sandbox, another site (`Sec-Fetch-Site`, `Origin`) or a tampered or
  expired one: `403 staff_surface_invalid`.
* Where the pack is on, `crt_*` records and `/crt/...` actions are written **only** from the staff origin
  (`403 staff_surface_required` anywhere else). `contact`, `document` and `company` stay writable from any
  page; the backend stamps each write with whether it came from the staff surface, and the pack's verdict
  counts a person's write only when it did (otherwise the field reads "Can't tell", like an agent's).
* The staff page is minted only for `Sec-Fetch-Mode: navigate` + `Sec-Fetch-Dest: document`, carries
  `frame-ancestors 'none'`, `Cross-Origin-Opener-Policy: same-origin` and a nonce-only `script-src`.
* A platform page name is reserved: the sandbox `PUT /dev/pages/<file>` and `POST /api/canvas/pages` refuse
  it (`409 platform_page_reserved`), and `/pages/<that name>` is never served. The agent may keep its own read
  view under another name. `jb.staffBase` is the staff pages' base URL on a platform page and `null`
  elsewhere: link staff pages with it.
