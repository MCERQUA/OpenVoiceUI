module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml down",
      },
    },
    {
      method: "local.set",
      params: {
        running: false,
      },
    },
  ],
}
