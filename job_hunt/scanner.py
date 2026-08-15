"""Local job scan orchestration: connectors, scoring, state and local reports."""

from __future__ import annotations

import csv
import html
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.analysis.service import JobAnalyzer
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.connectors.base import ConnectorContext
from job_hunt.connectors.registry import build_connector, normalize_company
from job_hunt.domain.models import UnifiedJob
from job_hunt.http_client import RobotsPolicy, SafeHttpClient
from job_hunt.llm.config import LLMSettings
from job_hunt.local_config import validate_local_config
from job_hunt.log import get_logger
from job_hunt.normalization import canonicalize_url, description_hash, normalize_match_text
from job_hunt.notifier import send_windows_notification
from job_hunt.ollama import OllamaClient, OllamaSettings, OllamaUnavailableError
from job_hunt.reports import SearchRunReport
from job_hunt.state_store import SCHEMA_VERSION, atomic_write_json, load_json_state

logger = get_logger("autopilot.scanner")

STATE_FILE = Path("state/seen_jobs.json")
LAST_SCAN_FILE = Path("state/last_scan.json")
JOB_HISTORY_FILE = Path("state/job_history.json")
LAST_RUN_REPORT_FILE = Path("state/last_run_report.json")
OUTPUT_DIR = Path("output")

EXPORT_FIELDS = [
    "Company", "Role", "Location", "Application URL", "Score (%)",
    "Deterministic Score (%)", "Stack", "Region", "Reason", "Worth Applying",
    "Lifecycle", "Scan Date",
]


def load_state() -> dict[str, Any]:
    raw = load_json_state(STATE_FILE, {"schema_version": SCHEMA_VERSION, "seen_urls": []})
    if not isinstance(raw, dict):
        return {"schema_version": SCHEMA_VERSION, "seen_urls": []}
    raw.setdefault("schema_version", SCHEMA_VERSION)
    raw.setdefault("seen_urls", [])
    return raw


def save_state(state: dict[str, Any]) -> None:
    state["schema_version"] = SCHEMA_VERSION
    atomic_write_json(STATE_FILE, state)


def _semantic_key(job: UnifiedJob | dict[str, Any]) -> str:
    if isinstance(job, UnifiedJob):
        values = (job.company, job.title, job.location or "")
    else:
        values = (
            str(job.get("company", "")),
            str(job.get("extracted_title") or job.get("title", "")),
            str(job.get("location", "")),
        )
    return "|".join(normalize_match_text(value) for value in values)


def deduplicate_jobs(jobs: list[UnifiedJob]) -> tuple[list[UnifiedJob], int]:
    by_url: dict[str, UnifiedJob] = {}
    semantic: dict[str, str] = {}
    removed = 0
    for job in jobs:
        canonical = canonicalize_url(str(job.original_url))
        key = _semantic_key(job)
        existing_url = semantic.get(key)
        if canonical in by_url:
            removed += 1
            if len(job.description) > len(by_url[canonical].description):
                by_url[canonical] = job
            continue
        if existing_url and existing_url in by_url:
            removed += 1
            if len(job.description) > len(by_url[existing_url].description):
                by_url.pop(existing_url)
                by_url[canonical] = job
                semantic[key] = canonical
            continue
        by_url[canonical] = job
        semantic[key] = canonical
    return list(by_url.values()), removed


def _ollama_available(config: dict[str, Any]) -> tuple[bool, str | None]:
    settings = OllamaSettings.from_config(config)
    try:
        with OllamaClient(settings) as client:
            models = client.list_models()
    except OllamaUnavailableError:
        return False, "Ollama unavailable; deterministic scoring remains active"
    if settings.chat_model not in models and f"{settings.chat_model}:latest" not in models:
        return False, f"Ollama chat model is not installed: {settings.chat_model}"
    return True, None


def score_jobs(jobs: list[UnifiedJob], config: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_path = config.get("candidate_profile_path")
    preferences_path = config.get("search_preferences_path")
    profile = load_candidate_profile(Path(candidate_path)) if candidate_path else load_candidate_profile()
    preferences = (
        load_search_preferences(Path(preferences_path))
        if preferences_path
        else load_search_preferences()
    )
    llm_settings = LLMSettings.from_application_config(config)
    if llm_settings.enabled:
        available, warning = _ollama_available(config)
        if not available:
            llm_settings.enabled = False
            logger.warning(warning)
    analyzer = JobAnalyzer(profile, preferences, llm_settings)
    deterministic_scorer = DeterministicScorer(preferences, profile)
    minimum_score = preferences.filters.minimum_score
    try:
        ai_review_limit = int(os.getenv("AUTOPILOT_AI_REVIEW_LIMIT", "50"))
    except ValueError:
        ai_review_limit = 50
    ai_review_limit = max(0, min(ai_review_limit, 500))
    prepared = []
    for job in jobs:
        deterministic = deterministic_scorer.score(job)
        prepared.append((job, deterministic, consolidate_analysis(deterministic)))
    review_candidates = [
        str(job.id)
        for job, deterministic, deterministic_result in sorted(
            prepared,
            key=lambda item: item[2].total_score,
            reverse=True,
        )
        if deterministic.filter_decision.eligible
        and deterministic_result.total_score >= minimum_score
    ]
    review_job_ids = set(review_candidates[:ai_review_limit]) if llm_settings.enabled else set()
    records: list[dict[str, Any]] = []
    for job, deterministic, deterministic_result in prepared:
        analysis = (
            analyzer.analyze(job) if str(job.id) in review_job_ids else deterministic_result
        )
        record = {
            "_schema_version": SCHEMA_VERSION,
            "job_id": str(job.id),
            "source_name": job.source_name,
            "url": canonicalize_url(str(job.original_url)),
            "apply_url": str(job.apply_url or job.original_url),
            "company": job.company,
            "title": job.title,
            "extracted_title": job.title,
            "content": job.description,
            "location": job.location or "",
            "location_remote": job.location or job.work_mode.value,
            "region": job.country or "",
            "score": round(analysis.total_score),
            "deterministic_score": round(deterministic_result.total_score),
            "matched_skills": deterministic.matched_terms,
            "missing_skills": deterministic.gaps,
            "disqualifiers": deterministic.filter_decision.exclusion_reasons,
            "location_match": not deterministic.filter_decision.geographic_restrictions,
            "seniority_match": deterministic.components.seniority >= 60,
            "stack": ", ".join(deterministic.matched_terms[:6]),
            "reason": analysis.explanation,
            "worth_applying": analysis.total_score >= minimum_score,
            "analysis": analysis.model_dump(mode="json"),
            "description_hash": description_hash(job.description),
            "published_at": job.published_at.isoformat() if job.published_at else None,
            "collected_at": job.collected_at.isoformat(),
        }
        records.append(record)
    return sorted(records, key=lambda item: item["score"], reverse=True)


def _apply_lifecycle(
    records: list[dict[str, Any]],
    history: list[dict[str, Any]],
    scanned_companies: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    previous_by_url = {
        canonicalize_url(str(item.get("url", ""))): item
        for item in history if isinstance(item, dict) and item.get("url")
    }
    previous_by_semantic = {_semantic_key(item): item for item in history if isinstance(item, dict)}
    current_urls: set[str] = set()
    decisions: Counter[str] = Counter()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for record in records:
        url = canonicalize_url(record["url"])
        current_urls.add(url)
        previous = previous_by_url.get(url)
        semantic_previous = previous_by_semantic.get(_semantic_key(record))
        if previous:
            lifecycle = "updated" if previous.get("description_hash") != record.get("description_hash") else "unchanged"
        elif semantic_previous:
            lifecycle = "republished"
        else:
            lifecycle = "new"
        record["lifecycle"] = lifecycle
        record["scan_date"] = today
        record["last_seen"] = today
        record["first_seen"] = (previous or semantic_previous or {}).get("first_seen", today)
        decisions[lifecycle] += 1
        previous_by_url[url] = record
        previous_by_semantic[_semantic_key(record)] = record

    for old in history:
        if not isinstance(old, dict) or not old.get("url"):
            continue
        if old.get("company") in scanned_companies and canonicalize_url(old["url"]) not in current_urls:
            old = dict(old)
            old["lifecycle"] = "removed"
            old["removed_at"] = today
            decisions["removed"] += 1
            previous_by_url[canonicalize_url(old["url"])] = old
    merged = list(previous_by_url.values())
    return records, merged, decisions


def _row(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "Company": job.get("company", ""), "Role": job.get("extracted_title", ""),
        "Location": job.get("location_remote", ""), "Application URL": job.get("url", ""),
        "Score (%)": job.get("score", ""), "Deterministic Score (%)": job.get("deterministic_score", ""),
        "Stack": job.get("stack", ""), "Region": job.get("region", ""),
        "Reason": job.get("reason", ""), "Worth Applying": "Yes" if job.get("worth_applying") else "No",
        "Lifecycle": job.get("lifecycle", ""), "Scan Date": job.get("scan_date", ""),
    }


def export_local_reports(jobs: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    csv_path = OUTPUT_DIR / f"jobs_{stamp}.csv"
    json_path = OUTPUT_DIR / f"jobs_{stamp}.json"
    html_path = OUTPUT_DIR / f"jobs_{stamp}.html"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(_row(job) for job in jobs)
    atomic_write_json(json_path, jobs, backup=False)
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(job.get('score', '')))}</td>"
        f"<td>{html.escape(str(job.get('company', '')))}</td>"
        f"<td>{html.escape(str(job.get('title', '')))}</td>"
        f"<td>{html.escape(str(job.get('location_remote', '')))}</td>"
        f"<td><a rel='noopener noreferrer' href='{html.escape(str(job.get('url', '')), quote=True)}'>view</a></td>"
        "</tr>" for job in jobs
    )
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Autopilot Job Hunt</title>"
        "<h1>Local job report</h1><p>No application is submitted automatically.</p>"
        "<table><thead><tr><th>Score</th><th>Company</th><th>Role</th><th>Location</th><th>URL</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>", encoding="utf-8"
    )
    return csv_path, json_path, html_path


def _initialize_database(config: dict[str, Any]):
    if not config.get("persistence", {}).get("enabled", False):
        return None
    try:
        from job_hunt.persistence.database import Database, get_database_url
        from job_hunt.persistence.migration import upgrade_database

        database_url = get_database_url()
        upgrade_database(database_url)
        return Database(database_url)
    except Exception as exc:
        logger.warning("Local database unavailable; JSON state remains active (%s)", type(exc).__name__)
        return None


def _persist_jobs(jobs: list[UnifiedJob], database) -> Counter[str]:
    from job_hunt.persistence.job_ingestion import JobIngestionService

    decisions: Counter[str] = Counter()
    try:
        batch_size = int(os.getenv("AUTOPILOT_DB_INGEST_BATCH_SIZE", "50"))
    except ValueError:
        batch_size = 50
    batch_size = max(10, min(batch_size, 500))
    for offset in range(0, len(jobs), batch_size):
        with database.session() as session:
            ingestion = JobIngestionService(session)
            for job in jobs[offset : offset + batch_size]:
                outcome = ingestion.ingest(job)
                decisions[outcome.decision.value] += 1
    return decisions


def run_scan(config: dict, companies: list[dict]) -> None:
    validate_local_config(config)
    started = time.monotonic()
    context = ConnectorContext()
    collected: list[UnifiedJob] = []
    errors: list[str] = []
    warnings: list[str] = []
    sources: list[str] = []
    scanned_companies: set[str] = set()

    with SafeHttpClient(connector="registry") as client:
        robots = RobotsPolicy(client)
        for raw in companies:
            try:
                company = normalize_company(raw)
                if not company.get("enabled", True):
                    continue
                connector = build_connector(company, client, robots)
                client.connector = connector.source_name
                sources.append(f"{company['name']}:{connector.source_name}")
                result = connector.collect(context)
                if result.status == "success":
                    scanned_companies.add(company["name"])
                    collected.extend(result.jobs)
                errors.extend(f"{company['name']}:{issue.code}" for issue in result.errors)
                warnings.extend(f"{company['name']}:{issue.code}" for issue in result.warnings)
            except Exception as exc:
                errors.append(f"{raw.get('name', 'unknown')}:{getattr(exc, 'code', type(exc).__name__)}")

    jobs, duplicate_count = deduplicate_jobs(collected)
    database = _initialize_database(config)
    if database is not None:
        try:
            _persist_jobs(jobs, database)
        finally:
            database.dispose()

    records = score_jobs(jobs, config)
    history = load_json_state(JOB_HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []
    records, merged_history, lifecycle = _apply_lifecycle(records, history, scanned_companies)
    history_limit = int(config.get("state", {}).get("history_limit", 5000))
    merged_history = sorted(
        merged_history, key=lambda item: str(item.get("last_seen") or item.get("scan_date") or ""), reverse=True
    )[: max(100, min(100_000, history_limit))]

    atomic_write_json(LAST_SCAN_FILE, records)
    atomic_write_json(JOB_HISTORY_FILE, merged_history)
    state = load_state()
    state["seen_urls"] = sorted({*state.get("seen_urls", []), *(item["url"] for item in records)})
    state["last_scan"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    csv_path, json_path, html_path = export_local_reports(records)

    minimum = int(config.get("candidate", {}).get("min_score", 60))
    top_n = int(config.get("candidate", {}).get("top_n", 5))
    top = [item for item in records if item["score"] >= minimum][:top_n]
    report = SearchRunReport(
        sources_consulted=sources,
        source_errors={"connectors": " | ".join(errors)} if errors else {},
        jobs_collected=len(collected),
        jobs_new=lifecycle["new"],
        jobs_updated=lifecycle["updated"] + lifecycle["republished"],
        duplicates_removed=duplicate_count + lifecycle["unchanged"],
        jobs_analyzed=len(records), jobs_above_threshold=len(top),
        duration_seconds=time.monotonic() - started, errors=errors, warnings=warnings,
    )
    atomic_write_json(LAST_RUN_REPORT_FILE, report.model_dump(mode="json"))

    logger.info(report.as_text())
    for index, item in enumerate(top, 1):
        logger.info("#%d [%d] %s @ %s", index, item["score"], item["title"], item["company"])
    logger.info("Local reports: %s, %s, %s", csv_path, json_path, html_path)
    if config.get("notifications", {}).get("windows_toast"):
        send_windows_notification("Autopilot Job Hunt", f"{len(top)} local matches found")
