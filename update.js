module.exports = {
  run: [
    // Stop if running
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

    // Rebuild images
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml build",
      },
    },

    // Start back up
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml up -d",
      },
    },

    {
      method: "local.set",
      params: {
        running: true,
      },
    },

    {
      method: "browser.open",
      params: {
        url: "http://localhost:{{local.PORT||5001}}",
      },
    },
  ],
}
