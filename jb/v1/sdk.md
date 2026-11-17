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
| `GET /<collection>` | `where=<json>&sort=-field&limit=&cursor=&include=a,b&archived=1` |
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
