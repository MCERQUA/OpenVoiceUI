#!/usr/bin/env bash
# =============================================================================
# keep-alive-system :: workspace-backup.sh
# Git-based workspace snapshot utility
# Usage: ./workspace-backup.sh [--init|--status]
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SELF_DIR}/keep-alive.conf"

LOG_FILE="${LOG_DIR}/workspace-backup.log"
mkdir -p "${LOG_DIR}"

log() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    printf '[%s] %s\n' "${ts}" "$*" | tee -a "${LOG_FILE}"
}

# --- Handle --status ---
if [[ "${1:-}" == "--status" ]]; then
    if [[ -d "${WORKSPACE_DIR}/.git" ]]; then
        echo "Git initialized: yes"
        echo "Commits: $(git -C "${WORKSPACE_DIR}" rev-list --count HEAD 2>/dev/null || echo 0)"
        echo "Last commit: $(git -C "${WORKSPACE_DIR}" log -1 --format='%ci %s' 2>/dev/null || echo 'none')"
        git -C "${WORKSPACE_DIR}" status --short
    else
        echo "Git initialized: no"
    fi
    exit 0
fi

# --- Initialize git if needed ---
init_git() {
    if [[ ! -d "${WORKSPACE_DIR}/.git" ]]; then
        log "Initializing git repository in ${WORKSPACE_DIR}"
        git -C "${WORKSPACE_DIR}" init --initial-branch=main
        git -C "${WORKSPACE_DIR}" config user.name "keep-alive-system"
        git -C "${WORKSPACE_DIR}" config user.email "keep-alive@localhost"

        cat > "${WORKSPACE_DIR}/.gitignore" <<'GITIGNORE_EOF'
node_modules/
.DS_Store
*.tmp
*.swp
*.log
__pycache__/
GITIGNORE_EOF

        git -C "${WORKSPACE_DIR}" add -A
        git -C "${WORKSPACE_DIR}" commit -m "Initial workspace snapshot" --allow-empty
        log "Git repository initialized with initial commit"
    fi
}

# --- Handle --init ---
if [[ "${1:-}" == "--init" ]]; then
    init_git
    exit 0
fi

# --- Main: take snapshot ---
if [[ "${BACKUP_ENABLED}" != "true" ]]; then
    log "Backups disabled in configuration, skipping"
    exit 0
fi

init_git

cd "${WORKSPACE_DIR}"

git add -A

if git diff --cached --quiet; then
    log "No workspace changes to snapshot"
    exit 0
fi

file_count=$(git diff --cached --name-only | wc -l)
timestamp=$(date '+%Y-%m-%d %H:%M:%S')
commit_msg="Workspace snapshot ${timestamp} (${file_count} files changed)"

git commit -m "${commit_msg}"
log "Snapshot committed: ${commit_msg}"

# --- Prune if over retention limit ---
commit_count=$(git rev-list --count HEAD)
if (( commit_count > BACKUP_MAX_COMMITS )); then
    git gc --auto --quiet 2>/dev/null || true
fi

"${SELF_DIR}/notify.sh" info "Workspace Backup" "${file_count} files changed"

exit 0
