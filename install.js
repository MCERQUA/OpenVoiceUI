module.exports = {
  run: [
    // Step 1: Verify Docker is installed and running
    {
      method: "shell.run",
      params: {
        message: "node check-docker.js",
      },
    },

    // Step 2: Collect API keys from user
    // Matches OpenClaw's onboarding wizard providers + Groq for TTS
    {
      method: "input",
      params: {
        title: "OpenVoiceUI Setup",
        description: "Enter your AI provider API keys below. You need at least one AI provider key and a Groq key for voice synthesis. All fields except Groq are optional — fill in whichever providers you use.",
        form: [
          // --- Major Providers ---
          {
            key: "ANTHROPIC_API_KEY",
            title: "Anthropic API Key (recommended — default provider)",
            description: "console.anthropic.com/settings/keys",
            placeholder: "sk-ant-...",
            required: false,
          },
          {
            key: "OPENAI_API_KEY",
            title: "OpenAI API Key",
            description: "platform.openai.com/api-keys",
            placeholder: "sk-...",
            required: false,
          },
          {
            key: "GEMINI_API_KEY",
            title: "Google Gemini API Key",
            description: "aistudio.google.com/apikey",
            placeholder: "AIza...",
            required: false,
          },
          {
            key: "OPENROUTER_API_KEY",
            title: "OpenRouter API Key (100+ models)",
            description: "openrouter.ai/keys",
            placeholder: "sk-or-...",
            required: false,
          },
          {
            key: "MISTRAL_API_KEY",
            title: "Mistral API Key",
            description: "console.mistral.ai/api-keys",
            placeholder: "",
            required: false,
          },
          {
            key: "XAI_API_KEY",
            title: "xAI API Key (Grok)",
            description: "console.x.ai",
            placeholder: "xai-...",
            required: false,
          },
          {
            key: "ZAI_API_KEY",
            title: "Z.AI API Key (GLM)",
            description: "z.ai",
            placeholder: "",
            required: false,
          },
          {
            key: "CEREBRAS_API_KEY",
            title: "Cerebras API Key",
            description: "cloud.cerebras.ai",
            placeholder: "",
            required: false,
          },
          {
            key: "TOGETHER_API_KEY",
            title: "Together AI API Key",
            description: "api.together.xyz/settings/api-keys",
            placeholder: "",
            required: false,
          },
          {
            key: "HF_TOKEN",
            title: "Hugging Face Token",
            description: "huggingface.co/settings/tokens",
            placeholder: "hf_...",
            required: false,
          },
          // --- Asian Providers ---
          {
            key: "MOONSHOT_API_KEY",
            title: "Moonshot API Key (Kimi)",
            description: "platform.moonshot.cn",
            placeholder: "",
            required: false,
          },
          {
            key: "KIMI_API_KEY",
            title: "Kimi Coding API Key",
            description: "platform.moonshot.cn",
            placeholder: "",
            required: false,
          },
          {
            key: "MINIMAX_API_KEY",
            title: "MiniMax API Key",
            description: "platform.minimaxi.com",
            placeholder: "",
            required: false,
          },
          {
            key: "QIANFAN_API_KEY",
            title: "Qianfan API Key",
            description: "cloud.baidu.com",
            placeholder: "",
            required: false,
          },
          {
            key: "MODELSTUDIO_API_KEY",
            title: "Alibaba Model Studio API Key",
            description: "modelstudio.aliyun.com",
            placeholder: "",
            required: false,
          },
          {
            key: "XIAOMI_API_KEY",
            title: "Xiaomi API Key",
            description: "xiaomi.com",
            placeholder: "",
            required: false,
          },
          {
            key: "VOLCANO_ENGINE_API_KEY",
            title: "Volcano Engine API Key (Doubao)",
            description: "volcengine.com",
            placeholder: "",
            required: false,
          },
          {
            key: "BYTEPLUS_API_KEY",
            title: "BytePlus API Key",
            description: "byteplus.com",
            placeholder: "",
            required: false,
          },
          // --- Other Providers ---
          {
            key: "SYNTHETIC_API_KEY",
            title: "Synthetic API Key",
            description: "synthetic.com",
            placeholder: "",
            required: false,
          },
          {
            key: "VENICE_API_KEY",
            title: "Venice AI API Key",
            description: "venice.ai",
            placeholder: "",
            required: false,
          },
          {
            key: "OPENCODE_ZEN_API_KEY",
            title: "OpenCode API Key (Zen)",
            description: "opencode.ai",
            placeholder: "",
            required: false,
          },
          // --- Gateways / Proxies ---
          {
            key: "KILOCODE_API_KEY",
            title: "Kilo Gateway API Key",
            description: "kilocode.ai",
            placeholder: "",
            required: false,
          },
          {
            key: "AI_GATEWAY_API_KEY",
            title: "Vercel AI Gateway API Key",
            description: "vercel.com",
            placeholder: "",
            required: false,
          },
          {
            key: "CLOUDFLARE_AI_GATEWAY_API_KEY",
            title: "Cloudflare AI Gateway API Key",
            description: "dash.cloudflare.com",
            placeholder: "",
            required: false,
          },
          {
            key: "LITELLM_API_KEY",
            title: "LiteLLM API Key",
            description: "litellm.ai",
            placeholder: "",
            required: false,
          },
          // --- Required: Groq (TTS + LLM) ---
          {
            key: "GROQ_API_KEY",
            title: "Groq API Key (required — Text-to-Speech + fast LLM)",
            description: "Free tier at console.groq.com",
            placeholder: "gsk_...",
            required: true,
          },
          // --- Settings ---
          {
            key: "PORT",
            title: "Port (default: 5001)",
            placeholder: "5001",
            required: false,
          },
        ],
      },
    },

    // Step 3: Create openclaw config directory
    {
      method: "shell.run",
      params: {
        message: "{{platform === 'win32' ? 'if not exist openclaw-data mkdir openclaw-data' : 'mkdir -p openclaw-data'}}",
      },
    },

    // Step 4: Write openclaw config (nested keys for v2026.3.2+)
    {
      method: "fs.write",
      params: {
        path: "openclaw-data/openclaw.json",
        text: JSON.stringify({
          gateway: {
            mode: "local",
            port: 18791,
            bind: "lan",
            auth: {
              token: "pinokio-local-token",
            },
            trustedProxies: ["127.0.0.1", "172.16.0.0/12", "10.0.0.0/8"],
            controlUi: {
              allowInsecureAuth: true,
              dangerouslyDisableDeviceAuth: true,
              allowedOrigins: [
                "http://localhost:18791",
                "http://127.0.0.1:18791",
                "http://localhost:5001",
                "http://127.0.0.1:5001",
              ],
            },
          },
          agents: {
            defaults: {
              thinkingDefault: "off",
              timeoutSeconds: 120,
            },
          },
        }, null, 2),
      },
    },

    // Step 5: Write .env with all provider keys
    {
      method: "fs.write",
      params: {
        path: ".env",
        text: [
          "# OpenVoiceUI — generated by Pinokio installer",
          "PORT={{input.PORT||5001}}",
          "DOMAIN=localhost",
          "SECRET_KEY=pinokio-local-install",
          "",
          "# OpenClaw Gateway",
          "CLAWDBOT_GATEWAY_URL=ws://127.0.0.1:18791",
          "CLAWDBOT_AUTH_TOKEN=pinokio-local-token",
          "GATEWAY_SESSION_KEY=voice-main-1",
          "",
          "# AI Provider Keys (openclaw uses whichever are set)",
          "ANTHROPIC_API_KEY={{input.ANTHROPIC_API_KEY}}",
          "OPENAI_API_KEY={{input.OPENAI_API_KEY}}",
          "GEMINI_API_KEY={{input.GEMINI_API_KEY}}",
          "OPENROUTER_API_KEY={{input.OPENROUTER_API_KEY}}",
          "MISTRAL_API_KEY={{input.MISTRAL_API_KEY}}",
          "XAI_API_KEY={{input.XAI_API_KEY}}",
          "ZAI_API_KEY={{input.ZAI_API_KEY}}",
          "CEREBRAS_API_KEY={{input.CEREBRAS_API_KEY}}",
          "TOGETHER_API_KEY={{input.TOGETHER_API_KEY}}",
          "HF_TOKEN={{input.HF_TOKEN}}",
          "MOONSHOT_API_KEY={{input.MOONSHOT_API_KEY}}",
          "KIMI_API_KEY={{input.KIMI_API_KEY}}",
          "MINIMAX_API_KEY={{input.MINIMAX_API_KEY}}",
          "QIANFAN_API_KEY={{input.QIANFAN_API_KEY}}",
          "MODELSTUDIO_API_KEY={{input.MODELSTUDIO_API_KEY}}",
          "XIAOMI_API_KEY={{input.XIAOMI_API_KEY}}",
          "VOLCANO_ENGINE_API_KEY={{input.VOLCANO_ENGINE_API_KEY}}",
          "BYTEPLUS_API_KEY={{input.BYTEPLUS_API_KEY}}",
          "SYNTHETIC_API_KEY={{input.SYNTHETIC_API_KEY}}",
          "VENICE_API_KEY={{input.VENICE_API_KEY}}",
          "OPENCODE_ZEN_API_KEY={{input.OPENCODE_ZEN_API_KEY}}",
          "KILOCODE_API_KEY={{input.KILOCODE_API_KEY}}",
          "AI_GATEWAY_API_KEY={{input.AI_GATEWAY_API_KEY}}",
          "CLOUDFLARE_AI_GATEWAY_API_KEY={{input.CLOUDFLARE_AI_GATEWAY_API_KEY}}",
          "LITELLM_API_KEY={{input.LITELLM_API_KEY}}",
          "",
          "# TTS — Groq (also available as fast LLM provider)",
          "GROQ_API_KEY={{input.GROQ_API_KEY}}",
          "USE_GROQ=true",
          "USE_GROQ_TTS=true",
          "",
          "# Supertonic TTS (ships with docker compose)",
          "SUPERTONIC_API_URL=http://supertonic:8765",
        ],
        join: "\n",
      },
    },

    // Step 6: Build Docker images (first run takes a few minutes)
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml build",
      },
    },

    // Step 7: Done
    {
      method: "notify",
      params: {
        html: "OpenVoiceUI installed! Click <b>Start</b> to launch.<br><br>The app opens at <code>http://localhost:5001</code>.<br>To change your AI model or add more provider keys later, open <code>http://localhost:18791</code> (token: <code>pinokio-local-token</code>).",
      },
    },
  ],
}
