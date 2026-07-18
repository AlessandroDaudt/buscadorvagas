"""Idempotent import of the original JSON state into the relational store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunt.persistence.models import (
    JobAnalysisRecord,
    JobRecord,
    JobSnapshotRecord,
    JobSourceRecord,
)
from job_hunt.persistence.repositories import CompanyRepository

MAX_LEGACY_FILE_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class LegacyImportReport:
    files_read: int = 0
    jobs_created: int = 0
    jobs_existing: int = 0
    jobs_skipped: int = 0


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.stat().st_size > MAX_LEGACY_FILE_BYTES:
        raise ValueError(f"Legacy state file exceeds size limit: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Legacy state file must contain a JSON list: {path}")
    return [item for item in payload if isinstance(item, dict)]


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scan_datetime(value: object) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def import_legacy_state(session: Session, state_dir: Path = Path("state")) -> LegacyImportReport:
    paths = [state_dir / "job_history.json", state_dir / "last_scan.json"]
    files_read = sum(path.exists() for path in paths)
    candidates: dict[str, dict[str, Any]] = {}
    for path in paths:
        for item in _read_jobs(path):
            url = str(item.get("url", "")).strip()
            if url.startswith(("https://", "http://")):
                candidates[url] = item

    companies = CompanyRepository(session)
    created = existing = skipped = 0
    for canonical_url, item in candidates.items():
        company_name = str(item.get("company", "")).strip()
        title = str(item.get("extracted_title") or item.get("title") or "").strip()
        if not company_name or not title:
            skipped += 1
            continue

        job = session.scalar(select(JobRecord).where(JobRecord.canonical_url == canonical_url))
        if job is not None:
            existing += 1
            continue

        company = companies.get_or_create(company_name)
        seen_at = _scan_datetime(item.get("scan_date"))
        description = str(item.get("content", ""))
        description_hash = _hash_text(description) if description else None
        job = JobRecord(
            company_id=company.id,
            title=title,
            normalized_title=_normalized_text(title),
            canonical_url=canonical_url,
            description_hash=description_hash,
            location=str(item.get("location_remote") or item.get("location") or "") or None,
            modality="unknown",
            country=None,
            seniority=None,
            contract_type="unknown",
            status="active",
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            times_seen=1,
        )
        session.add(job)
        session.flush()
        session.add(
            JobSourceRecord(
                job_id=job.id,
                source_name="legacy_json",
                source_url=canonical_url,
                external_id=_hash_text(canonical_url),
                apply_url=canonical_url,
                collection_status="collected",
                raw_data=item,
            )
        )
        if description and description_hash:
            session.add(
                JobSnapshotRecord(
                    job_id=job.id,
                    collected_at=seen_at,
                    content_hash=description_hash,
                    description=description,
                    snapshot_data=item,
                    change_summary={"imported_from": "legacy_json"},
                )
            )
        score = item.get("score")
        if isinstance(score, (int, float)) and 0 <= float(score) <= 100:
            analysis_payload = {
                "reason": str(item.get("reason", "")),
                "stack": str(item.get("stack", "")),
                "legacy": True,
            }
            session.add(
                JobAnalysisRecord(
                    job_id=job.id,
                    score_total=float(score),
                    score_data={"total": float(score)},
                    explanation_data=analysis_payload,
                    provider="legacy",
                    model=None,
                    prompt_version=None,
                    cache_key=_hash_text(
                        json.dumps(
                            {"url": canonical_url, "score": score, "reason": item.get("reason")},
                            sort_keys=True,
                        )
                    ),
                )
            )
        created += 1

    return LegacyImportReport(
        files_read=files_read,
        jobs_created=created,
        jobs_existing=existing,
        jobs_skipped=skipped,
    )
