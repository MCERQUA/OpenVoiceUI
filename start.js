module.exports = {
  daemon: true,
  run: [
    // Start containers in foreground (Pinokio daemon mode manages lifecycle)
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.local.yml up",
        on: [{
          // Match OpenVoiceUI's startup message (not OpenClaw's earlier "listening on ws://")
          event: "/OpenVoiceUI starting on port/i",
          done: true,
        }],
      },
    },

    // Inject the pre-paired device identity into the OpenVoiceUI container.
    // This was generated during install (setup-config.js) and pre-registered
    // in openclaw-data/devices/paired.json. We docker exec it in because
    // file bind-mounts into Docker named volumes break on Windows.
    // This runs BEFORE the user can open the browser, so the identity is
    // already in place when OpenVoiceUI first connects to OpenClaw.
    {
      method: "shell.run",
      params: {
        message: "node inject-device-identity.js",
      },
    },

    // Set URL so Pinokio shows "Open" button.
    // Device is already pre-paired — no approval step needed.
    {
      method: "local.set",
      params: {
        url: "http://localhost:{{local.PORT||5001}}",
      },
    },

    // Safety fallback: if inject failed (e.g. container restarted and lost
    // the injected file), auto-approve will catch the device when the user
    // opens the browser. Runs in background with long poll.
    {
      method: "shell.run",
      params: {
        message: "node auto-approve-devices.js",
      },
    },
  ],
}
