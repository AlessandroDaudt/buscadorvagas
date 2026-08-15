from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from pydantic import HttpUrl

from job_hunt.connectors.base import CollectionResult, ConnectorContext, JsonHttpClient, SourceIssue
from job_hunt.domain.models import CollectionStatus, ContractType, UnifiedJob
from job_hunt.normalization import detect_work_mode, strip_html

_BOARD_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_HOST = "api.ashbyhq.com"


class AshbyConnector:
    source_name = "ashby"

    def __init__(self, board: str, company: str, client: JsonHttpClient) -> None:
        if not _BOARD_RE.fullmatch(board):
            raise ValueError("Invalid Ashby board token")
        self.board = board
        self.company = company
        self.client = client

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        try:
            payload = self.client.get_json(
                f"https://{_HOST}/posting-api/job-board/{self.board}", allowed_hosts={_HOST}
            )
            jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            if not isinstance(jobs, list):
                raise ValueError("Ashby response field 'jobs' must be a list")
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
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("jobUrl") or raw.get("applyUrl") or "").strip()
        if not title or not url:
            return None
        location = str(raw.get("location") or "").strip()
        description = strip_html(str(raw.get("descriptionHtml") or raw.get("descriptionPlain") or ""))
        published = None
        if raw.get("publishedAt"):
            try:
                published = datetime.fromisoformat(str(raw["publishedAt"]).replace("Z", "+00:00"))
            except ValueError:
                pass
        return UnifiedJob(
            source_name=self.source_name,
            original_url=HttpUrl(url),
            company=self.company,
            title=title,
            description=description,
            location=location or None,
            work_mode=detect_work_mode(title, location, description[:5000]),
            published_at=published,
            collected_at=context.collected_at,
            external_id=str(raw.get("id") or raw.get("jobUrl") or "")[:500] or None,
            contract_type=ContractType.UNKNOWN,
            apply_url=HttpUrl(str(raw.get("applyUrl") or url)),
            collection_status=CollectionStatus.COLLECTED,
        )
