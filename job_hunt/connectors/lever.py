from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import HttpUrl

from job_hunt.connectors.base import (
    CollectionResult,
    ConnectorContext,
    JsonHttpClient,
    SourceIssue,
)
from job_hunt.domain.models import CollectionStatus, ContractType, UnifiedJob
from job_hunt.normalization import detect_work_mode, strip_html

_SITE_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_GLOBAL_HOST = "api.lever.co"
_EU_HOST = "api.eu.lever.co"


class LeverConnector:
    source_name = "lever"

    def __init__(
        self,
        site: str,
        company: str,
        client: JsonHttpClient,
        *,
        eu_instance: bool = False,
    ) -> None:
        if not _SITE_RE.fullmatch(site):
            raise ValueError("Invalid Lever site token")
        self.site = site
        self.company = company
        self.client = client
        self.host = _EU_HOST if eu_instance else _GLOBAL_HOST

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        url = f"https://{self.host}/v0/postings/{self.site}?mode=json"
        try:
            payload = self.client.get_json(url, allowed_hosts={self.host})
            if not isinstance(payload, list):
                raise ValueError("Lever response must be a list")
            for raw in payload:
                if not isinstance(raw, dict):
                    result.warnings.append(SourceIssue("invalid_item", "Ignored non-object job"))
                    continue
                parsed = self._parse_job(raw, context)
                if parsed is not None:
                    result.jobs.append(parsed)
        except Exception as exc:
            result.status = "failed"
            result.errors.append(SourceIssue("request_failed", str(exc), retryable=True))
        result.duration_seconds = time.monotonic() - started
        return result

    def _parse_job(self, raw: dict[str, Any], context: ConnectorContext) -> UnifiedJob | None:
        job_id = str(raw.get("id", "")).strip()
        title = str(raw.get("text", "")).strip()
        hosted_url = str(raw.get("hostedUrl") or raw.get("applyUrl") or "").strip()
        if not job_id or not title or not hosted_url:
            return None
        raw_categories = raw.get("categories")
        categories: dict[str, Any] = raw_categories if isinstance(raw_categories, dict) else {}
        location = str(categories.get("location", "")).strip()
        commitment = str(categories.get("commitment", "")).casefold()
        description = str(raw.get("descriptionPlain") or "").strip()
        if not description:
            description = strip_html(str(raw.get("description", "")))
        additional = raw.get("lists")
        if isinstance(additional, list):
            parts = [description]
            for section in additional:
                if isinstance(section, dict):
                    parts.append(str(section.get("text", "")))
                    parts.append(strip_html(str(section.get("content", ""))))
            description = "\n".join(part for part in parts if part).strip()

        contract = ContractType.UNKNOWN
        if "full" in commitment:
            contract = ContractType.FULL_TIME
        elif "part" in commitment:
            contract = ContractType.PART_TIME
        elif "contract" in commitment:
            contract = ContractType.CONTRACTOR
        created_at = None
        created_millis = raw.get("createdAt")
        if isinstance(created_millis, (int, float)):
            created_at = datetime.fromtimestamp(created_millis / 1000, tz=timezone.utc)
        apply_url = str(raw.get("applyUrl") or hosted_url)
        return UnifiedJob(
            source_name=self.source_name,
            original_url=HttpUrl(hosted_url),
            company=self.company,
            title=title,
            description=description[:500_000],
            location=location or None,
            work_mode=detect_work_mode(title, location, description[:5_000]),
            published_at=created_at,
            collected_at=context.collected_at,
            external_id=job_id,
            seniority=None,
            contract_type=contract,
            apply_url=HttpUrl(apply_url),
            collection_status=CollectionStatus.COLLECTED,
        )
