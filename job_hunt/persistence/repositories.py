"""Small repositories used by the application layer."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunt.domain.models import CandidateProfile, MasterResume
from job_hunt.persistence.models import (
    CandidateProfileRecord,
    CompanyRecord,
    ResumeMasterRecord,
)


def normalize_company_name(name: str) -> str:
    return " ".join(name.casefold().split())


def canonical_json_hash(data: dict) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CandidateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_profile(self, profile: CandidateProfile) -> CandidateProfileRecord:
        current = self.session.scalar(
            select(CandidateProfileRecord).where(CandidateProfileRecord.active.is_(True))
        )
        data = profile.model_dump(mode="json")
        if current is None:
            current = CandidateProfileRecord(
                name=profile.identity.name,
                schema_version=profile.schema_version,
                profile_data=data,
                active=True,
            )
            self.session.add(current)
        else:
            current.name = profile.identity.name
            current.schema_version = profile.schema_version
            current.profile_data = data
        self.session.flush()
        return current

    def save_resume(
        self, candidate: CandidateProfileRecord, resume: MasterResume
    ) -> ResumeMasterRecord:
        data = resume.model_dump(mode="json")
        content_hash = canonical_json_hash(data)
        existing = self.session.scalar(
            select(ResumeMasterRecord).where(
                ResumeMasterRecord.candidate_profile_id == candidate.id,
                ResumeMasterRecord.version == resume.version,
            )
        )
        if existing is None:
            existing = ResumeMasterRecord(
                candidate_profile_id=candidate.id,
                version=resume.version,
                language=resume.language,
                content_data=data,
                content_hash=content_hash,
                approved=resume.approved,
            )
            self.session.add(existing)
        else:
            existing.language = resume.language
            existing.content_data = data
            existing.content_hash = content_hash
            existing.approved = resume.approved
        self.session.flush()
        return existing


class CompanyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(self, name: str, *, priority: bool = False) -> CompanyRecord:
        normalized = normalize_company_name(name)
        company = self.session.scalar(
            select(CompanyRecord).where(CompanyRecord.normalized_name == normalized)
        )
        if company is None:
            company = CompanyRecord(
                display_name=name,
                normalized_name=normalized,
                priority=priority,
            )
            self.session.add(company)
            self.session.flush()
        elif priority and not company.priority:
            company.priority = True
        return company

