"""Transactional normalization, deduplication, and job history."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunt.domain.models import UnifiedJob
from job_hunt.normalization import (
    canonicalize_url,
    description_hash,
    normalize_match_text,
)
from job_hunt.persistence.models import JobRecord, JobSnapshotRecord, JobSourceRecord
from job_hunt.persistence.repositories import CompanyRepository


class IngestDecision(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    REPUBLISHED = "republished"
    DUPLICATE = "duplicate"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class IngestResult:
    job_id: str
    decision: IngestDecision
    matched_by: str
    description_changed: bool = False


class JobIngestionService:
    def __init__(self, session: Session, *, similarity_threshold: float = 0.94) -> None:
        if not 0.8 <= similarity_threshold <= 1:
            raise ValueError("similarity_threshold must be between 0.8 and 1")
        self.session = session
        self.similarity_threshold = similarity_threshold
        self.companies = CompanyRepository(session)

    def ingest(self, incoming: UnifiedJob) -> IngestResult:
        company = self.companies.get_or_create(incoming.company)
        canonical_url = canonicalize_url(str(incoming.original_url))
        incoming_hash = description_hash(incoming.description) if incoming.description else None
        normalized_title = normalize_match_text(incoming.title)
        existing, matched_by = self._find_existing(
            incoming,
            company.id,
            canonical_url,
            incoming_hash,
            normalized_title,
        )

        if existing is None:
            job = JobRecord(
                company_id=company.id,
                title=incoming.title,
                normalized_title=normalized_title,
                canonical_url=canonical_url,
                description_hash=incoming_hash,
                location=incoming.location,
                modality=incoming.work_mode.value,
                country=incoming.country,
                seniority=incoming.seniority,
                contract_type=incoming.contract_type.value,
                status="active",
                published_at=incoming.published_at,
                first_seen_at=incoming.first_seen_at or incoming.collected_at,
                last_seen_at=incoming.last_seen_at or incoming.collected_at,
                times_seen=incoming.times_seen,
            )
            self.session.add(job)
            self.session.flush()
            self._upsert_source(job, incoming)
            if incoming_hash:
                self._add_snapshot(job, incoming, incoming_hash, {"created": True})
            return IngestResult(job.id, IngestDecision.NEW, "none", bool(incoming_hash))

        was_closed = existing.status == "closed"
        old_hash = existing.description_hash
        description_changed = bool(incoming_hash and old_hash and incoming_hash != old_hash)
        same_description = bool(incoming_hash and old_hash == incoming_hash)
        source_created = self._upsert_source(existing, incoming)

        existing.title = incoming.title
        existing.normalized_title = normalized_title
        existing.location = incoming.location or existing.location
        existing.modality = (
            incoming.work_mode.value
            if incoming.work_mode.value != "unknown"
            else existing.modality
        )
        existing.country = incoming.country or existing.country
        existing.seniority = incoming.seniority or existing.seniority
        if incoming.contract_type.value != "unknown":
            existing.contract_type = incoming.contract_type.value
        existing.published_at = incoming.published_at or existing.published_at
        existing.last_seen_at = incoming.last_seen_at or incoming.collected_at
        existing.times_seen += max(1, incoming.times_seen)
        existing.status = "active"
        if incoming_hash:
            existing.description_hash = incoming_hash

        if incoming_hash is not None and (description_changed or not old_hash):
            self._add_snapshot(
                existing,
                incoming,
                incoming_hash,
                {"description_changed": description_changed, "previous_hash": old_hash},
            )
        if was_closed:
            decision = IngestDecision.REPUBLISHED
        elif description_changed or (incoming_hash and not old_hash):
            decision = IngestDecision.UPDATED
        elif source_created or matched_by not in {"source_external_id", "canonical_url"}:
            decision = IngestDecision.DUPLICATE
        elif same_description or not incoming_hash:
            decision = IngestDecision.UNCHANGED
        else:
            decision = IngestDecision.UPDATED
        return IngestResult(existing.id, decision, matched_by, description_changed)

    def _find_existing(
        self,
        incoming: UnifiedJob,
        company_id: str,
        canonical_url: str,
        incoming_hash: str | None,
        normalized_title: str,
    ) -> tuple[JobRecord | None, str]:
        if incoming.external_id:
            source_match = self.session.scalar(
                select(JobRecord)
                .join(JobSourceRecord, JobSourceRecord.job_id == JobRecord.id)
                .where(
                    JobSourceRecord.source_name == incoming.source_name,
                    JobSourceRecord.external_id == incoming.external_id,
                )
            )
            if source_match:
                return source_match, "source_external_id"

        url_match = self.session.scalar(
            select(JobRecord).where(JobRecord.canonical_url == canonical_url)
        )
        if url_match:
            return url_match, "canonical_url"

        if incoming_hash:
            hash_match = self.session.scalar(
                select(JobRecord).where(
                    JobRecord.company_id == company_id,
                    JobRecord.description_hash == incoming_hash,
                )
            )
            if hash_match:
                return hash_match, "description_hash"

        candidates = self.session.scalars(
            select(JobRecord).where(
                JobRecord.company_id == company_id,
                JobRecord.normalized_title == normalized_title,
            )
        ).all()
        normalized_location = normalize_match_text(incoming.location or "")
        for candidate in candidates:
            if normalize_match_text(candidate.location or "") == normalized_location:
                if not incoming.description:
                    return candidate, "company_title_location"
                snapshot = self.session.scalar(
                    select(JobSnapshotRecord)
                    .where(JobSnapshotRecord.job_id == candidate.id)
                    .order_by(JobSnapshotRecord.collected_at.desc())
                    .limit(1)
                )
                if snapshot:
                    similarity = SequenceMatcher(
                        None,
                        normalize_match_text(snapshot.description[:50_000]),
                        normalize_match_text(incoming.description[:50_000]),
                    ).ratio()
                    if similarity >= self.similarity_threshold:
                        return candidate, "text_similarity"
        return None, "none"

    def _upsert_source(self, job: JobRecord, incoming: UnifiedJob) -> bool:
        conditions = [
            JobSourceRecord.job_id == job.id,
            JobSourceRecord.source_name == incoming.source_name,
        ]
        if incoming.external_id:
            conditions.append(JobSourceRecord.external_id == incoming.external_id)
        else:
            conditions.append(JobSourceRecord.source_url == str(incoming.original_url))
        source = self.session.scalar(select(JobSourceRecord).where(*conditions))
        created = source is None
        if source is None:
            source = JobSourceRecord(
                job_id=job.id,
                source_name=incoming.source_name,
                source_url=str(incoming.original_url),
                external_id=incoming.external_id,
                apply_url=str(incoming.apply_url) if incoming.apply_url else None,
                collection_status=incoming.collection_status.value,
                raw_data=incoming.model_dump(mode="json"),
            )
            self.session.add(source)
        else:
            source.source_url = str(incoming.original_url)
            source.apply_url = str(incoming.apply_url) if incoming.apply_url else source.apply_url
            source.collection_status = incoming.collection_status.value
            source.raw_data = incoming.model_dump(mode="json")
        self.session.flush()
        return created

    def _add_snapshot(
        self,
        job: JobRecord,
        incoming: UnifiedJob,
        content_hash: str,
        change_summary: dict,
    ) -> None:
        self.session.add(
            JobSnapshotRecord(
                job_id=job.id,
                collected_at=incoming.collected_at,
                content_hash=content_hash,
                description=incoming.description,
                snapshot_data=incoming.model_dump(mode="json"),
                change_summary=change_summary,
            )
        )
