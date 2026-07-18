"""Persist generated-document metadata without storing secrets or raw prompts."""

from pathlib import Path

from sqlalchemy.orm import Session

from job_hunt.documents.generator import GeneratedPackage
from job_hunt.persistence.models import GeneratedDocumentRecord


class GeneratedDocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_package(
        self,
        *,
        job_id: str,
        resume_master_id: str | None,
        package: GeneratedPackage,
    ) -> list[GeneratedDocumentRecord]:
        records: list[GeneratedDocumentRecord] = []
        candidates = [
            ("resume", package.resume_markdown),
            ("cover_letter", package.cover_letter_markdown),
            ("resume", package.resume_docx),
            ("cover_letter", package.cover_letter_docx),
            ("resume", package.resume_pdf),
            ("cover_letter", package.cover_letter_pdf),
        ]
        for document_type, path in candidates:
            if path is None:
                continue
            record = GeneratedDocumentRecord(
                job_id=job_id,
                resume_master_id=resume_master_id,
                document_type=document_type,
                language=package.manifest.language,
                file_format=Path(path).suffix.lstrip("."),
                storage_path=str(path),
                version=package.manifest.version,
                model=package.manifest.model_used,
                prompt_version=package.manifest.prompt_version,
                master_hash=package.manifest.master_hash,
                diff_data=package.manifest.changes,
            )
            self.session.add(record)
            records.append(record)
        self.session.flush()
        return records
