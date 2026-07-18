"""Relational persistence models.

Public IDs are random UUID strings. JSON columns preserve structured evidence while
frequently filtered fields remain relational columns.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CandidateProfileRecord(TimestampMixin, Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    profile_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ResumeMasterRecord(TimestampMixin, Base):
    __tablename__ = "resume_masters"
    __table_args__ = (UniqueConstraint("candidate_profile_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    candidate_profile_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    content_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CompanyRecord(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    careers_url: Mapped[str | None] = mapped_column(String(2000))
    priority: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    silenced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class JobRecord(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_company_title", "company_id", "normalized_title"),
        Index("ix_jobs_last_seen", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2000), unique=True)
    description_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    location: Mapped[str | None] = mapped_column(String(500))
    modality: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    country: Mapped[str | None] = mapped_column(String(120))
    seniority: Mapped[str | None] = mapped_column(String(100))
    contract_type: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class JobSourceRecord(TimestampMixin, Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", name="uq_source_external_id"),
        Index("ix_job_sources_job_source", "job_id", "source_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(500))
    apply_url: Mapped[str | None] = mapped_column(String(2000))
    collection_status: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class JobSnapshotRecord(Base):
    __tablename__ = "job_snapshots"
    __table_args__ = (Index("ix_job_snapshots_job_collected", "job_id", "collected_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    change_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class JobAnalysisRecord(Base):
    __tablename__ = "job_analyses"
    __table_args__ = (Index("ix_job_analyses_job_created", "job_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    score_total: Mapped[float] = mapped_column(Float, nullable=False)
    score_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    explanation_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(300))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    cache_key: Mapped[str | None] = mapped_column(String(64), unique=True)


class SalaryEstimateRecord(TimestampMixin, Base):
    __tablename__ = "salary_estimates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    minimum: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    maximum: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    gross_net: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(1000), nullable=False)
    information_date: Mapped[date] = mapped_column(Date, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    original_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class GeneratedDocumentRecord(TimestampMixin, Base):
    __tablename__ = "generated_documents"
    __table_args__ = (
        UniqueConstraint("job_id", "document_type", "language", "file_format", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_master_id: Mapped[str | None] = mapped_column(
        ForeignKey("resume_masters.id", ondelete="SET NULL")
    )
    document_type: Mapped[str] = mapped_column(String(30), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    file_format: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str | None] = mapped_column(String(300))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    master_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ApplicationRecord(TimestampMixin, Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="discovered")
    notes: Mapped[str | None] = mapped_column(Text)


class ApplicationEventRecord(Base):
    __tablename__ = "application_events"
    __table_args__ = (Index("ix_application_events_application_time", "application_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(50))
    to_status: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SearchRunRecord(Base):
    __tablename__ = "search_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="America/Sao_Paulo")
    summary_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_data: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)


class NotificationRecord(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    search_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_runs.id", ondelete="SET NULL"), index=True
    )
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(500))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromptVersionRecord(TimestampMixin, Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LLMUsageRecord(Base):
    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_created_provider", "created_at", "provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    search_run_id: Mapped[str | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_analyses.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(300), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserSettingRecord(TimestampMixin, Base):
    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    value_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
