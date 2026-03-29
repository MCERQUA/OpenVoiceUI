FROM node:22-bookworm-slim

# System packages for native Node modules (canvas, sharp, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3 \
    git \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Use pnpm via corepack (handles native modules better than npm)
RUN corepack enable && corepack prepare pnpm@latest --activate
ENV PNPM_HOME=/usr/local/share/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN mkdir -p $PNPM_HOME

# OpenClaw version — pinned to the version tested with this OpenVoiceUI release.
# Override at build time: docker compose build --build-arg OPENCLAW_VERSION=2026.3.7
ARG OPENCLAW_VERSION=2026.3.24
RUN pnpm add -g openclaw@${OPENCLAW_VERSION} && pnpm approve-builds -g

# ByteRover — long-term structured memory via context engine plugin
# brv CLI: query/curate knowledge in .brv/context-tree/ (human-readable markdown)
# Plugin: auto-runs brv query before each LLM call, brv curate after each turn
RUN pnpm add -g byterover-cli clawhub \
    && BRV_RUN=$(find /usr/local/share/pnpm/global -name 'run.js' -path '*/byterover-cli/bin/*' | head -1) \
    && printf '#!/bin/sh\nexec /usr/local/bin/node "%s" "$@"\n' "$BRV_RUN" > /usr/local/bin/brv-direct \
    && chmod +x /usr/local/bin/brv-direct \
    && mkdir -p /home/node/.openclaw/extensions/byterover \
    && cd /home/node/.openclaw/extensions/byterover \
    && npm init -y --silent \
    && npm install --save @byterover/byterover \
    && printf '{"name":"byterover","version":"1.1.2","private":true,"main":"node_modules/@byterover/byterover/index.ts","openclaw":{"compat":{"pluginApi":">=2026.3.22"},"extensions":["./node_modules/@byterover/byterover/index.ts"]}}\n' > package.json \
    && printf '{"id":"byterover","name":"ByteRover","description":"ByteRover context engine plugin","version":"1.1.2","configSchema":{"type":"object","properties":{"brvPath":{"type":"string"},"cwd":{"type":"string"},"queryTimeoutMs":{"type":"number"},"curateTimeoutMs":{"type":"number"}}}}\n' > openclaw.plugin.json \
    && chown -R node:node /home/node/.openclaw

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
        pnpm add -g @openai/codex; \
    elif [ "$CODING_CLI" = "claude" ]; then \
        pnpm add -g @anthropic-ai/claude-code; \
    elif [ "$CODING_CLI" = "opencode" ]; then \
        pnpm add -g opencode-ai; \
    elif [ "$CODING_CLI" = "pi" ]; then \
        pnpm add -g @mariozechner/pi-coding-agent; \
    fi

EXPOSE 18791

CMD ["openclaw", "gateway", "--port", "18791"]
