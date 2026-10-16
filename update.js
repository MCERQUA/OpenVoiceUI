module.exports = {
  run: [
    // Stop running containers
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.local.yml down",
      },
    },

    // Pull latest code and record the exact git SHA for registry check-in
    {
      method: "shell.run",
      params: {
        message: [
          "git pull",
          "git rev-parse HEAD > GIT_HASH",
        ],
      },
    },

    // Bundled agent skills update with the pull (they are mounted read-only from ./skills).
    // Validate them and make sure the mountpoint exists for installs made before skills shipped.
    {
      method: "shell.run",
      params: {
        message: "node install-skills.js --check --mountpoint openclaw-data/skills/openvoiceui",
      },
    },

    // Rebuild images with latest changes (GIT_HASH baked in for check-in)
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.local.yml build",
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
