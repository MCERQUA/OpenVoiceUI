---
sidebar_position: 3
title: Security
---

# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in OpenVoiceUI, please report it responsibly.

**DO NOT open a public GitHub issue for security vulnerabilities.**

Instead, email: **mikecerqua@gmail.com**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

## Response Timeline

- **Acknowledgment:** Within 48 hours
- **Assessment:** Within 1 week
- **Fix:** Depends on severity (critical: ASAP, high: 1 week, medium: next release)

## Scope

### In Scope

- XSS (cross-site scripting) via LLM responses or user input
- Prompt injection that bypasses system prompt boundaries
- Path traversal on file-serving endpoints
- Authentication bypass (when Clerk auth is enabled)
- Rate limiting bypass
- Information disclosure (error messages, stack traces, internal paths)
- Server-side request forgery (SSRF)
- Denial of service via resource exhaustion

### Out of Scope

- Vulnerabilities in third-party dependencies (report to upstream)
- Issues requiring physical access to the server
- Social engineering attacks
- Vulnerabilities in the OpenClaw Gateway itself (separate project)

## Known Limitations

### Prompt Injection (Issue #23)

The system prompt and user input are currently concatenated in the same message field sent to the LLM. A prompt armor delimiter provides partial mitigation, but full separation requires an OpenClaw Gateway protocol change to support distinct system/user message roles.

**Status:** Open, labeled `needs-gateway-support`

**Mitigation:** Prompt armor boundary, face name sanitization (bracket stripping), input length limit (4000 chars)

## Security Measures

- **XSS:** All LLM output is HTML-escaped before rendering (`escapeHtml()` on every `innerHTML` path)
- **CSP:** Content Security Policy headers restrict script/resource origins
- **Rate Limiting:** 200 requests/minute per IP (configurable)
- **Input Validation:** Message length caps, file size limits, path sanitization
- **Error Handling:** Generic error messages only -- no internal details in HTTP responses
- **CORS:** Strict origin matching (no wildcards)

## For Contributors

Before submitting security-related PRs, please review:
- [Architecture](https://github.com/MCERQUA/OpenVoiceUI/blob/main/docs/ARCHITECTURE.md) -- understand the full system
- [PR Review Checklist](https://github.com/MCERQUA/OpenVoiceUI/blob/main/docs/PR-REVIEW-CHECKLIST.md) -- security checks for every PR

**Important:** Security fixes must not break existing functionality. If a fix requires changing how a system works (e.g., the tag system, display pipeline), the PR must also include fixes for all affected systems. See the PR Review Checklist for the list of systems that must stay in sync.
