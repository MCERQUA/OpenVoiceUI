# Twitter Bookmarks Plugin

Connect your X/Twitter account to fetch and research your bookmarks through your AI agent.

## Setup

1. Install the plugin from Settings
2. Open the "Twitter Bookmarks Setup" page
3. Enter your `ct0` and `auth_token` cookies from x.com
4. Click Connect

## How to get cookies

1. Go to x.com in your browser (make sure you're logged in)
2. Press F12 to open dev tools
3. Click Application tab → Cookies → https://x.com
4. Copy the values for `ct0` and `auth_token`

## Usage

Ask your agent:
- "Check my bookmarks"
- "What's new in my X bookmarks?"
- "Research my latest bookmarks"

## Technical Details

- Uses X's internal GraphQL API (same as x.com frontend)
- No paid X API key required
- Cookies stored on server only (never in browser)
- Cookies expire when your X session expires (~yearly)
