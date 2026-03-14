module.exports = {
  daemon: true,
  run: [
    // Start all containers
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml up -d",
      },
    },

    // Wait for OpenVoiceUI to be ready (reads port from .env, polls /health/ready)
    {
      method: "shell.run",
      params: {
        message: `node -e "
const http = require('http');
const fs = require('fs');
let port = 5001;
try {
  const env = fs.readFileSync('.env', 'utf8');
  const m = env.match(/^PORT=(\\d+)/m);
  if (m) port = parseInt(m[1]);
} catch(e) {}
console.log('Waiting for OpenVoiceUI on port ' + port + '...');
let attempts = 0;
const check = () => {
  attempts++;
  const req = http.get('http://localhost:' + port + '/health/ready', (r) => {
    if (r.statusCode < 400) {
      console.log('READY url=http://localhost:' + port);
      process.exit(0);
    } else {
      if (attempts < 60) setTimeout(check, 3000);
      else { console.log('Timed out — check docker logs'); process.exit(0); }
    }
  });
  req.on('error', () => {
    if (attempts < 60) setTimeout(check, 3000);
    else { console.log('Timed out — check docker logs'); process.exit(0); }
  });
  req.end();
};
check();
"`,
        on: [{
          event: "/READY url=(.+)/",
          done: true,
          run: {
            method: "local.set",
            params: { url: "{{event.matches.[0].[1]}}" }
          }
        }]
      },
    },
  ],
}
