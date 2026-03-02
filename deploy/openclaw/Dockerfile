FROM node:22-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN npm i -g openclaw@latest

# Optional: install a coding CLI so the coding-agent skill is available.
# Set CODING_CLI at build time:  docker compose build --build-arg CODING_CLI=codex
# Or set CODING_CLI=codex in your .env and use the docker-compose args block.
#
# Options (same as openclaw's setup wizard):
#   codex    — OpenAI Codex CLI  (requires OPENAI_API_KEY)
#   claude   — Anthropic Claude Code  (requires ANTHROPIC_API_KEY)
#   opencode — OpenCode  (bring your own provider key)
#   pi       — Pi coding agent  (bring your own provider key)
#   none     — skip (coding-agent skill will be inactive)
ARG CODING_CLI=none
RUN if [ "$CODING_CLI" = "codex" ]; then \
        npm i -g @openai/codex; \
    elif [ "$CODING_CLI" = "claude" ]; then \
        npm i -g @anthropic-ai/claude-code; \
    elif [ "$CODING_CLI" = "opencode" ]; then \
        npm i -g opencode-ai; \
    elif [ "$CODING_CLI" = "pi" ]; then \
        npm i -g @mariozechner/pi-coding-agent; \
    fi

EXPOSE 18791

CMD ["openclaw", "gateway", "--port", "18791"]
