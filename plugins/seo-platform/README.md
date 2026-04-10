# SEO Platform Plugin

Full SEO dashboard powered by [DataForSEO](https://dataforseo.com) — keyword research, rank tracking, backlink analysis, site audits, AI visibility, local SEO, and brand monitoring.

## Features

- **Dashboard** — Portfolio overview with cost tracking
- **Keyword Research** — Suggestions, search volume, difficulty, SERP analysis
- **Keyword Gap** — Competitive keyword gap analysis
- **Rank Tracking** — Position monitoring with history charts
- **Site Audit** — On-page technical SEO crawl (Lighthouse)
- **Domain Analysis** — WHOIS, technologies, categories, competitors
- **Backlinks** — Referring domains, anchor text, growth trends
- **AI Visibility** — Track mentions in ChatGPT, Claude, Gemini, Perplexity
- **Local SEO** — Google Business Profile, reviews, map rankings
- **Content Monitor** — Brand mentions, sentiment analysis, ratings

## Setup

1. Install the plugin from the OpenVoiceUI plugin catalog
2. Open **SEO Settings** from the desktop
3. Enter your DataForSEO API credentials (email + API password)
4. Open **SEO Platform** and add your first domain

## DataForSEO Account

Sign up at [dataforseo.com](https://dataforseo.com). Pricing is pay-as-you-go:

| Action | Typical Cost |
|--------|-------------|
| Keyword research | $0.001 - $0.02 |
| Rank tracking | $0.02 |
| Domain analysis | ~$0.09 |
| Backlink analysis | $0.02 - $0.05 |
| Site audit | $0.02 - $0.10 |
| AI visibility scan | ~$0.10 |

## For Platform Operators

If you host OpenVoiceUI for multiple users, set these environment variables to provide shared credentials:

```env
DATAFORSEO_LOGIN=your@email.com
DATAFORSEO_PASSWORD=your_api_password
```

When env vars are set, users skip the settings page — credentials are injected server-side.

## Architecture

- **Frontend**: Single HTML page with vanilla JS (no build step)
- **Backend**: Flask blueprint with SQLite storage
- **Proxy**: All DataForSEO requests go through the server (credentials never reach the browser)
- **Cache**: Every API response is automatically saved — repeat views are free
