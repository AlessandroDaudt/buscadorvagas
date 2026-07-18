import pytest
from pydantic import HttpUrl

from job_hunt.applications import ApplicationService
from job_hunt.domain.models import ApplicationStatus, UnifiedJob
from job_hunt.persistence.database import Database
from job_hunt.persistence.job_ingestion import JobIngestionService
from job_hunt.persistence.migration import upgrade_database


def test_application_pipeline_records_history_and_guards_terminal_status(tmp_path):
    url = f"sqlite:///{(tmp_path / 'applications.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    try:
        with database.session() as session:
            result = JobIngestionService(session).ingest(
                UnifiedJob(
                    source_name="fixture",
                    original_url=HttpUrl("https://example.com/jobs/1"),
                    company="Example",
                    title="Security Engineer",
                )
            )
            service = ApplicationService(session)
            application = service.set_status(result.job_id, ApplicationStatus.PLANNED)
            assert application.status == ApplicationStatus.PLANNED
            service.set_status(result.job_id, ApplicationStatus.REJECTED, notes="Closed")
            with pytest.raises(ValueError, match="terminal"):
                service.set_status(result.job_id, ApplicationStatus.SAVED)
            reopened = service.set_status(
                result.job_id, ApplicationStatus.SAVED, allow_reopen=True
            )
            assert reopened.status == ApplicationStatus.SAVED
    finally:
        database.dispose()
