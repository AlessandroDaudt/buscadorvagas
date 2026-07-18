"""Read-only dashboard queries with allowlisted sorting and bounded pagination."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from job_hunt.persistence.models import (
    ApplicationEventRecord,
    ApplicationRecord,
    CompanyRecord,
    GeneratedDocumentRecord,
    JobAnalysisRecord,
    JobRecord,
    JobSnapshotRecord,
    JobSourceRecord,
    SalaryEstimateRecord,
)
from job_hunt.web.schemas import DashboardSummary, JobDetail, JobPage, JobSummary


def _group_counts(session: Session, statement: Select, *, unknown: str = "Unknown") -> dict[str, int]:
    return {str(key or unknown): int(count) for key, count in session.execute(statement).all()}


def dashboard_summary(session: Session) -> DashboardSummary:
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    return DashboardSummary(
        new_jobs=int(
            session.scalar(select(func.count()).select_from(JobRecord).where(JobRecord.first_seen_at >= cutoff))
            or 0
        ),
        high_score_jobs=int(
            session.scalar(
                select(func.count(func.distinct(JobAnalysisRecord.job_id))).where(
                    JobAnalysisRecord.score_total >= 80
                )
            )
            or 0
        ),
        total_jobs=int(session.scalar(select(func.count()).select_from(JobRecord)) or 0),
        by_company=_group_counts(
            session,
            select(CompanyRecord.display_name, func.count(JobRecord.id))
            .join(JobRecord, JobRecord.company_id == CompanyRecord.id)
            .group_by(CompanyRecord.display_name)
            .order_by(func.count(JobRecord.id).desc())
            .limit(20),
        ),
        by_source=_group_counts(
            session,
            select(JobSourceRecord.source_name, func.count(func.distinct(JobSourceRecord.job_id)))
            .group_by(JobSourceRecord.source_name)
            .order_by(func.count(func.distinct(JobSourceRecord.job_id)).desc()),
        ),
        by_location=_group_counts(
            session,
            select(JobRecord.location, func.count(JobRecord.id))
            .group_by(JobRecord.location)
            .order_by(func.count(JobRecord.id).desc())
            .limit(20),
        ),
        by_modality=_group_counts(
            session,
            select(JobRecord.modality, func.count(JobRecord.id)).group_by(JobRecord.modality),
        ),
        by_salary_currency=_group_counts(
            session,
            select(SalaryEstimateRecord.currency, func.count(func.distinct(SalaryEstimateRecord.job_id)))
            .group_by(SalaryEstimateRecord.currency),
        ),
        applications_by_status=_group_counts(
            session,
            select(ApplicationRecord.status, func.count(ApplicationRecord.id)).group_by(
                ApplicationRecord.status
            ),
        ),
    )


def list_jobs(
    session: Session,
    *,
    search: str | None = None,
    company: str | None = None,
    modality: str | None = None,
    user_status: str | None = None,
    minimum_score: float | None = None,
    sort: str = "last_seen",
    direction: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> JobPage:
    latest_score = (
        select(JobAnalysisRecord.score_total)
        .where(JobAnalysisRecord.job_id == JobRecord.id)
        .order_by(JobAnalysisRecord.created_at.desc())
        .limit(1)
        .correlate(JobRecord)
        .scalar_subquery()
    )
    source_url = (
        select(JobSourceRecord.apply_url)
        .where(JobSourceRecord.job_id == JobRecord.id)
        .order_by(JobSourceRecord.updated_at.desc())
        .limit(1)
        .correlate(JobRecord)
        .scalar_subquery()
    )
    filters = []
    if search:
        term = f"%{search[:200]}%"
        filters.append(or_(JobRecord.title.ilike(term), CompanyRecord.display_name.ilike(term)))
    if company:
        filters.append(CompanyRecord.normalized_name == " ".join(company.casefold().split()))
    if modality:
        filters.append(JobRecord.modality == modality[:30])
    if user_status:
        filters.append(JobRecord.user_status == user_status[:30])
    if minimum_score is not None:
        filters.append(latest_score >= minimum_score)

    count_statement = (
        select(func.count(JobRecord.id))
        .select_from(JobRecord)
        .join(CompanyRecord, CompanyRecord.id == JobRecord.company_id)
        .where(*filters)
    )
    total = int(session.scalar(count_statement) or 0)
    sort_columns = {
        "last_seen": JobRecord.last_seen_at,
        "published": JobRecord.published_at,
        "title": JobRecord.title,
        "company": CompanyRecord.display_name,
        "score": latest_score,
    }
    sort_column = sort_columns.get(sort, JobRecord.last_seen_at)
    ordering = sort_column.asc() if direction == "asc" else sort_column.desc()
    statement = (
        select(JobRecord, CompanyRecord.display_name, latest_score.label("score"), source_url.label("url"))
        .join(CompanyRecord, CompanyRecord.id == JobRecord.company_id)
        .where(*filters)
        .order_by(ordering, JobRecord.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        JobSummary(
            id=job.id,
            title=job.title,
            company=company_name,
            location=job.location,
            modality=job.modality,
            user_status=job.user_status,
            score=float(score) if score is not None else None,
            last_seen_at=job.last_seen_at,
            source_url=url,
        )
        for job, company_name, score, url in session.execute(statement).all()
    ]
    return JobPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        pages=math.ceil(total / page_size) if total else 0,
    )


def job_detail(session: Session, job_id: str) -> JobDetail | None:
    row = session.execute(
        select(JobRecord, CompanyRecord).join(
            CompanyRecord, CompanyRecord.id == JobRecord.company_id
        ).where(JobRecord.id == job_id)
    ).one_or_none()
    if row is None:
        return None
    job, company = row
    sources = session.scalars(
        select(JobSourceRecord).where(JobSourceRecord.job_id == job_id).order_by(JobSourceRecord.updated_at.desc())
    ).all()
    snapshot = session.scalar(
        select(JobSnapshotRecord).where(JobSnapshotRecord.job_id == job_id).order_by(JobSnapshotRecord.collected_at.desc()).limit(1)
    )
    analysis = session.scalar(
        select(JobAnalysisRecord).where(JobAnalysisRecord.job_id == job_id).order_by(JobAnalysisRecord.created_at.desc()).limit(1)
    )
    salary = session.scalar(
        select(SalaryEstimateRecord).where(SalaryEstimateRecord.job_id == job_id).order_by(SalaryEstimateRecord.created_at.desc()).limit(1)
    )
    application = session.scalar(
        select(ApplicationRecord).where(ApplicationRecord.job_id == job_id)
    )
    events = (
        session.scalars(
            select(ApplicationEventRecord)
            .where(ApplicationEventRecord.application_id == application.id)
            .order_by(ApplicationEventRecord.occurred_at.desc())
        ).all()
        if application
        else []
    )
    documents = session.scalars(
        select(GeneratedDocumentRecord).where(GeneratedDocumentRecord.job_id == job_id).order_by(GeneratedDocumentRecord.created_at.desc())
    ).all()
    return JobDetail(
        job={
            "id": job.id,
            "title": job.title,
            "location": job.location,
            "modality": job.modality,
            "country": job.country,
            "seniority": job.seniority,
            "contract_type": job.contract_type,
            "status": job.status,
            "user_status": job.user_status,
            "published_at": job.published_at,
            "first_seen_at": job.first_seen_at,
            "last_seen_at": job.last_seen_at,
            "times_seen": job.times_seen,
        },
        company={
            "id": company.id,
            "name": company.display_name,
            "priority": company.priority,
            "silenced": company.silenced,
        },
        sources=[
            {
                "name": source.source_name,
                "url": source.source_url,
                "apply_url": source.apply_url,
                "external_id": source.external_id,
                "status": source.collection_status,
            }
            for source in sources
        ],
        latest_snapshot=(
            {
                "collected_at": snapshot.collected_at,
                "description": snapshot.description,
                "changes": snapshot.change_summary,
            }
            if snapshot
            else None
        ),
        latest_analysis=analysis.explanation_data.get("analysis") if analysis else None,
        latest_salary=(
            {
                "minimum": float(salary.minimum) if salary.minimum is not None else None,
                "maximum": float(salary.maximum) if salary.maximum is not None else None,
                "currency": salary.currency,
                "period": salary.period,
                "kind": salary.kind,
                "confidence": salary.confidence,
                "source": salary.source,
                "rationale": salary.rationale,
            }
            if salary
            else None
        ),
        application=(
            {"id": application.id, "status": application.status, "notes": application.notes}
            if application
            else None
        ),
        application_events=[
            {
                "from": event.from_status,
                "to": event.to_status,
                "notes": event.notes,
                "occurred_at": event.occurred_at,
            }
            for event in events
        ],
        documents=[
            {
                "id": document.id,
                "type": document.document_type,
                "language": document.language,
                "format": document.file_format,
                "version": document.version,
                "created_at": document.created_at,
            }
            for document in documents
        ],
    )
