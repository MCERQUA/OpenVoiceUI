module.exports = {
  run: [
    // Stop running containers
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml down",
      },
    },

    // Pull latest code
    {
      method: "shell.run",
      params: {
        message: "git pull",
      },
    },

    // Rebuild images with latest changes
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml build",
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
