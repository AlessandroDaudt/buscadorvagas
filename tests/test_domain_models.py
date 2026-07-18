from datetime import date

import pytest
from pydantic import ValidationError

from job_hunt.domain.models import (
    GrossNet,
    JobAnalysisResult,
    Recommendation,
    SalaryEstimateResult,
    SalaryKind,
    SalaryPeriod,
    ScoreComponents,
    UnifiedJob,
)


def _components() -> ScoreComponents:
    return ScoreComponents(
        technical=80,
        experience=80,
        seniority=80,
        location=80,
        language=80,
        salary=80,
        education=80,
        certifications=80,
    )


def test_unified_job_requires_http_url():
    with pytest.raises(ValidationError):
        UnifiedJob(
            source_name="fixture",
            original_url="file:///etc/passwd",
            company="Acme",
            title="Security Engineer",
        )


def test_analysis_recommendation_must_match_score():
    with pytest.raises(ValidationError, match="recommendation must match"):
        JobAnalysisResult(
            total_score=85,
            components=_components(),
            recommendation=Recommendation.LOW,
            explanation="Structured explanation",
        )


def test_salary_range_must_be_ordered():
    with pytest.raises(ValidationError, match="maximum"):
        SalaryEstimateResult(
            minimum=20_000,
            maximum=10_000,
            currency="BRL",
            period=SalaryPeriod.MONTHLY,
            gross_net=GrossNet.GROSS,
            kind=SalaryKind.PUBLISHED,
            confidence=1,
            source="job posting",
            information_date=date(2026, 7, 18),
            rationale="Explicitly published",
        )

