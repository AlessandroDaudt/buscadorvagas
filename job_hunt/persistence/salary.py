"""Persistence adapter for published, inferred, converted, or estimated salaries."""

from decimal import Decimal

from sqlalchemy.orm import Session

from job_hunt.domain.models import SalaryEstimateResult
from job_hunt.persistence.models import SalaryEstimateRecord


class SalaryEstimateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, job_id: str, estimate: SalaryEstimateResult) -> SalaryEstimateRecord:
        record = SalaryEstimateRecord(
            job_id=job_id,
            minimum=Decimal(str(estimate.minimum)) if estimate.minimum is not None else None,
            maximum=Decimal(str(estimate.maximum)) if estimate.maximum is not None else None,
            currency=estimate.currency,
            period=estimate.period.value,
            gross_net=estimate.gross_net.value,
            kind=estimate.kind.value,
            confidence=estimate.confidence,
            source=estimate.source,
            information_date=estimate.information_date,
            rationale=estimate.rationale,
            original_data={
                "minimum": estimate.original_minimum,
                "maximum": estimate.original_maximum,
                "currency": estimate.original_currency,
            },
        )
        self.session.add(record)
        self.session.flush()
        return record
