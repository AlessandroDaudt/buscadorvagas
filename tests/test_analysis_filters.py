from datetime import datetime, timedelta, timezone

from pydantic import HttpUrl

from job_hunt.analysis.filters import detect_geographic_restrictions, evaluate_filters
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.domain.models import UnifiedJob, WorkMode


def _job(description: str, *, mode: WorkMode = WorkMode.REMOTE, published_at=None) -> UnifiedJob:
    return UnifiedJob(
        source_name="fixture",
        original_url=HttpUrl("https://jobs.example.com/1"),
        company="Example",
        title="Security Engineer",
        description=description,
        location="Remote",
        work_mode=mode,
        published_at=published_at,
    )


def test_remote_us_only_is_reported_not_silently_discarded():
    profile = load_candidate_profile()
    preferences = load_search_preferences()
    decision = evaluate_filters(
        _job("Remote - US only. Must be authorized to work in the United States."),
        preferences,
        profile,
    )
    assert decision.eligible
    assert decision.geographic_restrictions
    assert "Estados Unidos" in decision.geographic_restrictions[0]


def test_global_remote_has_no_geographic_restriction():
    profile = load_candidate_profile()
    job = _job("Worldwide remote. Work from anywhere on endpoint security.")
    assert detect_geographic_restrictions(job, profile) == []


def test_disabled_mode_and_old_job_are_exclusion_reasons():
    profile = load_candidate_profile()
    preferences = load_search_preferences()
    preferences.filters.include_onsite = False
    now = datetime.now(timezone.utc)
    decision = evaluate_filters(
        _job("Onsite role", mode=WorkMode.ONSITE, published_at=now - timedelta(days=90)),
        preferences,
        profile,
        now=now,
    )
    assert not decision.eligible
    assert len(decision.exclusion_reasons) == 2


def test_unknown_mode_and_date_are_warnings():
    decision = evaluate_filters(
        _job("Security role", mode=WorkMode.UNKNOWN),
        load_search_preferences(),
        load_candidate_profile(),
    )
    assert any("publicação" in warning for warning in decision.warnings)
    assert any("Modalidade" in warning for warning in decision.warnings)


def test_configurable_company_keyword_and_contract_filters_are_enforced():
    profile = load_candidate_profile()
    preferences = load_search_preferences()
    preferences.filters.company_allowlist = ["Other Company"]
    preferences.filters.required_keywords = ["CyberArk"]
    preferences.filters.contract_types = ["contractor"]
    decision = evaluate_filters(_job("Endpoint security role"), preferences, profile)
    assert not decision.eligible
    assert len(decision.exclusion_reasons) == 3
