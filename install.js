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
            key: "ANTHROPIC_API_KEY",
            title: "[RECOMMENDED] Anthropic API Key — best results, higher cost",
            description: "console.anthropic.com/settings/keys — Claude models, highest quality",
            placeholder: "sk-ant-...",
            required: false,
          },
          {
            key: "ZAI_API_KEY",
            title: "[RECOMMENDED] Z.AI API Key — good results, low cost",
            description: "z.ai — GLM models, great value for daily use",
            placeholder: "",
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
          // SETTINGS
          // ═══════════════════════════════════════════════════════════
          {
            key: "PORT",
            title: "Port (default: 5001)",
            placeholder: "5001",
            required: false,
          },
        ],
      },
    },

    // Step 3: Save input values to JSON file using json.set
    // Pinokio {{input.*}} templates don't resolve in fs.write or shell.run env
    // on some platforms. json.set is a different code path that handles them.
    // We save ALL keys to a JSON file, then setup-config.js reads it.
    { method: "json.set", params: { path: "pinokio-input.json", key: "PORT", value: "{{input.PORT||5001}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "GROQ_API_KEY", value: "{{input.GROQ_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "DEEPGRAM_API_KEY", value: "{{input.DEEPGRAM_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "ANTHROPIC_API_KEY", value: "{{input.ANTHROPIC_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "ZAI_API_KEY", value: "{{input.ZAI_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "OPENAI_API_KEY", value: "{{input.OPENAI_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "GEMINI_API_KEY", value: "{{input.GEMINI_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "OPENROUTER_API_KEY", value: "{{input.OPENROUTER_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "MISTRAL_API_KEY", value: "{{input.MISTRAL_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "XAI_API_KEY", value: "{{input.XAI_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "CEREBRAS_API_KEY", value: "{{input.CEREBRAS_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "TOGETHER_API_KEY", value: "{{input.TOGETHER_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "HF_TOKEN", value: "{{input.HF_TOKEN}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "MOONSHOT_API_KEY", value: "{{input.MOONSHOT_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "KIMI_API_KEY", value: "{{input.KIMI_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "MINIMAX_API_KEY", value: "{{input.MINIMAX_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "QIANFAN_API_KEY", value: "{{input.QIANFAN_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "MODELSTUDIO_API_KEY", value: "{{input.MODELSTUDIO_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "XIAOMI_API_KEY", value: "{{input.XIAOMI_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "VOLCANO_ENGINE_API_KEY", value: "{{input.VOLCANO_ENGINE_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "BYTEPLUS_API_KEY", value: "{{input.BYTEPLUS_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "SYNTHETIC_API_KEY", value: "{{input.SYNTHETIC_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "VENICE_API_KEY", value: "{{input.VENICE_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "OPENCODE_ZEN_API_KEY", value: "{{input.OPENCODE_ZEN_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "KILOCODE_API_KEY", value: "{{input.KILOCODE_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "AI_GATEWAY_API_KEY", value: "{{input.AI_GATEWAY_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "CLOUDFLARE_AI_GATEWAY_API_KEY", value: "{{input.CLOUDFLARE_AI_GATEWAY_API_KEY}}" } },
    { method: "json.set", params: { path: "pinokio-input.json", key: "LITELLM_API_KEY", value: "{{input.LITELLM_API_KEY}}" } },

    // Step 4: Generate .env, openclaw.json, and auth-profiles.json from the saved JSON
    {
      method: "shell.run",
      params: {
        message: "node setup-config.js",
      },
    },

    // Step 5: Build Docker images (first run takes a few minutes)
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml build",
      },
    },

    // Step 6: Done
    {
      method: "notify",
      params: {
        html: "OpenVoiceUI installed! Click <b>Start</b> to launch.<br><br>The app opens at <code>http://localhost:5001</code>.<br>To change your AI model or add more provider keys later, open <code>http://localhost:18791</code> (token: <code>pinokio-local-token</code>).",
      },
    },
  ],
}
