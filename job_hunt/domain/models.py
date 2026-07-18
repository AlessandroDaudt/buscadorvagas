"""Validated domain contracts.

These models are intentionally independent from SQLAlchemy. External pages and LLM
responses must pass through a model in this module before they are trusted by the rest
of the application.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class ContractType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACTOR = "contractor"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class CollectionStatus(StrEnum):
    COLLECTED = "collected"
    PARTIAL = "partial"
    FAILED = "failed"
    CLOSED = "closed"


class JobLifecycleStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SalaryKind(StrEnum):
    PUBLISHED = "published"
    INFERRED = "inferred"
    CONVERTED = "converted"
    ESTIMATED = "estimated"


class SalaryPeriod(StrEnum):
    HOURLY = "hourly"
    MONTHLY = "monthly"
    ANNUAL = "annual"
    UNKNOWN = "unknown"


class GrossNet(StrEnum):
    GROSS = "gross"
    NET = "net"
    UNKNOWN = "unknown"


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    ANALYZED = "analyzed"
    SAVED = "saved"
    PLANNED = "application_planned"
    SUBMITTED = "application_submitted"
    RECRUITER_INTERVIEW = "recruiter_interview"
    TECHNICAL_INTERVIEW = "technical_interview"
    MANAGER_INTERVIEW = "manager_interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    CLOSED = "closed"


class SkillEvidence(StrEnum):
    PROVEN = "proven"
    RELATED = "related"
    TRANSFERABLE = "transferable"
    NOT_MET = "not_met"


class Recommendation(StrEnum):
    EXCELLENT = "excellent_match"
    STRONG = "strong_match"
    GOOD = "good_match"
    PARTIAL = "partial_match"
    LOW = "low_priority"


class Identity(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    home_city: Annotated[str, Field(min_length=1, max_length=120)]
    state: Annotated[str, Field(min_length=1, max_length=120)]
    country: Annotated[str, Field(min_length=1, max_length=120)]


class WorkPreferences(StrictModel):
    remote_preferred: bool = True
    open_to_brazil: bool = True
    open_to_international_from_brazil: bool = True
    hybrid_or_onsite_states: list[str] = Field(default_factory=list, max_length=20)
    location_priority: list[str] = Field(default_factory=list, max_length=20)


class ProfessionalSummary(StrictModel):
    years_in_technology: int = Field(ge=0, le=80)
    dell_years_minimum: int = Field(ge=0, le=80)
    recent_employer: str = Field(min_length=1, max_length=200)
    domains: list[str] = Field(default_factory=list, max_length=100)


class CandidateExperience(StrictModel):
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    skills: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def dates_are_ordered(self) -> CandidateExperience:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class EducationItem(StrictModel):
    qualification: str = Field(min_length=1, max_length=300)
    institution: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=1900, le=2200)


class LanguageItem(StrictModel):
    language: str = Field(min_length=1, max_length=100)
    level: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=500)


class CandidateProfile(StrictModel):
    schema_version: Literal[1] = 1
    identity: Identity
    work_preferences: WorkPreferences
    professional_summary: ProfessionalSummary
    experiences: list[CandidateExperience] = Field(default_factory=list, max_length=100)
    education: list[EducationItem] = Field(default_factory=list, max_length=50)
    certifications: list[str] = Field(default_factory=list, max_length=100)
    languages: list[LanguageItem] = Field(default_factory=list, max_length=20)


class SearchFilters(StrictModel):
    minimum_score: int = Field(default=60, ge=0, le=100)
    minimum_salary: float | None = Field(default=None, ge=0)
    salary_currency: str = Field(default="BRL", pattern=r"^[A-Z]{3}$")
    include_remote: bool = True
    include_hybrid: bool = True
    include_onsite: bool = True
    max_age_days: int = Field(default=30, ge=1, le=3650)
    include_seen: bool = False
    include_discarded: bool = False
    include_applied: bool = False
    role_keywords: list[str] = Field(default_factory=list, max_length=200)
    required_keywords: list[str] = Field(default_factory=list, max_length=200)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=200)
    technology_keywords: list[str] = Field(default_factory=list, max_length=300)
    company_allowlist: list[str] = Field(default_factory=list, max_length=500)
    company_blocklist: list[str] = Field(default_factory=list, max_length=500)
    locations: list[str] = Field(default_factory=list, max_length=200)
    countries: list[str] = Field(default_factory=list, max_length=100)
    seniorities: list[str] = Field(default_factory=list, max_length=50)
    languages: list[str] = Field(default_factory=list, max_length=50)
    contract_types: list[ContractType] = Field(default_factory=list, max_length=10)


class ScheduleConfiguration(StrictModel):
    enabled: bool = False
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=100)
    days: list[
        Literal[
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
    ] = Field(default_factory=list, max_length=7)
    time: str = Field(default="08:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    max_duration_minutes: int = Field(default=120, ge=1, le=1440)


class SearchPreferences(StrictModel):
    schema_version: Literal[1] = 1
    priority_roles: list[str] = Field(min_length=1, max_length=200)
    priority_technologies: list[str] = Field(min_length=1, max_length=300)
    monitored_companies: list[str] = Field(min_length=1, max_length=500)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    schedule: ScheduleConfiguration = Field(default_factory=ScheduleConfiguration)

    @field_validator("priority_roles", "priority_technologies", "monitored_companies")
    @classmethod
    def no_blank_or_duplicate_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("lists cannot contain blank values")
        folded = [value.casefold() for value in cleaned]
        if len(folded) != len(set(folded)):
            raise ValueError("lists cannot contain case-insensitive duplicates")
        return cleaned


class ContactInformation(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    linkedin: str | None = Field(default=None, max_length=500)
    github: str | None = Field(default=None, max_length=500)
    location: str = Field(min_length=1, max_length=300)


class ResumeExperience(StrictModel):
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=300)
    start_date: date | None = None
    end_date: date | None = None
    responsibilities: list[str] = Field(default_factory=list, max_length=100)
    achievements: list[str] = Field(default_factory=list, max_length=100)
    technologies: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def dates_are_ordered(self) -> ResumeExperience:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ResumeProject(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    technologies: list[str] = Field(default_factory=list, max_length=100)
    url: str | None = Field(default=None, max_length=500)


class MasterResume(StrictModel):
    schema_version: Literal[1] = 1
    version: int = Field(default=1, ge=1)
    approved: bool = False
    language: str = Field(default="pt-BR", min_length=2, max_length=20)
    contact: ContactInformation
    summary: str = Field(min_length=1, max_length=5000)
    experiences: list[ResumeExperience] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=500)
    education: list[EducationItem] = Field(default_factory=list, max_length=50)
    certifications: list[str] = Field(default_factory=list, max_length=100)
    languages: list[LanguageItem] = Field(default_factory=list, max_length=20)
    projects: list[ResumeProject] = Field(default_factory=list, max_length=100)


class UnifiedJob(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    source_name: str = Field(min_length=1, max_length=100)
    original_url: HttpUrl
    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=500_000)
    location: str | None = Field(default=None, max_length=500)
    work_mode: WorkMode = WorkMode.UNKNOWN
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    external_id: str | None = Field(default=None, max_length=500)
    salary_text: str | None = Field(default=None, max_length=1000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    seniority: str | None = Field(default=None, max_length=100)
    contract_type: ContractType = ContractType.UNKNOWN
    apply_url: HttpUrl | None = None
    collection_status: CollectionStatus = CollectionStatus.COLLECTED
    country: str | None = Field(default=None, max_length=120)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    times_seen: int = Field(default=1, ge=1)


class ScoreComponents(StrictModel):
    technical: float = Field(ge=0, le=100)
    experience: float = Field(ge=0, le=100)
    seniority: float = Field(ge=0, le=100)
    location: float = Field(ge=0, le=100)
    language: float = Field(ge=0, le=100)
    salary: float = Field(ge=0, le=100)
    education: float = Field(ge=0, le=100)
    certifications: float = Field(ge=0, le=100)


class SkillAssessment(StrictModel):
    skill: str = Field(min_length=1, max_length=200)
    evidence: SkillEvidence
    rationale: str = Field(min_length=1, max_length=1000)


class JobAnalysisResult(StrictModel):
    schema_version: Literal[1] = 1
    total_score: float = Field(ge=0, le=100)
    components: ScoreComponents
    strengths: list[str] = Field(default_factory=list, max_length=100)
    gaps: list[str] = Field(default_factory=list, max_length=100)
    unmet_mandatory_requirements: list[str] = Field(default_factory=list, max_length=100)
    unmet_desirable_requirements: list[str] = Field(default_factory=list, max_length=100)
    transferable_technologies: list[str] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    geographic_restrictions: list[str] = Field(default_factory=list, max_length=50)
    skills: list[SkillAssessment] = Field(default_factory=list, max_length=300)
    recommendation: Recommendation
    explanation: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def recommendation_matches_score_band(self) -> JobAnalysisResult:
        expected = (
            Recommendation.EXCELLENT
            if self.total_score >= 90
            else Recommendation.STRONG
            if self.total_score >= 80
            else Recommendation.GOOD
            if self.total_score >= 70
            else Recommendation.PARTIAL
            if self.total_score >= 60
            else Recommendation.LOW
        )
        if self.recommendation != expected:
            raise ValueError("recommendation must match total_score band")
        return self


class SalaryEstimateResult(StrictModel):
    minimum: float | None = Field(default=None, ge=0)
    maximum: float | None = Field(default=None, ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: SalaryPeriod
    gross_net: GrossNet = GrossNet.UNKNOWN
    kind: SalaryKind
    confidence: float = Field(ge=0, le=1)
    source: str = Field(min_length=1, max_length=1000)
    information_date: date
    rationale: str = Field(min_length=1, max_length=5000)
    original_minimum: float | None = Field(default=None, ge=0)
    original_maximum: float | None = Field(default=None, ge=0)
    original_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def range_is_ordered(self) -> SalaryEstimateResult:
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        return self
