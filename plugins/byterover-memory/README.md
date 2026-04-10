# ByteRover Long-Term Memory

Persistent long-term memory for OpenClaw-powered AI agents. This plugin gives
your agent a knowledge base that survives across sessions, container restarts,
and even redeployments (as long as the workspace volume is preserved).

## How It Works

ByteRover operates as an OpenClaw **context engine plugin**. On every
conversation turn:

1. **Before the LLM call** -- ByteRover queries the context tree with the
   current user message and injects relevant knowledge into the system prompt.
   This happens synchronously with a 10-second timeout so it does not block
   conversation flow.

2. **After the LLM response** -- ByteRover curates the conversation
   asynchronously, extracting facts, preferences, decisions, and other
   knowledge into its context tree. This runs in the background (detached)
   so it never delays the next response.

The context tree is stored as **human-readable markdown files** in
`.brv/context-tree/` inside the workspace. You can read, edit, or back up
these files directly -- they are plain text.

## Prerequisites

- A running OpenVoiceUI + OpenClaw deployment (Docker)
- The OpenClaw container must use **pnpm** (the JamBot/multi-tenant image uses
  pnpm; public Docker images may use npm -- check your Dockerfile)
- An LLM API key for context curation (Google Gemini, Groq, OpenAI, etc.)
  The free ByteRover provider works but is rate-limited.

## Installation

Run the install script **on the host machine** (not inside the container):

```bash
# Default container name "openclaw"
bash plugins/byterover-memory/install.sh

# Specify a different container
bash plugins/byterover-memory/install.sh openclaw-myuser
```

The installer will:

1. Install `byterover-cli` and `clawhub` via pnpm global
2. Create a direct Node.js shim for the brv CLI
3. Install the `@byterover/byterover` extension into OpenClaw
4. Create the workspace symlink fix (see Troubleshooting)
5. Prompt you to select and authenticate an LLM provider
6. Patch `openclaw.json` with the plugin configuration
7. Run a test query to verify everything works

After installation, **restart the container**:

```bash
docker restart openclaw-myuser
```

## Provider Setup

ByteRover needs an LLM to curate context. Connect one during install, or
manually afterward:

```bash
docker exec openclaw-myuser brv providers connect google --api-key YOUR_KEY
```

### Supported Providers

| Provider       | ID               | Env Variable          | Notes                     |
|----------------|------------------|-----------------------|---------------------------|
| ByteRover      | `byterover`      | (none)                | Free tier, rate-limited   |
| OpenAI         | `openai`         | `OPENAI_API_KEY`      |                           |
| Anthropic      | `anthropic`      | `ANTHROPIC_API_KEY`   |                           |
| Google Gemini  | `google`         | `GEMINI_API_KEY`      | Recommended (fast, cheap) |
| Groq           | `groq`           | `GROQ_API_KEY`        | Very fast inference       |
| MiniMax        | `minimax`        | `MINIMAX_API_KEY`     |                           |
| GLM (Z.AI)     | `glm`            | `GLM_API_KEY`         |                           |
| OpenRouter     | `openrouter`     | `OPENROUTER_API_KEY`  | Multi-model routing       |
| xAI (Grok)     | `xai`            | `XAI_API_KEY`         |                           |
| Mistral        | `mistral`        | `MISTRAL_API_KEY`     |                           |
| Together AI    | `togetherai`     | `TOGETHER_API_KEY`    |                           |
| DeepInfra      | `deepinfra`      | `DEEPINFRA_API_KEY`   |                           |
| Cohere         | `cohere`         | `COHERE_API_KEY`      |                           |
| Perplexity     | `perplexity`     | `PERPLEXITY_API_KEY`  |                           |
| Cerebras       | `cerebras`       | `CEREBRAS_API_KEY`    |                           |

Check current provider status:

```bash
docker exec openclaw-myuser brv providers list
```

## Verifying It Works

After installing and restarting, check the container logs for this line:

```
assemble injecting systemPromptAddition
```

This confirms ByteRover is querying the context tree and injecting knowledge
into the system prompt before each LLM call.

You can also check the context tree directly:

```bash
# List context tree files
docker exec openclaw-myuser ls -la /home/node/.openclaw/workspace/.brv/context-tree/

# Read a specific knowledge file
docker exec openclaw-myuser cat /home/node/.openclaw/workspace/.brv/context-tree/some-topic.md
```

And run a manual query:

```bash
docker exec openclaw-myuser bash -c \
  "cd /home/node/.openclaw/workspace && brv query 'what do you remember about the user?'"
```

## Configuration

The plugin adds this block to `openclaw.json`:

```json
{
  "plugins": {
    "slots": {
      "contextEngine": "byterover"
    },
    "entries": {
      "byterover": {
        "enabled": true,
        "config": {
          "brvPath": "/usr/local/share/pnpm/brv",
          "cwd": "/home/node/.openclaw/workspace"
        }
      }
    }
  }
}
```

### Config Options

| Key              | Type   | Default                                | Description                                    |
|------------------|--------|----------------------------------------|------------------------------------------------|
| `brvPath`        | string | `/usr/local/share/pnpm/brv`            | Path to the brv CLI binary                     |
| `cwd`            | string | `/home/node/.openclaw/workspace`       | Working directory for brv (where .brv/ lives)  |
| `queryTimeoutMs` | number | 10000                                  | Max time for pre-LLM context query             |
| `curateTimeoutMs`| number | 30000                                  | Max time for post-response curation            |

## Compaction Integration

ByteRover works alongside OpenClaw's built-in compaction system. The
recommended setup:

- ByteRover does **not** own compaction (`ownsCompaction: false` in the
  extension defaults). OpenClaw's compaction still fires at the configured
  `reserveTokensFloor`.
- ByteRover pre-extracts knowledge every turn, so when compaction does fire,
  less knowledge is lost (it already lives in the context tree).
- Recommended `reserveTokensFloor`: 80000 for a 204K context window.

## Uninstall

```bash
bash plugins/byterover-memory/uninstall.sh openclaw-myuser
docker restart openclaw-myuser
```

The uninstaller removes the plugin config and extension but preserves:

- The brv and clawhub CLI tools (may be used elsewhere)
- Provider credentials
- The context tree data (`.brv/context-tree/`) -- your accumulated knowledge

To fully remove everything including knowledge data:

```bash
docker exec openclaw-myuser rm -rf /home/node/.openclaw/workspace/.brv
docker exec openclaw-myuser pnpm remove -g byterover-cli clawhub
```

## Troubleshooting

### "workspace-openvoiceui" directory not found

ByteRover's plugin resolves the workspace directory based on the OpenClaw
agent ID. If your agent is named "openvoiceui" (the default), the plugin
looks for `/home/node/.openclaw/workspace-openvoiceui` instead of
`/home/node/.openclaw/workspace`.

The install script creates a symlink to fix this:

```bash
# Created automatically by install.sh:
workspace-openvoiceui -> workspace
```

If you see this error after a container rebuild, re-run the installer or
create the symlink manually:

```bash
docker exec -u node openclaw-myuser \
  ln -s /home/node/.openclaw/workspace /home/node/.openclaw/workspace-openvoiceui
```

### Provider authentication errors

```bash
# Check which provider is active
docker exec openclaw-myuser brv providers list

# Re-connect with a new key
docker exec openclaw-myuser brv providers connect google --api-key NEW_KEY
```

### "brv: command not found" in container

The brv CLI is installed via pnpm global. It lives at
`/usr/local/share/pnpm/brv`. If OpenClaw cannot find it, check:

1. The `brvPath` in openclaw.json points to the correct location
2. The pnpm global bin directory exists:
   ```bash
   docker exec openclaw-myuser ls -la /usr/local/share/pnpm/brv
   ```
3. If the pnpm shim has issues, create a direct shim:
   ```bash
   # Find the actual entry point
   docker exec openclaw-myuser \
     find /usr/local/share/pnpm/global -name "run.js" -path "*/byterover-cli/bin/*"

   # Create direct shim (replace RUN_JS_PATH with the path found above)
   docker exec openclaw-myuser bash -c 'cat > /home/node/.local/bin/brv-direct << EOF
   #!/bin/sh
   exec node "RUN_JS_PATH" "\$@"
   EOF'
   docker exec openclaw-myuser chmod +x /home/node/.local/bin/brv-direct
   ```
   Then update `brvPath` in openclaw.json to `/home/node/.local/bin/brv-direct`.

### Context tree not growing

If `.brv/context-tree/` stays empty after several conversations:

1. Check that the provider is connected and has a valid API key
2. Look for curation errors in the container logs:
   ```bash
   docker logs openclaw-myuser 2>&1 | grep -i "byterover\|brv\|curate"
   ```
3. Verify the brv config exists:
   ```bash
   docker exec openclaw-myuser cat /home/node/.openclaw/workspace/.brv/config.json
   ```
4. Try a manual curate:
   ```bash
   docker exec openclaw-myuser bash -c \
     "cd /home/node/.openclaw/workspace && brv curate 'The user prefers dark themes.'"
   ```

### Extension version mismatch

If OpenClaw logs show plugin compatibility errors:

```bash
# Check extension version
docker exec openclaw-myuser \
  node -e "console.log(require('/home/node/.openclaw/extensions/byterover/node_modules/@byterover/byterover/package.json').version)"

# Reinstall to get latest
bash plugins/byterover-memory/install.sh openclaw-myuser
```

## Data Backup

The context tree is the most valuable data this plugin creates. Back it up:

```bash
# Copy context tree to host
docker cp openclaw-myuser:/home/node/.openclaw/workspace/.brv ./brv-backup-$(date +%Y%m%d)

# Or if the workspace is a bind mount, just back up the host path
```

The context tree files are plain markdown. You can version-control them,
merge knowledge from multiple agents, or edit them by hand.
