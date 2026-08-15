from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from pydantic import HttpUrl

from job_hunt.connectors.base import (
    CollectionResult,
    CompanyConfig,
    ConnectorContext,
    SourceIssue,
    TextHttpClient,
)
from job_hunt.connectors.jsonld import parse_job_postings
from job_hunt.domain.models import CollectionStatus, ContractType, UnifiedJob
from job_hunt.http_client import BlockedByRobotsError, RobotsPolicy
from job_hunt.normalization import detect_work_mode, strip_html

_JOB_PATH = re.compile(r"/(job|jobs|opening|openings|position|positions|vacancy|vacancies|role|roles|apply)(/|\?|$)", re.I)
_CAPTCHA = ("captcha", "cf-chl-captcha", "g-recaptcha", "hcaptcha")
_AUTH = ("sign in to continue", "login required", "authentication required")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current: str | None = None
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "a":
            self.current = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self.current is not None:
            self.links.append((self.current, " ".join(self.text).strip()))
            self.current = None


class GenericHtmlConnector:
    source_name = "generic_html"

    def __init__(self, company: CompanyConfig, client: TextHttpClient, robots: RobotsPolicy) -> None:
        self.company, self.client, self.robots = company, client, robots

    @property
    def allowed_hosts(self) -> set[str]:
        host = urlsplit(self.company["careers_url"]).hostname or ""
        return {host, *self.company.get("allowed_domains", [])}

    def collect(self, context: ConnectorContext) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(source_name=self.source_name)
        url = self.company["careers_url"]
        try:
            self.robots.require_allowed(url, allowed_hosts=self.allowed_hosts)
            html = self.client.get_text(url, allowed_hosts=self.allowed_hosts)
            lower = html.casefold()
            if any(marker in lower for marker in _CAPTCHA):
                result.status = "failed"
                result.errors.append(SourceIssue("captcha_detected", "CAPTCHA detected; source skipped"))
                return result
            if any(marker in lower for marker in _AUTH):
                result.status = "failed"
                result.errors.append(SourceIssue("authentication_required", "Login required; source skipped"))
                return result
            result.jobs.extend(parse_job_postings(html, company_fallback=self.company["name"], source_url=url, context=context))
            parser = _LinkParser()
            parser.feed(html)
            parser.close()
            existing = {str(job.original_url) for job in result.jobs}
            for href, label in parser.links:
                absolute = urljoin(url, href)
                parsed = urlsplit(absolute)
                host = (parsed.hostname or "").casefold()
                if parsed.scheme != "https" or host not in {item.casefold() for item in self.allowed_hosts}:
                    continue
                if absolute in existing or not _JOB_PATH.search(parsed.path):
                    continue
                title = strip_html(label)[:500]
                if len(title) < 3:
                    continue
                result.jobs.append(UnifiedJob(
                    source_name=self.source_name, original_url=HttpUrl(absolute), company=self.company["name"],
                    title=title, description="", location=self.company.get("location"),
                    work_mode=detect_work_mode(title, self.company.get("location")),
                    collected_at=context.collected_at, external_id=absolute[-500:],
                    contract_type=ContractType.UNKNOWN, apply_url=HttpUrl(absolute),
                    collection_status=CollectionStatus.COLLECTED,
                ))
                existing.add(absolute)
            if not result.jobs:
                result.warnings.append(SourceIssue("unsupported_source", "No public static job data found"))
        except BlockedByRobotsError as exc:
            result.status = "failed"
            result.errors.append(SourceIssue("blocked_by_robots", str(exc)))
        except Exception as exc:
            result.status = "failed"
            result.errors.append(
                SourceIssue(getattr(exc, "code", "temporarily_unavailable"), str(exc), True)
            )
        finally:
            result.duration_seconds = time.monotonic() - started
        return result
