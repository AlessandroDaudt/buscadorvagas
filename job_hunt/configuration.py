"""Load and validate non-secret configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from job_hunt.domain.models import CandidateProfile, MasterResume, SearchPreferences
from job_hunt.salary import SalaryConfiguration

DEFAULT_CANDIDATE_PROFILE = Path("config/candidate_profile.json")
DEFAULT_SEARCH_PREFERENCES = Path("config/search_preferences.json")
DEFAULT_MASTER_RESUME = Path("resume/master_resume.json")
DEFAULT_SALARY_CONFIGURATION = Path("config/salary_benchmarks.json")
MAX_CONFIG_BYTES = 2 * 1024 * 1024

ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigurationError(ValueError):
    """Raised when a local configuration file is unsafe or invalid."""


def _load_json_model(path: Path, model: type[ModelT]) -> ModelT:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ConfigurationError(f"Cannot read configuration file {path}: {exc}") from exc
    if size > MAX_CONFIG_BYTES:
        raise ConfigurationError(f"Configuration file {path} exceeds {MAX_CONFIG_BYTES} bytes")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return model.model_validate(payload)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}") from exc
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid configuration in {path}: {exc}") from exc


def load_candidate_profile(path: Path = DEFAULT_CANDIDATE_PROFILE) -> CandidateProfile:
    return _load_json_model(path, CandidateProfile)


def load_search_preferences(path: Path = DEFAULT_SEARCH_PREFERENCES) -> SearchPreferences:
    return _load_json_model(path, SearchPreferences)


def load_master_resume(path: Path = DEFAULT_MASTER_RESUME) -> MasterResume:
    return _load_json_model(path, MasterResume)


def load_salary_configuration(
    path: Path = DEFAULT_SALARY_CONFIGURATION,
) -> SalaryConfiguration:
    return _load_json_model(path, SalaryConfiguration)


def enrich_legacy_config(config: dict) -> dict:
    """Attach validated v2 configuration without breaking the legacy CLI shape."""
    candidate_path = Path(config.get("candidate_profile_path", DEFAULT_CANDIDATE_PROFILE))
    search_path = Path(config.get("search_preferences_path", DEFAULT_SEARCH_PREFERENCES))
    resume_path = Path(config.get("master_resume_path", DEFAULT_MASTER_RESUME))

    if candidate_path.exists():
        profile = load_candidate_profile(candidate_path)
        config["candidate_profile"] = profile.model_dump(mode="json")
        candidate = config.setdefault("candidate", {})
        candidate.setdefault("name", profile.identity.name)
        if not candidate.get("profile"):
            domains = ", ".join(profile.professional_summary.domains)
            candidate["profile"] = (
                f"{profile.professional_summary.years_in_technology}+ years in technology; "
                f"{domains}"
            )
        if not candidate.get("seeking"):
            candidate["seeking"] = (
                "Remote roles in Brazil, Latin America, or international roles "
                "that accept professionals based in Brazil"
            )
    if search_path.exists():
        preferences = load_search_preferences(search_path)
        config["search_preferences"] = preferences.model_dump(mode="json")
        candidate = config.setdefault("candidate", {})
        candidate.setdefault("min_score", preferences.filters.minimum_score)
        if not candidate.get("search_keywords"):
            roles = preferences.priority_roles[:16]
            candidate["search_keywords"] = " OR ".join(f'"{role}"' for role in roles)
    if resume_path.exists():
        resume = load_master_resume(resume_path)
        config["master_resume"] = resume.model_dump(mode="json")
    return config
