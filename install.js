module.exports = {
  run: [
    // Step 1: Collect API keys from user
    {
      method: "input",
      params: {
        title: "OpenVoiceUI Setup",
        description: "Enter your API keys to get started. A free Groq key is required for voice synthesis.",
        form: [
          {
            key: "GROQ_API_KEY",
            title: "Groq API Key (required — Text-to-Speech + LLM)",
            description: "Free tier available at console.groq.com",
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

    // Step 2: Install Python dependencies in a virtual environment
    {
      method: "shell.run",
      params: {
        venv: "env",
        message: [
          "pip install -r requirements.txt",
        ],
      },
    },

    // Step 3: Install OpenClaw gateway (Node.js AI gateway)
    {
      method: "shell.run",
      params: {
        message: [
          "npm install -g openclaw@2026.3.2",
        ],
      },
    },

    // Step 4: Create OpenClaw config (auth disabled for local single-user)
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
console.log('OpenClaw config created');
"`,
      },
    },

    // Step 5: Create .env from .env.example with keys filled in
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
env = env.replace(/^CLAWDBOT_GATEWAY_URL=.*/m, 'CLAWDBOT_GATEWAY_URL=ws://127.0.0.1:18791');
env = env.replace(/^USE_GROQ=.*/m, 'USE_GROQ=true');
env = env.replace(/^USE_GROQ_TTS=.*/m, 'USE_GROQ_TTS=true');
fs.writeFileSync('.env', env);
console.log('.env created (port=' + port + ')');
"`,
        env: {
          GROQ_API_KEY: "{{input.GROQ_API_KEY}}",
          PORT: "{{input.PORT||5001}}",
        },
      },
    },

    // Step 6: Done
    {
      method: "notify",
      params: {
        html: "OpenVoiceUI installed! Click <b>Start</b> to launch.",
      },
    },
  ],
}
