"""Fast, auditable recalculation of the dashboard job ranking."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.domain.models import (
    CollectionStatus,
    ContractType,
    UnifiedJob,
    WorkMode,
)
from job_hunt.persistence.database import Database
from job_hunt.persistence.models import (
    CompanyRecord,
    JobAnalysisRecord,
    JobRecord,
    JobSnapshotRecord,
    JobSourceRecord,
)

ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class RankingCandidate:
    job_id: str
    job: UnifiedJob


def _enum_value(enum_type, value: str | None, fallback):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return fallback


class RankingRefreshService:
    """Re-score stored active jobs against the current profile and preferences."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _candidates(self) -> tuple[list[RankingCandidate], int]:
        database = Database(self.database_url)
        candidates: list[RankingCandidate] = []
        skipped = 0
        try:
            with database.session() as session:
                rows = session.execute(
                    select(JobRecord, CompanyRecord)
                    .join(CompanyRecord, CompanyRecord.id == JobRecord.company_id)
                    .where(
                        JobRecord.status == "active",
                        JobRecord.user_status != "discarded",
                    )
                    .order_by(JobRecord.last_seen_at.desc())
                ).all()
                for record, company in rows:
                    snapshot = session.scalar(
                        select(JobSnapshotRecord)
                        .where(JobSnapshotRecord.job_id == record.id)
                        .order_by(JobSnapshotRecord.collected_at.desc())
                        .limit(1)
                    )
                    source = session.scalar(
                        select(JobSourceRecord)
                        .where(JobSourceRecord.job_id == record.id)
                        .order_by(JobSourceRecord.updated_at.desc())
                        .limit(1)
                    )
                    original_url = record.canonical_url or (source.source_url if source else None)
                    if not original_url:
                        skipped += 1
                        continue
                    snapshot_data = dict(snapshot.snapshot_data or {}) if snapshot else {}
                    salary = snapshot_data.get("salary_text") or snapshot_data.get("salary")
                    try:
                        unified = UnifiedJob(
                            id=UUID(record.id),
                            source_name=source.source_name if source else "stored_job",
                            original_url=original_url,
                            apply_url=source.apply_url if source and source.apply_url else None,
                            external_id=source.external_id if source else None,
                            company=company.display_name,
                            title=record.title,
                            description=snapshot.description if snapshot else "",
                            location=record.location,
                            work_mode=_enum_value(WorkMode, record.modality, WorkMode.UNKNOWN),
                            published_at=record.published_at,
                            collected_at=(
                                snapshot.collected_at if snapshot else record.last_seen_at
                            ),
                            salary_text=str(salary)[:1000] if salary else None,
                            seniority=record.seniority,
                            contract_type=_enum_value(
                                ContractType, record.contract_type, ContractType.UNKNOWN
                            ),
                            collection_status=_enum_value(
                                CollectionStatus,
                                source.collection_status if source else None,
                                CollectionStatus.COLLECTED,
                            ),
                            country=record.country,
                            first_seen_at=record.first_seen_at,
                            last_seen_at=record.last_seen_at,
                            times_seen=max(1, record.times_seen),
                        )
                    except ValueError:
                        skipped += 1
                        continue
                    candidates.append(RankingCandidate(record.id, unified))
        finally:
            database.dispose()
        return candidates, skipped

    def refresh(self, progress: ProgressCallback | None = None) -> dict[str, Any]:
        profile = load_candidate_profile()
        preferences = load_search_preferences()
        scorer = DeterministicScorer(preferences, profile)
        candidates, skipped = self._candidates()
        total = len(candidates)
        if progress:
            progress(10, f"Recalculando {total} vagas com o perfil atual")

        scored: list[dict[str, Any]] = []
        pending_records: list[JobAnalysisRecord] = []
        progress_step = max(1, total // 100)
        database = Database(self.database_url)
        try:
            for index, candidate in enumerate(candidates, 1):
                analysis = consolidate_analysis(scorer.score(candidate.job))
                pending_records.append(
                    JobAnalysisRecord(
                        job_id=candidate.job_id,
                        score_total=analysis.total_score,
                        score_data={
                            "components": analysis.components.model_dump(mode="json"),
                            "forced_ranking_refresh": True,
                        },
                        explanation_data={
                            "analysis": analysis.model_dump(mode="json"),
                            "forced_ranking_refresh": True,
                        },
                        provider=None,
                        model=None,
                        prompt_version="ranking-refresh-v1",
                        cache_key=None,
                    )
                )
                if len(pending_records) >= 50 or index == total:
                    with database.session() as session:
                        session.add_all(pending_records)
                    pending_records.clear()
                scored.append(
                    {
                        "job_id": candidate.job_id,
                        "title": candidate.job.title,
                        "company": candidate.job.company,
                        "score": analysis.total_score,
                    }
                )
                if progress and (
                    index == total or index == 1 or index % progress_step == 0
                ):
                    percent = 10 + int(index / max(1, total) * 85)
                    progress(percent, f"Ranking recalculado: {index}/{total} vagas")
        finally:
            database.dispose()

        scored.sort(key=lambda item: item["score"], reverse=True)
        return {
            "analyzed": len(scored),
            "skipped": skipped,
            "minimum_score": preferences.filters.minimum_score,
            "top_jobs": scored[:8],
        }
