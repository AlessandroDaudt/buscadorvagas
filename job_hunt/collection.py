"""Application service for running connectors and persisting normalized jobs."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from job_hunt.connectors.base import CollectionResult, ConnectorContext, JobConnector
from job_hunt.domain.models import UnifiedJob
from job_hunt.log import log_context
from job_hunt.metrics import metrics
from job_hunt.persistence.database import Database
from job_hunt.persistence.job_ingestion import JobIngestionService


@dataclass
class CollectionReport:
    sources_consulted: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    jobs_collected: int = 0
    decisions: Counter[str] = field(default_factory=Counter)
    results: list[CollectionResult] = field(default_factory=list)


@dataclass(frozen=True)
class ConnectorRetryPolicy:
    attempts: int = 3
    initial_backoff_seconds: float = 1
    maximum_backoff_seconds: float = 30


def _collect_with_retry(
    connector: JobConnector,
    context: ConnectorContext,
    policy: ConnectorRetryPolicy,
    sleep: Callable[[float], None],
) -> CollectionResult:
    result = connector.collect(context)
    for retry_index in range(1, policy.attempts):
        if result.status != "failed":
            break
        if result.errors and not any(issue.retryable for issue in result.errors):
            break
        delay = min(
            policy.maximum_backoff_seconds,
            policy.initial_backoff_seconds * (2 ** (retry_index - 1)),
        )
        sleep(delay)
        result = connector.collect(context)
    return result


def collect_and_persist(
    connectors: list[JobConnector],
    database: Database,
    context: ConnectorContext | None = None,
    *,
    retry_policy: ConnectorRetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectionReport:
    run_context = context or ConnectorContext()
    report = CollectionReport()
    with database.session() as session:
        ingestion = JobIngestionService(session)
        for connector in connectors:
            report.sources_consulted.append(connector.source_name)
            with log_context(source_id=connector.source_name):
                result = _collect_with_retry(
                    connector,
                    run_context,
                    retry_policy or ConnectorRetryPolicy(),
                    sleep,
                )
            metrics.increment("sources_consulted_total")
            metrics.observe(f"connector_duration.{connector.source_name}", result.duration_seconds)
            report.results.append(result)
            if result.status == "failed":
                metrics.increment(f"source_errors.{connector.source_name}")
                report.sources_failed.append(connector.source_name)
                continue
            report.jobs_collected += len(result.jobs)
            metrics.increment("jobs_collected_total", len(result.jobs))
            for job in result.jobs:
                outcome = ingestion.ingest(job)
                report.decisions[outcome.decision.value] += 1
    return report


def persist_unified_jobs(jobs: list[UnifiedJob], database: Database) -> Counter[str]:
    decisions: Counter[str] = Counter()
    with database.session() as session:
        ingestion = JobIngestionService(session)
        for job in jobs:
            result = ingestion.ingest(job)
            decisions[result.decision.value] += 1
    return decisions
