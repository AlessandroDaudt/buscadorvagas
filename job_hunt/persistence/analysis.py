"""Relational cache and usage ledger for explainable analyses."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from job_hunt.domain.models import JobAnalysisResult
from job_hunt.llm.base import StructuredResponse
from job_hunt.metrics import metrics
from job_hunt.persistence.models import JobAnalysisRecord, LLMUsageRecord, PromptVersionRecord


class AnalysisRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_cached(self, cache_key: str) -> JobAnalysisResult | None:
        record = self.session.scalar(
            select(JobAnalysisRecord).where(JobAnalysisRecord.cache_key == cache_key)
        )
        if record is None:
            metrics.increment("analysis_cache_misses_total")
            return None
        metrics.increment("analysis_cache_hits_total")
        payload = record.explanation_data.get("analysis")
        return JobAnalysisResult.model_validate(payload) if payload else None

    def save(
        self,
        *,
        job_id: str,
        analysis: JobAnalysisResult,
        cache_key: str,
        prompt_version: str,
        prompt_hash: str,
        responses: list[StructuredResponse],
    ) -> JobAnalysisRecord:
        primary_response = responses[0] if responses else None
        provider = primary_response.provider if primary_response else None
        model = primary_response.model if primary_response else None
        record = JobAnalysisRecord(
            job_id=job_id,
            score_total=analysis.total_score,
            score_data={"components": analysis.components.model_dump(mode="json")},
            explanation_data={"analysis": analysis.model_dump(mode="json")},
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            cache_key=cache_key,
        )
        self.session.add(record)
        self.session.flush()
        prompt = self.session.scalar(
            select(PromptVersionRecord).where(
                PromptVersionRecord.name == "job_analysis",
                PromptVersionRecord.version == prompt_version,
            )
        )
        if prompt is None:
            self.session.add(
                PromptVersionRecord(
                    name="job_analysis",
                    version=prompt_version,
                    content_hash=prompt_hash,
                    template_path=f"job_hunt/prompts/job_analysis/{prompt_version}",
                    active=True,
                )
            )
        for response in responses:
            self.session.add(
                LLMUsageRecord(
                    job_id=job_id,
                    analysis_id=record.id,
                    provider=response.provider,
                    model=response.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    estimated_cost=Decimal(str(response.usage.estimated_cost_usd)),
                    currency="USD",
                    cached=False,
                )
            )
        return record

    def monthly_cost_usd(self, *, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        value = self.session.scalar(
            select(func.coalesce(func.sum(LLMUsageRecord.estimated_cost), 0)).where(
                LLMUsageRecord.created_at >= month_start
            )
        )
        return float(value or 0)
