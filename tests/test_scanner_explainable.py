from pydantic import HttpUrl

from job_hunt import scanner
from job_hunt.domain.models import UnifiedJob
from job_hunt.persistence.database import Database
from job_hunt.persistence.migration import upgrade_database


def test_score_jobs_runs_deterministically_when_ai_disabled(monkeypatch):
    monkeypatch.setattr(scanner, "_ollama_available", lambda _config: (False, "fixture"))
    job = UnifiedJob(
        source_name="fixture", original_url=HttpUrl("https://example.com/jobs/1"),
        company="Microsoft", title="Senior Endpoint Security Engineer",
        location="Remote Brazil", description="Microsoft Defender for Endpoint, Entra ID, EDR, Windows and Linux",
    )
    config = {
        "ollama": {"base_url": "http://localhost:11434"},
        "ai": {"enabled": False},
    }
    output = scanner.score_jobs([job], config)
    assert len(output) == 1
    assert output[0]["deterministic_score"] > 0
    assert output[0]["analysis"]["components"]["technical"] >= 70


def test_score_jobs_bounds_expensive_ai_reviews(monkeypatch):
    analyzed = []

    class Analyzer:
        def __init__(self, *_args, **_kwargs):
            pass

        def analyze(self, job):
            analyzed.append(job.id)
            deterministic = scanner.DeterministicScorer(
                scanner.load_search_preferences(), scanner.load_candidate_profile()
            ).score(job)
            return scanner.consolidate_analysis(deterministic)

    monkeypatch.setenv("AUTOPILOT_AI_REVIEW_LIMIT", "1")
    monkeypatch.setattr(scanner, "_ollama_available", lambda _config: (True, None))
    monkeypatch.setattr(scanner, "JobAnalyzer", Analyzer)
    jobs = [
        UnifiedJob(
            source_name="fixture",
            original_url=HttpUrl(f"https://example.com/jobs/{index}"),
            company="Microsoft",
            title=f"Senior Security Engineer {index}",
            location="Remote Brazil",
            description="Microsoft Defender for Endpoint, Entra ID, EDR, Windows and Linux",
        )
        for index in range(3)
    ]

    output = scanner.score_jobs(
        jobs,
        {"ollama": {"base_url": "http://localhost:11434"}, "ai": {"enabled": True}},
    )

    assert len(output) == 3
    assert len(analyzed) == 1


def test_persistence_accepts_unified_jobs(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    job = UnifiedJob(source_name="fixture", original_url=HttpUrl("https://example.com/jobs/1"), company="Example", title="Security Engineer", description="Endpoint security")
    try:
        decisions = scanner._persist_jobs([job], database)
        assert decisions["new"] == 1
    finally:
        database.dispose()
