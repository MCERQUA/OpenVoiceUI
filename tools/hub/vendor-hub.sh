#!/usr/bin/env bash
# vendor-hub.sh — copy the JamBot Hub (MCERQUA/jamdash public/) at ONE pinned commit into hub/ of this repo.
#
#   tools/hub/vendor-hub.sh <jamdash-git-dir> <commit>
#
# * Reads the tree with `git archive <commit> public` (never the working copy, so uncommitted edits never ship).
# * Drops public/data/ (the demo fixtures): a business's host never carries made-up numbers.
# * Keeps hub/_ovu/ (OpenVoiceUI's own files, e.g. hub-auth.js); refuses if jamdash itself ships an _ovu/ dir.
# * Writes hub/VENDORED.json: repo, full commit, UTC time, file count, sha256 per file. routes/hub.py serves it
#   as /hub/version.json and stamps the commit into every /hub/ page (<meta name="jambot-hub">).
# Nothing outside hub/ is touched. The previous tree is MOVED (never deleted) into the temp dir printed at the end;
# git also tracks every vendored file, so any earlier pin is recoverable.
set -euo pipefail
SRC="${1:?usage: vendor-hub.sh <jamdash-git-dir> <commit>}"
REV="${2:?usage: vendor-hub.sh <jamdash-git-dir> <commit>}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$HERE/hub"
COMMIT="$(git -C "$SRC" rev-parse --verify "$REV^{commit}")"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/vendor-hub.XXXXXX")"
git -C "$SRC" archive "$COMMIT" public | tar -x -C "$TMP"
[ -f "$TMP/public/index.html" ] || { echo "vendor-hub: $COMMIT has no public/index.html" >&2; exit 2; }
[ -e "$TMP/public/_ovu" ] && { echo "vendor-hub: jamdash ships its own _ovu/ (reserved for OpenVoiceUI)" >&2; exit 2; }
if find "$TMP/public" -type l | grep -q .; then echo "vendor-hub: symlinks in the archive; refusing" >&2; exit 2; fi
mkdir -p "$DEST"
# rebuild: everything in hub/ except _ovu/ is replaced by the archive's public/ (minus data/)
for e in "$DEST"/* "$DEST"/.[!.]*; do
  [ -e "$e" ] || continue
  [ "$(basename "$e")" = "_ovu" ] && continue
  mv "$e" "$TMP/.replaced-$(basename "$e")"
done
( cd "$TMP/public" && tar -c --exclude=./data . ) | tar -x -C "$DEST"
python3 - "$DEST" "$COMMIT" "$(git -C "$SRC" config --get remote.origin.url || echo unknown)" <<'PY'
import hashlib, json, os, sys, datetime
dest, commit, repo = sys.argv[1:4]
if 'jamdash' in repo:
    repo = 'MCERQUA/jamdash'
files = {}
for root, dirs, names in os.walk(dest):
    dirs[:] = sorted(d for d in dirs if d != '_ovu')
    for n in sorted(names):
        p = os.path.join(root, n)
        rel = os.path.relpath(p, dest)
        if rel == 'VENDORED.json':
            continue
        files[rel] = hashlib.sha256(open(p, 'rb').read()).hexdigest()
assert 'index.html' in files and not any(r.startswith('data/') for r in files)
pin = {'repo': repo, 'commit': commit, 'vendored_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
       'file_count': len(files), 'fixtures_dropped': True, 'sha256': files}
open(os.path.join(dest, 'VENDORED.json'), 'w').write(json.dumps(pin, indent=1, sort_keys=True) + '\n')
print(f'vendored {repo}@{commit[:7]}: {len(files)} files into {dest} (data/ dropped)')
PY
echo "vendor-hub: previous tree (if any) kept under $TMP"
