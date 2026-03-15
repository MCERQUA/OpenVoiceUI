# Gateway Device Pairing Fix

**Date:** 2026-03-15
**Issue:** Agent fails to connect to OpenClaw gateway with `NOT_PAIRED` error
**Status:** Fixed

## Symptom

After a clean install, the OpenVoiceUI container continuously fails to connect
to the OpenClaw gateway with:

```
Gateway auth failed: {'code': 'NOT_PAIRED', 'message': 'pairing required',
  'details': {'code': 'PAIRING_REQUIRED', ...}}
```

The error repeats every ~60 seconds as the exponential backoff retries exhaust,
preventing all voice conversations from working.

## Root Cause

**Public key format mismatch in the pre-paired device entry.**

The OpenClaw gateway handshake compares the device's public key from the
WebSocket connect request against the stored entry in `devices/paired.json`:

```javascript
// gateway-cli (openclaw source, line ~22808)
const paired = await getPairedDevice(device.id);
if (!(paired?.publicKey === devicePublicKey)) {
    if (!await requirePairing("not-paired")) return;
}
```

The Python client (`services/gateways/openclaw.py:121`) sends the public key as
**base64url of raw Ed25519 bytes** (e.g., `"ZVWsYN3DT9WfQyKO5i9YN..."`).

The old `setup-config.js` wrote the public key in **PEM format**
(`"-----BEGIN PUBLIC KEY-----\n..."`). These never match, so the gateway always
triggers the pairing flow.

Additionally, the merge logic for pending pairing requests uses AND for the
`silent` flag:

```javascript
silent: Boolean(existing.silent && incoming.silent)
```

Once a non-silent pending request exists, all subsequent attempts inherit
`silent: false`, permanently blocking auto-approval. The pending request
refreshes its `ts` on every attempt, preventing TTL expiry.

### Missing fields

The old paired entry also lacked required fields (`deviceId`, `role`, `roles`,
`scopes`, `approvedScopes`, `tokens`) that the gateway uses for authorization
after the initial public key check passes.

## Fix

Changed `setup-config.js` (section 4: "Pre-pair device identity") to:

1. **Write the public key in base64url format** instead of PEM:
   ```javascript
   const pubB64url = rawPub.toString("base64url");
   ```

2. **Include all required fields** matching the format that
   `approveDevicePairing()` writes:
   - `deviceId`, `publicKey` (base64url), `displayName`
   - `platform`, `clientId`, `clientMode`
   - `role`, `roles`, `scopes`, `approvedScopes`
   - `tokens` with a pre-generated operator token
   - `createdAtMs`, `approvedAtMs`

3. **Clear `pending.json`** to remove stale pending requests that would block
   silent auto-approval via the AND merge logic.

## Files Changed

- `setup-config.js` — Section 4: Pre-pair device identity

## For Clean Installs

The fix is entirely in `setup-config.js`, which runs during `install.js` step 4.
No changes needed to Docker images, `openclaw.py`, or `docker-compose.yml`.

After a clean install:
1. `install.js` collects API keys and runs `setup-config.js`
2. `setup-config.js` generates the device keypair, writes `paired.json` in the
   correct format, and clears `pending.json`
3. `start.js` starts containers — the gateway reads `paired.json` and recognizes
   the device immediately

## Verification

After applying the fix and restarting containers:
- No `NOT_PAIRED` errors in `docker logs openvoiceuigit-openvoiceui-1`
- Conversation API responds with LLM output (metrics show `llm=92ms`)
- Gateway accepts the WS connection without requiring manual approval

## Known Remaining Issue

The `supertonic` hostname cannot be resolved from the `openvoiceui` container
because `network_mode: "service:openclaw"` in `docker-compose.yml` bypasses
Docker's service DNS. Supertonic TTS falls back to a local model (which may
fail if voice style files are missing). This is a separate networking issue.
