from job_hunt.collection import collect_and_persist
from job_hunt.connectors.base import CollectionResult, ConnectorContext, SourceIssue
from job_hunt.domain.models import UnifiedJob
from job_hunt.persistence.database import Database
from job_hunt.persistence.models import Base


class FakeConnector:
    source_name = "fixture"

    def __init__(self, result):
        self.result = result

    def collect(self, _context):
        return self.result


def test_collection_service_persists_jobs_and_reports_decisions(tmp_path):
    job = UnifiedJob(
        source_name="fixture",
        original_url="https://example.com/jobs/1",
        company="Example",
        title="IAM Engineer",
        description="Identity and access management",
    )
    connector = FakeConnector(CollectionResult(source_name="fixture", jobs=[job]))
    database = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        report = collect_and_persist([connector], database, ConnectorContext())
        assert report.jobs_collected == 1
        assert report.decisions["new"] == 1
        assert report.sources_consulted == ["fixture"]
    finally:
        database.dispose()


def test_collection_service_keeps_source_failure_isolated(tmp_path):
    result = CollectionResult(
        source_name="fixture",
        status="failed",
        errors=[SourceIssue("down", "source unavailable", retryable=True)],
    )
    database = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        report = collect_and_persist([FakeConnector(result)], database)
        assert report.sources_failed == ["fixture"]
        assert report.jobs_collected == 0
    finally:
        database.dispose()

