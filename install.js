module.exports = {
  run: [
    // Step 1: Check Docker is installed and running — clear message if not
    {
      method: "shell.run",
      params: {
        message: [
          'node -e "',
          "const { execSync } = require('child_process');",
          "try { execSync('docker --version', { stdio: 'pipe' }); }",
          "catch(e) {",
          "  console.error('\\n\\n=== Docker is not installed ===\\n');",
          "  console.error('OpenVoiceUI requires Docker to run.\\n');",
          "  console.error('Download and install Docker Desktop from:\\n  https://www.docker.com/products/docker-desktop/\\n');",
          "  console.error('After installing, open Docker Desktop and wait for it to fully start,');",
          "  console.error('then try installing OpenVoiceUI again.\\n');",
          "  process.exit(1);",
          "}",
          "try { execSync('docker info', { stdio: 'pipe' }); }",
          "catch(e) {",
          "  console.error('\\n\\n=== Docker is installed but not running ===\\n');",
          "  console.error('Please open Docker Desktop and wait for it to fully start');",
          "  console.error('(the whale icon in your system tray should stop animating),');",
          "  console.error('then try installing OpenVoiceUI again.\\n');",
          "  process.exit(1);",
          "}",
          "try { execSync('docker compose version', { stdio: 'pipe' }); }",
          "catch(e) {",
          "  console.error('\\n\\n=== Docker Compose not found ===\\n');",
          "  console.error('Please update Docker Desktop to the latest version.\\n');",
          "  process.exit(1);",
          "}",
          "console.log('Docker is installed and running.');",
          '"',
        ].join(""),
      },
    },

    // Step 2: Collect API keys from user
    {
      method: "input",
      params: {
        title: "OpenVoiceUI Setup",
        description: "Configure your AI provider keys. REQUIRED keys are needed for the app to function. RECOMMENDED keys give you the best AI experience. All other keys are optional.",
        form: [
          // ═══════════════════════════════════════════════════════════
          // REQUIRED — app won't work without these
          // ═══════════════════════════════════════════════════════════
          {
            key: "GROQ_API_KEY",
            title: "[REQUIRED] Groq API Key — Text-to-Speech + fast LLM",
            description: "Free tier at console.groq.com — powers voice synthesis",
            placeholder: "gsk_...",
            required: true,
          },
          {
            key: "DEEPGRAM_API_KEY",
            title: "[REQUIRED] Deepgram API Key — Speech-to-Text",
            description: "Free tier at console.deepgram.com — powers voice recognition",
            placeholder: "",
            required: true,
          },

          // ═══════════════════════════════════════════════════════════
          // RECOMMENDED — pick at least one AI provider
          // ═══════════════════════════════════════════════════════════
          {
            key: "ZAI_API_KEY",
            title: "[RECOMMENDED] Z.AI API Key — great quality, low cost",
            description: "z.ai — GLM models, excellent value for daily use",
            placeholder: "",
            required: false,
          },
          {
            key: "ANTHROPIC_API_KEY",
            title: "[RECOMMENDED] Anthropic API Key — Claude models",
            description: "console.anthropic.com/settings/keys — highest quality",
            placeholder: "sk-ant-...",
            required: false,
          },
          {
            key: "OPENAI_API_KEY",
            title: "[RECOMMENDED] OpenAI API Key — GPT models",
            description: "platform.openai.com/api-keys",
            placeholder: "sk-...",
            required: false,
          },

          // ═══════════════════════════════════════════════════════════
          // OPTIONAL — additional providers
          // ═══════════════════════════════════════════════════════════
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

          // ═══════════════════════════════════════════════════════════
          // ADDITIONAL TTS PROVIDERS
          // ═══════════════════════════════════════════════════════════
          {
            key: "ELEVENLABS_API_KEY",
            title: "ElevenLabs API Key — Premium TTS + Instant Voice Cloning",
            description: "Plans from $5/mo at elevenlabs.io — premium quality, 29 languages, instant voice cloning",
            placeholder: "",
            required: false,
          },
          {
            key: "ELEVENLABS_VOICE_ID",
            title: "ElevenLabs Voice ID (find in ElevenLabs Voice Library)",
            description: "Select or clone a voice at elevenlabs.io/app/voice-library, then paste the voice ID here",
            placeholder: "",
            required: false,
          },
          {
            key: "RESEMBLE_API_KEY",
            title: "Resemble AI API Key — Chatterbox TTS (streaming, voice cloning)",
            description: "Pay-as-you-go from $5 at resemble.ai — high-quality streaming TTS with emotion control",
            placeholder: "",
            required: false,
          },
          {
            key: "RESEMBLE_VOICE_UUID",
            title: "Resemble Voice UUID (create at app.resemble.ai)",
            description: "Clone or select a voice in your Resemble dashboard, then paste the voice UUID here",
            placeholder: "",
            required: false,
          },

          // ═══════════════════════════════════════════════════════════
          // MUSIC GENERATION
          // ═══════════════════════════════════════════════════════════
          {
            key: "SUNO_API_KEY",
            title: "Suno API Key — AI Music Generation",
            description: "Get your key at sunoapi.org/user → API Keys. Generates full AI songs from text prompts (~45s per song).",
            placeholder: "",
            required: false,
          },

          // ═══════════════════════════════════════════════════════════
          // SETTINGS
          // ═══════════════════════════════════════════════════════════
          {
            key: "PORT",
            title: "Port (default: 5001)",
            description: "Port to run OpenVoiceUI on",
            placeholder: "5001",
            required: false,
          },
        ],
      },
    },

    // Step 3: Generate ALL config files in one shot.
    // Pinokio's {{input.X}} only carries to the immediately next step,
    // so we pass everything via env and do all file writes in one call.
    {
      method: "shell.run",
      params: {
        message: "node setup-config.js",
        env: {
          PINOKIO_PORT: "{{input.PORT||5001}}",
          PINOKIO_GROQ_API_KEY: "{{input.GROQ_API_KEY}}",
          PINOKIO_DEEPGRAM_API_KEY: "{{input.DEEPGRAM_API_KEY}}",
          PINOKIO_ANTHROPIC_API_KEY: "{{input.ANTHROPIC_API_KEY}}",
          PINOKIO_ZAI_API_KEY: "{{input.ZAI_API_KEY}}",
          PINOKIO_OPENAI_API_KEY: "{{input.OPENAI_API_KEY}}",
          PINOKIO_GEMINI_API_KEY: "{{input.GEMINI_API_KEY}}",
          PINOKIO_OPENROUTER_API_KEY: "{{input.OPENROUTER_API_KEY}}",
          PINOKIO_MISTRAL_API_KEY: "{{input.MISTRAL_API_KEY}}",
          PINOKIO_XAI_API_KEY: "{{input.XAI_API_KEY}}",
          PINOKIO_CEREBRAS_API_KEY: "{{input.CEREBRAS_API_KEY}}",
          PINOKIO_TOGETHER_API_KEY: "{{input.TOGETHER_API_KEY}}",
          PINOKIO_HF_TOKEN: "{{input.HF_TOKEN}}",
          PINOKIO_MOONSHOT_API_KEY: "{{input.MOONSHOT_API_KEY}}",
          PINOKIO_KIMI_API_KEY: "{{input.KIMI_API_KEY}}",
          PINOKIO_MINIMAX_API_KEY: "{{input.MINIMAX_API_KEY}}",
          PINOKIO_QIANFAN_API_KEY: "{{input.QIANFAN_API_KEY}}",
          PINOKIO_MODELSTUDIO_API_KEY: "{{input.MODELSTUDIO_API_KEY}}",
          PINOKIO_XIAOMI_API_KEY: "{{input.XIAOMI_API_KEY}}",
          PINOKIO_VOLCANO_ENGINE_API_KEY: "{{input.VOLCANO_ENGINE_API_KEY}}",
          PINOKIO_BYTEPLUS_API_KEY: "{{input.BYTEPLUS_API_KEY}}",
          PINOKIO_SYNTHETIC_API_KEY: "{{input.SYNTHETIC_API_KEY}}",
          PINOKIO_VENICE_API_KEY: "{{input.VENICE_API_KEY}}",
          PINOKIO_OPENCODE_ZEN_API_KEY: "{{input.OPENCODE_ZEN_API_KEY}}",
          PINOKIO_KILOCODE_API_KEY: "{{input.KILOCODE_API_KEY}}",
          PINOKIO_AI_GATEWAY_API_KEY: "{{input.AI_GATEWAY_API_KEY}}",
          PINOKIO_CLOUDFLARE_AI_GATEWAY_API_KEY: "{{input.CLOUDFLARE_AI_GATEWAY_API_KEY}}",
          PINOKIO_LITELLM_API_KEY: "{{input.LITELLM_API_KEY}}",
          PINOKIO_ELEVENLABS_API_KEY: "{{input.ELEVENLABS_API_KEY}}",
          PINOKIO_ELEVENLABS_VOICE_ID: "{{input.ELEVENLABS_VOICE_ID}}",
          PINOKIO_RESEMBLE_API_KEY: "{{input.RESEMBLE_API_KEY}}",
          PINOKIO_RESEMBLE_VOICE_UUID: "{{input.RESEMBLE_VOICE_UUID}}",
          PINOKIO_SUNO_API_KEY: "{{input.SUNO_API_KEY}}",
        },
      },
    },

    // Step 4: Record git SHA for registry check-in, then build Docker images
    {
      method: "shell.run",
      params: {
        message: [
          "git rev-parse HEAD > GIT_HASH",
          "docker compose -f docker-compose.yml -f docker-compose.local.yml build",
        ],
      },
    },

    // Step 5: Mark as installed and store port
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
        html: "OpenVoiceUI installed! Click <b>&#9654; Start</b> to launch.<br><br>The app opens at <code>http://localhost:{{input.PORT||5001}}</code>.<br>To change your AI model or add more provider keys later, open <code>http://localhost:18791</code>.",
      },
    },
  ],
}
