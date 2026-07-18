"""Compatibility adapter for the original TinyFish discovery implementation."""

from __future__ import annotations

import time
from typing import Any

from pydantic import HttpUrl

from job_hunt.connectors.base import CollectionResult, ConnectorContext, SourceIssue
from job_hunt.domain.models import CollectionStatus, ContractType, UnifiedJob
from job_hunt.normalization import detect_work_mode
from job_hunt.scanner import discover_job_urls, fetch_job_details


class TinyFishConnector:
    source_name = "tinyfish"

    def __init__(
        self,
        client: Any,
        company: dict,
        candidate: dict | None = None,
        seen_urls: set[str] | None = None,
    ) -> None:
        self.client = client
        self.company = company
        self.candidate = candidate or {}
        self.seen_urls = seen_urls or set()

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        try:
            discovered = discover_job_urls(
                self.client,
                self.company,
                self.seen_urls,
                self.candidate,
            )
            for raw in fetch_job_details(self.client, discovered):
                url = str(raw.get("url", "")).strip()
                title = str(raw.get("title", "")).strip()
                if not url or not title:
                    result.warnings.append(SourceIssue("invalid_item", "Ignored incomplete job"))
                    continue
                description = str(raw.get("content", ""))
                location = str(raw.get("location", "")).strip()
                result.jobs.append(
                    UnifiedJob(
                        source_name=self.source_name,
                        original_url=HttpUrl(url),
                        company=str(raw.get("company") or self.company.get("name") or ""),
                        title=title,
                        description=description,
                        location=location or None,
                        work_mode=detect_work_mode(title, location, description[:5_000]),
                        collected_at=context.collected_at,
                        external_id=None,
                        contract_type=ContractType.UNKNOWN,
                        apply_url=HttpUrl(url),
                        collection_status=CollectionStatus.COLLECTED,
                    )
                )
        except Exception as exc:
            result.status = "failed"
            result.errors.append(SourceIssue("collection_failed", str(exc), retryable=True))
        result.duration_seconds = time.monotonic() - started
        return result
