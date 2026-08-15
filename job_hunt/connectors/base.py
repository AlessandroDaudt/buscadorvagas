from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict
from uuid import UUID, uuid4

from job_hunt.domain.models import UnifiedJob


@dataclass(frozen=True)
class ConnectorContext:
    run_id: UUID = field(default_factory=uuid4)
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SourceIssue:
    code: str
    message: str
    retryable: bool = False


@dataclass
class CollectionResult:
    source_name: str
    jobs: list[UnifiedJob] = field(default_factory=list)
    errors: list[SourceIssue] = field(default_factory=list)
    warnings: list[SourceIssue] = field(default_factory=list)
    duration_seconds: float = 0.0
    status: str = "success"


class JobConnector(Protocol):
    source_name: str

    def collect(self, context: ConnectorContext) -> CollectionResult: ...


class CompanyConfig(TypedDict, total=False):
    name: str
    careers_url: str
    search_domain: str
    location: str
    region: str
    connector: str
    enabled: bool
    allowed_domains: list[str]
    board_token: str
    site: str
    account: str
    company_id: str


class JsonHttpClient(Protocol):
    def get_json(self, url: str, *, allowed_hosts: set[str] | None = None) -> Any: ...


class TextHttpClient(JsonHttpClient, Protocol):
    def get_text(self, url: str, *, allowed_hosts: set[str]) -> str: ...
