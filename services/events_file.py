"""The Brain events file (<private dir>/openvoiceui-events.jsonl), kept private to the server.

It carries conversation text (type 'conversation', 'message'), and /tmp is
shared with the terminal's sandbox user. Before: the file was appended with
open('a') at mode 0644, so the sandbox could read every event; and if the
sandbox created the file first, the server's appends failed (fs.protected_regular)
while services/memory_client.py read the sandbox's forged "conversation" events.

Now:
  - ensure_private() at server boot creates it 0600 with O_EXCL|O_NOFOLLOW, or,
    if it exists, verifies it is a regular file owned by the server's uid
    (fixing the mode to 0600). Anything else (a symlink, another uid's file) is
    REFUSED: events are not written or read until the file is the server's.
  - append_event() / open_for_read() repeat that ownership check on the fd they
    actually use, so a file created later by someone else is never trusted.
"""

import json
import logging
import os
import stat

def _default_path():
    # Server-private dir, not /tmp (review 2 of PR #597: no security-relevant file
    # in a dir the sandbox shares). Falls back to a non-existent marker path if the
    # private dir cannot be made, so events are simply disabled.
    try:
        from services.private_dir import private_path
        return str(private_path('openvoiceui-events.jsonl'))
    except Exception:
        return '/nonexistent/openvoiceui-events.jsonl'


EVENTS_PATH = _default_path()

logger = logging.getLogger(__name__)


def _owned_regular(fd) -> bool:
    st = os.fstat(fd)
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()


def ensure_private(path=EVENTS_PATH) -> bool:
    """Create the events file 0600 (O_EXCL) or verify the existing one is ours."""
    path = str(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        pass
    except OSError as e:
        logger.error(f'events file {path}: cannot create ({e}); events disabled')
        return False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        logger.error(f'events file {path}: refusing a symlink or unreadable file ({e}); events disabled')
        return False
    try:
        if not _owned_regular(fd):
            logger.error(f'events file {path}: not a regular file owned by uid {os.geteuid()}; '
                         'refusing it (events disabled)')
            return False
        os.fchmod(fd, 0o600)
        return True
    finally:
        os.close(fd)


def append_event(event: dict, path=EVENTS_PATH) -> bool:
    """Append one JSON line, only into a regular file the server owns. Never raises."""
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                     | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    except OSError:
        return False
    try:
        if not _owned_regular(fd):
            return False
        os.write(fd, (json.dumps(event) + '\n').encode('utf-8'))
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def open_for_read(path=EVENTS_PATH):
    """A text file object on the events file if it is the server's own, else None."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not _owned_regular(fd):
            os.close(fd)
            return None
        os.set_blocking(fd, True)
        return os.fdopen(fd, 'r')
    except Exception:
        os.close(fd)
        return None
