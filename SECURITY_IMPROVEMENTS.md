# Security Improvements - Automated Scanning

Automated security scanning with zero code changes.

## What's Included

### 1. Dependabot (`.github/dependabot.yml`)
- Weekly dependency updates for Python, GitHub Actions, Docker
- Automatic PRs for security patches
- Labeled with `dependencies` and `security`

### 2. Security Workflow (`.github/workflows/security.yml`)
Three scanners:
- **Bandit** - Python SAST (finds hardcoded secrets, SQL injection, etc.)
- **pip-audit** - CVE scanning for Python dependencies
- **Trivy** - Container and filesystem vulnerability scanning

Runs on: push to main/dev, PRs, weekly schedule, manual trigger

### 3. Benefits

✅ Zero breaking changes (config files only)  
✅ Automated vulnerability monitoring  
✅ Free GitHub features  
✅ Non-blocking (reports only, doesn't fail builds)

## Usage

### View Dependabot PRs
1. Go to **Pull Requests** tab
2. Filter by `dependencies` or `security` label
3. Review and merge

### View Scan Results
1. Go to **Actions** tab → **Security Scanning** workflow
2. View logs and download artifacts
3. **Security** tab shows Trivy results

### Respond to Findings
- **High severity**: Review CVE, update dependency or patch
- **Medium/Low**: Update during regular maintenance
- **False positives**: Document in `.bandit` or Trivy config

## Maintenance

**Weekly**: Review Dependabot PRs (5-10 min)  
**Monthly**: Check security scan results

## Resources

- [Dependabot](https://docs.github.com/en/code-security/dependabot)
- [Bandit](https://bandit.readthedocs.io/)
- [pip-audit](https://pypi.org/project/pip-audit/)
- [Trivy](https://trivy.dev/)

---

**Summary**: Automated security monitoring with zero code changes and zero risk. Immediate value through dependency updates and vulnerability scanning.
