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
    // Note: first launch may take 5-10 minutes while Supertonic TTS downloads its model.
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
console.log('Waiting for OpenVoiceUI on port ' + port + ' (first launch may take up to 10 min)...');
let attempts = 0;
const MAX = 120;
const check = () => {
  attempts++;
  if (attempts % 10 === 0) console.log('Still waiting... (' + Math.round(attempts * 5 / 60) + ' min elapsed)');
  const req = http.get('http://localhost:' + port + '/health/ready', (r) => {
    if (r.statusCode < 400) {
      const marker = 'OVU' + '_OPEN';
      console.log(marker + '=http://localhost:' + port);
      process.exit(0);
    } else {
      if (attempts < MAX) setTimeout(check, 5000);
      else { console.log('Timed out after 10 min — check Docker Desktop logs for errors'); process.exit(0); }
    }
  });
  req.on('error', () => {
    if (attempts < MAX) setTimeout(check, 5000);
    else { console.log('Timed out after 10 min — check Docker Desktop logs for errors'); process.exit(0); }
  });
  req.end();
};
check();
"`,
        on: [{
          event: "/OVU_OPEN=(.+)/",
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
