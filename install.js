module.exports = {
  run: [
    // Step 1: Verify Docker is available and running
    {
      method: "shell.run",
      params: {
        message: "docker --version && docker compose version",
        on: [{
          event: "/error|not found|command not found/i",
          done: true,
          run: {
            method: "notify",
            params: {
              html: "Docker is not installed or not running. Please install <a href='https://www.docker.com/products/docker-desktop/'>Docker Desktop</a> and start it, then try again."
            }
          }
        }]
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
  allowInsecureAuth: true,
  dangerouslyDisableDeviceAuth: true,
  thinkingDefault: 'off',
  trustedProxies: ['127.0.0.1', '172.0.0.0/8', '10.0.0.0/8'],
  timeoutSeconds: 120
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
      },
    },

    {
      method: "notify",
      params: {
        html: "OpenVoiceUI installed! Click <b>Start</b> to launch.",
      },
    },
  ],
}
