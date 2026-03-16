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

    // Auto-approve any pending device pairing requests.
    // OpenClaw requires device pairing for WebSocket connections.
    // dangerouslyDisableDeviceAuth only affects the control UI, not WS.
    // This approves whatever device OpenVoiceUI auto-generates on first connect.
    {
      method: "shell.run",
      params: {
        message: "node auto-approve-devices.js",
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
