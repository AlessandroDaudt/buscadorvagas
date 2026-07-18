"""Application service for running connectors and persisting normalized jobs."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from job_hunt.connectors.base import CollectionResult, ConnectorContext, JobConnector
from job_hunt.domain.models import UnifiedJob
from job_hunt.persistence.database import Database
from job_hunt.persistence.job_ingestion import JobIngestionService


@dataclass
class CollectionReport:
    sources_consulted: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    jobs_collected: int = 0
    decisions: Counter[str] = field(default_factory=Counter)
    results: list[CollectionResult] = field(default_factory=list)


def collect_and_persist(
    connectors: list[JobConnector],
    database: Database,
    context: ConnectorContext | None = None,
) -> CollectionReport:
    run_context = context or ConnectorContext()
    report = CollectionReport()
    with database.session() as session:
        ingestion = JobIngestionService(session)
        for connector in connectors:
            report.sources_consulted.append(connector.source_name)
            result = connector.collect(run_context)
            report.results.append(result)
            if result.status == "failed":
                report.sources_failed.append(connector.source_name)
                continue
            report.jobs_collected += len(result.jobs)
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
