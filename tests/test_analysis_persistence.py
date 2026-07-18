from pydantic import HttpUrl
from sqlalchemy import select

from job_hunt.analysis.models import LLMJobReview
from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.domain.models import UnifiedJob
from job_hunt.llm.base import StructuredResponse, TokenUsage
from job_hunt.persistence.analysis import AnalysisRepository
from job_hunt.persistence.database import Database
from job_hunt.persistence.job_ingestion import JobIngestionService
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import JobRecord, LLMUsageRecord, PromptVersionRecord


def test_analysis_cache_and_usage_are_persisted(tmp_path):
    url = f"sqlite:///{(tmp_path / 'analysis.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    job = UnifiedJob(
        source_name="fixture",
        original_url=HttpUrl("https://example.com/job/1"),
        company="Example",
        title="Security Engineer",
        description="Defender for Endpoint",
    )
    analysis = consolidate_analysis(
        DeterministicScorer(load_search_preferences(), load_candidate_profile()).score(job),
        LLMJobReview(explanation="Structured review."),
    )
    try:
        with database.session() as session:
            JobIngestionService(session).ingest(job)
        with database.session() as session:
            job_id = session.scalar(select(JobRecord.id))
            assert job_id
            repository = AnalysisRepository(session)
            repository.save(
                job_id=job_id,
                analysis=analysis,
                cache_key="a" * 64,
                prompt_version="v1",
                prompt_hash="b" * 64,
                responses=[StructuredResponse(
                    LLMJobReview(explanation="Structured review."),
                    "openai",
                    "fixture",
                    TokenUsage(100, 50, 0.25),
                    0.1,
                )],
            )
        with database.session() as session:
            repository = AnalysisRepository(session)
            assert repository.get_cached("a" * 64) == analysis
            assert repository.monthly_cost_usd() == 0.25
            assert session.scalar(select(LLMUsageRecord)) is not None
            assert session.scalar(select(PromptVersionRecord)) is not None
    finally:
        database.dispose()
