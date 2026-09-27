# One-click connectors

Admin → **Connectors** is a card per outside service with one **Connect** button (OAuth). The client signs in with the service and approves access. Nobody finds or pastes a key. Admin → **Connections**, the key-pasting list, stays as the fallback.

## What is built in

| Connector id | Service | What connecting does | Env for the platform app |
|---|---|---|---|
| `supabase_connect` | Supabase | lists the orgs and projects the user granted; the user picks the project their app uses; fills the existing `supabase` credential (`url`, `anon_key`, `service_role_key`) from the Management API | `SUPABASE_OAUTH_CLIENT_ID` / `_SECRET` |
| `github` | GitHub | stores a token with `repo read:user` for a builder | `GITHUB_OAUTH_CLIENT_ID` / `_SECRET` |
| `netlify_connect` | Netlify | stores a token for sites, deploys and env vars (Netlify has no scopes) | `NETLIFY_OAUTH_CLIENT_ID` / `_SECRET` |

The entries live in `config/connectors-catalog.json` and are merged under the operator's `platform-credentials.json`, which wins on any id it also defines. Each card stays disabled ("Not available yet") until both env vars for its provider are set. The existing OAuth entries (Google, Facebook, Instagram, QuickBooks) show up in the same view.

## Supabase flow

1. **Connect**: authorize at `https://api.supabase.com/v1/oauth/authorize` with PKCE S256. No `scope` parameter is sent, because Supabase scopes are set on the app itself.
2. **Callback**: exchange at `POST https://api.supabase.com/v1/oauth/token`. The body is form-encoded, the client id and secret go in an **HTTP Basic** header, and the PKCE `code_verifier` is included. Refresh uses the same endpoint with `grant_type=refresh_token`.
3. **Picker**: `GET /v1/organizations`, `GET /v1/projects` (plus `GET /v1/profile` for the email, best effort). If the currently saved Project URL points to a project outside the connected org, the picker says so in plain words. That is the "another account owns your app's project" case.
4. **Fill**: `GET /v1/projects/{ref}/api-keys?reveal=true`. The legacy `anon` + `service_role` JWT keys are used first. If the project rejects them, the new `sb_publishable_` + `sb_secret_` keys are used. The keys are validated (below) before saving, and the save goes through `set_credential`, so they sync to `.openclaw.env` and openclaw restarts exactly as with a paste.
5. **Disconnect**: the refresh token is revoked with `POST /v1/oauth/revoke`, then the token file is moved aside to `.revoked`. It is never deleted. The filled project keys stay saved.

Docs: <https://supabase.com/docs/guides/integrations/build-a-supabase-oauth-integration>, scopes: <https://supabase.com/docs/guides/integrations/build-a-supabase-oauth-integration/oauth-scopes>, spec: <https://api.supabase.com/api/v1-json>.

## Validate before save (paste path)

Every save of the `supabase` credential is checked against the project in box 1. The check has three verdicts, never two:

| Check | Verdict |
|---|---|
| legacy JWT key whose `ref` differs from the URL's project ref | **fail**, offline, names both projects |
| secret key in the anon box, or publishable key in the service box | **fail**, offline |
| expired JWT | **fail** |
| service key: `GET <url>/rest/v1/` with `apikey` → 200 | **pass** |
| anon key: `GET <url>/auth/v1/settings` with `apikey` → 200 | **pass** |
| 401/403 with "Invalid API key" | **fail**: "different project, or deleted/rotated" |
| unreachable, timeout, 5xx/540, anything else | **cannot_tell**, saved but marked unverified |

A **fail** is not saved. The response is 422 and the UI offers "Save anyway". Every verdict is stored next to the values, together with a fingerprint of those values. The card shows *Working* / *Rejected by provider* / *Saved, not verified* / *Saved, not checked yet*, and never plain "Connected", for a credential that has a validator. A verdict whose fingerprint no longer matches the saved values counts as unchecked. The **Test** button runs the same check.

## Redirect URIs: the relay

Each tenant has its own domain, and not every provider will take one redirect URI per tenant:

| Provider | Callback URLs per app (measured in their docs, 2026-09-27) |
|---|---|
| Supabase | several (dashboard form "Add URL"), https only. Supabase's own docs recommend a state-based relay for dynamic URLs |
| GitHub OAuth App | up to 10. Optional subdomain wildcard matching, which GitHub warns against |
| Netlify | one "Redirect URI" field |

Entries with `oauth.redirect: "relay"` therefore use ONE platform callback when `OAUTH_RELAY_BASE_URL` is set:

```
<OAUTH_RELAY_BASE_URL>/api/vault/oauth/relay/<provider>
```

The relay is public, like the callback, and serves GET only. It reads the tenant domain `d` from `state`, checks it against `OAUTH_RELAY_ALLOWED_DOMAINS` (`.jam-bot.com` = any subdomain, and an empty list refuses everything), and sends a 302 to `https://<d>/api/vault/oauth/callback/<provider>` with `code` and `state` untouched. It reads no vault and exchanges nothing. The tenant callback still verifies its own single-use state nonce (F-6). The PKCE verifier never leaves the tenant, so a code delivered to the wrong place cannot be redeemed. The same `redirect_uri` is used at authorize and at exchange. When `OAUTH_RELAY_BASE_URL` is empty, the entry falls back to the per-tenant callback.

Both relay env vars must be identical on every instance, because they are read when the connect URL is built and again when the code is exchanged.

## Also fixed on the way

- Token refresh read only the legacy `platform-oauth.json`, so every env-configured provider (Google included) failed to refresh after its first access token expired. It now uses the same app-credential resolution as authorize/exchange.
- The expiry check refreshed only after the token had been dead for 60 s. It now refreshes 60 s before expiry.
- Tokens without `refresh_token` or `expires_in` (Netlify) read as disconnected. They no longer invent a 1 h expiry.
- GitHub's 200 + `{"error": ...}` token response is treated as a failure, and `Accept: application/json` is sent on every token request.

## Files

`services/connectors.py` (Supabase client, validator, PKCE, relay) · `services/vault.py` (catalog merge, OAuth plumbing, verdict storage) · `routes/vault.py` (`/api/vault/connectors*`, `/api/vault/oauth/relay/<provider>`, validate-before-save) · `config/connectors-catalog.json` · `src/admin.html` (Connectors panel) · `tests/test_vault_connectors.py`.
