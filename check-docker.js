// check-docker.js — Pre-flight Docker check for Pinokio install.
// Verifies Docker CLI, daemon, and Compose are available.
// Handles Windows PATH issues and gives actionable error messages.

const { execSync } = require("child_process");
const os = require("os");
const fs = require("fs");

function exec(cmd, timeoutMs) {
  try {
    return execSync(cmd, { encoding: "utf8", timeout: timeoutMs || 15000 }).trim();
  } catch {
    return null;
  }
}

function execDetail(cmd, timeoutMs) {
  try {
    return {
      ok: true,
      out: execSync(cmd, { encoding: "utf8", timeout: timeoutMs || 15000 }).trim(),
    };
  } catch (e) {
    return {
      ok: false,
      err: ((e.stderr || "") + " " + (e.message || "")).trim(),
    };
  }
}

// --- Windows: Docker Desktop may not be on PATH in Pinokio's conda shell ---
if (os.platform() === "win32" && !exec("docker --version")) {
  const candidates = [
    (process.env.ProgramFiles || "C:\\Program Files") +
      "\\Docker\\Docker\\resources\\bin",
    (process.env.LOCALAPPDATA || "") +
      "\\Programs\\Docker\\Docker\\resources\\bin",
  ];
  for (const dir of candidates) {
    try {
      fs.accessSync(dir);
      process.env.PATH = dir + ";" + (process.env.PATH || "");
      console.log("  Found Docker at: " + dir);
      break;
    } catch {
      // not found here, try next
    }
  }
}

// --- Check 1: Docker CLI installed ---
const ver = exec("docker --version");
if (!ver) {
  console.error("\n  ERROR: Docker is not installed.\n");
  console.error("  OpenVoiceUI runs in Docker containers.");
  console.error(
    "  Install Docker Desktop: https://docker.com/products/docker-desktop/\n"
  );
  process.exit(1);
}
console.log("  " + ver);

// --- Check 2: Docker daemon running ---
const info = execDetail("docker info", 30000);
if (!info.ok) {
  if (
    info.err.includes("permission denied") ||
    info.err.includes("Permission denied")
  ) {
    console.error("\n  ERROR: Permission denied accessing Docker.\n");
    if (os.platform() === "linux") {
      console.error("  Add your user to the docker group:");
      console.error("    sudo usermod -aG docker $USER");
      console.error("  Then log out and log back in.\n");
    } else {
      console.error("  Try running Pinokio as administrator.\n");
    }
  } else {
    console.error("\n  ERROR: Docker is installed but not running.\n");
    if (os.platform() === "win32" || os.platform() === "darwin") {
      console.error(
        "  Open Docker Desktop and wait for it to finish starting."
      );
    } else {
      console.error("  Start Docker: sudo systemctl start docker");
    }
    console.error("  Then click Install again.\n");
  }
  process.exit(1);
}

// --- Check 3: Docker Compose v2 ---
const compose = exec("docker compose version");
if (!compose) {
  console.error("\n  ERROR: Docker Compose v2 is not available.\n");
  console.error("  It ships with Docker Desktop — update your Docker Desktop");
  console.error(
    "  or install the plugin: https://docs.docker.com/compose/install/\n"
  );
  process.exit(1);
}
console.log("  " + compose);

console.log("\n  Docker is ready!\n");
