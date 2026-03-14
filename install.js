module.exports = {
  run: [
    // Step 1: Verify Docker is available and daemon is running
    {
      method: "shell.run",
      params: {
        message: `node -e "
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

// On Windows, docker may not be on PATH inside conda/pinokio shell
// Try common install locations if direct call fails
const winPaths = [
  'C:\\\\Program Files\\\\Docker\\\\Docker\\\\resources\\\\bin',
  process.env.LOCALAPPDATA + '\\\\Docker\\\\resources\\\\bin',
  'C:\\\\ProgramData\\\\DockerDesktop\\\\version-bin',
];

function findDocker() {
  // Try PATH first
  try {
    execSync('docker version --format ok', { stdio: 'pipe', timeout: 10000 });
    return 'docker';
  } catch(e) {}
  // On Windows, check common install locations
  if (process.platform === 'win32') {
    for (const p of winPaths) {
      const exe = path.join(p, 'docker.exe');
      if (fs.existsSync(exe)) {
        process.env.PATH = p + ';' + process.env.PATH;
        return exe;
      }
    }
  }
  return null;
}

const docker = findDocker();
if (!docker) {
  console.error('ERROR: Docker CLI not found. Install Docker Desktop from https://docker.com/products/docker-desktop');
  process.exit(1);
}

// Docker CLI found — now check if daemon is running
try {
  execSync('docker info', { stdio: 'pipe', timeout: 15000 });
  console.log('Docker is running');
} catch(e) {
  const msg = e.stderr ? e.stderr.toString() : e.message;
  console.error('ERROR: Docker daemon not responding. Make sure Docker Desktop is fully started.');
  console.error('Detail: ' + msg.split('\\n')[0]);
  process.exit(1);
}
"`,
      },
    },

    // Step 2: Collect API keys from user
    {
      method: "input",
      params: {
        title: "OpenVoiceUI Setup",
        description: "Enter your API keys to get started. A free Groq key is required for voice synthesis.",
        form: [
          {
            key: "GROQ_API_KEY",
            title: "Groq API Key (required — Text-to-Speech)",
            description: "Free tier available at console.groq.com. Used for Orpheus voice synthesis.",
            placeholder: "gsk_...",
            required: true,
          },
          {
            key: "PORT",
            title: "Port (optional, default: 5001)",
            description: "The local port OpenVoiceUI will run on.",
            placeholder: "5001",
            required: false,
          },
        ],
      },
    },

    // Step 3: Create openclaw-data dir and write local openclaw config (auth disabled for local use)
    {
      method: "shell.run",
      params: {
        message: `node -e "
const fs = require('fs');
fs.mkdirSync('openclaw-data', { recursive: true });
const config = {
  gateway: {
    mode: 'local',
    port: 18791,
    bind: 'lan',
    trustedProxies: ['127.0.0.1', '172.16.0.0/12', '10.0.0.0/8'],
    controlUi: {
      allowInsecureAuth: true,
      dangerouslyDisableDeviceAuth: true,
    },
  },
  agents: {
    defaults: {
      thinkingDefault: 'off',
      timeoutSeconds: 120,
    },
  },
};
fs.writeFileSync('openclaw-data/openclaw.json', JSON.stringify(config, null, 2));
console.log('openclaw-data/openclaw.json created');
"`,
      },
    },

    // Step 4: Generate .env from .env.example with keys filled in
    {
      method: "shell.run",
      params: {
        message: `node -e "
const fs = require('fs');
const crypto = require('crypto');
let env = fs.readFileSync('.env.example', 'utf8');
const port = process.env.PORT || '5001';
const token = crypto.randomBytes(32).toString('hex');
const secret = crypto.randomBytes(32).toString('hex');
env = env.replace(/^GROQ_API_KEY=.*/m, 'GROQ_API_KEY=' + process.env.GROQ_API_KEY);
env = env.replace(/^SECRET_KEY=.*/m, 'SECRET_KEY=' + secret);
env = env.replace(/^PORT=.*/m, 'PORT=' + port);
env = env.replace(/^DOMAIN=.*/m, 'DOMAIN=localhost');
env = env.replace(/^CLAWDBOT_AUTH_TOKEN=.*/m, 'CLAWDBOT_AUTH_TOKEN=' + token);
fs.writeFileSync('.env', env);
console.log('.env created (port=' + port + ')');
"`,
        env: {
          GROQ_API_KEY: "{{input.GROQ_API_KEY}}",
          PORT: "{{input.PORT||5001}}",
        },
      },
    },

    // Step 5: Build Docker images (takes a few minutes on first run)
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml build",
        on: [{
          event: "/pipe.*docker|docker.*not running|cannot connect|error during connect/i",
          done: true,
          run: {
            method: "notify",
            params: {
              html: "Docker Desktop is not running. Please open Docker Desktop, wait for it to start, then click <b>Reinstall</b>."
            }
          }
        }]
      },
    },

    // Step 6: Mark as installed and store port for Pinokio state tracking
    {
      method: "local.set",
      params: {
        installed: true,
        PORT: "{{input.PORT||5001}}",
      },
    },

    {
      method: "notify",
      params: {
        html: "OpenVoiceUI installed! Click <b>Start</b> to launch.<br><br>On first start, open <code>http://localhost:18791</code> to configure your AI provider (Anthropic, OpenAI, Ollama, etc.) through the OpenClaw setup wizard.",
      },
    },
  ],
}
