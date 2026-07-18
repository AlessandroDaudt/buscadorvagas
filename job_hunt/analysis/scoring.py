"""Explainable scoring with deterministic weights and bounded LLM review."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from job_hunt.analysis.filters import evaluate_filters
from job_hunt.analysis.models import DeterministicAssessment, LLMJobReview
from job_hunt.domain.models import (
    CandidateProfile,
    JobAnalysisResult,
    Recommendation,
    ScoreComponents,
    SearchPreferences,
    UnifiedJob,
    WorkMode,
)
from job_hunt.normalization import normalize_match_text

COMPONENT_WEIGHTS: dict[str, float] = {
    "technical": 0.25,
    "experience": 0.18,
    "seniority": 0.10,
    "location": 0.18,
    "language": 0.08,
    "salary": 0.06,
    "education": 0.07,
    "certifications": 0.08,
}

_TRANSFERABLE_GROUPS = (
    {"microsoft entra id", "azure active directory", "azure ad", "iam", "identity"},
    {"microsoft defender for endpoint", "edr", "xdr", "endpoint security"},
    {"microsoft graph", "microsoft graph api", "rest api", "oauth 2.0"},
    {"microsoft sentinel", "siem", "kql", "log analysis", "incident response"},
    {"cyberark", "pam", "pim", "jit"},
    {"windows", "linux", "vmware", "systems engineering"},
    {"networking", "wireshark", "tls", "connectivity troubleshooting"},
    {"enterprise support", "technical support", "troubleshooting", "escalation"},
)


def _terms(text: str) -> set[str]:
    normalized = normalize_match_text(text)
    return {term for term in normalized.replace("/", " ").split() if len(term) > 2}


def _cosine_similarity(left: str, right: str) -> float:
    a, b = Counter(_terms(left)), Counter(_terms(right))
    if not a or not b:
        return 0.0
    numerator = sum(a[key] * b.get(key, 0) for key in a)
    denominator = math.sqrt(sum(value * value for value in a.values())) * math.sqrt(
        sum(value * value for value in b.values())
    )
    return numerator / denominator if denominator else 0.0


def _contains(text: str, phrase: str) -> bool:
    return normalize_match_text(phrase) in text


def _bounded(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 2)


def _candidate_skill_names(profile: CandidateProfile) -> set[str]:
    values = set(profile.professional_summary.domains)
    for experience in profile.experiences:
        values.update(experience.skills)
    values.update(profile.certifications)
    return {normalize_match_text(value) for value in values}


def _related_skills(job_text: str, candidate_skills: set[str]) -> list[str]:
    related: set[str] = set()
    for group in _TRANSFERABLE_GROUPS:
        requested = {skill for skill in group if skill in job_text}
        proven = group & candidate_skills
        if requested and proven and not requested.issubset(proven):
            related.update(sorted(requested - proven))
    return sorted(related)


@dataclass(frozen=True)
class DeterministicScorer:
    preferences: SearchPreferences
    profile: CandidateProfile

    def score(self, job: UnifiedJob) -> DeterministicAssessment:
        job_text = normalize_match_text(
            " ".join((job.title, job.description, job.location or "", job.seniority or ""))
        )
        candidate_skills = _candidate_skill_names(self.profile)
        priority_matches = [
            technology
            for technology in self.preferences.priority_technologies
            if _contains(job_text, technology)
        ]
        proven_matches = sorted(skill for skill in candidate_skills if skill in job_text)
        related = _related_skills(job_text, candidate_skills)

        role_matches = [
            role for role in self.preferences.priority_roles if _contains(job_text, role)
        ]
        role_similarity = max(
            (_cosine_similarity(job.title, role) for role in self.preferences.priority_roles),
            default=0,
        )
        technical = 30 + min(55, len(set(priority_matches)) * 7) + min(15, len(proven_matches) * 2)
        experience = 45 + min(35, len(proven_matches) * 3) + (20 if role_matches else role_similarity * 20)

        title = normalize_match_text(job.title)
        if any(level in title for level in ("senior", "sr ", "lead", "principal", "staff")):
            seniority = 92
        elif any(level in title for level in ("junior", "jr ", "intern", "trainee")):
            seniority = 35
        else:
            seniority = 75

        decision = evaluate_filters(job, self.preferences, self.profile)
        if decision.geographic_restrictions:
            location = 20
        elif job.work_mode == WorkMode.REMOTE:
            location = 100
        elif job.work_mode in (WorkMode.HYBRID, WorkMode.ONSITE):
            location_text = normalize_match_text(job.location or job.description[:2000])
            location = 90 if "rio grande do sul" in location_text else 45
        else:
            location = 60

        language = 92 if any(word in job_text for word in ("english", "ingles")) else 85
        salary = 70 if job.salary_text else 50
        education = 90 if self.profile.education else 50
        certifications = 55 + min(
            45,
            sum(12 for certification in self.profile.certifications if _contains(job_text, certification)),
        )

        strengths = []
        if role_matches:
            strengths.append(f"Cargo prioritário identificado: {role_matches[0]}.")
        if proven_matches:
            strengths.append(
                "Conhecimentos comprovados correspondentes: " + ", ".join(proven_matches[:8]) + "."
            )
        if priority_matches:
            strengths.append(
                "Tecnologias prioritárias presentes: " + ", ".join(priority_matches[:8]) + "."
            )
        gaps = [] if priority_matches else ["Poucas tecnologias prioritárias foram identificadas."]
        risks = list(decision.warnings)
        injection_markers = (
            "ignore previous instructions",
            "ignore all instructions",
            "system prompt",
            "assistant instructions",
            "reveal your prompt",
        )
        if any(marker in job_text for marker in injection_markers):
            risks.append("A descrição contém possível tentativa de prompt injection.")

        return DeterministicAssessment(
            components=ScoreComponents(
                technical=_bounded(technical),
                experience=_bounded(experience),
                seniority=_bounded(seniority),
                location=_bounded(location),
                language=_bounded(language),
                salary=_bounded(salary),
                education=_bounded(education),
                certifications=_bounded(certifications),
            ),
            strengths=strengths,
            gaps=gaps,
            transferable_technologies=related,
            risks=risks,
            matched_terms=sorted(set(priority_matches) | set(proven_matches)),
            filter_decision=decision,
        )


def _recommendation(score: float) -> Recommendation:
    if score >= 90:
        return Recommendation.EXCELLENT
    if score >= 80:
        return Recommendation.STRONG
    if score >= 70:
        return Recommendation.GOOD
    if score >= 60:
        return Recommendation.PARTIAL
    return Recommendation.LOW


def consolidate_analysis(
    deterministic: DeterministicAssessment,
    review: LLMJobReview | None = None,
) -> JobAnalysisResult:
    base = deterministic.components.model_dump()
    adjustments = review.component_adjustments.model_dump() if review else {}
    components = ScoreComponents(
        **{name: _bounded(value + adjustments.get(name, 0)) for name, value in base.items()}
    )
    total = round(
        sum(getattr(components, name) * weight for name, weight in COMPONENT_WEIGHTS.items()),
        2,
    )
    if not deterministic.filter_decision.eligible:
        total = min(total, 59.0)
    strengths = list(dict.fromkeys(deterministic.strengths + (review.strengths if review else [])))
    gaps = list(dict.fromkeys(deterministic.gaps + (review.gaps if review else [])))
    risks = list(dict.fromkeys(deterministic.risks + (review.risks if review else [])))
    restrictions = list(
        dict.fromkeys(
            deterministic.filter_decision.geographic_restrictions
            + (review.geographic_restrictions if review else [])
        )
    )
    explanation = (
        review.explanation
        if review
        else "Score calculado por regras determinísticas; análise de IA não executada."
    )
    return JobAnalysisResult(
        total_score=total,
        components=components,
        strengths=strengths,
        gaps=gaps,
        unmet_mandatory_requirements=(review.unmet_mandatory_requirements if review else []),
        unmet_desirable_requirements=(review.unmet_desirable_requirements if review else []),
        transferable_technologies=list(
            dict.fromkeys(
                deterministic.transferable_technologies
                + (review.transferable_technologies if review else [])
            )
        ),
        risks=risks,
        geographic_restrictions=restrictions,
        skills=review.skills if review else [],
        recommendation=_recommendation(total),
        explanation=explanation,
    )
