#!/usr/bin/env node
// diagnose.js — Check every part of the OpenVoiceUI + OpenClaw connection chain.
// Run this after containers are up to find exactly where things break.
//
// Usage: node diagnose.js

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.local.yml";
let failures = 0;

function check(name, fn) {
  try {
    const result = fn();
    console.log(`  ✅ ${name}${result ? ': ' + result : ''}`);
    return true;
  } catch (e) {
    console.log(`  ❌ ${name}: ${e.message}`);
    failures++;
    return false;
  }
}

function exec(cmd, opts = {}) {
  return execSync(cmd, { encoding: "utf8", timeout: 15000, ...opts }).trim();
}

console.log("\n=== OpenVoiceUI Pinokio Diagnostics ===\n");

// --- 1. Config files exist ---
console.log("1. Config files:");
check("openclaw.json exists", () => {
  if (!fs.existsSync("openclaw-data/openclaw.json")) throw new Error("MISSING — run install first");
  return "ok";
});
check(".env exists", () => {
  if (!fs.existsSync(".env")) throw new Error("MISSING — run install first");
  return "ok";
});
check("auth-profiles.json exists", () => {
  const p = "openclaw-data/agents/main/agent/auth-profiles.json";
  if (!fs.existsSync(p)) throw new Error("MISSING");
  const data = JSON.parse(fs.readFileSync(p, "utf8"));
  return `${Object.keys(data).length} provider(s)`;
});
check("devices/paired.json exists", () => {
  const p = "openclaw-data/devices/paired.json";
  if (!fs.existsSync(p)) throw new Error("MISSING — device pairing will fail");
  const data = JSON.parse(fs.readFileSync(p, "utf8"));
  return `${Object.keys(data).length} paired device(s)`;
});
check("pre-paired-device.json exists", () => {
  if (!fs.existsSync("openclaw-data/pre-paired-device.json")) throw new Error("MISSING — inject will fail");
  return "ok";
});

// --- 2. Auth token consistency ---
console.log("\n2. Auth token match:");
check("tokens match between .env and openclaw.json", () => {
  const env = fs.readFileSync(".env", "utf8");
  const tokenMatch = env.match(/CLAWDBOT_AUTH_TOKEN=(.+)/);
  if (!tokenMatch) throw new Error(".env missing CLAWDBOT_AUTH_TOKEN");
  const envToken = tokenMatch[1].trim();

  const config = JSON.parse(fs.readFileSync("openclaw-data/openclaw.json", "utf8"));
  const configToken = config?.gateway?.auth?.token;
  if (!configToken) throw new Error("openclaw.json missing gateway.auth.token");

  if (envToken !== configToken) throw new Error(`MISMATCH! .env="${envToken.slice(0,8)}..." vs config="${configToken.slice(0,8)}..."`);
  return `both = ${envToken.slice(0, 8)}...`;
});

// --- 3. OpenClaw config validation ---
console.log("\n3. OpenClaw config:");
check("openclaw.json structure", () => {
  const config = JSON.parse(fs.readFileSync("openclaw-data/openclaw.json", "utf8"));
  const issues = [];
  if (!config.gateway) issues.push("missing gateway");
  if (!config.agents) issues.push("missing agents");
  if (!config.gateway?.auth?.token) issues.push("missing gateway.auth.token");
  if (!config.gateway?.controlUi?.dangerouslyDisableDeviceAuth) issues.push("missing dangerouslyDisableDeviceAuth");
  if (!config.agents?.defaults?.thinkingDefault) issues.push("missing thinkingDefault");
  if (issues.length) throw new Error(issues.join(", "));
  const model = config.agents.defaults.model || "(auto-select from available providers)";
  return `model=${model}`;
});

// --- 4. Device identity consistency ---
console.log("\n4. Device identity:");
check("pre-paired identity matches paired.json", () => {
  const identity = JSON.parse(fs.readFileSync("openclaw-data/pre-paired-device.json", "utf8"));
  const paired = JSON.parse(fs.readFileSync("openclaw-data/devices/paired.json", "utf8"));
  if (!paired[identity.deviceId]) throw new Error(`deviceId ${identity.deviceId.slice(0,16)}... NOT in paired.json`);
  return `deviceId=${identity.deviceId.slice(0, 16)}... is paired`;
});

// --- 5. Containers running ---
console.log("\n5. Containers:");
check("openclaw container running", () => {
  const out = exec(`${COMPOSE} ps --format json openclaw 2>/dev/null || echo ""`);
  if (!out || out.includes('"State":"exited"') || !out.includes('"State":"running"'))
    throw new Error("NOT RUNNING");
  return "running";
});
check("openvoiceui container running", () => {
  const out = exec(`${COMPOSE} ps --format json openvoiceui 2>/dev/null || echo ""`);
  if (!out || out.includes('"State":"exited"') || !out.includes('"State":"running"'))
    throw new Error("NOT RUNNING");
  return "running";
});

// --- 6. Gateway health ---
console.log("\n6. Gateway health:");
check("openclaw gateway responding on 18791", () => {
  const out = exec(`${COMPOSE} exec -T openclaw node -e "const h=require('http');h.get('http://localhost:18791',r=>{console.log(r.statusCode);process.exit(0)}).on('error',e=>{console.log('ERROR:'+e.message);process.exit(1)})"`);
  if (out.includes("ERROR")) throw new Error(out);
  return `HTTP ${out}`;
});

// --- 7. OpenVoiceUI health ---
console.log("\n7. OpenVoiceUI health:");
check("flask app responding on 5001", () => {
  const port = (fs.readFileSync(".env", "utf8").match(/PORT=(\d+)/) || [, "5001"])[1];
  const out = exec(`${COMPOSE} exec -T openvoiceui python3 -c "import urllib.request; r=urllib.request.urlopen('http://localhost:${port}/health/ready',timeout=5); print(r.status)"`);
  if (!out.includes("200")) throw new Error(`got ${out}`);
  return `HTTP ${out}`;
});

// --- 8. Device identity in container ---
console.log("\n8. Device identity injection:");
check("identity file in openvoiceui container", () => {
  const out = exec(`${COMPOSE} exec -T openvoiceui sh -c 'cat /app/runtime/uploads/.device-identity.json 2>/dev/null || echo MISSING'`);
  if (out === "MISSING") throw new Error("NOT INJECTED — inject-device-identity.js may have failed");
  const data = JSON.parse(out);
  return `deviceId=${data.deviceId.slice(0, 16)}...`;
});
check("container identity matches paired.json", () => {
  const containerOut = exec(`${COMPOSE} exec -T openvoiceui sh -c 'cat /app/runtime/uploads/.device-identity.json 2>/dev/null || echo "{}"'`);
  const containerIdentity = JSON.parse(containerOut);
  if (!containerIdentity.deviceId) throw new Error("no identity in container");

  const paired = JSON.parse(fs.readFileSync("openclaw-data/devices/paired.json", "utf8"));
  if (!paired[containerIdentity.deviceId]) throw new Error(`container deviceId ${containerIdentity.deviceId.slice(0,16)}... NOT in paired.json`);
  return "matched";
});

// --- 9. Gateway connection test ---
console.log("\n9. Gateway connection test:");
check("openvoiceui can reach openclaw gateway", () => {
  const out = exec(`${COMPOSE} exec -T openvoiceui python3 -c "
import asyncio, websockets, json, os
async def test():
    url = os.getenv('CLAWDBOT_GATEWAY_URL', 'ws://127.0.0.1:18791')
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if 'challenge' in str(msg.get('event','')):
                print('CHALLENGE_OK')
            else:
                print('UNEXPECTED:' + json.dumps(msg)[:100])
    except Exception as e:
        print('FAIL:' + str(e))
asyncio.run(test())
"`, { timeout: 20000 });
  if (out.includes("FAIL")) throw new Error(out);
  if (!out.includes("CHALLENGE_OK")) throw new Error(`unexpected: ${out}`);
  return "received challenge";
});

// --- 10. Recent logs ---
console.log("\n10. Recent container logs (last 20 lines each):");
try {
  console.log("\n  --- openclaw logs ---");
  const clawLogs = exec(`${COMPOSE} logs --tail=20 openclaw 2>&1`);
  console.log("  " + clawLogs.split("\n").join("\n  "));
} catch (e) {
  console.log("  (could not read openclaw logs)");
}
try {
  console.log("\n  --- openvoiceui logs ---");
  const ovuLogs = exec(`${COMPOSE} logs --tail=20 openvoiceui 2>&1`);
  console.log("  " + ovuLogs.split("\n").join("\n  "));
} catch (e) {
  console.log("  (could not read openvoiceui logs)");
}

// --- Summary ---
console.log(`\n=== Summary: ${failures === 0 ? 'All checks passed ✅' : `${failures} failure(s) ❌`} ===\n`);
if (failures > 0) {
  console.log("Fix the ❌ items above. The first failure is usually the root cause.\n");
}
