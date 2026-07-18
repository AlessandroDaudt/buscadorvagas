import json
import logging

import pytest
from sqlalchemy import select

from job_hunt import operations
from job_hunt.log import get_logger, log_context
from job_hunt.metrics import MetricsRegistry
from job_hunt.persistence.database import Database
from job_hunt.persistence.models import SearchRunRecord


def test_execute_scan_records_history_report_and_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{(tmp_path / 'tracked.db').as_posix()}"
    report_path = tmp_path / "last_run_report.json"
    monkeypatch.setattr(operations, "REPORT_PATH", report_path)
    monkeypatch.setattr(operations, "get_database_url", lambda: database_url)

    def fake_scan(_config, _companies):
        report_path.write_text(
            json.dumps({"jobs_collected": 3, "source_errors": {}, "errors": []}),
            encoding="utf-8",
        )
        return "done"

    result = operations.execute_scan(
        {"persistence": {"enabled": True}},
        [{"name": "Fixture"}],
        fake_scan,
    )
    assert result == "done"
    database = Database(database_url)
    try:
        with database.session() as session:
            record = session.scalar(select(SearchRunRecord))
            assert record is not None
            assert record.status == "completed"
            assert record.finished_at is not None
            assert record.summary_data["jobs_collected"] == 3
    finally:
        database.dispose()
    assert (tmp_path / "state" / "metrics.json").exists()


def test_metrics_snapshot_and_atomic_write(tmp_path):
    registry = MetricsRegistry()
    registry.increment("jobs_collected_total", 2)
    registry.observe("connector_duration", 1.25)
    path = tmp_path / "metrics.json"
    registry.write(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["counters"]["jobs_collected_total"] == 2
    assert payload["durations"]["connector_duration"]["max_seconds"] == 1.25


def test_execute_scan_releases_lock_when_scanner_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def fail(_config, _companies):
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError, match="fixture failure"):
        operations.execute_scan({"persistence": {"enabled": False}}, [], fail)
    assert not (tmp_path / "state" / "scan.lock").exists()


def test_json_logging_redacts_secret_and_adds_context(monkeypatch, capsys):
    name = "autopilot.test-structured-log"
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_FILE", "")
    logger = get_logger(name)
    try:
        with log_context(run_id="run-123", source_id="source-1"):
            logger.info("api_key=do-not-log")
            logger.info("duration %.2f", 1.25)
        payload, duration_payload = [
            json.loads(line) for line in capsys.readouterr().out.splitlines()
        ]
        assert payload["message"] == "api_key=[REDACTED]"
        assert payload["run_id"] == "run-123"
        assert payload["source_id"] == "source-1"
        assert duration_payload["message"] == "duration 1.25"
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logging.Logger.manager.loggerDict.pop(name, None)
