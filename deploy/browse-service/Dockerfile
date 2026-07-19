# jambot-browse — server-side co-browsing service (issue #154, Phase 1)
#
# One shared headless-Chromium pool for the whole JamBot fleet, isolated
# per-tenant by BrowserContext. Runs on the jambot-shared docker network,
# same pattern as supertonic-tts — NOT baked into every OVU image (Chromium
# is ~450MB; 26 copies would be wasteful).
#
# The Playwright base image ships a matching Chromium + all the system libs
# (fonts, nss, gtk, etc.) so we don't hand-maintain the apt list.
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

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
