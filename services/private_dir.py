"""The server's private directory: state and temp files no other uid may touch.

/tmp is shared with the terminal's sandbox user (and, on a host install, with
everyone), so the server never puts a security-relevant file there: a
predictable name in /tmp can be pre-planted as a symlink, and anything written
0644 there is readable by the sandbox. Everything that used /tmp now lives in
PRIVATE_DIR (default <runtime>/.server-private, overridable with
OVU_PRIVATE_DIR), created 0700 and owned by the server's uid:

  private_dir()        the directory itself, verified on every call
  private_path(name)   a fixed-name file in it (events file, update prompt, ...)
  private_subdir(name) a 0700 subdirectory (vault-clients fallback, tmp/)
  set_process_tempdir() points tempfile (and TMPDIR for children) at
                       private_subdir('tmp'), so every mkstemp/NamedTemporaryFile
                       in the server lands there
"""

import errno
import os
import stat
import tempfile
from pathlib import Path

from services.paths import RUNTIME_DIR

PRIVATE_DIR = Path(os.getenv('OVU_PRIVATE_DIR', str(RUNTIME_DIR / '.server-private')))


class NotPrivate(OSError):
    """The directory exists but is not a plain 0700 directory owned by this uid."""


def _ensure(path: Path) -> Path:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid():
            raise NotPrivate(errno.EPERM, f'{path} is owned by uid {st.st_uid}, not {os.geteuid()}')
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    return path


def private_dir() -> Path:
    return _ensure(PRIVATE_DIR)


def private_subdir(name: str) -> Path:
    if not name or '/' in name or name in ('.', '..'):
        raise ValueError(name)
    return _ensure(private_dir() / name)


def private_path(name: str) -> Path:
    if not name or '/' in name or name in ('.', '..'):
        raise ValueError(name)
    return private_dir() / name


def is_private_dir(path) -> bool:
    """True when `path` is a plain directory owned by this uid with no group/other access."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        return st.st_uid == os.geteuid() and not (st.st_mode & 0o077)
    finally:
        os.close(fd)


def set_process_tempdir() -> Path:
    d = private_subdir('tmp')
    tempfile.tempdir = str(d)
    os.environ['TMPDIR'] = str(d)
    return d
