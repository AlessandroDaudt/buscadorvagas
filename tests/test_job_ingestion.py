from sqlalchemy import func, select

from job_hunt.domain.models import UnifiedJob
from job_hunt.persistence.database import Database
from job_hunt.persistence.job_ingestion import IngestDecision, JobIngestionService
from job_hunt.persistence.models import Base, JobRecord, JobSnapshotRecord, JobSourceRecord


def _job(source="greenhouse", url="https://example.com/jobs/1", external_id="1", **changes):
    data = {
        "source_name": source,
        "original_url": url,
        "company": "Example",
        "title": "Security Engineer",
        "description": "Build and support endpoint security controls.",
        "location": "Remote - Brazil",
        "external_id": external_id,
        "apply_url": url,
    }
    data.update(changes)
    return UnifiedJob(**data)


def test_duplicate_across_two_sources_preserves_both_sources(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            service = JobIngestionService(session)
            first = service.ingest(_job())
            second = service.ingest(
                _job(
                    source="lever",
                    url="https://jobs.lever.co/example/abc",
                    external_id="abc",
                )
            )
            assert first.decision == IngestDecision.NEW
            assert second.decision == IngestDecision.DUPLICATE
            assert first.job_id == second.job_id
        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(JobRecord)) == 1
            assert session.scalar(select(func.count()).select_from(JobSourceRecord)) == 2
    finally:
        database.dispose()


def test_changed_description_creates_snapshot_and_update(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            service = JobIngestionService(session)
            service.ingest(_job())
            updated = service.ingest(_job(description="Updated endpoint and IAM responsibilities."))
            assert updated.decision == IngestDecision.UPDATED
            assert updated.description_changed is True
        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(JobSnapshotRecord)) == 2
    finally:
        database.dispose()


def test_closed_job_seen_again_is_republished(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        with database.session() as session:
            service = JobIngestionService(session)
            created = service.ingest(_job())
            record = session.get(JobRecord, created.job_id)
            assert record is not None
            record.status = "closed"
        with database.session() as session:
            result = JobIngestionService(session).ingest(_job())
            assert result.decision == IngestDecision.REPUBLISHED
    finally:
        database.dispose()
