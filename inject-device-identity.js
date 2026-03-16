#!/usr/bin/env node
// inject-device-identity.js — Sync device identity between OpenVoiceUI and OpenClaw.
//
// Called by start.js AFTER containers are up but BEFORE the user opens the browser.
//
// Handles two scenarios:
//   1. Fresh install (no identity in container): inject the pre-paired identity
//   2. Reinstall (old identity in volume): read it and update paired.json to match
//
// The Docker named volume persists .device-identity.json across container recreates.
// If we always inject a new identity, it conflicts with the old one still in the volume.
// Instead, we sync: whatever identity the container has, paired.json must match.

const { execSync } = require("child_process");
const fs = require("fs");
const crypto = require("crypto");

const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml";
const IDENTITY_FILE = "openclaw-data/pre-paired-device.json";
const PAIRED_FILE = "openclaw-data/devices/paired.json";
const CONTAINER_PATH = "/app/runtime/uploads/.device-identity.json";

function exec(cmd, opts = {}) {
  return execSync(cmd, { encoding: "utf8", timeout: 15000, stdio: ["pipe", "pipe", "pipe"], ...opts }).trim();
}

// Step 1: Check if the container already has a device identity (from a previous run)
let containerIdentity = null;
try {
  const out = exec(`${COMPOSE} exec -T openvoiceui cat ${CONTAINER_PATH}`);
  if (out && out.startsWith("{")) {
    containerIdentity = JSON.parse(out);
    if (containerIdentity.deviceId) {
      console.log(`  Found existing device identity in container: ${containerIdentity.deviceId.slice(0, 16)}...`);
    } else {
      containerIdentity = null;
    }
  }
} catch (e) {
  // File doesn't exist — fresh volume
}

if (containerIdentity) {
  // Scenario 2: Container already has an identity (Docker volume persisted it).
  // Update paired.json to match whatever the container has.
  try {
    // Re-derive the base64url public key from PEM for paired.json
    const pubPemLines = containerIdentity.publicKeyPem.split("\n").filter(l => !l.startsWith("---") && l.trim());
    const derB64 = pubPemLines.join("");
    const derBuf = Buffer.from(derB64, "base64");
    // Ed25519 SPKI DER is 44 bytes: 12 byte header + 32 byte raw key
    const rawPub = derBuf.slice(-32);
    const pubB64url = rawPub.toString("base64url");

    const nowMs = Date.now();
    const pairingToken = crypto.randomBytes(32).toString("hex");
    const paired = {};
    paired[containerIdentity.deviceId] = {
      deviceId: containerIdentity.deviceId,
      publicKey: pubB64url,
      displayName: "pinokio-openvoiceui",
      platform: "linux",
      clientId: "cli",
      clientMode: "cli",
      role: "operator",
      roles: ["operator"],
      scopes: ["operator.read", "operator.write"],
      approvedScopes: ["operator.read", "operator.write"],
      tokens: {
        operator: {
          token: pairingToken,
          role: "operator",
          scopes: ["operator.read", "operator.write"],
          createdAtMs: nowMs,
        },
      },
      createdAtMs: nowMs,
      approvedAtMs: nowMs,
    };

    fs.mkdirSync("openclaw-data/devices", { recursive: true });
    fs.writeFileSync(PAIRED_FILE, JSON.stringify(paired, null, 2) + "\n");
    fs.writeFileSync("openclaw-data/devices/pending.json", "{}\n");
    console.log(`  Updated paired.json to match existing container identity`);

    // Also update the pre-paired file so they stay in sync
    fs.writeFileSync(IDENTITY_FILE, JSON.stringify(containerIdentity, null, 2) + "\n");

    // Restart openclaw so it picks up the new paired.json
    console.log(`  Restarting openclaw to load updated paired.json...`);
    try {
      exec(`${COMPOSE} restart openclaw`, { timeout: 30000 });
      console.log(`  OpenClaw restarted — device ${containerIdentity.deviceId.slice(0, 16)}... is now paired`);
    } catch (e) {
      console.log(`  Warning: openclaw restart failed — you may need to restart manually`);
    }
  } catch (e) {
    console.log(`  Warning: could not sync existing identity to paired.json: ${e.message}`);
  }
} else {
  // Scenario 1: Fresh install — no identity in container yet.
  // Inject the pre-paired identity that setup-config.js generated.
  if (!fs.existsSync(IDENTITY_FILE)) {
    console.log("  No pre-paired device identity found — skipping injection");
    console.log("  (Run install again to generate one)");
    process.exit(0);
  }

  const identity = fs.readFileSync(IDENTITY_FILE, "utf8");

  try {
    exec(
      `${COMPOSE} exec -T openvoiceui sh -c 'mkdir -p /app/runtime/uploads && cat > ${CONTAINER_PATH}'`,
      { input: identity, timeout: 15000 }
    );
    const deviceId = JSON.parse(identity).deviceId || "unknown";
    console.log(`  Injected pre-paired device identity: ${deviceId.slice(0, 16)}...`);
  } catch (e) {
    console.log("  Warning: Could not inject device identity:", e.message.split("\n")[0]);
  }
}
