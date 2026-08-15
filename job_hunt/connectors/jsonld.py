from __future__ import annotations

import json
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from pydantic import HttpUrl

from job_hunt.connectors.base import (
    CollectionResult,
    CompanyConfig,
    ConnectorContext,
    SourceIssue,
    TextHttpClient,
)
from job_hunt.domain.models import CollectionStatus, ContractType, UnifiedJob
from job_hunt.http_client import BlockedByRobotsError, RobotsPolicy
from job_hunt.normalization import detect_work_mode, strip_html


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_jsonld = False
        self.parts: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): (value or "") for key, value in attrs}
        if tag.casefold() == "script" and values.get("type", "").casefold() == "application/ld+json":
            self.in_jsonld = True
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.in_jsonld:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self.in_jsonld:
            self.blocks.append("".join(self.parts))
            self.in_jsonld = False


def _objects(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _objects(value["@graph"])


def _location(value: Any) -> str:
    entries = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address", entry)
        if isinstance(address, dict):
            text = ", ".join(
                str(address.get(key))
                for key in ("addressLocality", "addressRegion", "addressCountry")
                if address.get(key)
            )
            if text:
                parts.append(text)
    return " / ".join(dict.fromkeys(parts))


def parse_job_postings(html: str, *, company_fallback: str, source_url: str, context: ConnectorContext) -> list[UnifiedJob]:
    parser = _JsonLdParser()
    parser.feed(html)
    parser.close()
    jobs: list[UnifiedJob] = []
    for block in parser.blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        for raw in _objects(payload):
            raw_type = raw.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            if "JobPosting" not in types:
                continue
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or source_url).strip()
            if not title or not url.startswith("https://"):
                continue
            organization = raw.get("hiringOrganization")
            company = str(organization.get("name") or company_fallback) if isinstance(organization, dict) else company_fallback
            description = strip_html(str(raw.get("description") or ""))
            location = _location(raw.get("jobLocation"))
            if raw.get("jobLocationType") == "TELECOMMUTE":
                location = f"Remote{f' - {location}' if location else ''}"
            published = None
            if raw.get("datePosted"):
                try:
                    published = datetime.fromisoformat(str(raw["datePosted"]).replace("Z", "+00:00"))
                except ValueError:
                    pass
            jobs.append(UnifiedJob(
                source_name="jsonld", original_url=HttpUrl(url), company=company,
                title=title, description=description, location=location or None,
                work_mode=detect_work_mode(title, location, description[:5000]),
                published_at=published, collected_at=context.collected_at,
                external_id=str(raw.get("identifier") or url)[:500],
                salary_text=str(raw.get("baseSalary"))[:1000] if raw.get("baseSalary") else None,
                contract_type=ContractType.UNKNOWN, apply_url=HttpUrl(url),
                collection_status=CollectionStatus.COLLECTED,
            ))
    return jobs


class JsonLdConnector:
    source_name = "jsonld"

    def __init__(self, company: CompanyConfig, client: TextHttpClient, robots: RobotsPolicy) -> None:
        self.company, self.client, self.robots = company, client, robots

    @property
    def allowed_hosts(self) -> set[str]:
        host = urlsplit(self.company["careers_url"]).hostname or ""
        return {host, *self.company.get("allowed_domains", [])}

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        try:
            self.robots.require_allowed(self.company["careers_url"], allowed_hosts=self.allowed_hosts)
            html = self.client.get_text(self.company["careers_url"], allowed_hosts=self.allowed_hosts)
            result.jobs = parse_job_postings(html, company_fallback=self.company["name"], source_url=self.company["careers_url"], context=context)
            if not result.jobs:
                result.warnings.append(SourceIssue("unsupported_source", "No JobPosting JSON-LD found"))
        except BlockedByRobotsError as exc:
            result.status = "failed"
            result.errors.append(SourceIssue("blocked_by_robots", str(exc)))
        except Exception as exc:
            result.status = "failed"
            result.errors.append(SourceIssue(getattr(exc, "code", "temporarily_unavailable"), str(exc), True))
        result.duration_seconds = time.monotonic() - started
        return result
