"""Operational wrapper for tracked, mutually exclusive scans."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from job_hunt.log import get_logger, log_context, redact_text
from job_hunt.metrics import metrics
from job_hunt.persistence.database import Database, get_database_url
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import SearchRunRecord
from job_hunt.scheduler import ScanLock

logger = get_logger("autopilot.operations")
REPORT_PATH = Path("state/last_run_report.json")


def _report_marker() -> int | None:
    try:
        return REPORT_PATH.stat().st_mtime_ns
    except OSError:
        return None


def _fresh_report(previous_marker: int | None) -> dict[str, Any]:
    try:
        if REPORT_PATH.stat().st_mtime_ns == previous_marker:
            return {}
        payload = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def execute_scan(
    config: dict[str, Any],
    companies: list[dict[str, Any]],
    scan_callable: Callable[[dict[str, Any], list[dict[str, Any]]], Any],
) -> Any:
    """Execute the legacy-compatible scanner with run history and metrics."""
    lock_required = os.getenv("AUTOPILOT_EXTERNAL_SCAN_LOCK") != "1"
    lock = ScanLock() if lock_required else None
    if lock:
        lock.acquire()
    run_id = str(uuid4())
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    marker = _report_marker()
    database: Database | None = None
    tracking_enabled = bool(config.get("persistence", {}).get("enabled", False))
    timezone_name = str(
        config.get("search_preferences", {}).get("schedule", {}).get(
            "timezone", "America/Sao_Paulo"
        )
    )
    if tracking_enabled:
        try:
            database_url = get_database_url()
            upgrade_database(database_url)
            database = Database(database_url)
            with database.session() as session:
                session.add(
                    SearchRunRecord(
                        id=run_id,
                        started_at=started_at,
                        status="running",
                        timezone=timezone_name,
                    )
                )
        except Exception:
            database = None
            logger.exception("search run persistence could not be initialized")
    status = "completed"
    result: Any = None
    error: Exception | None = None
    try:
        with log_context(run_id=run_id):
            result = scan_callable(config, companies)
    except Exception as exc:
        status = "failed"
        error = exc
        logger.exception("scan failed", extra={"run_id": run_id, "status": status})
    finally:
        try:
            duration = time.monotonic() - started
            report = _fresh_report(marker)
            if status == "completed" and report.get("errors"):
                status = "completed_with_errors"
            metrics.increment("search_runs_total")
            metrics.increment(f"search_runs_{status}_total")
            metrics.increment("jobs_collected_total", float(report.get("jobs_collected", 0)))
            metrics.increment("source_errors_total", float(len(report.get("source_errors", {}))))
            metrics.observe("search_run_duration", duration)
            try:
                metrics.write()
            except OSError:
                logger.warning("metrics snapshot could not be written")
            if database is not None:
                try:
                    with database.session() as session:
                        record = session.get(SearchRunRecord, run_id)
                        if record is not None:
                            record.finished_at = datetime.now(timezone.utc)
                            record.status = status
                            record.summary_data = report
                            record.error_data = (
                                [type(error).__name__]
                                if error
                                else [redact_text(str(item)) for item in report.get("errors", [])]
                            )
                finally:
                    database.dispose()
            logger.info(
                "tracked scan finished",
                extra={"run_id": run_id, "duration": round(duration, 3), "status": status},
            )
        finally:
            if lock:
                lock.release()
    if error:
        raise error
    return result
