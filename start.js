module.exports = async (kernel) => {
  let port = await kernel.port(5001)
  return {
    daemon: true,
    run: [
      // Start OpenClaw gateway in background
      {
        method: "shell.run",
        params: {
          message: "openclaw gateway --port 18791 --home ./openclaw-data",
          background: true,
        },
      },

      // Wait a moment for openclaw to initialize
      {
        method: "shell.run",
        params: {
          message: "node -e \"setTimeout(()=>process.exit(0),3000)\"",
        },
      },

      // Start Flask server (the main daemon process)
      {
        method: "shell.run",
        params: {
          venv: "env",
          env: {
            PORT: `${port}`,
            CLAWDBOT_GATEWAY_URL: "ws://127.0.0.1:18791",
            SUPERTONIC_API_URL: "",
          },
          message: "python server.py",
          on: [{
            // Detect when Flask is listening
            event: `/Listening on.*${port}|Running on.*${port}|http.*${port}/i`,
            done: true,
          }],
        },
      },

      // Store the URL so menu can show "Open" button
      {
        method: "local.set",
        params: {
          url: `http://localhost:${port}`,
        },
      },
    ],
  }
}
