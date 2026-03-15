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

    // Auto-approve any pending device pairing requests
    // OpenClaw requires device pairing but the pre-paired identity mount
    // doesn't work with Docker named volumes on Windows. This approves
    // whatever device OpenVoiceUI auto-generates on first connection.
    {
      method: "shell.run",
      params: {
        message: "node auto-approve-devices.js",
      },
    },

    // Set URL so pinokio.js shows "Open" button
    {
      method: "local.set",
      params: {
        url: "http://localhost:5001",
      },
    },
  ],
}
