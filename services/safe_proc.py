"""Child-process environment for the server's fixed-argv subprocesses.

The server's own environment holds every platform key and token. A child that
only needs to run `git`, `ffmpeg`, `ps`, ... gets none of it: minimal_env()
passes PATH/HOME/locale/TZ/TMPDIR and nothing else. Used by every process an
agent-reachable route can trigger (PR #597 review 2: "a fixed argv with no
caller-controlled shell and no secrets exposure").
"""

import os

_KEEP = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'USER', 'LOGNAME')


def minimal_env(extra=None) -> dict:
    env = {k: os.environ[k] for k in _KEEP if os.environ.get(k)}
    env.setdefault('PATH', '/usr/local/bin:/usr/bin:/bin')
    if extra:
        env.update(extra)
    return env
