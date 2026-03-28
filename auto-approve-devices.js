#!/usr/bin/env node
// auto-approve-devices.js — Approve all pending OpenClaw devices.
// Runs on the host, execs into the openclaw container to move pending → paired.
// Called by start.js after containers are healthy.
//
// Polls for up to 2 minutes (24 retries × 5s) because OpenVoiceUI doesn't
// connect to OpenClaw until the user opens the browser — which happens AFTER
// Pinokio shows the "Open" button. We need to wait for that.

const { execSync } = require("child_process");

const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.local.yml";
const MAX_ATTEMPTS = 24;  // 24 × 5s = 2 minutes
const DELAY_MS = 5000;

// MSYS_NO_PATHCONV=1 prevents Git Bash on Windows from converting /container/paths
const EXEC_ENV = Object.assign({}, process.env, { MSYS_NO_PATHCONV: "1" });

// Node one-liner that runs INSIDE the openclaw container
const script = `
const fs = require('fs');
try {
  const pendingPath = '/root/.openclaw/devices/pending.json';
  const pairedPath = '/root/.openclaw/devices/paired.json';
  let pending = {};
  let paired = {};
  try { pending = JSON.parse(fs.readFileSync(pendingPath, 'utf8')); } catch(e) {}
  try { paired = JSON.parse(fs.readFileSync(pairedPath, 'utf8')); } catch(e) {}
  const pairedCount = Object.keys(paired).length;
  let count = 0;
  for (const entry of Object.values(pending)) {
    if (entry.deviceId && entry.publicKey) {
      paired[entry.deviceId] = {
        publicKey: entry.publicKey,
        name: entry.name || 'auto-approved',
        role: entry.role || 'operator',
        roles: entry.roles || ['operator'],
        scopes: entry.scopes || ['operator.read', 'operator.write'],
        paired: true,
        pairedAt: new Date().toISOString(),
        autoApproved: true,
      };
      count++;
    }
  }
  if (count > 0) {
    fs.writeFileSync(pairedPath, JSON.stringify(paired, null, 2));
    fs.writeFileSync(pendingPath, '{}');
    console.log('APPROVED:' + count);
  } else if (pairedCount > 0) {
    console.log('ALREADY_PAIRED:' + pairedCount);
  } else {
    console.log('APPROVED:0');
  }
} catch(e) {
  console.log('ERROR:' + e.message);
}
`.replace(/\n/g, " ").trim();

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function run() {
  console.log("  Waiting for browser to connect (up to 2 minutes)...");

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const result = execSync(
        `${COMPOSE} exec -T openclaw node -e "${script.replace(/"/g, '\\"')}"`,
        { encoding: "utf8", timeout: 15000, env: EXEC_ENV }
      ).trim();

      // New device(s) approved
      const approvedMatch = result.match(/APPROVED:(\d+)/);
      if (approvedMatch && parseInt(approvedMatch[1]) > 0) {
        console.log(`  Auto-approved ${approvedMatch[1]} device(s) — ready to use!`);
        return;
      }

      // Device already paired from a previous session
      const pairedMatch = result.match(/ALREADY_PAIRED:(\d+)/);
      if (pairedMatch && parseInt(pairedMatch[1]) > 0) {
        console.log(`  Device already paired (${pairedMatch[1]} device(s)) — ready to use!`);
        return;
      }

      // Nothing yet — keep polling
      if (attempt < MAX_ATTEMPTS) {
        // Only log every 4th attempt to avoid spam
        if (attempt % 4 === 0) {
          const remaining = Math.round((MAX_ATTEMPTS - attempt) * DELAY_MS / 1000);
          console.log(`  Still waiting for browser connection... (${remaining}s remaining)`);
        }
        await sleep(DELAY_MS);
      } else {
        console.log("  Timeout: No device connected after 2 minutes.");
        console.log("  If you see NOT_PAIRED errors, open the browser and restart the app.");
      }
    } catch (e) {
      if (attempt < MAX_ATTEMPTS) {
        // Container might still be starting — retry silently
        await sleep(DELAY_MS);
      } else {
        console.log("  Device auto-approve failed:", e.message.split("\n")[0]);
      }
    }
  }
}

run();
