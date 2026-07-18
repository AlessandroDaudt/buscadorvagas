"""Internal schemas used to combine deterministic and model-assisted analysis."""

from __future__ import annotations

from pydantic import Field

from job_hunt.domain.models import ScoreComponents, SkillAssessment, StrictModel


class FilterDecision(StrictModel):
    eligible: bool = True
    exclusion_reasons: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    geographic_restrictions: list[str] = Field(default_factory=list, max_length=50)


class ComponentAdjustments(StrictModel):
    """Bounded LLM suggestions; final score remains application-controlled."""

    technical: float = Field(default=0, ge=-10, le=10)
    experience: float = Field(default=0, ge=-10, le=10)
    seniority: float = Field(default=0, ge=-10, le=10)
    location: float = Field(default=0, ge=-10, le=10)
    language: float = Field(default=0, ge=-10, le=10)
    salary: float = Field(default=0, ge=-10, le=10)
    education: float = Field(default=0, ge=-10, le=10)
    certifications: float = Field(default=0, ge=-10, le=10)


class LLMJobReview(StrictModel):
    """Structured model output. It intentionally has no total-score field."""

    schema_version: int = Field(default=1, ge=1, le=1)
    component_adjustments: ComponentAdjustments = Field(default_factory=ComponentAdjustments)
    strengths: list[str] = Field(default_factory=list, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=30)
    unmet_mandatory_requirements: list[str] = Field(default_factory=list, max_length=30)
    unmet_desirable_requirements: list[str] = Field(default_factory=list, max_length=30)
    transferable_technologies: list[str] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    geographic_restrictions: list[str] = Field(default_factory=list, max_length=20)
    skills: list[SkillAssessment] = Field(default_factory=list, max_length=100)
    explanation: str = Field(min_length=1, max_length=5000)


class DeterministicAssessment(StrictModel):
    components: ScoreComponents
    strengths: list[str] = Field(default_factory=list, max_length=50)
    gaps: list[str] = Field(default_factory=list, max_length=50)
    transferable_technologies: list[str] = Field(default_factory=list, max_length=50)
    risks: list[str] = Field(default_factory=list, max_length=50)
    matched_terms: list[str] = Field(default_factory=list, max_length=200)
    filter_decision: FilterDecision
