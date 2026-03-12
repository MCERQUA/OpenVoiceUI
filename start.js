module.exports = {
  run: [
    // Start all containers
    {
      method: "shell.run",
      params: {
        message: "docker compose -f docker-compose.yml -f docker-compose.pinokio.yml up -d",
      },
    },

    // Wait for OpenVoiceUI to be ready
    {
      method: "shell.run",
      params: {
        message: `node -e "
const http = require('http');
const port = process.env.PORT || 5001;
let attempts = 0;
const check = () => {
  attempts++;
  http.get('http://localhost:' + port + '/health/ready', (r) => {
    if (r.statusCode < 400) {
      console.log('Ready after ' + attempts + ' attempts');
    } else {
      if (attempts < 30) setTimeout(check, 2000);
      else { console.log('Timeout — opening anyway'); }
    }
  }).on('error', () => {
    if (attempts < 30) setTimeout(check, 2000);
    else { console.log('Timeout — opening anyway'); }
  });
};
check();
"`,
        env: {
          PORT: "{{local.PORT||5001}}",
        },
      },
    },

    // Mark as running
    {
      method: "local.set",
      params: {
        running: true,
      },
    },

    // Open OpenVoiceUI in browser
    {
      method: "browser.open",
      params: {
        url: "http://localhost:{{local.PORT||5001}}",
      },
    },
  ],
}
