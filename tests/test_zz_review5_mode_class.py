"""Review 5 of PR #597 (non-author), R4-B1 CLASS: a server write must not re-lock a file the
tenant agent edits in place, and the "keep the existing mode" semantics must not open a new
hole (keep a planted special bit, copy a planted symlink target's mode, hang on a FIFO,
write through a link swapped in between the O_EXCL miss and the reopen).

Written from the threat model (agent uid 1000 owns uploads/, canvas-pages/, music/ ...; the
server is uid 1001), not from the round-5 code. Every probe asserts the SECURE outcome:
FAILED means the hole is real.
"""
import os
import stat
import threading
import time

import pytest


def _mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


# -- every in-place writer API keeps an agent-editable 0666 file 0666 (R4-B1 class) -----------
#    On 47d88bf the write_text / write_bytes / BeneathFile rows FAIL (re-moded to 0644).

def _w_write_text(root, name):
    from services.safe_open import Anchored
    Anchored(root, name).write_text('{"v": 2}')


def _w_write_bytes(root, name):
    from services.safe_open import Anchored
    Anchored(root, name).write_bytes(b'{"v": 2}')


def _w_write_beneath(root, name):
    from services.safe_open import write_beneath
    write_beneath(str(root), name, b'{"v": 2}')


def _w_beneathfile(root, name):
    from services.safe_open import BeneathFile, open_dir_beneath
    dfd = open_dir_beneath(root, None)
    try:
        BeneathFile(dfd, name).write_bytes(b'{"v": 2}')
    finally:
        os.close(dfd)


def _w_open_w(root, name):
    from services.safe_open import Anchored
    with Anchored(root, name).open('w') as f:
        f.write('{"v": 2}')


def _w_append(root, name):
    from services.safe_open import Anchored
    Anchored(root, name).append_text('\n')


def _w_atomic_none(root, name):
    from services.safe_open import Anchored
    Anchored(root, name).atomic_write_text('{"v": 2}', mode=None)


@pytest.mark.parametrize('writer', [_w_write_text, _w_write_bytes, _w_write_beneath,
                                    _w_beneathfile, _w_open_w, _w_append, _w_atomic_none],
                         ids=lambda f: f.__name__[3:])
def test_in_place_writer_keeps_an_agent_editable_file_0666(tmp_path, writer):
    f = tmp_path / 'page.json'
    f.write_text('{"v": 1}')
    os.chmod(f, 0o666)                      # server-owned file the agent edits in place
    writer(tmp_path, 'page.json')
    assert _mode(f) == 0o666, (f'{writer.__name__[3:]} re-moded an agent-editable 0666 file to '
                               f'{oct(_mode(f))}: the agent (uid 1000) gets EACCES on its next edit')


# -- mode=None must not import a planted file's special bits or a symlink target's mode ----------

def test_atomic_mode_none_never_carries_setuid_setgid_sticky(tmp_path):
    from services.safe_open import Anchored
    f = tmp_path / 'side.json'
    f.write_text('x')
    os.chmod(f, 0o7777)                     # agent plants a regular file with every bit set
    Anchored(tmp_path, 'side.json').atomic_write_text('{}', mode=None)
    m = os.lstat(f).st_mode
    assert not (m & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)), (
        f'server-written file inherited special bits from a planted file: {oct(stat.S_IMODE(m))}')


def test_atomic_mode_none_does_not_copy_a_symlink_targets_mode(tmp_path):
    from services.safe_open import Anchored
    outside = tmp_path / 'outside'
    outside.write_text('secret')
    os.chmod(outside, 0o777)
    root = tmp_path / 'uploads'
    root.mkdir()
    (root / 'side.json').symlink_to(outside)
    Anchored(root, 'side.json').atomic_write_text('{}', mode=None)
    assert _mode(root / 'side.json') == 0o644, 'mode copied through a planted symlink'
    assert outside.read_text() == 'secret' and _mode(outside) == 0o777, 'symlink target touched'


# -- the O_EXCL-miss -> reopen path: special files and a link swapped in between ----------------

def test_overwrite_of_a_planted_fifo_with_a_reader_neither_hangs_nor_writes(tmp_path):
    from services.safe_open import Anchored
    fifo = tmp_path / 'q.json'
    os.mkfifo(fifo)
    got = []

    def reader():                           # the agent holds the read end open
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        end = time.time() + 3
        while time.time() < end:
            try:
                b = os.read(fd, 100)
                if b:
                    got.append(b)
            except BlockingIOError:
                pass
            time.sleep(0.02)
        os.close(fd)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.2)
    result = {}

    def write():
        try:
            Anchored(tmp_path, 'q.json').write_text('SERVER-DATA')
            result['r'] = 'wrote'
        except OSError as e:
            result['r'] = f'refused: {e.__class__.__name__}'

    w = threading.Thread(target=write, daemon=True)
    w.start()
    w.join(5)
    assert not w.is_alive(), 'server writer hung on a planted FIFO'
    t.join(5)
    assert result.get('r', '').startswith('refused') and not got, (
        f'server wrote into a planted FIFO: {result} {got}')


def test_overwrite_of_a_dangling_symlink_is_refused_not_created_through(tmp_path):
    from services.safe_open import Anchored, UnsafePath
    root = tmp_path / 'canvas-pages'
    root.mkdir()
    target = tmp_path / 'app' / 'server_new.py'     # does not exist yet
    target.parent.mkdir()
    (root / 'p.html').symlink_to(target)          # O_EXCL misses (EEXIST), reopen must not follow
    with pytest.raises((UnsafePath, OSError)):
        Anchored(root, 'p.html').write_text('import os')
    assert not target.exists(), 'server created a file through a dangling symlink'


def test_overwrite_of_a_directory_at_the_name_is_refused(tmp_path):
    from services.safe_open import Anchored
    (tmp_path / 'd.json').mkdir()
    with pytest.raises(OSError):
        Anchored(tmp_path, 'd.json').write_text('x')
