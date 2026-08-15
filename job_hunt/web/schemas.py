"""Strict request and response contracts for the dashboard API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from job_hunt.domain.models import ApplicationStatus, StrictModel


class DashboardSummary(StrictModel):
    new_jobs: int = Field(ge=0)
    high_score_jobs: int = Field(ge=0)
    total_jobs: int = Field(ge=0)
    by_company: dict[str, int]
    by_source: dict[str, int]
    by_location: dict[str, int]
    by_modality: dict[str, int]
    by_salary_currency: dict[str, int]
    applications_by_status: dict[str, int]


class JobSummary(StrictModel):
    id: str
    title: str
    company: str
    location: str | None
    modality: str
    user_status: str
    score: float | None
    recommendation: str | None = None
    salary_minimum: float | None = None
    salary_maximum: float | None = None
    salary_currency: str | None = None
    seniority: str | None = None
    country: str | None = None
    lifecycle_status: str = "active"
    published_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime
    source_url: str | None


class JobPage(StrictModel):
    items: list[JobSummary]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class JobDetail(StrictModel):
    job: dict[str, Any]
    company: dict[str, Any]
    sources: list[dict[str, Any]]
    latest_snapshot: dict[str, Any] | None
    latest_analysis: dict[str, Any] | None
    latest_salary: dict[str, Any] | None
    application: dict[str, Any] | None
    application_events: list[dict[str, Any]]
    documents: list[dict[str, Any]]


class DispositionUpdate(StrictModel):
    status: Literal["discovered", "saved", "discarded"]
    reasons: list[str] = Field(default_factory=list, max_length=8)
    note: str | None = Field(default=None, max_length=1000)


class FeedbackUpdate(StrictModel):
    reasons: list[str] = Field(default_factory=list, max_length=8)
    note: str | None = Field(default=None, max_length=1000)


class ActiveLearningAnswer(StrictModel):
    question_id: str = Field(min_length=1, max_length=100)
    answer: str = Field(min_length=1, max_length=100)


class ApplicationUpdate(StrictModel):
    status: ApplicationStatus
    notes: str | None = Field(default=None, max_length=5000)
    allow_reopen: bool = False


class SettingUpdate(StrictModel):
    key: Literal[
        "priority_roles",
        "monitored_companies",
        "priority_technologies",
        "minimum_score",
        "minimum_salary",
        "search_schedule",
        "ai_provider",
        "ai_model",
        "ai_cost_limits",
        "enabled_sources",
    ]
    value: list[str] | str | int | float | bool | dict[str, Any] | None


class DocumentRequest(StrictModel):
    language: Literal["en", "pt-BR"] = "en"
    create_docx: bool = True
    create_pdf: bool = False


class DocumentResponse(StrictModel):
    version: int
    files: list[str]
    changes: dict[str, Any]
