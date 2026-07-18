"""Configurable filters, including detection of misleading remote claims."""

from __future__ import annotations

from datetime import datetime, timezone

from job_hunt.analysis.models import FilterDecision
from job_hunt.domain.models import CandidateProfile, SearchPreferences, UnifiedJob, WorkMode
from job_hunt.normalization import normalize_match_text

_US_ONLY_MARKERS = (
    "remote within the united states",
    "remote in the united states",
    "us based only",
    "u.s. based only",
    "must reside in the us",
    "must reside in the united states",
    "must be authorized to work in the united states",
    "no visa sponsorship",
    "remote - us only",
    "remote us only",
)
_COUNTRY_RESIDENCE_MARKERS = (
    "must reside in",
    "must be located in",
    "residents only",
    "work authorization required",
)
_GLOBAL_MARKERS = (
    "work from anywhere",
    "worldwide remote",
    "remote worldwide",
    "global remote",
    "anywhere in the world",
)
_BRAZIL_MARKERS = ("brazil", "brasil", "latin america", "latam", "south america")
_FREQUENT_OFFICE_MARKERS = (
    "days per week in office",
    "days a week in office",
    "weekly onsite",
    "frequent travel to the office",
)


def detect_geographic_restrictions(job: UnifiedJob, profile: CandidateProfile) -> list[str]:
    text = normalize_match_text(" ".join((job.title, job.location or "", job.description[:40_000])))
    restrictions: list[str] = []
    if job.work_mode == WorkMode.REMOTE:
        if any(marker in text for marker in _US_ONLY_MARKERS):
            restrictions.append("Vaga remota restrita aos Estados Unidos ou à autorização local.")
        elif any(marker in text for marker in _GLOBAL_MARKERS):
            return restrictions
        elif any(marker in text for marker in _BRAZIL_MARKERS):
            return restrictions
        elif any(marker in text for marker in _COUNTRY_RESIDENCE_MARKERS):
            restrictions.append("A vaga remota exige residência ou autorização em local específico.")
    if any(marker in text for marker in _FREQUENT_OFFICE_MARKERS):
        state = normalize_match_text(profile.identity.state)
        if state not in text and "rio grande do sul" not in text:
            restrictions.append("Comparecimento frequente ao escritório pode ser incompatível.")
    return restrictions


def evaluate_filters(
    job: UnifiedJob,
    preferences: SearchPreferences,
    profile: CandidateProfile,
    *,
    now: datetime | None = None,
) -> FilterDecision:
    filters = preferences.filters
    reasons: list[str] = []
    warnings: list[str] = []
    searchable = normalize_match_text(
        " ".join((job.title, job.description[:100_000], job.location or "", job.country or ""))
    )
    company = normalize_match_text(job.company)

    def normalized(values: list[str]) -> list[str]:
        return [normalize_match_text(value) for value in values]

    if filters.company_allowlist and company not in normalized(filters.company_allowlist):
        reasons.append("Empresa fora da lista permitida configurada.")
    if company in normalized(filters.company_blocklist):
        reasons.append("Empresa silenciada ou bloqueada nos filtros.")
    missing = [value for value in filters.required_keywords if normalize_match_text(value) not in searchable]
    if missing:
        reasons.append("Palavras-chave obrigatórias ausentes: " + ", ".join(missing) + ".")
    excluded = [value for value in filters.excluded_keywords if normalize_match_text(value) in searchable]
    if excluded:
        reasons.append("Palavras-chave excluídas encontradas: " + ", ".join(excluded) + ".")
    if filters.role_keywords and not any(
        normalize_match_text(value) in normalize_match_text(job.title)
        for value in filters.role_keywords
    ):
        reasons.append("Cargo fora dos termos de cargo configurados.")
    if filters.technology_keywords and not any(
        normalize_match_text(value) in searchable for value in filters.technology_keywords
    ):
        reasons.append("Nenhuma tecnologia obrigatória configurada foi encontrada.")
    if filters.locations and not any(value in searchable for value in normalized(filters.locations)):
        reasons.append("Localização fora das regiões configuradas.")
    if filters.countries and not any(value in searchable for value in normalized(filters.countries)):
        reasons.append("País fora da lista configurada.")
    if filters.seniorities and not any(value in searchable for value in normalized(filters.seniorities)):
        reasons.append("Senioridade fora da lista configurada.")
    if filters.languages and not any(value in searchable for value in normalized(filters.languages)):
        reasons.append("Idioma exigido não corresponde aos filtros configurados.")
    if filters.contract_types and job.contract_type not in filters.contract_types:
        reasons.append("Tipo de contrato fora da lista configurada.")

    if job.work_mode == WorkMode.REMOTE and not filters.include_remote:
        reasons.append("Modalidade remota desabilitada nos filtros.")
    elif job.work_mode == WorkMode.HYBRID and not filters.include_hybrid:
        reasons.append("Modalidade híbrida desabilitada nos filtros.")
    elif job.work_mode == WorkMode.ONSITE and not filters.include_onsite:
        reasons.append("Modalidade presencial desabilitada nos filtros.")

    current = now or datetime.now(timezone.utc)
    if job.published_at:
        published = job.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(0, (current - published).days)
        if age_days > filters.max_age_days:
            reasons.append(
                f"Vaga publicada há {age_days} dias; limite configurado: {filters.max_age_days}."
            )
    else:
        warnings.append("Data de publicação não informada; a idade não pôde ser validada.")

    restrictions = detect_geographic_restrictions(job, profile)
    if restrictions:
        warnings.extend(restrictions)

    if job.work_mode == WorkMode.UNKNOWN:
        warnings.append("Modalidade de trabalho não identificada.")
    if not job.salary_text:
        warnings.append("Salário não publicado; o filtro salarial será avaliado após estimativa.")

    return FilterDecision(
        eligible=not reasons,
        exclusion_reasons=reasons,
        warnings=warnings,
        geographic_restrictions=restrictions,
    )
