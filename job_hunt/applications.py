"""Audited application pipeline transitions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunt.domain.models import ApplicationStatus
from job_hunt.persistence.models import ApplicationEventRecord, ApplicationRecord

_TERMINAL = {
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
    ApplicationStatus.WITHDRAWN,
    ApplicationStatus.CLOSED,
}


class ApplicationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def set_status(
        self,
        job_id: str,
        status: ApplicationStatus,
        *,
        notes: str | None = None,
        allow_reopen: bool = False,
    ) -> ApplicationRecord:
        application = self.session.scalar(
            select(ApplicationRecord).where(ApplicationRecord.job_id == job_id)
        )
        if application is None:
            application = ApplicationRecord(job_id=job_id, status=ApplicationStatus.DISCOVERED.value)
            self.session.add(application)
            self.session.flush()
        previous = ApplicationStatus(application.status)
        if previous in _TERMINAL and previous != status and not allow_reopen:
            raise ValueError("terminal application status requires explicit reopen")
        if previous == status:
            return application
        application.status = status.value
        if notes:
            application.notes = notes
        self.session.add(
            ApplicationEventRecord(
                application_id=application.id,
                from_status=previous.value,
                to_status=status.value,
                notes=notes,
            )
        )
        self.session.flush()
        return application
