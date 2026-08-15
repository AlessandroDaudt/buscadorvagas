"""Cross-platform scheduled scan runner with a single-run lock."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
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
LOCK_HEARTBEAT_SECONDS = 5
LOCK_LEASE_SECONDS = 30
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


def _process_started_at(pid: int) -> float | None:
    """Return the process start time as a Unix timestamp when the OS exposes it."""
    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            fields = stat.rpartition(")")[2].split()
            start_ticks = int(fields[19])
            boot_time = next(
                int(line.split()[1])
                for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
                if line.startswith("btime ")
            )
            return boot_time + start_ticks / int(os.sysconf("SC_CLK_TCK"))
        except (OSError, ValueError, IndexError, StopIteration):
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return None
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            try:
                if not ctypes.windll.kernel32.GetProcessTimes(
                    process,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return ticks / 10_000_000 - 11_644_473_600
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        except (AttributeError, OSError, ValueError):
            return None
    return None


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ScanLock:
    """Atomic filesystem lock that works on Windows and Linux."""

    def __init__(self, path: Path = Path("state/scan.lock"), *, stale_seconds: int = 7800) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self.token = uuid4().hex
        self.acquired = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def _existing_owner_is_active(self, age: float) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return age <= self.stale_seconds

        owner_host = str(payload.get("hostname", ""))
        if owner_host and owner_host != socket.gethostname():
            # PIDs cannot be inspected across Docker namespaces. A live owner refreshes
            # the mtime, so an expired lease is enough to recover after container replacement.
            return age <= min(self.stale_seconds, LOCK_LEASE_SECONDS)

        started_at = _process_started_at(pid)
        if started_at is None:
            return _process_is_running(pid) and age <= self.stale_seconds

        try:
            lock_created_at = float(payload["created_at"])
        except (KeyError, TypeError, ValueError):
            lock_created_at = self.path.stat().st_mtime
        if started_at > lock_created_at + 1:
            # The PID was reused or the container restarted after this lock was written.
            return False

        recorded_start = payload.get("process_started_at")
        if recorded_start is not None:
            try:
                if abs(started_at - float(recorded_start)) > 1:
                    return False
            except (TypeError, ValueError):
                return age <= self.stale_seconds
        return True

    def _refresh_heartbeat(self) -> None:
        while not self._heartbeat_stop.wait(LOCK_HEARTBEAT_SECONDS):
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if payload.get("token") != self.token:
                    return
                self.path.touch()
            except (OSError, json.JSONDecodeError):
                return

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._refresh_heartbeat,
            name=f"autopilot-scan-lock-{self.token[:8]}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0
                if attempt < 2 and not self._existing_owner_is_active(max(0, age)):
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                raise ScanAlreadyRunning(f"another scan owns {self.path}") from exc
            else:
                payload = json.dumps(
                    {
                        "pid": os.getpid(),
                        "token": self.token,
                        "created_at": time.time(),
                        "hostname": socket.gethostname(),
                        "process_started_at": _process_started_at(os.getpid()),
                    }
                ).encode()
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                self.acquired = True
                self._start_heartbeat()
                return
        raise ScanAlreadyRunning(f"another scan owns {self.path}")

    def release(self) -> None:
        if not self.acquired:
            return
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=LOCK_HEARTBEAT_SECONDS + 1)
            self._heartbeat_thread = None
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
            delay = min(60, 2**attempt * 5)
            logger.warning("scheduled scan failed; retrying", extra={"status": "retry"})
            sleep(delay)
    return ScheduledRunResult(return_code=result.returncode, attempts=retries + 1)


def run_scheduled_once(schedule: ScheduleConfiguration) -> ScheduledRunResult:
    retries = max(0, min(5, int(os.getenv("SCHEDULER_RETRIES", "1"))))
    stale_seconds = schedule.max_duration_minutes * 60 + 600
    with ScanLock(stale_seconds=stale_seconds):
        if os.getenv("AUTOPILOT_DISCOVERY_ON_SCHEDULE", "true").casefold() not in {
            "0",
            "false",
            "no",
        }:
            try:
                from job_hunt.discovery import run_public_portal_discovery
                from job_hunt.persistence.database import get_database_url

                result = run_public_portal_discovery(get_database_url())
                logger.info(
                    "scheduled public portal discovery finished",
                    extra={"proposals": result["proposal_count"]},
                )
            except Exception:
                # Discovery is advisory; a failed proposal batch must not suppress the normal scan.
                logger.exception("scheduled public portal discovery failed")
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


def serve_schedule_file(path: Path = Path("config/search_preferences.json")) -> None:
    """Serve a schedule that can be changed atomically by the local web panel."""
    from job_hunt.configuration import load_search_preferences

    poll_seconds = max(1, min(60, int(os.getenv("SCHEDULER_POLL_SECONDS", "30"))))
    target: datetime | None = None
    signature: str | None = None
    logger.info("dynamic scheduler active", extra={"preferences_path": str(path)})
    while True:
        try:
            schedule = load_search_preferences(path).schedule
        except ValueError:
            logger.exception("scheduler could not reload preferences")
            time.sleep(poll_seconds)
            continue
        current_signature = schedule.model_dump_json()
        if current_signature != signature:
            signature = current_signature
            target = next_run_at(schedule) if schedule.enabled else None
            logger.info(
                "scheduler configuration reloaded",
                extra={
                    "enabled": schedule.enabled,
                    "next_run": target.isoformat() if target else None,
                },
            )
        if not schedule.enabled or target is None:
            time.sleep(poll_seconds)
            continue
        remaining = (target - datetime.now(target.tzinfo)).total_seconds()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))
            continue
        try:
            result = run_scheduled_once(schedule)
            logger.info(
                "scheduled scan finished",
                extra={"status": "ok" if result.return_code == 0 else "failed"},
            )
        except ScanAlreadyRunning:
            logger.warning("scheduled scan skipped because another scan is running")
        target = next_run_at(schedule, datetime.now(target.tzinfo) + timedelta(seconds=1))
        time.sleep(1)
