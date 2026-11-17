#!/bin/sh
# Terminal sandbox: give the terminal canvas block's shell its own user.
#
# Runs as root at image build time, AFTER the `appuser` (server) user exists.
# The server process (appuser, uid 1001) keeps every secret it already has;
# the terminal shell runs as `sandbox` (uid 1500), which:
#   - gets no inherited environment (no API keys, tokens, Clerk secret)
#   - cannot read /proc/1/environ (different uid)
#   - cannot enter /app (code, runtime/db, vault, uploads, every tenant mount
#     under /app/runtime) or /mnt (mounted platform key files) — both are
#     0750 with a group the sandbox user is not in
#   - works in /srv/code-workspace, shared with the server through the
#     `sandbox` group so the Monaco editor block sees the same files
#
# Used by the Dockerfile and by tests/test_terminal_sandbox_image.sh, so the
# image and the proof run the same steps.
set -eu

CALLER_UID="${OVU_CALLER_UID:-1001}"
SANDBOX_UID="${OVU_SANDBOX_UID:-1500}"
WORKSPACE="${OVU_SANDBOX_WORKSPACE:-/srv/code-workspace}"
HELPER=/usr/local/libexec/ovu-sandbox-shell
SRC="$(dirname "$0")/ovu-sandbox-shell.c"

caller_user="$(getent passwd "$CALLER_UID" | cut -d: -f1)"
[ -n "$caller_user" ] || { echo "install.sh: no user with uid $CALLER_UID" >&2; exit 1; }
caller_group="$(id -gn "$caller_user")"

# 1. the sandbox user: own uid/gid, own home, NOT in the server's group
if ! getent passwd sandbox >/dev/null; then
    groupadd -g "$SANDBOX_UID" sandbox
    useradd -m -u "$SANDBOX_UID" -g sandbox -s /bin/bash -d /home/sandbox sandbox
fi
chmod 0700 /home/sandbox

# 2. the server joins the sandbox group (to share the code workspace);
#    the reverse never happens
usermod -aG sandbox "$caller_user"

# 3. the setuid helper (only the server's group may execute it)
mkdir -p /usr/local/libexec
gcc -O2 -Wall -Wextra -Werror \
    -DCALLER_UID="$CALLER_UID" -DSANDBOX_UID="$SANDBOX_UID" -DSANDBOX_GID="$SANDBOX_UID" \
    -DSANDBOX_WORKSPACE="\"$WORKSPACE\"" \
    -o "$HELPER" "$SRC"
chown "root:$caller_group" "$HELPER"
chmod 4750 "$HELPER"

# 4. the shared code workspace (setgid so new files stay in group sandbox)
mkdir -p "$WORKSPACE"
chown sandbox:sandbox "$WORKSPACE"
chmod 2770 "$WORKSPACE"

# 5. close the server's trees to the sandbox user. Bind mounts land under
#    these directories, so their own modes stop mattering: the
#    sandbox user cannot traverse the parent.
chown "$caller_user:$caller_group" /app
chmod 0750 /app
chown "root:$caller_group" /mnt
chmod 0750 /mnt

echo "terminal sandbox installed: helper=$HELPER sandbox_uid=$SANDBOX_UID workspace=$WORKSPACE"
