"""Salary parsing, configured estimation hierarchy, and optional currency conversion."""

from __future__ import annotations

import re
from datetime import date
from typing import Protocol

from pydantic import Field, model_validator

from job_hunt.domain.models import (
    GrossNet,
    SalaryEstimateResult,
    SalaryKind,
    SalaryPeriod,
    StrictModel,
    UnifiedJob,
)
from job_hunt.normalization import normalize_match_text

_NUMBER_RE = re.compile(r"(?<!\w)(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)[ ]*([kK])?")


class SalaryBenchmark(StrictModel):
    role_contains: str = Field(min_length=1, max_length=300)
    minimum: float = Field(ge=0)
    maximum: float = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: SalaryPeriod
    source: str = Field(min_length=1, max_length=1000)
    information_date: date
    confidence: float = Field(default=0.5, ge=0, le=1)
    company: str | None = Field(default=None, max_length=300)
    country: str | None = Field(default=None, max_length=120)
    seniority: str | None = Field(default=None, max_length=100)
    work_mode: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def ordered_range(self) -> SalaryBenchmark:
        if self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        return self


class ExchangeRate(StrictModel):
    from_currency: str = Field(pattern=r"^[A-Z]{3}$")
    to_currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: float = Field(gt=0)
    information_date: date
    source: str = Field(min_length=1, max_length=1000)


class SalaryConfiguration(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    benchmarks: list[SalaryBenchmark] = Field(default_factory=list, max_length=1000)
    exchange_rates: list[ExchangeRate] = Field(default_factory=list, max_length=100)


class ExternalSalarySource(Protocol):
    def estimate(self, job: UnifiedJob) -> SalaryEstimateResult | None: ...


def _parse_number(raw: str, suffix: str | None) -> float:
    value = raw.strip()
    if "." in value and "," in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        value = value.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif value.count(".") > 1 or value.count(",") > 1:
        value = value.replace(".", "").replace(",", "")
    elif re.fullmatch(r"\d{1,3}[.,]\d{3}", value):
        value = value.replace(".", "").replace(",", "")
    else:
        value = value.replace(",", ".")
    parsed = float(value)
    return parsed * 1000 if suffix else parsed


def parse_published_salary(job: UnifiedJob) -> SalaryEstimateResult | None:
    text = job.salary_text or ""
    if not text.strip():
        return None
    normalized = normalize_match_text(text)
    currency = (
        "BRL"
        if "r$" in text.lower() or "brl" in normalized
        else "EUR"
        if "€" in text or "eur" in normalized
        else "USD"
        if "us$" in text.lower() or "usd" in normalized or "$" in text
        else job.currency
    )
    if not currency:
        return None
    numbers = [_parse_number(number, suffix) for number, suffix in _NUMBER_RE.findall(text)]
    if not numbers:
        return None
    minimum = numbers[0]
    maximum = numbers[1] if len(numbers) > 1 else numbers[0]
    period = (
        SalaryPeriod.HOURLY
        if any(term in normalized for term in ("per hour", "hourly", "por hora"))
        else SalaryPeriod.MONTHLY
        if any(term in normalized for term in ("per month", "monthly", "por mes", "/mes"))
        else SalaryPeriod.ANNUAL
        if any(term in normalized for term in ("per year", "annual", "annually", "por ano"))
        else SalaryPeriod.UNKNOWN
    )
    return SalaryEstimateResult(
        minimum=min(minimum, maximum),
        maximum=max(minimum, maximum),
        currency=currency,
        period=period,
        gross_net=GrossNet.GROSS if "gross" in normalized or "bruto" in normalized else GrossNet.UNKNOWN,
        kind=SalaryKind.PUBLISHED,
        confidence=1,
        source=f"Salary explicitly published in the job at {job.original_url}",
        information_date=(job.published_at.date() if job.published_at else date.today()),
        rationale="Faixa extraída do texto salarial informado pela própria vaga.",
    )


class SalaryEstimator:
    def __init__(
        self,
        benchmarks: list[SalaryBenchmark] | None = None,
        *,
        official_ranges: list[SalaryBenchmark] | None = None,
        external_sources: list[ExternalSalarySource] | None = None,
    ) -> None:
        self.benchmarks = benchmarks or []
        self.official_ranges = official_ranges or []
        self.external_sources = external_sources or []

    def estimate(self, job: UnifiedJob) -> SalaryEstimateResult | None:
        published = parse_published_salary(job)
        if published:
            return published
        official = self._match(job, self.official_ranges, kind=SalaryKind.INFERRED)
        if official:
            return official
        configured = self._match(job, self.benchmarks, kind=SalaryKind.ESTIMATED)
        if configured:
            return configured
        for source in self.external_sources:
            estimate = source.estimate(job)
            if estimate:
                return estimate
        return None

    @staticmethod
    def _match(
        job: UnifiedJob,
        candidates: list[SalaryBenchmark],
        *,
        kind: SalaryKind,
    ) -> SalaryEstimateResult | None:
        title = normalize_match_text(job.title)
        company = normalize_match_text(job.company)
        for benchmark in candidates:
            if normalize_match_text(benchmark.role_contains) not in title:
                continue
            if benchmark.company and normalize_match_text(benchmark.company) != company:
                continue
            if benchmark.country and normalize_match_text(benchmark.country) != normalize_match_text(job.country or ""):
                continue
            if benchmark.seniority and normalize_match_text(benchmark.seniority) not in normalize_match_text(job.seniority or job.title):
                continue
            if benchmark.work_mode and benchmark.work_mode != job.work_mode.value:
                continue
            return SalaryEstimateResult(
                minimum=benchmark.minimum,
                maximum=benchmark.maximum,
                currency=benchmark.currency,
                period=benchmark.period,
                kind=kind,
                confidence=benchmark.confidence,
                source=benchmark.source,
                information_date=benchmark.information_date,
                rationale="Estimativa correspondente a cargo, empresa, país, senioridade e modalidade configurados.",
            )
        return None


def convert_salary(
    estimate: SalaryEstimateResult,
    exchange_rate: ExchangeRate,
) -> SalaryEstimateResult:
    if estimate.currency != exchange_rate.from_currency:
        raise ValueError("exchange rate source currency does not match salary currency")
    return SalaryEstimateResult(
        minimum=round(estimate.minimum * exchange_rate.rate, 2) if estimate.minimum is not None else None,
        maximum=round(estimate.maximum * exchange_rate.rate, 2) if estimate.maximum is not None else None,
        currency=exchange_rate.to_currency,
        period=estimate.period,
        gross_net=estimate.gross_net,
        kind=SalaryKind.CONVERTED,
        confidence=min(estimate.confidence, 0.95),
        source=f"{estimate.source}; converted using {exchange_rate.source}",
        information_date=exchange_rate.information_date,
        rationale="Conversão indicativa; o valor original permanece registrado.",
        original_minimum=estimate.minimum,
        original_maximum=estimate.maximum,
        original_currency=estimate.currency,
    )
