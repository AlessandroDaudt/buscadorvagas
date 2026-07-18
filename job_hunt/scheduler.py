"""Cross-platform scheduled scan runner with a single-run lock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from job_hunt.domain.models import ScheduleConfiguration
from job_hunt.log import get_logger

logger = get_logger("autopilot.scheduler")
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class ScanAlreadyRunning(RuntimeError):
    pass


class ScanLock:
    """Atomic filesystem lock that works on Windows and Linux."""

    def __init__(self, path: Path = Path("state/scan.lock"), *, stale_seconds: int = 7800) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self.token = uuid4().hex
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0
                if attempt == 0 and age > self.stale_seconds:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                raise ScanAlreadyRunning(f"another scan owns {self.path}") from exc
            else:
                payload = json.dumps(
                    {"pid": os.getpid(), "token": self.token, "created_at": time.time()}
                ).encode()
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                self.acquired = True
                return
        raise ScanAlreadyRunning(f"another scan owns {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
        self.acquired = False

    def __enter__(self) -> ScanLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def next_run_at(schedule: ScheduleConfiguration, now: datetime | None = None) -> datetime:
    try:
        zone = ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown scheduler timezone: {schedule.timezone}") from exc
    current = now.astimezone(zone) if now else datetime.now(zone)
    hour, minute = (int(part) for part in schedule.time.split(":"))
    allowed = {WEEKDAYS[day] for day in schedule.days} if schedule.days else set(range(7))
    for offset in range(8):
        day = current.date() + timedelta(days=offset)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        if candidate.weekday() in allowed and candidate >= current:
            return candidate
    raise RuntimeError("could not calculate the next scheduled scan")


@dataclass(frozen=True)
class ScheduledRunResult:
    return_code: int
    attempts: int
    timed_out: bool = False


def run_scan_subprocess(
    schedule: ScheduleConfiguration,
    *,
    retries: int = 1,
    command: list[str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> ScheduledRunResult:
    """Run the normal CLI in a bounded child process, retrying process failures."""
    scan_command = command or [sys.executable, "-m", "job_hunt.main", "scan"]
    timeout = schedule.max_duration_minutes * 60
    environment = dict(os.environ)
    environment["AUTOPILOT_EXTERNAL_SCAN_LOCK"] = "1"
    for attempt in range(retries + 1):
        try:
            result = run(scan_command, timeout=timeout, check=False, env=environment)
        except subprocess.TimeoutExpired:
            logger.error(
                "scheduled scan exceeded its duration limit",
                extra={"duration": timeout, "status": "timeout"},
            )
            return ScheduledRunResult(return_code=124, attempts=attempt + 1, timed_out=True)
        if result.returncode == 0:
            return ScheduledRunResult(return_code=0, attempts=attempt + 1)
        if attempt < retries:
            delay = min(60, 2 ** attempt * 5)
            logger.warning("scheduled scan failed; retrying", extra={"status": "retry"})
            sleep(delay)
    return ScheduledRunResult(return_code=result.returncode, attempts=retries + 1)


def run_scheduled_once(schedule: ScheduleConfiguration) -> ScheduledRunResult:
    retries = max(0, min(5, int(os.getenv("SCHEDULER_RETRIES", "1"))))
    stale_seconds = schedule.max_duration_minutes * 60 + 600
    with ScanLock(stale_seconds=stale_seconds):
        return run_scan_subprocess(schedule, retries=retries)


def serve_schedule(schedule: ScheduleConfiguration) -> None:
    if not schedule.enabled:
        raise ValueError("schedule is disabled in config/search_preferences.json")
    poll_seconds = max(1, min(60, int(os.getenv("SCHEDULER_POLL_SECONDS", "30"))))
    logger.info(
        f"scheduler active: {schedule.time} {schedule.timezone} on "
        f"{', '.join(schedule.days) if schedule.days else 'every day'}"
    )
    while True:
        next_run = next_run_at(schedule)
        while True:
            remaining = (next_run - datetime.now(next_run.tzinfo)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(poll_seconds, remaining))
        try:
            result = run_scheduled_once(schedule)
            logger.info(
                "scheduled scan finished",
                extra={"status": "ok" if result.return_code == 0 else "failed"},
            )
        except ScanAlreadyRunning:
            logger.warning("scheduled scan skipped because another scan is running")
        time.sleep(1)
