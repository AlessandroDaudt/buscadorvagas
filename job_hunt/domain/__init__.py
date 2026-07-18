"""Typed domain models shared by collectors, scoring, persistence, and UI."""

from job_hunt.domain.models import (
    ApplicationStatus,
    CandidateProfile,
    JobAnalysisResult,
    MasterResume,
    SalaryEstimateResult,
    SearchPreferences,
    UnifiedJob,
)

__all__ = [
    "ApplicationStatus",
    "CandidateProfile",
    "JobAnalysisResult",
    "MasterResume",
    "SalaryEstimateResult",
    "SearchPreferences",
    "UnifiedJob",
]

