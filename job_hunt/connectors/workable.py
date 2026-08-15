from __future__ import annotations

import re
import time
from typing import Any

from pydantic import HttpUrl

from job_hunt.connectors.base import CollectionResult, ConnectorContext, JsonHttpClient, SourceIssue
from job_hunt.domain.models import CollectionStatus, ContractType, UnifiedJob
from job_hunt.normalization import detect_work_mode, strip_html

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_HOST = "apply.workable.com"


class WorkableConnector:
    source_name = "workable"

    def __init__(self, account: str, company: str, client: JsonHttpClient) -> None:
        if not _ACCOUNT_RE.fullmatch(account):
            raise ValueError("Invalid Workable account token")
        self.account = account
        self.company = company
        self.client = client

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        try:
            payload = self.client.get_json(
                f"https://{_HOST}/api/v3/accounts/{self.account}/jobs",
                allowed_hosts={_HOST},
            )
            jobs = payload.get("results", payload.get("jobs", [])) if isinstance(payload, dict) else []
            if not isinstance(jobs, list):
                raise ValueError("Workable response jobs must be a list")
            for raw in jobs:
                if isinstance(raw, dict):
                    parsed = self._parse(raw, context)
                    if parsed:
                        result.jobs.append(parsed)
        except Exception as exc:
            result.status = "failed"
            result.errors.append(SourceIssue("request_failed", str(exc), retryable=True))
        result.duration_seconds = time.monotonic() - started
        return result

    def _parse(self, raw: dict[str, Any], context: ConnectorContext) -> UnifiedJob | None:
        short = str(raw.get("shortcode") or raw.get("id") or "").strip()
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or f"https://apply.workable.com/{self.account}/j/{short}/")
        if not short or not title:
            return None
        location_data = raw.get("location")
        if isinstance(location_data, dict):
            location = str(location_data.get("location_str") or location_data.get("city") or "")
        else:
            location = str(location_data or "")
        description = strip_html(str(raw.get("description") or raw.get("description_html") or ""))
        return UnifiedJob(
            source_name=self.source_name, original_url=HttpUrl(url), company=self.company,
            title=title, description=description, location=location or None,
            work_mode=detect_work_mode(title, location, description[:5000]),
            collected_at=context.collected_at, external_id=short,
            contract_type=ContractType.UNKNOWN, apply_url=HttpUrl(url),
            collection_status=CollectionStatus.COLLECTED,
        )
