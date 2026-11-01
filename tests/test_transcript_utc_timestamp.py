"""Pin #564: transcript JSON `timestamp` ends in 'Z', so it must be real UTC (Part 2 of #566)."""
import json
import time
from datetime import datetime, timezone

import pytest

import routes.transcripts as transcripts

TOLERANCE_S = 3


@pytest.fixture
def tz(monkeypatch):
    """Set the process TZ; restore it (and tzset) on teardown so other tests are unaffected."""
    def _set(name):
        monkeypatch.setenv('TZ', name)
        time.tzset()
    yield _set
    monkeypatch.undo()
    time.tzset()


def _parse(ts):
    assert ts.endswith('Z')
    return datetime.strptime(ts[:-1], '%Y-%m-%dT%H:%M:%S.%f').replace(tzinfo=timezone.utc)


def _delta_s(ts):
    return abs((datetime.now(timezone.utc) - _parse(ts)).total_seconds())


def _saved_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, 'TRANSCRIPTS_DIR', str(tmp_path))
    rel = transcripts.save_conversation_turn('hi', 'hello', session_id='s1', session_key='k')
    assert rel, 'save_conversation_turn returned None'
    data = json.loads((tmp_path / rel.split('/', 1)[1]).read_text(encoding='utf-8'))
    return data['timestamp']


def _pre_564_timestamp():
    """The formula removed by #564: container-local wall clock with a 'Z' bolted on."""
    now = datetime.now()
    return f'{now.strftime("%Y-%m-%dT%H:%M:%S")}.{now.microsecond // 1000:03d}Z'


@pytest.mark.parametrize('zone', ['America/New_York', 'UTC', 'Asia/Kolkata'])
def test_timestamp_is_real_utc(zone, tz, tmp_path, monkeypatch):
    tz(zone)
    assert _delta_s(_saved_timestamp(tmp_path, monkeypatch)) < TOLERANCE_S


def test_negative_control_pre_564_formula_fails_under_new_york(tz):
    tz('America/New_York')
    # Same assertion as above, applied to the old formula: it must NOT hold (off by 4-5h).
    assert _delta_s(_pre_564_timestamp()) > 3600
    with pytest.raises(AssertionError):
        assert _delta_s(_pre_564_timestamp()) < TOLERANCE_S
