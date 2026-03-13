module.exports = {
  run: [
    // Pull latest changes
    {
      method: "shell.run",
      params: {
        message: "git pull",
      },
    },

    // Reinstall Python dependencies
    {
      method: "shell.run",
      params: {
        venv: "env",
        message: "pip install -r requirements.txt",
      },
    },

    // Update OpenClaw to latest
    {
      method: "shell.run",
      params: {
        message: "npm install -g openclaw@2026.3.2",
      },
    },

    {
      method: "notify",
      params: {
        html: "OpenVoiceUI updated! Click <b>Start</b> to launch.",
      },
    },
  ],
}
