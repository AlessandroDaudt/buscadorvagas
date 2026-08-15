from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from job_hunt.persistence.models import ResumeVersionRecord
from job_hunt.resume_import.models import ResumeImportResult, ResumeValidationResult
from job_hunt.resume_import.validation import validate_markdown


class ResumeVersionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        result: ResumeImportResult,
        *,
        original_filename: str,
        source_hash: str,
        previous_version_id: str | None = None,
    ) -> ResumeVersionRecord:
        record = ResumeVersionRecord(
            original_filename=original_filename,
            source_format=result.source_format,
            source_hash=source_hash,
            markdown=result.markdown,
            extraction_method=result.extraction_method,
            detected_sections=result.detected_sections,
            warnings=result.warnings,
            metadata_data=result.metadata,
            status="draft",
            previous_version_id=previous_version_id,
            approved=False,
            active=False,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def edit(self, current: ResumeVersionRecord, markdown: str) -> ResumeVersionRecord:
        cleaned = markdown.strip()
        if not cleaned or len(cleaned) > 1_000_000:
            raise ValueError("Markdown must contain between 1 and 1,000,000 characters")
        validation = validate_markdown(cleaned)
        result = ResumeImportResult(
            markdown=cleaned + "\n",
            source_format="md",
            warnings=validation.warnings,
            detected_sections=validation.detected_sections,
            metadata={"edited_from": current.id},
            extraction_method="manual-web-edit",
        )
        return self.create(
            result,
            original_filename=current.original_filename,
            source_hash=hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
            previous_version_id=current.id,
        )

    def validate(self, record: ResumeVersionRecord) -> ResumeValidationResult:
        result = validate_markdown(record.markdown)
        record.warnings = result.warnings
        record.detected_sections = result.detected_sections
        if result.valid:
            record.status = "validated"
        return result

    def approve(self, record: ResumeVersionRecord) -> ResumeVersionRecord:
        validation = self.validate(record)
        if not validation.valid:
            raise ValueError("resume cannot be approved while validation errors remain")
        record.approved = True
        record.status = "approved"
        return record

    def activate(self, record: ResumeVersionRecord) -> ResumeVersionRecord:
        if not record.approved:
            raise ValueError("only an explicitly approved resume can become active")
        current = self.session.scalar(
            select(ResumeVersionRecord).where(ResumeVersionRecord.active.is_(True))
        )
        if current and current.id != record.id:
            current.active = False
            metadata = dict(current.metadata_data)
            metadata["deactivated_at"] = datetime.now(timezone.utc).isoformat()
            metadata["replaced_by"] = record.id
            current.metadata_data = metadata
        self.session.execute(
            update(ResumeVersionRecord)
            .where(ResumeVersionRecord.id != record.id)
            .values(active=False)
        )
        record.active = True
        return record

    def delete(self, record: ResumeVersionRecord) -> None:
        if record.active:
            raise ValueError("active resume cannot be deleted")
        self.session.delete(record)
