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
 * Two modes, nothing else:
 *   ovu-sandbox-shell          start the sandboxed login shell
 *   ovu-sandbox-shell --reap   drop to the sandbox uid and kill(-1, SIGKILL):
 *                              every sandbox process (setsid/nohup survivors
 *                              included) dies. The server runs this when the
 *                              last terminal session closes, because it cannot
 *                              signal another uid itself. No other argument
 *                              is accepted.
 *
 * Hardening:
 *   - refuses unless the REAL uid is the server uid (CALLER_UID)
 *   - reads no environment: nothing the caller passes can choose the target
 *     uid, the program or the directory
 *   - drops ALL supplementary groups, then gid, then uid (setres*), and
 *     verifies root cannot be regained
 *   - PR_SET_NO_NEW_PRIVS so su/passwd/mount (setuid in the base image) can
 *     never elevate from inside the sandbox
 *   - closes EVERY inherited fd above stderr: close_range(3, ~0U), falling
 *     back to closing each fd listed in /proc/self/fd
 *   - fresh minimal environment, umask 002
 *   - resource limits (per process unless noted), so a shell cannot starve or
 *     fill the server's container:
 *       RLIMIT_NPROC  256      processes for the sandbox uid (all sessions)
 *       RLIMIT_AS     4 GiB    address space (node/V8 reserve a lot of virtual
 *                              memory; 4 GiB lets node, python and git run)
 *       RLIMIT_CPU    3600 s   CPU seconds per process
 *       RLIMIT_FSIZE  1 GiB    largest file it can write
 *       RLIMIT_NOFILE 4096     open files
 *       RLIMIT_CORE   0        no core dumps
 */
#define _GNU_SOURCE
#include <errno.h>
#include <grp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
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
#define SANDBOX_AS     (4ULL << 30)
#define SANDBOX_CPU    3600
#define SANDBOX_FSIZE  (1ULL << 30)
#define SANDBOX_NOFILE 4096

static void die(const char *what) {
    /* stderr is the pty, so the terminal user sees why it closed. */
    fprintf(stderr, "ovu-sandbox-shell: refused: %s (%s)\r\n", what, strerror(errno));
    _exit(126);
}

static void close_inherited_fds(void) {
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 3U, ~0U, 0U) == 0) return;
#endif
    /* Fallback (kernel < 5.9): close exactly the fds that are open, however
     * high (a capped loop missed fds above the cap). Collect first, then close,
     * so closing does not disturb the directory walk. */
    int fds[4096];
    int n = 0, more = 1;
    while (more) {
        more = 0; n = 0;
        DIR *d = opendir("/proc/self/fd");
        if (!d) die("cannot list /proc/self/fd to close inherited fds");
        int self = dirfd(d);
        struct dirent *e;
        while ((e = readdir(d)) != NULL) {
            if (e->d_name[0] < '0' || e->d_name[0] > '9') continue;
            int fd = atoi(e->d_name);
            if (fd > 2 && fd != self) {
                if (n < (int)(sizeof fds / sizeof fds[0])) fds[n++] = fd; else more = 1;
            }
        }
        closedir(d);
        for (int i = 0; i < n; i++) close(fds[i]);
    }
}

static void drop_to_sandbox(void) {
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
}

static void set_limit(int res, rlim_t v) {
    /* Never above an existing (lower) hard limit: lowering always succeeds. */
    struct rlimit cur;
    if (getrlimit(res, &cur) == 0 && cur.rlim_max != RLIM_INFINITY && cur.rlim_max < v)
        v = cur.rlim_max;
    struct rlimit l = { v, v };
    if (setrlimit(res, &l) != 0) die("setrlimit");
}

int main(int argc, char **argv) {
    errno = 0;
    int reap = 0;
    if (argc == 2 && strcmp(argv[1], "--reap") == 0) reap = 1;
    else if (argc != 1) die("takes no arguments except --reap");
    if (getuid() != (uid_t)CALLER_UID) die("caller is not the OpenVoiceUI server user");
    if (geteuid() != 0) die("helper is not setuid root");

    close_inherited_fds();
    if (clearenv() != 0) die("clearenv");
    drop_to_sandbox();

    if (reap) {
        /* As the sandbox uid, -1 reaches exactly the processes this uid may
         * signal: every sandbox process (not this one, not the server). */
        if (kill(-1, SIGKILL) != 0 && errno != ESRCH) die("kill sandbox processes");
        _exit(0);
    }

    set_limit(RLIMIT_NPROC, SANDBOX_NPROC);
    set_limit(RLIMIT_AS, SANDBOX_AS);
    set_limit(RLIMIT_CPU, SANDBOX_CPU);
    set_limit(RLIMIT_FSIZE, SANDBOX_FSIZE);
    set_limit(RLIMIT_NOFILE, SANDBOX_NOFILE);
    set_limit(RLIMIT_CORE, 0);
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
