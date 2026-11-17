"""No-follow, beneath-root file access for paths an untrusted party can write.

Several trees the server reads and writes are also writable by someone it must
not trust: uploads/, canvas-pages/ and music dirs (the tenant agent's openclaw
container mounts them read-write) and the terminal sandbox's code workspace.
Checking a path (resolve() + containment) and then opening it BY NAME is a
race: between the two, the other party swaps a directory or the file for a
symlink and the server reads /proc/self/environ or writes into /app.

Everything here walks the path ONE COMPONENT AT A TIME from an fd on the
trusted root, opening each directory with O_DIRECTORY|O_NOFOLLOW and the final
component with O_NOFOLLOW, then fstat()s the opened fd. Whatever is swapped
in after a step cannot redirect it: the next step is relative to an fd that
already pins the real directory. A symlink anywhere in the path is refused
(ELOOP), '..' and absolute components are refused before any syscall, and the
final open uses O_NONBLOCK so a planted FIFO cannot hang the worker.

Callers then read/write/serve from the returned fd (or /proc/self/fd/<fd>,
which reopens the same inode, never re-resolves the name).
"""

import errno
import os
import stat

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class UnsafePath(OSError):
    """The path is not a plain relative path beneath the root, or it crosses a symlink."""


def split_rel(rel) -> list:
    """Lexically split a caller-supplied relative path; refuse anything that could climb."""
    if not isinstance(rel, str) or not rel or '\x00' in rel or '\\' in rel:
        raise UnsafePath(errno.EINVAL, 'invalid path')
    if rel.startswith('/'):
        raise UnsafePath(errno.EINVAL, 'absolute path')
    parts = [p for p in rel.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        raise UnsafePath(errno.EINVAL, 'path escapes its root')
    return parts


def open_root(root) -> int:
    """fd on the root directory. The root's own last component is NOT followed.

    Callers anchor at the agent-writable dir's MOUNT POINT (uploads/, canvas-pages/,
    music/, ... whose parents are the server's own tree) and walk everything below
    it no-follow. A root that is itself a symlink (e.g. an `albums` dir the agent
    replaced with `albums -> /app/runtime`) is refused with ELOOP, never followed.
    """
    try:
        return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePath(e.errno, f'root is not a plain directory: {root}') from None
        raise


def rel_under(root, path) -> str:
    """`path` (absolute, or relative to root) as a relative path under root, LEXICALLY.

    Nothing is resolved: the no-follow walk does the containment. Raises UnsafePath
    if the path is not lexically inside root or climbs with '..'."""
    p = str(path)
    if not os.path.isabs(p):
        return '/'.join(split_rel(p))
    r = os.path.normpath(str(root))
    n = os.path.normpath(p)
    if n == r:
        raise UnsafePath(errno.EINVAL, 'path is the root itself')
    if not n.startswith(r.rstrip('/') + '/') or '/../' in p + '/':
        raise UnsafePath(errno.EINVAL, f'path is not under its root: {p}')
    return '/'.join(split_rel(n[len(r.rstrip('/')) + 1:]))


def _walk_dirs(root_fd: int, parts, create=False, dir_mode=0o755):
    """fd of root/parts[0]/.../parts[-1], every step no-follow. Caller closes it."""
    fd = os.dup(root_fd)
    try:
        for name in parts:
            try:
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, dir_mode, dir_fd=fd)
                    created = True
                except FileExistsError:
                    created = False
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                if created:
                    try:
                        os.fchmod(nfd, dir_mode)  # exact mode, not mode & ~umask
                    except OSError:
                        pass
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafePath(e.errno, f'not a plain directory: {name}') from None
                raise
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_dir_beneath(root, rel, create=False, dir_mode=0o755) -> int:
    """fd on directory root/rel (rel '' or None = the root itself)."""
    parts = split_rel(rel) if rel else []
    root_fd = open_root(root)
    try:
        return _walk_dirs(root_fd, parts, create=create, dir_mode=dir_mode)
    finally:
        os.close(root_fd)


def open_beneath(root, rel, flags=os.O_RDONLY, mode=0o644, create_dirs=False,
                 dir_mode=0o755) -> int:
    """fd on the regular file root/rel, opened without following any symlink.

    flags: os.O_RDONLY, or os.O_WRONLY|os.O_CREAT|os.O_TRUNC (etc.) to write.
    Raises UnsafePath for symlinks / non-regular files, FileNotFoundError if absent.
    """
    parts = split_rel(rel)
    dfd = open_dir_beneath(root, '/'.join(parts[:-1]) if len(parts) > 1 else None,
                           create=create_dirs, dir_mode=dir_mode)
    try:
        return open_in_dir(dfd, parts[-1], flags, mode)
    finally:
        os.close(dfd)


def open_in_dir(dir_fd: int, name: str, flags=os.O_RDONLY, mode=0o644) -> int:
    """fd on regular file `name` directly inside dir_fd, no-follow, verified by fstat."""
    if not name or '/' in name or name in ('.', '..') or '\x00' in name:
        raise UnsafePath(errno.EINVAL, 'invalid file name')
    try:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, mode,
                     dir_fd=dir_fd)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise UnsafePath(e.errno, f'symlink refused: {name}') from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafePath(errno.EINVAL, f'not a regular file: {name}')
        os.set_blocking(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_beneath(root, rel, max_bytes=None) -> bytes:
    fd = open_beneath(root, rel)
    try:
        return read_fd(fd, max_bytes)
    finally:
        os.close(fd)


# Default ceiling for whole-file reads into memory (a planted 50 GB sparse file must
# not OOM the server). Streams (send_file_beneath) are not capped by this.
DEFAULT_MAX_READ = int(os.getenv('OVU_SAFE_READ_MAX_BYTES', str(256 * 1024 * 1024)))


def read_fd(fd: int, max_bytes=None) -> bytes:
    if max_bytes is None:
        max_bytes = DEFAULT_MAX_READ
    size = os.fstat(fd).st_size
    if max_bytes is not None and size > max_bytes:
        raise UnsafePath(errno.EFBIG, 'file too large')
    chunks = []
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            break
        chunks.append(b)
        if max_bytes is not None and sum(map(len, chunks)) > max_bytes:
            raise UnsafePath(errno.EFBIG, 'file too large')
    return b''.join(chunks)


def write_beneath(root, rel, data: bytes, mode=0o644, create_dirs=False, dir_mode=0o755) -> None:
    fd = open_beneath(root, rel, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode,
                      create_dirs=create_dirs, dir_mode=dir_mode)
    try:
        try:
            os.fchmod(fd, mode)  # exact mode on a file we own; EPERM on another user's file
        except OSError:
            pass
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)


def lstat_beneath(root, rel):
    """lstat of root/rel with the directories walked no-follow; None if absent/unsafe."""
    try:
        parts = split_rel(rel)
        dfd = open_dir_beneath(root, '/'.join(parts[:-1]) if len(parts) > 1 else None)
    except (OSError, UnsafePath):
        return None
    try:
        return os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
    except OSError:
        return None
    finally:
        os.close(dfd)


def fd_path(fd: int) -> str:
    """A path that reopens exactly this fd's inode (no name re-resolution)."""
    return f'/proc/self/fd/{fd}'


class BeneathFile:
    """A file slot inside an already-opened directory fd: write_bytes()/exists().

    Stands in for a Path where code later writes generated bytes: the directory
    is pinned when this is made, so swapping it for a symlink afterwards cannot
    redirect the write.
    """

    def __init__(self, dir_fd: int, name: str, mode: int = 0o644):
        self.dir_fd, self.name, self.mode = dir_fd, name, mode

    def write_bytes(self, data: bytes) -> int:
        fd = open_in_dir(self.dir_fd, self.name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, self.mode)
        try:
            try:
                os.fchmod(fd, self.mode)
            except OSError:
                pass
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            return len(data)
        finally:
            os.close(fd)

    def exists(self) -> bool:
        try:
            return stat.S_ISREG(os.stat(self.name, dir_fd=self.dir_fd, follow_symlinks=False).st_mode)
        except OSError:
            return False

    def __str__(self):
        return self.name


def _parent_and_name(rel):
    parts = split_rel(rel)
    return ('/'.join(parts[:-1]) or None), parts[-1]


def atomic_write_beneath(root, rel, data: bytes, mode=0o644, create_dirs=False, dir_mode=0o755) -> None:
    """Replace root/rel atomically: random O_EXCL temp in the pinned dir, fchmod, rename.

    The temp name is unpredictable and created O_CREAT|O_EXCL|O_NOFOLLOW, so a
    name the other party pre-planted (as a symlink) can never be written through;
    the rename replaces whatever sits at the final name without following it."""
    parent, name = _parent_and_name(rel)
    dfd = open_dir_beneath(root, parent, create=create_dirs, dir_mode=dir_mode)
    try:
        for _ in range(16):
            tmp = f'.{name[:40]}.{os.urandom(8).hex()}.tmp'
            try:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             mode, dir_fd=dfd)
                break
            except FileExistsError:
                continue
        else:
            raise UnsafePath(errno.EEXIST, 'could not create a temp file')
        try:
            try:
                os.fchmod(fd, mode)
            except OSError:
                pass
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        except BaseException:
            os.close(fd)
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
        os.close(fd)
        try:
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def append_beneath(root, rel, data: bytes, mode=0o644, create_dirs=False) -> None:
    fd = open_beneath(root, rel, os.O_WRONLY | os.O_APPEND | os.O_CREAT, mode, create_dirs=create_dirs)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)


def rename_beneath(root, src_rel, dst_rel, replace=True) -> None:
    """Rename inside root with both parents pinned (the names themselves are never followed)."""
    sp, sn = _parent_and_name(src_rel)
    dp, dn = _parent_and_name(dst_rel)
    sfd = open_dir_beneath(root, sp)
    try:
        dfd = open_dir_beneath(root, dp)
        try:
            if not replace:
                try:
                    os.stat(dn, dir_fd=dfd, follow_symlinks=False)
                    raise FileExistsError(errno.EEXIST, dst_rel)
                except FileNotFoundError:
                    pass
            os.replace(sn, dn, src_dir_fd=sfd, dst_dir_fd=dfd)
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def unlink_beneath(root, rel, missing_ok=False) -> None:
    """Remove root/rel with its parent pinned no-follow. unlink() never follows the last
    component (a symlink there is removed, not its target), so only the walk matters."""
    parent, name = _parent_and_name(rel)
    try:
        dfd = open_dir_beneath(root, parent)
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    try:
        os.unlink(name, dir_fd=dfd)
    except FileNotFoundError:
        if not missing_ok:
            raise
    finally:
        os.close(dfd)


def chmod_beneath(root, rel, mode) -> None:
    """fchmod on the regular file or directory root/rel, opened no-follow."""
    parent, name = _parent_and_name(rel)
    dfd = open_dir_beneath(root, parent)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise UnsafePath(e.errno, f'symlink refused: {name}') from None
            raise
        try:
            st = os.fstat(fd)
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise UnsafePath(errno.EINVAL, f'not a file or directory: {name}')
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def scandir_beneath(root, rel=None):
    """[(name, lstat)] of the directory root/rel, walked no-follow (symlinks show as links)."""
    dfd = open_dir_beneath(root, rel)
    try:
        out = []
        for name in os.listdir(dfd):
            try:
                out.append((name, os.stat(name, dir_fd=dfd, follow_symlinks=False)))
            except OSError:
                continue
        return out
    finally:
        os.close(dfd)


def send_file_beneath(root, rel, **kwargs):
    """flask.send_file of root/rel from ONE no-follow open (/proc/self/fd/<fd> = same inode).

    Returns None when the file is absent, a symlink, not a regular file, or the
    path is unsafe (callers answer 404). Size, Last-Modified and ranges work."""
    from flask import send_file
    try:
        fd = open_beneath(root, rel)
    except (OSError, UnsafePath):
        return None
    try:
        st = os.fstat(fd)
        kwargs.setdefault('download_name', split_rel(rel)[-1])
        kwargs.setdefault('etag', f'{st.st_ino:x}-{st.st_size:x}-{int(st.st_mtime_ns):x}')
        kwargs.setdefault('last_modified', st.st_mtime)
        return send_file(fd_path(fd), **kwargs)
    finally:
        os.close(fd)


class Anchored:
    """A path under a trusted root whose every operation is a no-follow, beneath-root one.

    Drop-in for the Path methods that matter here; `Anchored(UPLOADS_DIR, p)`
    accepts an absolute path lexically under the root, or a relative one. The
    root must be the agent-writable dir's MOUNT POINT (see open_root)."""

    def __init__(self, root, path):
        self.root = str(root)
        self.rel = rel_under(root, path)

    # -- names
    @property
    def name(self):
        return self.rel.rsplit('/', 1)[-1]

    @property
    def suffix(self):
        return os.path.splitext(self.name)[1]

    @property
    def stem(self):
        return os.path.splitext(self.name)[0]

    @property
    def path(self):
        return os.path.join(self.root, self.rel)

    def __str__(self):
        return self.path

    def __fspath__(self):
        # Review 3 of PR #597: an Anchored handed to open()/send_file()/os.* would be
        # opened BY NAME, following symlinks. Refuse; I/O goes through the methods
        # (str(a) / a.path stay available for logging and are flagged by the guard
        # when they reach a sink).
        raise TypeError('Anchored is not a filesystem path: use its no-follow methods')

    def child(self, name):
        return Anchored(self.root, f'{self.rel}/{name}')

    def sibling(self, name):
        parent = self.rel.rsplit('/', 1)[0] if '/' in self.rel else ''
        return Anchored(self.root, f'{parent}/{name}' if parent else name)

    # -- queries (never follow)
    def lstat(self):
        st = lstat_beneath(self.root, self.rel)
        if st is None:
            raise FileNotFoundError(errno.ENOENT, self.path)
        return st

    def exists(self):
        return lstat_beneath(self.root, self.rel) is not None

    def is_file(self):
        st = lstat_beneath(self.root, self.rel)
        return st is not None and stat.S_ISREG(st.st_mode)

    def is_dir(self):
        st = lstat_beneath(self.root, self.rel)
        return st is not None and stat.S_ISDIR(st.st_mode)

    def stat(self):
        return self.lstat()

    # -- I/O
    def read_bytes(self, max_bytes=None):
        return read_beneath(self.root, self.rel, max_bytes=max_bytes)

    def read_text(self, encoding='utf-8', errors=None, max_bytes=None):
        return self.read_bytes(max_bytes).decode(encoding, errors or 'strict')

    def write_bytes(self, data, mode=0o644, create_dirs=False):
        write_beneath(self.root, self.rel, data, mode=mode, create_dirs=create_dirs)
        return len(data)

    def write_text(self, text, encoding='utf-8', mode=0o644, create_dirs=False):
        return self.write_bytes(text.encode(encoding), mode=mode, create_dirs=create_dirs)

    def atomic_write_bytes(self, data, mode=0o644, create_dirs=False):
        atomic_write_beneath(self.root, self.rel, data, mode=mode, create_dirs=create_dirs)

    def atomic_write_text(self, text, encoding='utf-8', mode=0o644, create_dirs=False):
        self.atomic_write_bytes(text.encode(encoding), mode=mode, create_dirs=create_dirs)

    def append_bytes(self, data, mode=0o644, create_dirs=False):
        append_beneath(self.root, self.rel, data, mode=mode, create_dirs=create_dirs)

    def append_text(self, text, encoding='utf-8', mode=0o644, create_dirs=False):
        self.append_bytes(text.encode(encoding), mode=mode, create_dirs=create_dirs)

    def open(self, mode='r', encoding=None, errors=None, newline=None, perm=0o644, create_dirs=False):
        """A file object on a no-follow fd. Modes: r, rb, w, wb, a, ab, r+, rb+."""
        binary = 'b' in mode
        base = mode.replace('b', '').replace('t', '')
        flags = {'r': os.O_RDONLY, 'w': os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 'a': os.O_WRONLY | os.O_APPEND | os.O_CREAT, 'r+': os.O_RDWR,
                 'w+': os.O_RDWR | os.O_CREAT | os.O_TRUNC, 'a+': os.O_RDWR | os.O_APPEND | os.O_CREAT}[base]
        fd = open_beneath(self.root, self.rel, flags, perm, create_dirs=create_dirs)
        try:
            if binary:
                return os.fdopen(fd, mode)
            return os.fdopen(fd, mode, encoding=encoding or 'utf-8', errors=errors, newline=newline)
        except BaseException:
            os.close(fd)
            raise

    def chmod(self, mode):
        chmod_beneath(self.root, self.rel, mode)

    def unlink(self, missing_ok=False):
        unlink_beneath(self.root, self.rel, missing_ok=missing_ok)

    def mkdir(self, mode=0o755, parents=False, exist_ok=False):
        if not exist_ok and self.exists():
            raise FileExistsError(errno.EEXIST, self.path)
        if not parents and '/' in self.rel:
            os.close(open_dir_beneath(self.root, self.rel.rsplit('/', 1)[0]))
        os.close(open_dir_beneath(self.root, self.rel, create=True, dir_mode=mode))

    def rename(self, target):
        target = target if isinstance(target, Anchored) else Anchored(self.root, target)
        if target.root != self.root:
            raise UnsafePath(errno.EXDEV, 'rename across roots')
        rename_beneath(self.root, self.rel, target.rel)
        return target

    replace = rename

    def iterdir(self):
        return [self.child(n) for n, _ in scandir_beneath(self.root, self.rel)]

    def send(self, **kwargs):
        return send_file_beneath(self.root, self.rel, **kwargs)
