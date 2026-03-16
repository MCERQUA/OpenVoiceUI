module.exports = {
  daemon: true,
  run: [
    // Start containers in foreground (Pinokio daemon mode manages lifecycle)
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml up",
        on: [{
          // Match OpenVoiceUI's startup message (not OpenClaw's earlier "listening on ws://")
          event: "/OpenVoiceUI starting on port/i",
          done: true,
        }],
      },
    },

    // Set URL so pinokio.js shows "Open" button (uses port from install)
    {
      method: "local.set",
      params: {
        url: "http://localhost:{{local.PORT||5001}}",
      },
    },
  ],
}
