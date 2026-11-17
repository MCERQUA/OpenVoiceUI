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
    """fd on the trusted root directory (configured by the server, may be a mount)."""
    return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


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


def read_fd(fd: int, max_bytes=None) -> bytes:
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
