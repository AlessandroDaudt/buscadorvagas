"""Orchestrate deterministic scoring, cache, structured LLM review, and consensus."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from typing import Protocol

from job_hunt.analysis.models import ComponentAdjustments, LLMJobReview
from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.domain.models import (
    CandidateProfile,
    JobAnalysisResult,
    SearchPreferences,
    UnifiedJob,
)
from job_hunt.llm.base import StructuredResponse
from job_hunt.llm.config import LLMSettings
from job_hunt.llm.router import LLMRouter
from job_hunt.log import get_logger

logger = get_logger()

PROMPT_VERSION = "v1"


class AnalysisCache(Protocol):
    def get_cached(self, cache_key: str) -> JobAnalysisResult | None: ...

    def save(
        self,
        *,
        job_id: str,
        analysis: JobAnalysisResult,
        cache_key: str,
        prompt_version: str,
        prompt_hash: str,
        responses: list[StructuredResponse],
    ) -> object: ...


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user_template: str
    version: str
    content_hash: str


def load_analysis_prompt() -> PromptBundle:
    root = resources.files("job_hunt").joinpath(
        "prompts", "job_analysis", PROMPT_VERSION
    )
    system = root.joinpath("system.md").read_text(encoding="utf-8")
    user = root.joinpath("user.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()
    return PromptBundle(system=system, user_template=user, version=PROMPT_VERSION, content_hash=digest)


def analysis_cache_key(
    job: UnifiedJob,
    profile: CandidateProfile,
    preferences: SearchPreferences,
    settings: LLMSettings,
    prompt: PromptBundle,
) -> str:
    payload = {
        "job": job.model_dump(mode="json", exclude={"collected_at", "first_seen_at", "last_seen_at"}),
        "profile": profile.model_dump(mode="json"),
        "preferences": preferences.model_dump(mode="json"),
        "llm_settings": settings.model_dump(mode="json") if settings.enabled else None,
        "prompt": prompt.content_hash,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _merge_reviews(
    first: LLMJobReview,
    second: LLMJobReview,
    *,
    divergence_threshold: float,
) -> LLMJobReview:
    a = first.component_adjustments.model_dump()
    b = second.component_adjustments.model_dump()
    divergence = max(abs(a[key] - b[key]) for key in a)
    risks = list(dict.fromkeys(first.risks + second.risks))
    if divergence >= divergence_threshold:
        risks.append(f"Modelos revisores divergiram em até {divergence:.1f} pontos de componente.")

    def merged(attribute: str) -> list:
        return list(dict.fromkeys(getattr(first, attribute) + getattr(second, attribute)))

    return LLMJobReview(
        component_adjustments=ComponentAdjustments(
            **{key: round((a[key] + b[key]) / 2, 2) for key in a}
        ),
        strengths=merged("strengths"),
        gaps=merged("gaps"),
        unmet_mandatory_requirements=merged("unmet_mandatory_requirements"),
        unmet_desirable_requirements=merged("unmet_desirable_requirements"),
        transferable_technologies=merged("transferable_technologies"),
        risks=risks,
        geographic_restrictions=merged("geographic_restrictions"),
        skills=first.skills + [skill for skill in second.skills if skill not in first.skills],
        explanation=f"Análise principal: {first.explanation}\nRevisão de consenso: {second.explanation}",
    )


class JobAnalyzer:
    def __init__(
        self,
        profile: CandidateProfile,
        preferences: SearchPreferences,
        settings: LLMSettings,
        *,
        router: LLMRouter | None = None,
        cache: AnalysisCache | None = None,
    ) -> None:
        self.profile = profile
        self.preferences = preferences
        self.settings = settings
        self.router = router or LLMRouter(settings)
        self.cache = cache
        self.prompt = load_analysis_prompt()

    def analyze(self, job: UnifiedJob, *, job_id: str | None = None) -> JobAnalysisResult:
        cache_key = analysis_cache_key(
            job, self.profile, self.preferences, self.settings, self.prompt
        )
        if self.cache:
            cached = self.cache.get_cached(cache_key)
            if cached:
                logger.info("Job analysis cache hit")
                return cached

        deterministic = DeterministicScorer(self.preferences, self.profile).score(job)
        responses: list[StructuredResponse] = []
        review: LLMJobReview | None = None
        if self.settings.enabled:
            user_prompt = self.prompt.user_template.format(
                candidate_profile=json.dumps(
                    self.profile.model_dump(mode="json"), ensure_ascii=False, indent=2
                ),
                deterministic_assessment=json.dumps(
                    deterministic.model_dump(mode="json"), ensure_ascii=False, indent=2
                ),
                job_description=json.dumps(
                    {
                        "company": job.company,
                        "title": job.title,
                        "location": job.location,
                        "work_mode": job.work_mode,
                        "description": job.description[:60_000],
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
            )
            try:
                response = self.router.generate(
                    system_prompt=self.prompt.system,
                    user_prompt=user_prompt,
                    response_model=LLMJobReview,
                )
                responses.append(response)
                review = LLMJobReview.model_validate(response.data)
                if self.settings.consensus_reviewer:
                    reviewer_response = self.router.generate(
                        system_prompt=self.prompt.system,
                        user_prompt=user_prompt,
                        response_model=LLMJobReview,
                        provider_settings=self.settings.consensus_reviewer,
                    )
                    responses.append(reviewer_response)
                    reviewer = LLMJobReview.model_validate(reviewer_response.data)
                    review = _merge_reviews(
                        review,
                        reviewer,
                        divergence_threshold=self.settings.consensus_divergence_threshold,
                    )
            except Exception as exc:
                logger.warning("LLM review unavailable; retaining deterministic score (%s)", type(exc).__name__)
                deterministic.risks.append(
                    "Análise de IA indisponível; resultado calculado apenas por regras determinísticas."
                )

        analysis = consolidate_analysis(deterministic, review)
        if self.cache and job_id:
            self.cache.save(
                job_id=job_id,
                analysis=analysis,
                cache_key=cache_key,
                prompt_version=self.prompt.version,
                prompt_hash=self.prompt.content_hash,
                responses=responses,
            )
        return analysis
