#!/usr/bin/env node
// inject-device-identity.js — Inject pre-paired device identity into OpenVoiceUI container.
//
// Called by start.js AFTER containers are up but BEFORE the user opens the browser.
// This pipes the identity file (generated during install by setup-config.js) into
// the OpenVoiceUI container via docker exec, avoiding the Windows named-volume
// bind-mount issue that broke the old file-mount approach.
//
// The identity is also pre-registered in openclaw-data/devices/paired.json (which
// is a bind mount into the openclaw container), so OpenClaw already trusts this
// device. No auto-approval or browser interaction needed.

const { execSync } = require("child_process");
const fs = require("fs");

const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml";
const IDENTITY_FILE = "openclaw-data/pre-paired-device.json";
const CONTAINER_PATH = "/app/runtime/uploads/.device-identity.json";

if (!fs.existsSync(IDENTITY_FILE)) {
  console.log("  No pre-paired device identity found — skipping injection");
  console.log("  (Run install again to generate one, or device will need manual approval)");
  process.exit(0);
}

const identity = fs.readFileSync(IDENTITY_FILE, "utf8");

try {
  // Create the uploads dir (may not exist on first run) and write the identity file.
  // Uses stdin pipe to avoid shell escaping issues with JSON content.
  execSync(
    `${COMPOSE} exec -T openvoiceui sh -c 'mkdir -p /app/runtime/uploads && cat > ${CONTAINER_PATH}'`,
    { input: identity, timeout: 15000, stdio: ["pipe", "pipe", "pipe"] }
  );
  const deviceId = JSON.parse(identity).deviceId || "unknown";
  console.log(`  Injected device identity into OpenVoiceUI (${deviceId.slice(0, 16)}...)`);
} catch (e) {
  console.log("  Warning: Could not inject device identity:", e.message.split("\n")[0]);
  console.log("  Device will need to be approved manually or via auto-approve on first browser connect.");
}
