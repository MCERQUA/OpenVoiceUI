"""Review 3 of PR #597 (non-author): server-written canvas pages must stay editable in place
by the tenant agent (host ruling: the vuln was the predictable temp name, not the mode).

The agent runs as uid 1000 (openclaw `node`), the server as uid 1001 with groups 1001+1002;
the two share no group. openclaw's write/edit tools with tools.fs.workspaceOnly unset (all 25
fleet configs) call fs.writeFile(path) = open(O_WRONLY|O_TRUNC) IN PLACE, so a 1001:1001 0644
page is EACCES for the agent. Measured in throwaway containers (review3/modetest*):
  PR head:   SERVER_WROTE uid=1001 gid=1001 mode=644 -> AGENT_FS_WRITEFILE=EACCES
  live image SERVER_WROTE uid=1001 gid=1001 mode=666 -> AGENT_FS_WRITEFILE=ok
"""
import stat
from pathlib import Path


def test_server_written_page_is_writable_by_another_uid(tmp_path, monkeypatch):
    import routes.canvas as canvas
    monkeypatch.setattr(canvas, 'CANVAS_PAGES_DIR', tmp_path)
    canvas._write_page_replacing(tmp_path / 'p.html', '<html>x</html>')
    mode = stat.S_IMODE((tmp_path / 'p.html').stat().st_mode)
    assert mode & 0o002, (f'server-written page is {oct(mode)}: the agent (uid 1000, no shared '
                          'group with the server) can no longer edit it in place')
