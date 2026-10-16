---
name: canvas-page-visibility
description: Make one of your OpenVoiceUI canvas pages PUBLIC (viewable without login) or PRIVATE again with one call to the canvas API. Only on an explicit user request. Use when the user asks to make a page public or shareable, or to make it private again.
---

# Making a canvas page public (or private again)

## Hard rule: never public on your own

Never set `is_public: true` automatically, while creating a page, or as part of a routine.
A page goes public only when the user explicitly asks, in a separate message, after the page
already exists. Before doing it, warn them: "This makes the page viewable by anyone with the
link, no login required."

Visibility only matters when the install requires login (Clerk + `CANVAS_REQUIRE_AUTH=true`).
Without login every page is already reachable by anyone who can reach the server.

Examples use `http://localhost:5001`; if you changed `PORT`, use that port. The
`X-Agent-Key` header carries `AGENT_API_KEY` from the environment; on a login-required install
that key is what authorizes you (set it in `.env`), and it is harmless otherwise.

## Make a page public

```bash
curl -s -X PATCH "http://localhost:5001/api/canvas/manifest/page/<PAGE_ID>" \
  -H "X-Agent-Key: ${AGENT_API_KEY:-}" \
  -H "Content-Type: application/json" \
  -d '{"is_public": true}'
```

`<PAGE_ID>` is the filename without `.html`. Success is HTTP 200 with `"status": "ok"` and the
page record showing `"is_public": true`. The value must be a real JSON boolean (`true`/`false`),
not `1` or `"true"` (those get a 400).

## Make a page private again

```bash
curl -s -X PATCH "http://localhost:5001/api/canvas/manifest/page/<PAGE_ID>" \
  -H "X-Agent-Key: ${AGENT_API_KEY:-}" \
  -H "Content-Type: application/json" \
  -d '{"is_public": false}'
```

Making a page private is always allowed, even on a locked page. If you realise a page exposes
something it should not, close it immediately.

## Errors you may see

| Response | Meaning | What to do |
|----------|---------|------------|
| `429` `publish_cooldown` | The page is less than 30 seconds old. | Wait `retry_after_seconds`, retry. |
| `403` with `is_locked` | An admin locked this page. | You cannot make it public. Tell the user an admin must unlock it (admin panel, canvas pages list). You can still edit its content. |
| `403` `admin_only` | You tried to change `is_locked`. | Only an admin can lock or unlock. |
| `401` `auth_required` | No accepted credential. | `AGENT_API_KEY` is unset or does not match the server's. |
| `404` | No such page id. | Check the id against `GET /api/canvas/manifest`. |

## The public link

Once public the page is at `https://<your-domain>/pages/<PAGE_ID>.html` with no login. Before
you tell the user a public link works, fetch it exactly as a stranger would:

```bash
curl -s -o /dev/null -w '%{http_code} %{url_effective}\n' -L 'https://<your-domain>/pages/<PAGE_ID>.html'
```

`200` with the page's own content means done. `401`/`403` means it is still private. `404`, a
wrong host, or a `localhost`/container path inside the link means the link is wrong.

Do not run this check on the owner's own private pages and treat the `401` as a problem: a
private page answering `401` to a signed-out request is exactly right. Give the owner the link;
they open it signed in.

## HTML never goes into uploads

`/uploads/` shows only inert media inline (images, audio, video, pdf); office files, CSV and zip
download as attachments. `.html`, `.svg`, `.js` and other types download as plain text with
`nosniff`, on purpose: uploaded HTML rendered in the app's origin could steal a viewer's session. So anything a person should read as
a page is a canvas page (write it into the canvas pages dir or `POST /api/canvas/pages`), and its
visibility is controlled here, not by moving it into uploads.

## If the canvas write path seems broken, measure before working around it

```bash
ls -ld /app/runtime/canvas-pages
touch /app/runtime/canvas-pages/.probe && ls -l /app/runtime/canvas-pages/.probe
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS http://localhost:5001/api/canvas/pages
```

`POST /api/canvas/pages` answers `405` to a GET because it is POST-only: a `405` means the route
exists. Probe with a tiny throwaway file, never by re-posting a large real payload.
