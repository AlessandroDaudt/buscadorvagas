import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from job_hunt.domain.models import ScheduleConfiguration
from job_hunt.scheduler import (
    ScanAlreadyRunning,
    ScanLock,
    next_run_at,
    run_scan_subprocess,
)


def test_next_run_uses_configured_timezone_days_and_time():
    schedule = ScheduleConfiguration(
        timezone="America/Sao_Paulo",
        days=["monday", "wednesday"],
        time="08:00",
    )
    now = datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    result = next_run_at(schedule, now)
    assert result.isoformat() == "2026-07-20T08:00:00-03:00"


def test_scan_lock_is_exclusive_and_recoverable(tmp_path):
    path = tmp_path / "scan.lock"
    first = ScanLock(path)
    first.acquire()
    assert json.loads(path.read_text())["pid"] > 0
    with pytest.raises(ScanAlreadyRunning):
        ScanLock(path).acquire()
    first.release()
    with ScanLock(path):
        assert path.exists()
    assert not path.exists()


def test_scheduled_subprocess_retries_failures_without_sleeping():
    outcomes = iter([2, 0])
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, next(outcomes))

    result = run_scan_subprocess(
        ScheduleConfiguration(max_duration_minutes=1),
        retries=1,
        command=["autopilot", "scan"],
        run=fake_run,
        sleep=lambda _seconds: None,
    )
    assert result.return_code == 0
    assert result.attempts == 2
    assert calls[0][1]["timeout"] == 60
    assert calls[0][1]["env"]["AUTOPILOT_EXTERNAL_SCAN_LOCK"] == "1"


def test_scheduled_subprocess_reports_timeout():
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("scan", 60)

    result = run_scan_subprocess(
        ScheduleConfiguration(max_duration_minutes=1),
        retries=2,
        command=["scan"],
        run=timeout,
    )
    assert result.return_code == 124
    assert result.timed_out
    assert result.attempts == 1
