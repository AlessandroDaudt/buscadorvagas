from __future__ import annotations

import re
import time
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

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_HOST = "boards-api.greenhouse.io"


class GreenhouseConnector:
    source_name = "greenhouse"

    def __init__(self, board_token: str, company: str, client: JsonHttpClient) -> None:
        if not _TOKEN_RE.fullmatch(board_token):
            raise ValueError("Invalid Greenhouse board token")
        self.board_token = board_token
        self.company = company
        self.client = client

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        url = f"https://{_HOST}/v1/boards/{self.board_token}/jobs?content=true"
        try:
            payload = self.client.get_json(url, allowed_hosts={_HOST})
            jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            if not isinstance(jobs, list):
                raise ValueError("Greenhouse response field 'jobs' must be a list")
            for raw in jobs:
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
        job_id = raw.get("id")
        title = str(raw.get("title", "")).strip()
        absolute_url = str(raw.get("absolute_url", "")).strip()
        if job_id is None or not title or not absolute_url:
            return None
        location_data = raw.get("location")
        location = (
            str(location_data.get("name", "")).strip()
            if isinstance(location_data, dict)
            else ""
        )
        description = strip_html(str(raw.get("content", "")))[:500_000]
        return UnifiedJob(
            source_name=self.source_name,
            original_url=HttpUrl(absolute_url),
            company=self.company,
            title=title,
            description=description,
            location=location or None,
            work_mode=detect_work_mode(title, location, description[:5_000]),
            collected_at=context.collected_at,
            external_id=str(job_id),
            seniority=None,
            contract_type=ContractType.UNKNOWN,
            apply_url=HttpUrl(absolute_url),
            collection_status=CollectionStatus.COLLECTED,
        )
