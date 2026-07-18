from job_hunt import scanner
from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.domain.models import UnifiedJob


def _config():
    profile = load_candidate_profile()
    preferences = load_search_preferences()
    return {
        "scoring": {"engine": "explainable"},
        "ai": {"enabled": False, "primary": {"provider": "openai", "model": "fixture"}},
        "candidate_profile": profile.model_dump(mode="json"),
        "search_preferences": preferences.model_dump(mode="json"),
    }


def test_score_jobs_routes_to_explainable_engine(monkeypatch):
    profile, preferences = load_candidate_profile(), load_search_preferences()

    class FakeAnalyzer:
        def __init__(self, *_args, **_kwargs):
            pass

        def analyze(self, job: UnifiedJob):
            return consolidate_analysis(DeterministicScorer(preferences, profile).score(job))

    monkeypatch.setattr("job_hunt.analysis.service.JobAnalyzer", FakeAnalyzer)
    jobs = [
        {
            "company": "Microsoft",
            "title": "Senior Endpoint Security Engineer",
            "location": "Remote Brazil",
            "url": "https://example.com/jobs/1",
            "content": "Microsoft Defender for Endpoint, Entra ID, EDR, Windows and Linux",
        }
    ]
    output = scanner.score_jobs(jobs, "legacy resume ignored", _config())
    assert len(output) == 1
    assert output[0]["analysis"]["components"]["technical"] >= 70
    assert output[0]["worth_applying"] is True


def test_explainable_engine_skips_invalid_job_url():
    jobs = [{"company": "Example", "title": "Security", "location": "Remote", "url": "bad"}]
    assert scanner.score_jobs(jobs, "resume", _config()) == []
