# Canvas Employee Portal — Permission System Spec

**Status:** PLANNED — ready for development  
**Origin:** Mike SMS 2026-07-30 — tenants want to give employees access to specific canvas pages without exposing the full OVU voice agent dashboard.

---

## Problem

Tenants (JamBot clients) build canvas pages for their business — dashboards, reports, tools, project trackers. They want employees to use these pages day-to-day, but employees should NOT have access to the voice agent, the admin panel, AI conversation history, or any other OVU features. Currently there is no middle-ground: either you have full OVU access or nothing.

---

## Solution Overview

A lightweight Canvas Portal at `/portal` (per-tenant subdomain) with:

1. **Clerk roles** — employees get `canvas_viewer` role, full users keep `canvas_admin`/`canvas_user`
2. **Server-side page assignments** — tenant admin assigns specific canvas page IDs to specific employees
3. **Portal route** — `/portal` shows ONLY assigned pages, no OVU chrome
4. **Admin UI extension** — manage employees and their page assignments from existing `/admin` panel

---

## Architecture

### 1. Clerk Role Model

**Existing roles (unchanged):**
- Authenticated OVU user → full dashboard access

**New role via Clerk public metadata:**
```json
{ "jambot_role": "canvas_viewer" }
```

Tenant admin invites employees via Clerk dashboard or via new admin UI "Invite Employee" flow. Employee receives Clerk email invite, sets password, lands in portal on first login.

The portal backend checks `jambot_role === "canvas_viewer"` on every request. If not present, the user is treated as a full OVU user and redirected to the main dashboard.

### 2. Page Assignment Storage

Per-tenant JSON file (server-side, NOT localStorage):
```
/mnt/clients/<tenant>/openvoiceui/canvas-assignments.json
```

Schema:
```json
{
  "employees": {
    "<clerk_user_id>": {
      "name": "Jane Smith",
      "email": "jane@client.com",
      "page_ids": ["page-abc123", "page-def456"],
      "groups": ["sales-team"]
    }
  },
  "groups": {
    "sales-team": {
      "page_ids": ["page-abc123"]
    }
  }
}
```

Effective pages for an employee = their own `page_ids` UNION any group `page_ids` they belong to.

### 3. Backend Changes

**New service:** `services/portal.py`
- `get_assignments(tenant)` — load/parse canvas-assignments.json
- `get_employee_pages(tenant, clerk_user_id)` — return list of page IDs this user can see
- `save_assignment(tenant, clerk_user_id, page_ids)` — write back to file
- `invite_employee(tenant, email, name)` — call Clerk API to send org invite

**New API routes** (add to `routes/canvas.py` or new `routes/portal.py`):
```
GET  /api/portal/pages            → list of canvas pages assigned to current user (requires canvas_viewer role)
POST /api/portal/assign           → admin sets page_ids for an employee
POST /api/portal/employees        → admin invites/adds a new employee
GET  /api/portal/employees        → admin lists all employees + their page assignments
DELETE /api/portal/employees/<id> → admin removes employee access
```

**Modified canvas page serving:**
- `GET /pages/<page_name>` — currently checks `is_public` or valid Clerk session
- Add: if user has `canvas_viewer` role, additionally check they are assigned this page_id; 403 if not

### 4. Frontend — Portal Page (`/portal`)

New HTML canvas page: `default-pages/employee-portal.html`

What it shows:
- Clean grid of assigned canvas pages (card per page: title, description if set, thumbnail if available)
- Click → opens the canvas page in the same tab
- No OVU sidebar, no voice controls, no admin links, no AI chat UI
- Tenant branding (logo from tenant config, or white-label)
- Clerk UserButton top-right for sign out

What it does NOT show:
- Voice agent
- Admin panel link
- Other tenants' pages
- Unassigned pages (even `is_public` ones — portal is curated)

**Route decision:** `/portal` served by Flask, renders `employee-portal.html` injected with the current tenant config. The page calls `GET /api/portal/pages` on load to get the assigned pages list.

### 5. Admin UI Extension

Add new tab/section to existing `/admin` panel: **Employees**

Sections:
1. **Employee list** — table: Name, Email, Pages assigned (count), Groups, Remove button
2. **Invite employee** — email + name form, fires Clerk invite + adds to assignments file
3. **Page assignment** — click employee → multi-select of all canvas pages → save

Optionally: **Groups** — create a named group, add employees to it, assign pages to the group (all group members inherit). Good for "all sales reps see the CRM dashboard."

### 6. URL Structure

Option A (simpler — single subdomain):
- Portal: `<tenant>.jam-bot.com/portal`
- Assigned pages: `<tenant>.jam-bot.com/pages/<page>` (normal URL, auth-gated per role)

Option B (clean separation):
- Portal: `pages.<tenant>.jam-bot.com` → separate nginx vhost
- Requires wildcard on subdomain of subdomain — works with Cloudflare but needs DNS `*.pages.jam-bot.com → same IP`

**Recommend Option A first** — zero new DNS/SSL work, ships faster. Can upgrade to Option B later if tenants want the cleaner URL for employees.

---

## Security Model

- Canvas portal is Clerk-authenticated — no anonymous access
- `canvas_viewer` role cannot access OVU routes (`/`, `/admin`, `/api/canvas/manage`, etc.)
- Page assignments are server-side only — client cannot self-grant access
- `is_public` pages are still public to unauthenticated web — portal doesn't change that
- Admin-locked pages (`is_locked=true`) respect existing lock — can still be assigned to viewers
- Tenant isolation: assignments file scoped to tenant, backend never cross-queries

---

## Dev Plan — Phases

### Phase 1 — Core (MVP)
1. `services/portal.py` — load/save assignments JSON
2. `GET /api/portal/pages` route — returns assigned pages for authenticated canvas_viewer
3. `employee-portal.html` canvas page — grid view
4. Canvas page middleware: add role-check for canvas_viewer access

### Phase 2 — Admin Management
5. `GET/POST /api/portal/employees` + `POST /api/portal/assign`
6. Admin panel Employees tab — list + assign pages
7. Clerk invite flow (email invite to Clerk org or via Clerk backend API)

### Phase 3 — Groups + Polish
8. Groups model in assignments JSON + group assignment API
9. Portal branding (tenant logo, color from ACTIVE-STYLE.md)
10. Employee portal mobile-responsive layout

---

## Open Questions (resolve before Phase 1 dev)

1. **Clerk invites** — do we use Clerk Organizations per tenant, or just per-user `publicMetadata` role? Organizations is cleaner for multi-user tenants but requires enabling Orgs in Clerk dashboard. Simpler: just set `jambot_role: canvas_viewer` in metadata on invite.

2. **Portal URL** — confirm Option A (`/portal`) vs Option B (`pages.<tenant>...`). Option A ships faster.

3. **Page thumbnails** — portal cards look better with a page screenshot/thumbnail. Do we auto-generate on page save, or skip for MVP?

4. **Employee count limit** — should we cap viewers per tenant at the billing tier? Or unlimited for now since Clerk handles auth and there's no per-seat cost on our end?

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `services/portal.py` | CREATE |
| `routes/portal.py` (or add to canvas.py) | CREATE |
| `default-pages/employee-portal.html` | CREATE |
| `server.py` | MODIFY — register portal blueprint |
| `services/canvas.py` | MODIFY — add canvas_viewer role check on page serve |
| `admin.html` (canvas section) | MODIFY — add Employees tab |
| `docs/jambot/canvas-employee-portal-spec.md` | this file |
