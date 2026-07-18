from datetime import date
from pathlib import Path

from pydantic import HttpUrl
from sqlalchemy import select

from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.documents.generator import DocumentGenerator
from job_hunt.domain.models import MasterResume, SalaryPeriod, UnifiedJob
from job_hunt.persistence.database import Database
from job_hunt.persistence.documents import GeneratedDocumentRepository
from job_hunt.persistence.job_ingestion import JobIngestionService
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import GeneratedDocumentRecord, JobRecord, SalaryEstimateRecord
from job_hunt.persistence.salary import SalaryEstimateRepository
from job_hunt.salary import SalaryBenchmark, SalaryEstimator


def test_salary_and_multiple_document_formats_persist(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'records.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    job = UnifiedJob(
        source_name="fixture",
        original_url=HttpUrl("https://example.com/jobs/1"),
        company="Example",
        title="Security Engineer",
        description="Defender for Endpoint",
        country="Brazil",
    )
    master = MasterResume.model_validate_json(
        Path("resume/master_resume.en.json").read_text(encoding="utf-8")
    ).model_copy(update={"approved": True})
    analysis = consolidate_analysis(
        DeterministicScorer(load_search_preferences(), load_candidate_profile()).score(job)
    )
    package = DocumentGenerator(master, output_root=tmp_path / "docs").generate(
        job, analysis, create_docx=True
    )
    estimate = SalaryEstimator(
        [
            SalaryBenchmark(
                role_contains="Security Engineer",
                minimum=10_000,
                maximum=20_000,
                currency="BRL",
                period=SalaryPeriod.MONTHLY,
                source="Manual fixture",
                information_date=date(2026, 7, 1),
            )
        ]
    ).estimate(job)
    assert estimate
    try:
        with database.session() as session:
            JobIngestionService(session).ingest(job)
        with database.session() as session:
            job_id = session.scalar(select(JobRecord.id))
            assert job_id
            SalaryEstimateRepository(session).save(job_id, estimate)
            records = GeneratedDocumentRepository(session).save_package(
                job_id=job_id, resume_master_id=None, package=package
            )
            assert len(records) == 4
        with database.session() as session:
            assert len(session.scalars(select(GeneratedDocumentRecord)).all()) == 4
            assert session.scalar(select(SalaryEstimateRecord)) is not None
    finally:
        database.dispose()
