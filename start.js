module.exports = async (kernel) => {
  let port = await kernel.port(5001)
  return {
    daemon: true,
    run: [
      // Start all containers
      {
        method: "shell.run",
        params: {
          message: `docker compose -f docker-compose.yml -f docker-compose.pinokio.yml up -d`,
        },
      },

      // Wait for OpenVoiceUI to be ready (polls /health/ready)
      {
        method: "shell.run",
        params: {
          message: `node -e "
const http = require('http');
const port = ${port};
let attempts = 0;
const check = () => {
  attempts++;
  const req = http.get('http://localhost:' + port + '/health/ready', (r) => {
    if (r.statusCode < 400) {
      console.log('Ready on port ' + port + ' after ' + attempts + ' attempts');
      process.exit(0);
    } else {
      if (attempts < 60) setTimeout(check, 3000);
      else { console.log('Timed out waiting — check docker logs'); process.exit(0); }
    }
  });
  req.on('error', () => {
    if (attempts < 60) setTimeout(check, 3000);
    else { console.log('Timed out waiting — check docker logs'); process.exit(0); }
  });
  req.end();
};
check();
"`,
        },
      },

      // Store URL for the Open button in the menu
      {
        method: "local.set",
        params: {
          url: `http://localhost:${port}`,
        },
      },
    ],
  }
}
