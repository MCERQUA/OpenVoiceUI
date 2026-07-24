# jambot-browse — server-side co-browsing service (issue #154, Phase 1)
#
# One shared headless-Chromium pool for the whole JamBot fleet, isolated
# per-tenant by BrowserContext. Runs on the jambot-shared docker network,
# same pattern as supertonic-tts — NOT baked into every OVU image (Chromium
# is ~450MB; 26 copies would be wasteful).
#
# The Playwright base image ships a matching Chromium + all the system libs
# (fonts, nss, gtk, etc.) so we don't hand-maintain the apt list.
# ⚠️ KEEP THIS TAG IN LOCKSTEP WITH playwright== IN requirements.txt.
# The base image ships the Chromium build that its own Playwright expects; if
# the two drift, `playwright install chromium` below downloads a SECOND browser
# at build time and you silently ship two.
#
# This tag is also the ONLY thing that patches Chromium. It sat on v1.48.0
# (Chromium 130, Oct 2024) until 2026-07-24 — ~21 months of renderer/sandbox
# CVEs on the one container whose JOB is rendering untrusted attacker-controlled
# web content, and which egresses through a residential IP. Nothing flagged it
# because the image kept getting rebuilt, so it always LOOKED fresh; only the
# base layer was old. `mon:supply-chain` now tracks running-image age.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

# Python deps (Chromium itself is already in the base image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The base image bundles browsers under /ms-playwright already; make sure the
# chromium build our pinned playwright expects is present (no-op if cached).
RUN python3 -m playwright install chromium

COPY server.py .

# Run as the non-root 'pwuser' the Playwright image provides.
USER pwuser

EXPOSE 8712

# Single worker on purpose: one process owns the browser pool + session map.
CMD ["python3", "server.py"]
