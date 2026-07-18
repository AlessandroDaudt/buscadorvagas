from pathlib import Path

from sqlalchemy import inspect, select

from job_hunt.configuration import load_candidate_profile, load_master_resume
from job_hunt.persistence.database import Database
from job_hunt.persistence.models import Base, CandidateProfileRecord, CompanyRecord
from job_hunt.persistence.repositories import CandidateRepository, CompanyRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_database_creates_all_required_tables(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    try:
        Base.metadata.create_all(database.engine)
        tables = set(inspect(database.engine).get_table_names())
        assert {
            "candidate_profiles",
            "resume_masters",
            "jobs",
            "job_sources",
            "job_snapshots",
            "job_analyses",
            "salary_estimates",
            "generated_documents",
            "applications",
            "application_events",
            "companies",
            "search_runs",
            "notifications",
            "prompt_versions",
            "llm_usage",
            "user_settings",
        } <= tables
    finally:
        database.dispose()


def test_seed_repositories_are_idempotent(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    profile = load_candidate_profile(PROJECT_ROOT / "config" / "candidate_profile.json")
    resume = load_master_resume(PROJECT_ROOT / "resume" / "master_resume.json")
    try:
        with database.session() as session:
            repository = CandidateRepository(session)
            candidate = repository.save_profile(profile)
            repository.save_resume(candidate, resume)
            companies = CompanyRepository(session)
            first = companies.get_or_create("Microsoft", priority=True)
            second = companies.get_or_create(" microsoft ", priority=True)
            assert first.id == second.id
        with database.session() as session:
            assert len(session.scalars(select(CandidateProfileRecord)).all()) == 1
            assert len(session.scalars(select(CompanyRecord)).all()) == 1
    finally:
        database.dispose()

