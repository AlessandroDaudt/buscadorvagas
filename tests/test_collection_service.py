from job_hunt.collection import ConnectorRetryPolicy, collect_and_persist
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


class SequenceConnector:
    source_name = "fixture"

    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def collect(self, _context):
        self.calls += 1
        return next(self.results)


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
        connector = FakeConnector(result)
        report = collect_and_persist(
            [connector],
            database,
            retry_policy=ConnectorRetryPolicy(attempts=2, initial_backoff_seconds=0),
            sleep=lambda _seconds: None,
        )
        assert report.sources_failed == ["fixture"]
        assert report.jobs_collected == 0
    finally:
        database.dispose()


def test_collection_service_retries_only_retryable_failures(tmp_path):
    retryable = CollectionResult(
        source_name="fixture",
        status="failed",
        errors=[SourceIssue("timeout", "temporary", retryable=True)],
    )
    recovered = CollectionResult(source_name="fixture")
    connector = SequenceConnector([retryable, recovered])
    database = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        report = collect_and_persist(
            [connector],
            database,
            retry_policy=ConnectorRetryPolicy(attempts=3, initial_backoff_seconds=0),
            sleep=lambda _seconds: None,
        )
        assert connector.calls == 2
        assert report.sources_failed == []
    finally:
        database.dispose()
