#!/usr/bin/env node
// OpenVoiceUI CLI — zero-dependency entry point
// Usage: openvoiceui <command> [options]

const { execSync, spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const VERSION = require("../package.json").version;
const PROJECT_DIR = path.resolve(__dirname, "..");
const COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.local.yml";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function run(cmd, opts = {}) {
  return execSync(cmd, {
    cwd: PROJECT_DIR,
    stdio: opts.silent ? "pipe" : "inherit",
    ...opts,
  });
}

function runLive(cmd, args = []) {
  const child = spawn(cmd, args, {
    cwd: PROJECT_DIR,
    stdio: "inherit",
    shell: true,
  });
  child.on("error", (err) => {
    console.error(`Failed to run: ${cmd} ${args.join(" ")}`);
    console.error(err.message);
    process.exit(1);
  });
  return child;
}

function hasDocker() {
  try {
    execSync("docker --version", { stdio: "pipe" });
    execSync("docker compose version", { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

function isRunning() {
  try {
    const out = execSync(`${COMPOSE} ps --format json`, {
      cwd: PROJECT_DIR,
      stdio: "pipe",
    }).toString();
    // docker compose ps returns one JSON object per line
    const lines = out.trim().split("\n").filter(Boolean);
    return lines.length > 0;
  } catch {
    return false;
  }
}

function getPort() {
  const envPath = path.join(PROJECT_DIR, ".env");
  if (fs.existsSync(envPath)) {
    const content = fs.readFileSync(envPath, "utf8");
    const match = content.match(/^PORT=(\d+)/m);
    if (match) return match[1];
  }
  return "5001";
}

function printBanner() {
  console.log(`
  ╔══════════════════════════════════════╗
  ║         OpenVoiceUI v${VERSION.padEnd(14)}║
  ║   Voice-Powered AI Assistant        ║
  ╚══════════════════════════════════════╝
  `);
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

const commands = {
  setup: {
    desc: "Interactive setup — configure API keys and build Docker images",
    run: cmdSetup,
  },
  start: {
    desc: "Start OpenVoiceUI (Docker Compose)",
    run: cmdStart,
  },
  stop: {
    desc: "Stop OpenVoiceUI",
    run: cmdStop,
  },
  restart: {
    desc: "Restart OpenVoiceUI",
    run: cmdRestart,
  },
  status: {
    desc: "Show container status",
    run: cmdStatus,
  },
  logs: {
    desc: "Stream container logs (Ctrl+C to stop)",
    run: cmdLogs,
  },
  update: {
    desc: "Pull latest source and rebuild images",
    run: cmdUpdate,
  },
  config: {
    desc: "Open the OpenClaw control panel (localhost:18791)",
    run: cmdConfig,
  },
  help: {
    desc: "Show this help message",
    run: cmdHelp,
  },
};

// --- setup ---
async function cmdSetup() {
  printBanner();
  console.log("  Setting up OpenVoiceUI...\n");

  if (!hasDocker()) {
    console.error("  ERROR: Docker and Docker Compose are required.");
    console.error("  Install Docker: https://docs.docker.com/get-docker/\n");
    process.exit(1);
  }
  console.log("  [OK] Docker found\n");

  // Interactive key collection
  const readline = require("readline");
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  const ask = (q) =>
    new Promise((resolve) =>
      rl.question(q, (a) => resolve(a.trim()))
    );

  console.log("  ── Required Keys ──────────────────────────────────────");
  console.log("  These are needed for the app to function.\n");

  const groqKey = await ask(
    "  Groq API Key (console.groq.com) [REQUIRED]: "
  );
  if (!groqKey) {
    console.error("\n  ERROR: Groq API key is required for TTS.\n");
    rl.close();
    process.exit(1);
  }

  const deepgramKey = await ask(
    "  Deepgram API Key (console.deepgram.com) [optional]: "
  );
  if (!deepgramKey) {
    console.log(
      "  Skipped — browser speech recognition (WebSpeech) will be used for STT.\n"
    );
  }

  console.log("\n  ── AI Provider Keys (pick at least one) ──────────────");
  console.log("  Press Enter to skip any provider.\n");

  const zaiKey = await ask("  Z.AI API Key (z.ai): ");
  const anthropicKey = await ask("  Anthropic API Key (console.anthropic.com): ");
  const openaiKey = await ask("  OpenAI API Key (platform.openai.com): ");

  if (!zaiKey && !anthropicKey && !openaiKey) {
    console.log(
      "\n  WARNING: No AI provider key set. You can add one later in the .env file.\n"
    );
  }

  console.log("\n  ── Optional Keys ─────────────────────────────────────");
  console.log("  Press Enter to skip.\n");

  const geminiKey = await ask("  Gemini API Key (aistudio.google.com): ");
  const openrouterKey = await ask("  OpenRouter API Key (openrouter.ai): ");
  const sunoKey = await ask("  Suno API Key — AI Music (sunoapi.org): ");

  const portAnswer = await ask("  Port (default 5001): ");
  const port = portAnswer || "5001";

  rl.close();

  // Set env vars for setup-config.js
  const setupEnv = {
    PINOKIO_PORT: port,
    PINOKIO_GROQ_API_KEY: groqKey,
    PINOKIO_DEEPGRAM_API_KEY: deepgramKey,
    PINOKIO_ZAI_API_KEY: zaiKey,
    PINOKIO_ANTHROPIC_API_KEY: anthropicKey,
    PINOKIO_OPENAI_API_KEY: openaiKey,
    PINOKIO_GEMINI_API_KEY: geminiKey,
    PINOKIO_OPENROUTER_API_KEY: openrouterKey,
    PINOKIO_SUNO_API_KEY: sunoKey,
  };

  console.log("\n  Generating configuration files...\n");
  run("node setup-config.js", {
    env: { ...process.env, ...setupEnv },
  });

  console.log("\n  Building Docker images (this may take a few minutes)...\n");
  run(`${COMPOSE} build`);

  console.log(`
  ════════════════════════════════════════════════════════
  Setup complete!

  Start OpenVoiceUI:   openvoiceui start
  Open in browser:     http://localhost:${port}
  OpenClaw control:    http://localhost:18791
  View logs:           openvoiceui logs
  ════════════════════════════════════════════════════════
  `);
}

// --- start ---
function cmdStart() {
  printBanner();
  if (!hasDocker()) {
    console.error("  ERROR: Docker not found. Run: openvoiceui setup\n");
    process.exit(1);
  }

  const envPath = path.join(PROJECT_DIR, ".env");
  if (!fs.existsSync(envPath)) {
    console.error("  ERROR: No .env file found. Run: openvoiceui setup\n");
    process.exit(1);
  }

  // Pre-create devices directory before containers start. Without this,
  // openclaw (running as root) creates it first via the bind mount, making
  // it root-owned — then inject-device-identity.js can't write paired.json.
  fs.mkdirSync(path.join(PROJECT_DIR, "openclaw-data", "devices"), {
    recursive: true,
  });

  console.log("  Starting OpenVoiceUI...\n");
  run(`${COMPOSE} up -d`);

  // Inject pre-paired device identity if available
  const prePairedPath = path.join(
    PROJECT_DIR,
    "openclaw-data",
    "pre-paired-device.json"
  );
  if (fs.existsSync(prePairedPath)) {
    try {
      run("node inject-device-identity.js", { silent: true });
    } catch {
      // Non-fatal — device pairing may already be done
    }
  }

  // Connect ByteRover memory provider (free, no API key needed)
  // Runs inside the openclaw container after it's healthy
  try {
    run(`${COMPOSE} exec -T openclaw brv providers connect byterover`, { silent: true });
    console.log("  [OK] ByteRover memory connected\n");
  } catch {
    // Non-fatal — brv will prompt on first use
  }

  const port = getPort();
  console.log(`
  OpenVoiceUI is running!

  App:          http://localhost:${port}
  OpenClaw:     http://localhost:18791
  Stop:         openvoiceui stop
  Logs:         openvoiceui logs
  `);
}

// --- stop ---
function cmdStop() {
  console.log("  Stopping OpenVoiceUI...\n");
  run(`${COMPOSE} down`);
  console.log("  Stopped.\n");
}

// --- restart ---
function cmdRestart() {
  console.log("  Restarting OpenVoiceUI...\n");
  run(`${COMPOSE} down`);
  run(`${COMPOSE} up -d`);

  const port = getPort();
  console.log(`\n  Restarted. App: http://localhost:${port}\n`);
}

// --- status ---
function cmdStatus() {
  printBanner();
  if (!hasDocker()) {
    console.error("  Docker not found.\n");
    process.exit(1);
  }
  run(`${COMPOSE} ps`);
}

// --- logs ---
function cmdLogs() {
  const child = runLive("docker", ["compose", "-f", "docker-compose.yml", "-f", "docker-compose.local.yml", "logs", "-f", "--tail", "100"]);
  process.on("SIGINT", () => {
    child.kill("SIGINT");
    process.exit(0);
  });
}

// --- update ---
function cmdUpdate() {
  printBanner();
  console.log("  Updating OpenVoiceUI...\n");

  // Check if this is a git repo (cloned install) or npm install
  const gitDir = path.join(PROJECT_DIR, ".git");
  if (fs.existsSync(gitDir)) {
    console.log("  Pulling latest source...\n");
    run("git pull --ff-only");
  } else {
    console.log("  Installed via npm — update with: npm update -g openvoiceui\n");
  }

  console.log("  Rebuilding Docker images...\n");
  run(`${COMPOSE} build --pull`);

  if (isRunning()) {
    console.log("  Restarting containers...\n");
    run(`${COMPOSE} up -d`);
  }

  console.log("  Update complete!\n");
}

// --- config ---
function cmdConfig() {
  console.log("  OpenClaw control panel: http://localhost:18791\n");
  console.log("  Open this URL in your browser to change AI models,");
  console.log("  add provider keys, and configure agent behavior.\n");
}

// --- help ---
function cmdHelp() {
  printBanner();
  console.log("  Usage: openvoiceui <command>\n");
  console.log("  Commands:\n");
  for (const [name, cmd] of Object.entries(commands)) {
    console.log(`    ${name.padEnd(12)} ${cmd.desc}`);
  }
  console.log(`
  Quick start:
    openvoiceui setup     # configure keys + build images
    openvoiceui start     # launch the app
    open http://localhost:5001
  `);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const rawCommand = process.argv[2] || "help";
const command = (rawCommand === "--help" || rawCommand === "-h") ? "help" : rawCommand;

if (commands[command]) {
  const result = commands[command].run();
  // Handle async commands (setup uses readline)
  if (result && typeof result.catch === "function") {
    result.catch((err) => {
      console.error(`\n  ERROR: ${err.message}\n`);
      process.exit(1);
    });
  }
} else {
  console.error(`  Unknown command: ${command}\n`);
  cmdHelp();
  process.exit(1);
}
