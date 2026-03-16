#!/usr/bin/env node
// auto-approve-devices.js — Approve all pending OpenClaw devices.
// Runs on the host, execs into the openclaw container to move pending → paired.
// Called by start.js after containers are healthy.
// Retries up to 3 times with 5s delay to wait for OpenVoiceUI's first connect attempt.

const { execSync } = require("child_process");

const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml";
const MAX_ATTEMPTS = 3;
const DELAY_MS = 5000;

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
    fs.writeFileSync(pairedPath, JSON.stringify(paired, null, 2));
    fs.writeFileSync(pendingPath, '{}');
    console.log('APPROVED:' + count);
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
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const result = execSync(
        `${COMPOSE} exec -T openclaw node -e "${script.replace(/"/g, '\\"')}"`,
        { encoding: "utf8", timeout: 15000 }
      ).trim();

      const match = result.match(/APPROVED:(\d+)/);
      if (match && parseInt(match[1]) > 0) {
        console.log(`  Auto-approved ${match[1]} device(s) (attempt ${attempt})`);
        return;
      }

      if (attempt < MAX_ATTEMPTS) {
        console.log(`  No pending devices yet (attempt ${attempt}/${MAX_ATTEMPTS}), waiting ${DELAY_MS/1000}s...`);
        await sleep(DELAY_MS);
      } else {
        console.log("  No pending devices after all attempts (device may already be paired)");
      }
    } catch (e) {
      if (attempt < MAX_ATTEMPTS) {
        console.log(`  Auto-approve attempt ${attempt} failed, retrying...`);
        await sleep(DELAY_MS);
      } else {
        console.log("  Device auto-approve failed:", e.message.split("\n")[0]);
      }
    }
  }
}

run();
