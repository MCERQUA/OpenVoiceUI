# Booking Plugin for OpenVoiceUI

Adds appointment booking to client websites and the voice agent, backed by a
**shared [Cal.diy](https://cal.com) instance** at `cal.jam-bot.com` (Cal.com
lineage — REST API v2, Bearer auth, HMAC-signed webhooks).

## What This Plugin Provides

- **Booking canvas page** (`pages/booking.html`) — list upcoming bookings, cancel,
  and block out a whole day. All state is loaded from the server API; nothing is
  cached in the browser.
- **Setup wizard** (`pages/booking-setup.html`) — enter the per-tenant Cal.diy API
  key + username, test the connection, and copy the 3-line website embed snippet.
- **Backend API** (`routes/booking.py`, blueprint `booking_bp`, prefix `/api/booking`) —
  proxies the agent + canvas to Cal.diy so the API key stays server-side, plus a
  **signed webhook receiver** that fans booking events out to InkBox SMS + Twenty CRM.
- **Server-side config** — settings stored at `RUNTIME_DIR/booking-config.json`
  (bind-mounted volume, survives container restarts). Never localStorage.

## Architecture

One shared Cal.diy instance, **one user account per client**. The OVU plugin is the
bridge: voice agent and website both go through `/api/booking/*`, and the webhook
receiver is the single place that notifies the client and records the booking in CRM.

Full spec: `docs/jambot/booking-system-cal-diy.md`.

> **Cal.diy is not deployed yet.** The plugin degrades gracefully — `/api/booking/status`
> reports `configured:false` / `reachable:false`, and proxy endpoints return a clear
> 503 "not configured" instead of erroring.

## Configuration

Resolved per-value as: **config file** (set via the wizard) → **env var** → default.

| Setting | Env var | Default | Notes |
|---------|---------|---------|-------|
| API base URL | `CAL_API_URL` | `https://cal.jam-bot.com` | Shared instance |
| API key | `CAL_API_KEY` | — | Per-tenant; usually set via the wizard, not env |
| Username | `CAL_USERNAME` | — | The tenant's Cal.diy slug |
| Webhook secret | `CAL_WEBHOOK_SECRET` | — | HMAC key for `x-cal-signature-256` verification |
| API version | `CAL_API_VERSION` | `2024-08-13` | Cal.com v2 version header |
| Timeout (s) | `CAL_API_TIMEOUT` | `20` | |

For the **agent** to use booking, the plugin API is reachable in-container at
`http://localhost:<OVU_PORT>/api/booking/*`; the agent never needs the raw Cal.diy key.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/booking/status` | GET | Configured? reachable? authenticated? |
| `/api/booking/slots` | GET | Available slots (`eventTypeId`, `start`, `end`, …) |
| `/api/booking/bookings` | GET | List bookings (`status=upcoming` default) |
| `/api/booking/bookings` | POST | Create a booking |
| `/api/booking/bookings/<uid>/cancel` | POST | Cancel a booking |
| `/api/booking/block` | POST | Reserve/block slots for a day |
| `/api/booking/webhook` | POST | Signed Cal.diy webhook receiver (HMAC verified) |
| `/api/booking/config` | GET | Read saved config (secrets masked) |
| `/api/booking/config` | POST | Save config server-side |

## Webhook Bridge

`POST /api/booking/webhook`:
1. Reads the raw body and the `x-cal-signature-256` header.
2. If a webhook secret is configured, verifies `HMAC-SHA256(body, secret)` and
   rejects mismatches with 401. (No secret yet → accepts + logs as `verified:false`
   so wiring can be tested before deploy.)
3. Appends the event to a per-tenant JSONL at `RUNTIME_DIR/booking-events.jsonl`.
4. For `BOOKING_CREATED` / `BOOKING_CANCELLED` / `BOOKING_RESCHEDULED`, calls
   `notify_sms()` and `crm_record()`.

`notify_sms()` and `crm_record()` are **stubs with TODO markers** — InkBox SMS and
Twenty CRM wiring lands when the service is deployed. Customer-facing confirmations
are Cal.diy's job; the bridge only alerts the *client* and logs to CRM.

## Installation

This plugin auto-loads from the manifest scan — copy the `booking/` directory into
`/app/plugins/` (already here under the OVU source tree) and restart the OVU container.
On load the blueprint registers at `/api/booking/*` and the two pages are copied into
the canvas-pages runtime and registered in the manifest.

## Deploy prerequisites (not yet met)

- Cal.diy deployed at `cal.jam-bot.com` (Docker, ~4GB) + DNS + nginx + SMTP.
- One Cal.diy user account per client, with an API key and a webhook pointed at
  this plugin's `/api/booking/webhook` URL.

See `docs/jambot/booking-system-cal-diy.md` for the full deploy plan.
