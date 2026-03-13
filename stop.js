module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml down",
      },
    },
    {
      method: "shell.stop",
      params: {
        uri: "start.js",
      },
    },
  ],
}
