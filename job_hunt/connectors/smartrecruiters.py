from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from pydantic import HttpUrl

from job_hunt.connectors.base import CollectionResult, ConnectorContext, JsonHttpClient, SourceIssue
from job_hunt.domain.models import CollectionStatus, ContractType, UnifiedJob
from job_hunt.normalization import detect_work_mode, strip_html

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_HOST = "api.smartrecruiters.com"


class SmartRecruitersConnector:
    source_name = "smartrecruiters"

    def __init__(self, company_id: str, company: str, client: JsonHttpClient) -> None:
        if not _ID_RE.fullmatch(company_id):
            raise ValueError("Invalid SmartRecruiters company identifier")
        self.company_id = company_id
        self.company = company
        self.client = client

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        try:
            payload = self.client.get_json(
                f"https://{_HOST}/v1/companies/{self.company_id}/postings?limit=100",
                allowed_hosts={_HOST},
            )
            jobs = payload.get("content", []) if isinstance(payload, dict) else []
            if not isinstance(jobs, list):
                raise ValueError("SmartRecruiters response field 'content' must be a list")
            for raw in jobs:
                if not isinstance(raw, dict) or not raw.get("id"):
                    continue
                details = self.client.get_json(
                    f"https://{_HOST}/v1/companies/{self.company_id}/postings/{raw['id']}",
                    allowed_hosts={_HOST},
                )
                parsed = self._parse(details if isinstance(details, dict) else raw, context)
                if parsed:
                    result.jobs.append(parsed)
        except Exception as exc:
            result.status = "failed"
            result.errors.append(SourceIssue("request_failed", str(exc), retryable=True))
        result.duration_seconds = time.monotonic() - started
        return result

    def _parse(self, raw: dict[str, Any], context: ConnectorContext) -> UnifiedJob | None:
        job_id = str(raw.get("id") or "").strip()
        title = str(raw.get("name") or "").strip()
        url = str(raw.get("ref") or f"https://jobs.smartrecruiters.com/{self.company_id}/{job_id}")
        if not job_id or not title:
            return None
        raw_location = raw.get("location")
        location_data: dict[str, Any] = raw_location if isinstance(raw_location, dict) else {}
        location = ", ".join(
            str(location_data.get(key))
            for key in ("city", "region", "country")
            if location_data.get(key)
        )
        job_ad = raw.get("jobAd")
        sections = job_ad.get("sections", {}) if isinstance(job_ad, dict) else {}
        description = "\n".join(
            strip_html(str(value.get("text", "")))
            for value in sections.values()
            if isinstance(value, dict)
        )
        published = None
        if raw.get("releasedDate"):
            try:
                published = datetime.fromisoformat(str(raw["releasedDate"]).replace("Z", "+00:00"))
            except ValueError:
                pass
        return UnifiedJob(
            source_name=self.source_name,
            original_url=HttpUrl(url), company=self.company, title=title,
            description=description, location=location or None,
            work_mode=detect_work_mode(title, location, description[:5000]),
            published_at=published, collected_at=context.collected_at, external_id=job_id,
            contract_type=ContractType.UNKNOWN, apply_url=HttpUrl(url),
            collection_status=CollectionStatus.COLLECTED,
        )
