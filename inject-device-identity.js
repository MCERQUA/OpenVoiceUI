#!/usr/bin/env node
// inject-device-identity.js — Sync device identity between OpenVoiceUI and OpenClaw.
//
// Called by start.js AFTER containers are up but BEFORE the user opens the browser.
//
// Handles two scenarios:
//   1. Fresh install (no identity in volume): inject pre-paired identity
//   2. Reinstall (old identity in volume): read it, update paired.json to match
//
// Windows compatibility:
//   - MSYS_NO_PATHCONV=1 prevents Git Bash from mangling /container/paths
//   - docker cp avoids single-quote issues with cmd.exe
//   - No sh -c with single quotes (Windows doesn't support them)

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.local.yml";
const IDENTITY_FILE = "openclaw-data/pre-paired-device.json";
const PAIRED_FILE = "openclaw-data/devices/paired.json";
const CONTAINER_PATH = "/app/runtime/uploads/.device-identity.json";

// MSYS_NO_PATHCONV=1 prevents Git Bash on Windows from converting
// /app/runtime/... to E:/pinokio/bin/.../app/runtime/...
const EXEC_ENV = Object.assign({}, process.env, { MSYS_NO_PATHCONV: "1" });

function exec(cmd, opts) {
  return execSync(cmd, Object.assign(
    { encoding: "utf8", timeout: 15000, stdio: ["pipe", "pipe", "pipe"], env: EXEC_ENV },
    opts || {}
  )).trim();
}

function writePairedJson(deviceId, publicKeyPem) {
  // Derive base64url public key from PEM
  var pubPemLines = publicKeyPem.split("\n").filter(function(l) {
    return !l.startsWith("---") && l.trim();
  });
  var derBuf = Buffer.from(pubPemLines.join(""), "base64");
  // Ed25519 SPKI DER = 12 byte header + 32 byte raw key
  var rawPub = derBuf.slice(-32);
  var pubB64url = rawPub.toString("base64url");

  var nowMs = Date.now();
  var pairingToken = crypto.randomBytes(32).toString("hex");
  var paired = {};
  paired[deviceId] = {
    deviceId: deviceId,
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

  var pairedContent = JSON.stringify(paired, null, 2) + "\n";

  // Try direct write first (works when current user owns openclaw-data/devices/)
  try {
    fs.mkdirSync("openclaw-data/devices", { recursive: true });
    fs.writeFileSync(PAIRED_FILE, pairedContent);
    fs.writeFileSync("openclaw-data/devices/pending.json", "{}\n");
    return;
  } catch (e) {
    if (e.code !== "EACCES") throw e;
  }

  // Fallback: openclaw container created devices/ as root via bind mount.
  // Write via docker cp into the container — bind mount reflects it on host too.
  console.log("  devices/ is root-owned — writing via docker cp");
  var os = require("os");
  var tmpPaired = path.join(os.tmpdir(), "ovu-paired.json");
  var tmpPending = path.join(os.tmpdir(), "ovu-pending.json");
  fs.writeFileSync(tmpPaired, pairedContent);
  fs.writeFileSync(tmpPending, "{}\n");

  var cid = exec(COMPOSE + " ps -q openclaw");
  if (!cid) throw new Error("openclaw container not found for docker cp fallback");
  exec("docker cp " + JSON.stringify(tmpPaired) + " " + cid + ":/root/.openclaw/devices/paired.json");
  exec("docker cp " + JSON.stringify(tmpPending) + " " + cid + ":/root/.openclaw/devices/pending.json");

  try { fs.unlinkSync(tmpPaired); } catch (e2) { /* ignore */ }
  try { fs.unlinkSync(tmpPending); } catch (e2) { /* ignore */ }
}

// Step 1: Check if the container already has a device identity (from a previous run)
var containerIdentity = null;
try {
  var out = exec(COMPOSE + " exec -T openvoiceui cat " + CONTAINER_PATH);
  if (out && out.startsWith("{")) {
    containerIdentity = JSON.parse(out);
    if (!containerIdentity.deviceId) containerIdentity = null;
  }
} catch (e) {
  // File doesn't exist — fresh volume
}

if (containerIdentity) {
  // Scenario 2: Container already has an identity (Docker volume persisted it).
  // Update paired.json to match whatever the container has.
  console.log("  Found existing device identity: " + containerIdentity.deviceId.slice(0, 16) + "...");

  try {
    writePairedJson(containerIdentity.deviceId, containerIdentity.publicKeyPem);
    console.log("  Updated paired.json to match container identity");

    // Save locally so they stay in sync
    fs.writeFileSync(IDENTITY_FILE, JSON.stringify(containerIdentity, null, 2) + "\n");

    // No restart needed — openclaw-data/ is a bind mount so paired.json
    // changes are immediately visible inside the container. OpenClaw reads
    // paired.json on each WS connection attempt (getPairedDevice()), not
    // just at startup. DO NOT restart openclaw here — openvoiceui uses
    // network_mode: "service:openclaw" so restarting openclaw kills both.
    console.log("  Device is now paired (no restart needed — bind mount is live)");
  } catch (e) {
    console.log("  Warning: could not sync identity: " + e.message);
  }
} else {
  // Scenario 1: Fresh install — no identity in container yet.
  // Inject the pre-paired identity that setup-config.js generated.
  if (!fs.existsSync(IDENTITY_FILE)) {
    console.log("  No pre-paired device identity found — skipping");
    process.exit(0);
  }

  var identity = fs.readFileSync(IDENTITY_FILE, "utf8");

  try {
    // Get the container ID for docker cp (avoids shell quoting issues on Windows)
    var containerId = exec(COMPOSE + " ps -q openvoiceui");
    if (!containerId) throw new Error("openvoiceui container not found");

    // Ensure uploads dir exists (use double quotes, not single quotes — Windows compat)
    exec(COMPOSE + ' exec -T openvoiceui mkdir -p /app/runtime/uploads');

    // Write identity to a temp file and docker cp it in
    // docker cp avoids all shell quoting and stdin piping issues
    var tmpFile = path.join("openclaw-data", ".tmp-device-identity.json");
    fs.writeFileSync(tmpFile, identity);
    exec("docker cp " + JSON.stringify(tmpFile) + " " + containerId + ":" + CONTAINER_PATH);

    // Clean up temp file
    try { fs.unlinkSync(tmpFile); } catch (e) { /* ignore */ }

    var deviceId = JSON.parse(identity).deviceId || "unknown";
    console.log("  Injected pre-paired device identity: " + deviceId.slice(0, 16) + "...");
  } catch (e) {
    console.log("  Warning: Could not inject device identity: " + e.message.split("\n")[0]);
  }
}
