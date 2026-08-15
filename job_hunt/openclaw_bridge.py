"""Curated, approval-gated data bridge for the isolated OpenClaw researcher."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from job_hunt.configuration import (
    load_candidate_profile,
    load_master_resume,
    load_search_preferences,
)
from job_hunt.discovery import DiscoveryRegistryService, PublicPortalDiscoveryService
from job_hunt.learning import LearningService, SemanticIndexService
from job_hunt.log import get_logger, redact_text
from job_hunt.main import load_config
from job_hunt.ollama import OllamaSettings
from job_hunt.persistence.database import Database
from job_hunt.persistence.models import (
    CompanyRecord,
    JobRecord,
    PortalDiscoveryProposalRecord,
    ResumeVersionRecord,
)
from job_hunt.state_store import atomic_write_json as write_json_state
from job_hunt.state_store import load_json_state

logger = get_logger("autopilot.openclaw_bridge")
MAX_INBOX_BYTES = 256 * 1024
MAX_CANDIDATES_PER_BATCH = 12


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: Any) -> None:
    write_json_state(path, payload, backup=False)
    path.chmod(0o644)


class OpenClawResearchBridge:
    """Publish read-only context and ingest bounded research batches as pending proposals."""

    def __init__(
        self,
        database_url: str,
        *,
        context_directory: Path,
        exchange_directory: Path,
        interval_seconds: int = 30,
        candidate_profile_path: Path = Path("config/candidate_profile.json"),
        search_preferences_path: Path = Path("config/search_preferences.json"),
        master_resume_path: Path = Path("resume/master_resume.json"),
        companies_path: Path = Path("companies.json"),
        openclaw_model_id: str | None = None,
        openclaw_context_size: int | None = None,
        heartbeat_seconds: int | None = None,
    ) -> None:
        self.database_url = database_url
        self.context_directory = context_directory
        self.exchange_directory = exchange_directory
        self.interval_seconds = max(5, interval_seconds)
        self.candidate_profile_path = candidate_profile_path
        self.search_preferences_path = search_preferences_path
        self.master_resume_path = master_resume_path
        self.companies_path = companies_path
        self.openclaw_model_id = openclaw_model_id or os.getenv(
            "OPENCLAW_MODEL_ID", "qwen3.5:9b"
        )
        self.openclaw_context_size = openclaw_context_size or int(
            os.getenv("OPENCLAW_CONTEXT_SIZE", "65536")
        )
        self.heartbeat_seconds = heartbeat_seconds or int(
            os.getenv("OPENCLAW_HEARTBEAT_SECONDS", "43200")
        )
        self._next_warmup = time.monotonic() + max(300, self.heartbeat_seconds - 300)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            self.publish_context()
            self.process_inbox()
        except Exception:
            logger.exception("Initial OpenClaw research bridge cycle failed")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve,
            name="autopilot-openclaw-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _serve(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.publish_context()
                self.process_inbox()
                self.refresh_semantic_index()
                if time.monotonic() >= self._next_warmup:
                    self.warmup_model()
            except Exception:
                logger.exception("OpenClaw research bridge cycle failed")

    def _ollama_settings(self) -> OllamaSettings:
        settings = OllamaSettings.from_config(load_config())
        return settings.model_copy(
            update={
                "chat_model": self.openclaw_model_id,
                "context_size": self.openclaw_context_size,
                "keep_alive": "15m",
            }
        )

    def refresh_semantic_index(self) -> dict[str, Any]:
        return SemanticIndexService(self._ollama_settings()).refresh(self.context_directory)

    def warmup_model(self) -> None:
        from job_hunt.ollama import OllamaClient

        with OllamaClient(self._ollama_settings()) as client:
            client.chat(
                [{"role": "user", "content": "Responda apenas OK."}],
                temperature=0,
                max_tokens=2,
                model=self.openclaw_model_id,
            )
        self._next_warmup = time.monotonic() + max(300, self.heartbeat_seconds - 300)

    def publish_context(self) -> dict[str, int]:
        profile = load_candidate_profile(self.candidate_profile_path)
        preferences = load_search_preferences(self.search_preferences_path)
        company_state = load_json_state(self.companies_path, [])
        companies = [item for item in company_state if isinstance(item, dict)]
        database = Database(self.database_url)
        try:
            with database.session() as session:
                active_resume = session.scalar(
                    select(ResumeVersionRecord)
                    .where(
                        ResumeVersionRecord.active.is_(True),
                        ResumeVersionRecord.approved.is_(True),
                    )
                    .order_by(ResumeVersionRecord.updated_at.desc())
                    .limit(1)
                )
                proposals = session.scalars(
                    select(PortalDiscoveryProposalRecord).order_by(
                        PortalDiscoveryProposalRecord.created_at.desc()
                    )
                ).all()
                job_rows = session.execute(
                    select(JobRecord, CompanyRecord)
                    .join(CompanyRecord, CompanyRecord.id == JobRecord.company_id)
                    .where(JobRecord.user_status.in_(["saved", "discarded"]))
                    .order_by(JobRecord.updated_at.desc())
                    .limit(500)
                ).all()
                proposal_data = [
                    DiscoveryRegistryService.proposal_data(record) for record in proposals
                ]
                learning = LearningService(session)
                learned_preferences = learning.summary(preferences, profile)
                research_metrics = learning.metrics(self.exchange_directory / "receipts")
                benchmark = learning.benchmark()
                active_learning = {"questions": learning.questions()}
        finally:
            database.dispose()

        if active_resume is not None:
            resume_payload: dict[str, Any] = {
                "source": "approved_active_resume",
                "version_id": active_resume.id,
                "updated_at": active_resume.updated_at.isoformat(),
                "markdown": active_resume.markdown,
            }
        else:
            resume_payload = {
                "source": "structured_master_resume",
                "resume": load_master_resume(self.master_resume_path).model_dump(mode="json"),
            }

        monitored = [
            {
                key: item[key]
                for key in ("name", "careers_url", "location", "region")
                if item.get(key) not in (None, "")
            }
            for item in companies
            if item.get("enabled", True)
        ]
        company_feedback = {
            "instructions": (
                "Approved and rejected decisions are feedback signals. Never re-propose a rejected "
                "portal unless the user explicitly changes that decision. Pending entries also count "
                "as already proposed."
            ),
            "monitored_companies": monitored,
            "approved_proposals": [item for item in proposal_data if item["state"] == "approved"],
            "rejected_proposals": [item for item in proposal_data if item["state"] == "rejected"],
            "pending_proposals": [item for item in proposal_data if item["state"] == "pending"],
        }
        job_feedback = {
            "instructions": (
                "Use saved/discarded jobs as weak preference evidence, not absolute rules. "
                "The resume and explicit search preferences remain authoritative."
            ),
            "items": [
                {
                    "job_id": job.id,
                    "company": company.display_name,
                    "title": job.title,
                    "location": job.location,
                    "country": job.country,
                    "seniority": job.seniority,
                    "modality": job.modality,
                    "decision": job.user_status,
                    "feedback_reasons": list(job.feedback_reasons or []),
                    "feedback_note": job.feedback_note,
                    "updated_at": job.updated_at.isoformat(),
                }
                for job, company in job_rows
            ],
        }
        generated_at = datetime.now(timezone.utc).isoformat()
        self.context_directory.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(
            self.context_directory / "candidate_profile.json",
            profile.model_dump(mode="json"),
        )
        _atomic_write_json(
            self.context_directory / "search_preferences.json",
            preferences.model_dump(mode="json"),
        )
        _atomic_write_json(self.context_directory / "resume.json", resume_payload)
        _atomic_write_json(
            self.context_directory / "company_feedback.json",
            company_feedback,
        )
        _atomic_write_json(self.context_directory / "job_feedback.json", job_feedback)
        company_profiles = {
            "schema_version": 1,
            "items": [
                {
                    **item,
                    "state": "monitored",
                    "company_profile": {},
                }
                for item in monitored
            ]
            + [
                {
                    "company_name": item["company_name"],
                    "careers_url": item["careers_url"],
                    "state": item["state"],
                    "company_profile": item.get("evidence", {}).get("company_profile", {}),
                    "matched_profile_signals": item.get("evidence", {}).get(
                        "matched_profile_signals", []
                    ),
                }
                for item in proposal_data
            ],
        }
        _atomic_write_json(self.context_directory / "company_profiles.json", company_profiles)
        _atomic_write_json(
            self.context_directory / "learned_preferences.json", learned_preferences
        )
        _atomic_write_json(self.context_directory / "research_metrics.json", research_metrics)
        _atomic_write_json(self.context_directory / "benchmark.json", benchmark)
        _atomic_write_json(self.context_directory / "active_learning.json", active_learning)
        counts = {
            "monitored_companies": len(monitored),
            "approved_proposals": len(company_feedback["approved_proposals"]),
            "rejected_proposals": len(company_feedback["rejected_proposals"]),
            "pending_proposals": len(company_feedback["pending_proposals"]),
            "job_feedback": len(job_feedback["items"]),
        }
        _atomic_write_json(
            self.context_directory / "manifest.json",
            {
                "schema_version": 2,
                "generated_at": generated_at,
                "counts": counts,
                "learning": {
                    "benchmark_cases": len(benchmark["cases"]),
                    "benchmark_ready": benchmark["ready"],
                    "unanswered_questions": sum(
                        item.get("answer") is None for item in active_learning["questions"]
                    ),
                },
            },
        )
        _atomic_write_text(
            self.context_directory / "README.md",
            "# Contexto somente leitura do pesquisador\n\n"
            "Arquivos gerados pelo Autopilot. Trate currículo e perfil como privados. "
            "Não altere estes arquivos; envie novas empresas pelo script da skill `company-research`.\n",
        )
        return counts

    def process_inbox(self) -> list[dict[str, Any]]:
        inbox = self.exchange_directory / "inbox"
        receipts = self.exchange_directory / "receipts"
        processed = self.exchange_directory / "processed"
        inbox.mkdir(parents=True, exist_ok=True)
        receipts.mkdir(parents=True, exist_ok=True)
        processed.mkdir(parents=True, exist_ok=True)
        inbox.chmod(0o777)
        receipts.chmod(0o777)
        processed.chmod(0o777)
        outcomes: list[dict[str, Any]] = []
        for path in sorted(inbox.glob("*.json"))[:20]:
            outcome = self._process_batch(path)
            _atomic_write_json(receipts / path.name, outcome)
            path.replace(processed / path.name)
            outcomes.append(outcome)
        return outcomes

    def _process_batch(self, path: Path) -> dict[str, Any]:
        batch_id = path.stem[:100]
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INBOX_BYTES:
                raise ValueError("research batch must be a regular JSON file up to 256 KiB")
            payload = json.loads(path.read_text(encoding="utf-8"))
            candidates = payload.get("candidates") if isinstance(payload, dict) else None
            if not isinstance(candidates, list) or not candidates:
                raise ValueError("research batch must contain a non-empty candidates list")
            if len(candidates) > MAX_CANDIDATES_PER_BATCH:
                raise ValueError("research batch exceeds the 12-candidate limit")
            if not all(isinstance(item, dict) for item in candidates):
                raise ValueError("every research candidate must be an object")
            service = PublicPortalDiscoveryService(
                OllamaSettings.from_config(load_config())
            )
            suggestions, warnings = service.verify_candidates(candidates)
            database = Database(self.database_url)
            try:
                with database.session() as session:
                    saved = DiscoveryRegistryService(session).save_suggestions(suggestions)
            finally:
                database.dispose()
            return {
                "batch_id": batch_id,
                "state": "processed",
                "received": len(candidates),
                "accepted_as_pending": len(saved),
                "warnings": [redact_text(item)[:300] for item in warnings],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return {
                "batch_id": batch_id,
                "state": "rejected",
                "error": redact_text(str(exc))[:500],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
