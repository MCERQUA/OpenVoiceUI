/*
 * ovu-sandbox-shell — start the terminal canvas block's login shell as the
 * unprivileged `sandbox` user instead of the OpenVoiceUI server's own user.
 *
 * Why this exists: /ws/terminal hands a shell to anyone holding a Clerk
 * session or the tenant's X-Agent-Key. Before this helper the shell ran as
 * the server's uid (appuser, 1001), so it could read /proc/1/environ (every
 * secret the server holds), the db/vault dirs and the mounted key files, and
 * rewrite the server's own code. The server runs with no capabilities, so it
 * cannot change uid itself; this small setuid-root binary does exactly one
 * thing: drop to the sandbox uid/gid, scrub the environment, and exec bash.
 *
 * Installed by deploy/terminal-sandbox/install.sh as root:appuser mode 4750,
 * so only the server's uid (and group) can run it; the sandbox user cannot.
 *
 * Hardening:
 *   - refuses unless the REAL uid is the server uid (CALLER_UID)
 *   - takes no arguments and reads no environment: nothing the caller passes
 *     can choose the target uid, the program or the directory
 *   - drops ALL supplementary groups, then gid, then uid (setres*), and
 *     verifies root cannot be regained
 *   - PR_SET_NO_NEW_PRIVS so su/passwd/mount (setuid in the base image) can
 *     never elevate from inside the sandbox
 *   - closes every inherited fd above stderr
 *   - fresh minimal environment, umask 002, per-uid process limit
 */
#define _GNU_SOURCE
#include <errno.h>
#include <grp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef CALLER_UID
#define CALLER_UID 1001
#endif
#ifndef SANDBOX_UID
#define SANDBOX_UID 1500
#endif
#ifndef SANDBOX_GID
#define SANDBOX_GID 1500
#endif
#ifndef SANDBOX_HOME
#define SANDBOX_HOME "/home/sandbox"
#endif
#ifndef SANDBOX_WORKSPACE
#define SANDBOX_WORKSPACE "/srv/code-workspace"
#endif
#ifndef SANDBOX_NPROC
#define SANDBOX_NPROC 256
#endif

static void die(const char *what) {
    /* stderr is the pty, so the terminal user sees why it closed. */
    fprintf(stderr, "ovu-sandbox-shell: refused: %s (%s)\r\n", what, strerror(errno));
    _exit(126);
}

static void close_inherited_fds(void) {
    long maxfd = sysconf(_SC_OPEN_MAX);
    if (maxfd < 0 || maxfd > 65536) maxfd = 65536;
    for (int fd = 3; fd < maxfd; fd++) close(fd);
}

int main(int argc, char **argv) {
    (void)argv;
    errno = 0;
    if (argc != 1) die("takes no arguments");
    if (getuid() != (uid_t)CALLER_UID) die("caller is not the OpenVoiceUI server user");
    if (geteuid() != 0) die("helper is not setuid root");

    close_inherited_fds();
    if (clearenv() != 0) die("clearenv");

    if (setgroups(0, NULL) != 0) die("setgroups");
    if (setresgid(SANDBOX_GID, SANDBOX_GID, SANDBOX_GID) != 0) die("setresgid");
    if (setresuid(SANDBOX_UID, SANDBOX_UID, SANDBOX_UID) != 0) die("setresuid");

    /* Verify the drop is permanent. */
    uid_t r, e, s;
    gid_t rg, eg, sg;
    if (getresuid(&r, &e, &s) != 0 || r != SANDBOX_UID || e != SANDBOX_UID || s != SANDBOX_UID)
        die("uid drop did not stick");
    if (getresgid(&rg, &eg, &sg) != 0 || rg != SANDBOX_GID || eg != SANDBOX_GID || sg != SANDBOX_GID)
        die("gid drop did not stick");
    if (setuid(0) == 0 || seteuid(0) == 0) { errno = EPERM; die("root regained"); }
    if (getgroups(0, NULL) != 0) { errno = EPERM; die("supplementary groups remain"); }

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) die("no_new_privs");

    struct rlimit nproc = { SANDBOX_NPROC, SANDBOX_NPROC };
    setrlimit(RLIMIT_NPROC, &nproc);
    struct rlimit core = { 0, 0 };
    setrlimit(RLIMIT_CORE, &core);
    umask(002);

    const char *cwd = SANDBOX_WORKSPACE;
    if (chdir(cwd) != 0) {
        cwd = SANDBOX_HOME;
        if (chdir(cwd) != 0) die("no usable working directory");
    }

    char *envp[] = {
        "HOME=" SANDBOX_HOME,
        "USER=sandbox",
        "LOGNAME=sandbox",
        "SHELL=/bin/bash",
        "TERM=xterm-256color",
        "LANG=C.UTF-8",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        NULL,
    };
    char *shell_argv[] = { "-bash", NULL };   /* leading '-' = login shell */
    execve("/bin/bash", shell_argv, envp);
    char *sh_argv[] = { "-sh", NULL };
    execve("/bin/sh", sh_argv, envp);
    die("exec shell");
    return 127;
}
