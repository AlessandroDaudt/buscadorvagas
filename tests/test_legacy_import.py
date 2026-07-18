import json

from sqlalchemy import func, select

from job_hunt.persistence.database import Database
from job_hunt.persistence.legacy_import import import_legacy_state
from job_hunt.persistence.models import Base, JobAnalysisRecord, JobRecord, JobSourceRecord


def test_legacy_import_is_idempotent(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    legacy_job = {
        "url": "https://example.com/jobs/security-1",
        "company": "Example",
        "title": "Security Engineer",
        "location": "Remote",
        "content": "Secure endpoints",
        "score": 88,
        "reason": "Strong endpoint experience",
        "scan_date": "2026-07-18",
    }
    (state / "job_history.json").write_text(json.dumps([legacy_job]), encoding="utf-8")
    (state / "last_scan.json").write_text(json.dumps([legacy_job]), encoding="utf-8")
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            first = import_legacy_state(session, state)
            assert first.jobs_created == 1
        with database.session() as session:
            second = import_legacy_state(session, state)
            assert second.jobs_existing == 1
            assert session.scalar(select(func.count()).select_from(JobRecord)) == 1
            assert session.scalar(select(func.count()).select_from(JobSourceRecord)) == 1
            assert session.scalar(select(func.count()).select_from(JobAnalysisRecord)) == 1
    finally:
        database.dispose()


def test_legacy_import_skips_invalid_records(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "last_scan.json").write_text(
        json.dumps([{"url": "file:///tmp/x", "company": "X", "title": "Y"}]),
        encoding="utf-8",
    )
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            report = import_legacy_state(session, state)
            assert report.jobs_created == 0
    finally:
        database.dispose()
