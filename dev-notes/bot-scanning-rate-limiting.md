# Bot Scanning / Rate Limiting

**Date:** 2026-02-28
**Observed on:** Foamology (foamology.jam-bot.com), likely all client domains
**Priority:** Low — nothing is leaking
**GitHub:** #167 (closed)

## What's Happening

Automated bots constantly scan public websites looking for exposed sensitive files:
- `.env`, `.env.local`, `.env.production`, `.env.bak` — password/API key files
- `.git/config` — repo configuration
- `.aws/credentials` — cloud provider secrets
- `phpinfo.php`, `info.php`, `test.php` — server info pages
- `docker-compose.yml`, `terraform.tfstate` — infrastructure files
- `swagger.json`, `config.json`, `settings.py` — app config
- `storage/logs/laravel.log` — application logs

This is normal internet background noise — every public website gets this.

## Current Status: SAFE

All probes return:
- **302** (redirect to homepage) — Flask catches unknown routes
- **401** (unauthorized) — protected API routes reject them
- **404** (not found) — file doesn't exist

No secrets, configs, or sensitive data are exposed.

## Why It's Worth Addressing

1. **Log noise** — dozens of scan requests per minute clutter real traffic logs
2. **Resource waste** — each probe is a request the server processes
3. **Future risk** — if we misconfigure something, these bots find it within hours

## Possible Fixes (for later)

1. **Nginx rate limiting** — limit requests per IP per second
   ```nginx
   limit_req_zone $binary_remote_addr zone=one:10m rate=10r/s;
   ```

2. **Block known scanner patterns** — return 403 for requests to `.env`, `.git`, `.php` etc.
   ```nginx
   location ~ /\.(env|git|aws|ssh) { return 403; }
   location ~ \.php$ { return 403; }
   ```

3. **Cloudflare WAF rules** — block at the CDN level before requests reach the server

4. **fail2ban** — auto-ban IPs that make too many suspicious requests

## Log Evidence (Foamology, 2026-02-27 23:51-53)

```
GET /.env HTTP/1.1 → 302
GET /.env.local → 302
GET /.git/config → 302
GET /.aws/credentials → 302
GET /phpinfo.php → 302
GET /docker-compose.yml → 302
GET /terraform.tfstate → 302
GET /swagger.json → 302
GET /.htpasswd → 302
```

All from Cloudflare proxy IPs (162.158.x.x) — scanner traffic routed through CF.
