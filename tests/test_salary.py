from datetime import date

from pydantic import HttpUrl

from job_hunt.domain.models import SalaryKind, SalaryPeriod, UnifiedJob, WorkMode
from job_hunt.salary import (
    ExchangeRate,
    SalaryBenchmark,
    SalaryEstimator,
    convert_salary,
    parse_published_salary,
)


def _job(salary_text=None, *, currency=None):
    return UnifiedJob(
        source_name="fixture",
        original_url=HttpUrl("https://example.com/jobs/1"),
        company="Example",
        title="Senior Security Engineer",
        description="Security role",
        location="Remote Brazil",
        work_mode=WorkMode.REMOTE,
        country="Brazil",
        seniority="senior",
        salary_text=salary_text,
        currency=currency,
    )


def test_parse_published_usd_annual_range():
    estimate = parse_published_salary(_job("USD $120k - $150k annual gross"))
    assert estimate
    assert estimate.minimum == 120_000
    assert estimate.maximum == 150_000
    assert estimate.currency == "USD"
    assert estimate.period == SalaryPeriod.ANNUAL
    assert estimate.kind == SalaryKind.PUBLISHED
    assert estimate.confidence == 1


def test_parse_published_brl_monthly_range():
    estimate = parse_published_salary(_job("R$ 15.000 a R$ 20.000 por mês"))
    assert estimate
    assert estimate.minimum == 15_000
    assert estimate.maximum == 20_000
    assert estimate.currency == "BRL"
    assert estimate.period == SalaryPeriod.MONTHLY


def test_salary_absence_is_not_presented_as_confirmed():
    assert parse_published_salary(_job()) is None
    assert SalaryEstimator().estimate(_job()) is None


def test_configured_benchmark_is_labeled_estimated():
    benchmark = SalaryBenchmark(
        role_contains="Security Engineer",
        minimum=10_000,
        maximum=20_000,
        currency="BRL",
        period=SalaryPeriod.MONTHLY,
        source="Manual benchmark approved by user",
        information_date=date(2026, 7, 1),
        confidence=0.6,
        country="Brazil",
        seniority="senior",
        work_mode="remote",
    )
    estimate = SalaryEstimator([benchmark]).estimate(_job())
    assert estimate
    assert estimate.kind == SalaryKind.ESTIMATED
    assert estimate.confidence == 0.6


def test_conversion_keeps_original_values_and_source():
    published = parse_published_salary(_job("USD 100k-120k per year"))
    assert published
    converted = convert_salary(
        published,
        ExchangeRate(
            from_currency="USD",
            to_currency="BRL",
            rate=5,
            information_date=date(2026, 7, 18),
            source="User-configured exchange rate",
        ),
    )
    assert converted.kind == SalaryKind.CONVERTED
    assert converted.minimum == 500_000
    assert converted.original_currency == "USD"
