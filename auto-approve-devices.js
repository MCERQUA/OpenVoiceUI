#!/usr/bin/env node
// auto-approve-devices.js — Approve all pending OpenClaw devices.
// Runs on the host, execs into the openclaw container to move pending → paired.
// Called by start.js after containers are healthy.

const { execSync } = require("child_process");

const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml";

// Node one-liner that runs INSIDE the openclaw container
const script = `
const fs = require('fs');
try {
  const pending = JSON.parse(fs.readFileSync('/root/.openclaw/devices/pending.json', 'utf8'));
  const paired = JSON.parse(fs.readFileSync('/root/.openclaw/devices/paired.json', 'utf8'));
  let count = 0;
  for (const entry of Object.values(pending)) {
    if (entry.deviceId && entry.publicKey) {
      paired[entry.deviceId] = {
        publicKey: entry.publicKey,
        name: entry.name || 'auto-approved',
        paired: true,
        pairedAt: new Date().toISOString(),
        autoApproved: true,
      };
      count++;
    }
  }
  if (count > 0) {
    fs.writeFileSync('/root/.openclaw/devices/paired.json', JSON.stringify(paired, null, 2));
    fs.writeFileSync('/root/.openclaw/devices/pending.json', '{}');
    console.log('Auto-approved ' + count + ' device(s)');
  } else {
    console.log('No pending devices to approve');
  }
} catch(e) {
  console.log('Device approval skipped: ' + e.message);
}
`.replace(/\n/g, " ").trim();

try {
  const result = execSync(
    `${COMPOSE} exec -T openclaw node -e "${script.replace(/"/g, '\\"')}"`,
    { encoding: "utf8", timeout: 15000 }
  );
  console.log(result.trim());
} catch (e) {
  console.log("Device auto-approve failed (containers may still be starting):", e.message.split("\n")[0]);
}
