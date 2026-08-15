"""Auditable preference learning, evaluation and semantic retrieval for OpenClaw."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunt.domain.models import CandidateProfile, SearchPreferences
from job_hunt.ollama import OllamaClient, OllamaSettings
from job_hunt.persistence.models import (
    CompanyRecord,
    JobRecord,
    PortalDiscoveryProposalRecord,
    UserSettingRecord,
)
from job_hunt.state_store import atomic_write_json, load_json_state

POSITIVE_REASONS = {
    "role_match",
    "technology_match",
    "remote_brazil",
    "salary_match",
    "industry_match",
    "company_culture",
    "international_fit",
    "growth_potential",
}
NEGATIVE_REASONS = {
    "role_mismatch",
    "technology_mismatch",
    "onsite_required",
    "salary_low",
    "location_mismatch",
    "seniority_mismatch",
    "language_mismatch",
    "industry_mismatch",
    "company_not_interesting",
    "duplicate",
    "other",
}
FEEDBACK_REASONS = POSITIVE_REASONS | NEGATIVE_REASONS
ANSWER_SETTING_KEY = "openclaw.active_learning_answers"

ACTIVE_QUESTIONS = [
    {
        "id": "role_focus",
        "question": "Qual foco deve ter prioridade quando duas vagas forem igualmente aderentes?",
        "options": ["technical", "leadership", "balanced"],
    },
    {
        "id": "company_size",
        "question": "Qual porte de empresa voce prefere?",
        "options": ["enterprise", "startup", "any"],
    },
    {
        "id": "work_mode",
        "question": "Qual flexibilidade de modalidade deve ser considerada?",
        "options": ["remote_only", "hybrid_allowed", "any"],
    },
    {
        "id": "industry_focus",
        "question": "Que tipo de empresa deve aparecer primeiro?",
        "options": ["security_vendor", "broad_technology", "any"],
    },
    {
        "id": "international_contract",
        "question": "Contratos internacionais a partir do Brasil sao desejados?",
        "options": ["yes", "no", "case_by_case"],
    },
]


def validate_feedback(reasons: list[str], note: str | None) -> tuple[list[str], str | None]:
    cleaned = list(dict.fromkeys(str(item).strip() for item in reasons if str(item).strip()))
    unknown = set(cleaned) - FEEDBACK_REASONS
    if unknown:
        raise ValueError(f"unknown feedback reasons: {', '.join(sorted(unknown))}")
    if len(cleaned) > 8:
        raise ValueError("at most eight feedback reasons are allowed")
    clean_note = re.sub(r"\s+", " ", note or "").strip()[:1000] or None
    return cleaned, clean_note


class LearningService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def answers(self) -> dict[str, str]:
        self.session.flush()
        record = self.session.scalar(
            select(UserSettingRecord).where(UserSettingRecord.key == ANSWER_SETTING_KEY)
        )
        raw = (record.value_data or {}).get("answers", {}) if record else {}
        return {str(key): str(value) for key, value in raw.items()} if isinstance(raw, dict) else {}

    def questions(self) -> list[dict[str, Any]]:
        answers = self.answers()
        return [{**item, "answer": answers.get(str(item["id"]))} for item in ACTIVE_QUESTIONS]

    def answer_question(self, question_id: str, answer: str) -> dict[str, Any]:
        question = next((item for item in ACTIVE_QUESTIONS if item["id"] == question_id), None)
        if question is None:
            raise LookupError("active learning question not found")
        if answer not in question["options"]:
            raise ValueError("answer is not one of the allowed options")
        record = self.session.scalar(
            select(UserSettingRecord).where(UserSettingRecord.key == ANSWER_SETTING_KEY)
        )
        if record is None:
            record = UserSettingRecord(
                key=ANSWER_SETTING_KEY, value_data={"answers": {}}, is_secret=False
            )
            self.session.add(record)
        answers = dict((record.value_data or {}).get("answers", {}))
        answers[question_id] = answer
        record.value_data = {
            "answers": answers,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.session.flush()
        return {**question, "answer": answer}

    def summary(
        self, preferences: SearchPreferences, profile: CandidateProfile
    ) -> dict[str, Any]:
        self.session.flush()
        positive: Counter[str] = Counter()
        negative: Counter[str] = Counter()
        notes: list[dict[str, str]] = []
        jobs = self.session.scalars(
            select(JobRecord).where(JobRecord.user_status.in_(["saved", "discarded"]))
        ).all()
        proposals = self.session.scalars(
            select(PortalDiscoveryProposalRecord).where(
                PortalDiscoveryProposalRecord.state.in_(["approved", "rejected"])
            )
        ).all()
        reviewed_items: list[Any] = [*jobs, *proposals]
        for item in reviewed_items:
            decision = str(
                getattr(item, "user_status", None) or getattr(item, "state", "")
            )
            target = positive if decision in {"saved", "approved"} else negative
            target.update(str(value) for value in (item.feedback_reasons or []))
            if item.feedback_note:
                notes.append({"decision": decision, "note": item.feedback_note[:500]})
        signals = []
        for reason in sorted(set(positive) | set(negative)):
            yes, no = positive[reason], negative[reason]
            total = yes + no
            signals.append(
                {
                    "signal": reason,
                    "weight": round((yes - no) / total, 3) if total else 0,
                    "confidence": round(min(1.0, total / 5), 3),
                    "positive": yes,
                    "negative": no,
                }
            )
        return {
            "schema_version": 1,
            "authority_order": ["hard_constraints", "strong_preferences", "learned_signals"],
            "hard_constraints": {
                "countries": preferences.filters.countries,
                "locations": preferences.filters.locations,
                "include_remote": preferences.filters.include_remote,
                "include_hybrid": preferences.filters.include_hybrid,
                "include_onsite": preferences.filters.include_onsite,
                "excluded_keywords": preferences.filters.excluded_keywords,
                "work_preferences": profile.work_preferences.model_dump(mode="json"),
            },
            "strong_preferences": {
                "roles": preferences.priority_roles,
                "technologies": preferences.priority_technologies,
                "seniorities": preferences.filters.seniorities,
                "active_learning_answers": self.answers(),
            },
            "learned_signals": signals,
            "feedback_notes": notes[-50:],
            "sample_size": {"jobs": len(jobs), "companies": len(proposals)},
        }

    def metrics(self, receipts_directory: Path | None = None) -> dict[str, Any]:
        self.session.flush()
        proposals = self.session.scalars(select(PortalDiscoveryProposalRecord)).all()
        jobs = self.session.scalars(
            select(JobRecord).where(JobRecord.user_status.in_(["saved", "discarded"]))
        ).all()
        proposal_counts = Counter(item.state for item in proposals)
        job_counts = Counter(item.user_status for item in jobs)
        reviewed = proposal_counts["approved"] + proposal_counts["rejected"]
        decided_jobs = job_counts["saved"] + job_counts["discarded"]
        batches = received = accepted = warnings = 0
        if receipts_directory and receipts_directory.is_dir():
            for path in list(receipts_directory.glob("*.json"))[-200:]:
                value = load_json_state(path, {})
                if isinstance(value, dict) and value.get("state") == "processed":
                    batches += 1
                    received += int(value.get("received") or 0)
                    accepted += int(value.get("accepted_as_pending") or 0)
                    warnings += len(value.get("warnings") or [])
        return {
            "schema_version": 1,
            "proposals": dict(proposal_counts),
            "jobs": dict(job_counts),
            "approval_rate": round(proposal_counts["approved"] / reviewed, 3) if reviewed else None,
            "job_save_rate": round(job_counts["saved"] / decided_jobs, 3) if decided_jobs else None,
            "research_batches": batches,
            "candidates_received": received,
            "candidates_verified": accepted,
            "verification_rate": round(accepted / received, 3) if received else None,
            "verification_warnings": warnings,
        }

    def benchmark(self) -> dict[str, Any]:
        self.session.flush()
        cases: list[dict[str, Any]] = []
        for proposal in self.session.scalars(
            select(PortalDiscoveryProposalRecord).where(
                PortalDiscoveryProposalRecord.state.in_(["approved", "rejected"])
            )
        ).all():
            cases.append(
                {
                    "id": f"company:{proposal.id}",
                    "kind": "company",
                    "label": "positive" if proposal.state == "approved" else "negative",
                    "title": proposal.company_name,
                    "text": proposal.rationale[:1000],
                    "reasons": list(proposal.feedback_reasons or []),
                }
            )
        rows = self.session.execute(
            select(JobRecord, CompanyRecord)
            .join(CompanyRecord, CompanyRecord.id == JobRecord.company_id)
            .where(JobRecord.user_status.in_(["saved", "discarded"]))
        ).all()
        for job, company in rows:
            cases.append(
                {
                    "id": f"job:{job.id}",
                    "kind": "job",
                    "label": "positive" if job.user_status == "saved" else "negative",
                    "title": f"{job.title} at {company.display_name}",
                    "text": " ".join(filter(None, [job.location, job.country, job.seniority])),
                    "reasons": list(job.feedback_reasons or []),
                }
            )
        labels = Counter(item["label"] for item in cases)
        return {
            "schema_version": 1,
            "cases": cases[-200:],
            "coverage": dict(labels),
            "ready": labels["positive"] >= 5 and labels["negative"] >= 5,
            "target_per_label": 20,
        }


def _chunks_from_context(context_directory: Path) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    for filename in (
        "candidate_profile.json",
        "search_preferences.json",
        "learned_preferences.json",
        "job_feedback.json",
        "company_profiles.json",
        "benchmark.json",
    ):
        value = load_json_state(context_directory / filename, {})
        if not value:
            continue
        if filename == "company_profiles.json" and isinstance(value, dict):
            for index, profile in enumerate(value.get("items", [])[:800]):
                identity = str(
                    profile.get("careers_url")
                    or profile.get("company_name")
                    or profile.get("name")
                    or index
                )
                chunks.append(
                    {
                        "id": "company-profile:"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                        "source": filename,
                        "text": json.dumps(profile, ensure_ascii=False)[:2500],
                    }
                )
        else:
            text = json.dumps(value, ensure_ascii=False)
            for index in range(0, len(text), 2200):
                chunks.append(
                    {
                        "id": f"{filename}:{index // 2200}",
                        "source": filename,
                        "text": text[index : index + 2200],
                    }
                )
    resume = load_json_state(context_directory / "resume.json", {})
    if resume:
        resume_text = str(resume.get("markdown") or json.dumps(resume, ensure_ascii=False))
        for index in range(0, len(resume_text), 2200):
            chunks.append(
                {
                    "id": f"resume:{index // 2200}",
                    "source": "resume.json",
                    "text": resume_text[index : index + 2200],
                }
            )
    return chunks[:1000]


class SemanticIndexService:
    def __init__(self, settings: OllamaSettings) -> None:
        self.settings = settings

    def refresh(self, context_directory: Path) -> dict[str, Any]:
        chunks = _chunks_from_context(context_directory)
        digest = hashlib.sha256(
            json.dumps(chunks, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        path = context_directory / "semantic_index.json"
        metadata_path = context_directory / "semantic_index.meta.json"
        metadata = load_json_state(metadata_path, {})
        if (
            isinstance(metadata, dict)
            and metadata.get("source_digest") == digest
            and metadata.get("model") == self.settings.embedding_model
            and path.is_file()
        ):
            return {"updated": False, "chunks": int(metadata.get("chunks") or 0)}

        existing = load_json_state(path, {})
        if (
            isinstance(existing, dict)
            and existing.get("source_digest") == digest
            and existing.get("model") == self.settings.embedding_model
        ):
            item_count = len(existing.get("items", []))
            atomic_write_json(
                metadata_path,
                {"source_digest": digest, "chunks": item_count, "model": self.settings.embedding_model},
                backup=False,
            )
            return {"updated": False, "chunks": item_count}

        reusable = {}
        reusable_by_text = {}
        if isinstance(existing, dict) and existing.get("model") == self.settings.embedding_model:
            for item in existing.get("items", []):
                if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                    continue
                key = (
                    str(item.get("id")),
                    hashlib.sha256(str(item.get("text", "")).encode("utf-8")).hexdigest(),
                )
                reusable[key] = item["embedding"]
                reusable_by_text[key[1]] = item["embedding"]
        vectors_by_key = dict(reusable)
        missing = [
            item
            for item in chunks
            if (item["id"], hashlib.sha256(item["text"].encode("utf-8")).hexdigest())
            not in reusable
            and hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
            not in reusable_by_text
        ]
        if missing:
            with OllamaClient(self.settings) as client:
                vectors = client.embeddings([item["text"] for item in missing])
            for item, vector in zip(missing, vectors):
                key = (item["id"], hashlib.sha256(item["text"].encode("utf-8")).hexdigest())
                vectors_by_key[key] = vector
        items = []
        for item in chunks:
            key = (item["id"], hashlib.sha256(item["text"].encode("utf-8")).hexdigest())
            stored_vector = vectors_by_key.get(key) or reusable_by_text.get(key[1])
            if stored_vector is not None:
                items.append({**item, "embedding": stored_vector})
        payload = {
            "schema_version": 1,
            "model": self.settings.embedding_model,
            "source_digest": digest,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }
        atomic_write_json(path, payload, backup=False)
        atomic_write_json(
            metadata_path,
            {"source_digest": digest, "chunks": len(items), "model": self.settings.embedding_model},
            backup=False,
        )
        path.chmod(0o644)
        return {"updated": True, "chunks": len(items)}


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0
