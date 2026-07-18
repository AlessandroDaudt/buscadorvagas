import json
from pathlib import Path

import pytest

from job_hunt.configuration import (
    ConfigurationError,
    load_candidate_profile,
    load_master_resume,
    load_search_preferences,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_initial_candidate_profile_is_valid():
    profile = load_candidate_profile(PROJECT_ROOT / "config" / "candidate_profile.json")
    assert profile.identity.name == "Alessandro Luis Daudt"
    assert profile.professional_summary.years_in_technology >= 20
    assert {experience.company for experience in profile.experiences} == {
        "Microsoft",
        "Dell Technologies",
    }


def test_search_preferences_contain_requested_priorities():
    preferences = load_search_preferences(PROJECT_ROOT / "config" / "search_preferences.json")
    assert "Cybersecurity Engineer" in preferences.priority_roles
    assert "Microsoft Defender for Endpoint" in preferences.priority_technologies
    assert "Microsoft" in preferences.monitored_companies
    assert "Dell Technologies" in preferences.monitored_companies
    assert preferences.schedule.timezone == "America/Sao_Paulo"


def test_master_resume_keeps_unknown_dates_empty():
    resume = load_master_resume(PROJECT_ROOT / "resume" / "master_resume.json")
    assert resume.contact.name == "Alessandro Luis Daudt"
    assert resume.approved is False
    assert all(experience.start_date is None for experience in resume.experiences)
    assert all(not experience.achievements for experience in resume.experiences)


def test_configuration_rejects_unknown_fields(tmp_path):
    source = json.loads(
        (PROJECT_ROOT / "config" / "candidate_profile.json").read_text(encoding="utf-8")
    )
    source["unexpected"] = "not allowed"
    target = tmp_path / "profile.json"
    target.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="extra_forbidden"):
        load_candidate_profile(target)


def test_configuration_rejects_oversized_file(tmp_path):
    target = tmp_path / "large.json"
    target.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="exceeds"):
        load_candidate_profile(target)

