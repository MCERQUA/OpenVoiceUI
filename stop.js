module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.local.yml down",
      },
    },
    {
      method: "script.stop",
      params: {
        uri: "start.js",
      },
    },
  ],
}
