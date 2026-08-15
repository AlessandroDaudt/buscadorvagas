import json
from types import SimpleNamespace

import pytest

import job_hunt.learning as learning_module
from job_hunt.learning import LearningService, SemanticIndexService, validate_feedback
from job_hunt.ollama import OllamaSettings
from job_hunt.persistence.database import Database
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import CompanyRecord, JobRecord, PortalDiscoveryProposalRecord


class Dumpable(SimpleNamespace):
    def model_dump(self, *, mode):
        assert mode == "json"
        return vars(self)


def _database(tmp_path):
    url = f"sqlite:///{(tmp_path / 'learning.db').as_posix()}"
    upgrade_database(url)
    return url


def _preferences():
    return SimpleNamespace(
        priority_roles=["Security Engineer"],
        priority_technologies=["Microsoft Sentinel"],
        filters=SimpleNamespace(
            countries=["Brazil"],
            locations=["Remote"],
            include_remote=True,
            include_hybrid=False,
            include_onsite=False,
            excluded_keywords=["unpaid"],
            seniorities=["senior"],
        ),
    )


def test_learning_separates_authority_and_builds_metrics_benchmark(tmp_path):
    url = _database(tmp_path)
    database = Database(url)
    with database.session() as session:
        company = CompanyRecord(display_name="Acme", normalized_name="acme")
        session.add(company)
        session.flush()
        session.add(
            JobRecord(
                company_id=company.id,
                title="Security Engineer",
                normalized_title="security engineer",
                user_status="saved",
                feedback_reasons=["technology_match"],
            )
        )
        session.add_all(
            [
                PortalDiscoveryProposalRecord(
                    company_name="Good Co",
                    careers_url="https://good.example/careers",
                    rationale="Remote security roles",
                    state="approved",
                    feedback_reasons=["role_match", "remote_brazil"],
                ),
                PortalDiscoveryProposalRecord(
                    company_name="Bad Co",
                    careers_url="https://bad.example/careers",
                    rationale="Onsite only",
                    state="rejected",
                    feedback_reasons=["onsite_required"],
                ),
            ]
        )
        service = LearningService(session)
        answer = service.answer_question("work_mode", "remote_only")
        assert answer["answer"] == "remote_only"
        summary = service.summary(
            _preferences(), Dumpable(work_preferences=Dumpable(remote=True))
        )
        assert summary["authority_order"] == [
            "hard_constraints",
            "strong_preferences",
            "learned_signals",
        ]
        assert summary["hard_constraints"]["include_onsite"] is False
        assert summary["strong_preferences"]["active_learning_answers"]["work_mode"] == (
            "remote_only"
        )
        weights = {item["signal"]: item["weight"] for item in summary["learned_signals"]}
        assert weights["role_match"] == 1
        assert weights["onsite_required"] == -1
        assert service.metrics()["approval_rate"] == 0.5
        benchmark = service.benchmark()
        assert benchmark["coverage"] == {"positive": 2, "negative": 1}
        assert benchmark["ready"] is False
    database.dispose()


def test_feedback_validation_and_semantic_index_cache(tmp_path, monkeypatch):
    assert validate_feedback(["role_match", "role_match"], "  strong   match ") == (
        ["role_match"],
        "strong match",
    )
    with pytest.raises(ValueError, match="unknown"):
        validate_feedback(["invented"], None)

    context = tmp_path / "context"
    context.mkdir()
    (context / "candidate_profile.json").write_text(
        json.dumps({"skills": ["Sentinel", "Entra ID"]}), encoding="utf-8"
    )
    calls = []

    class FakeOllama:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def embeddings(self, texts):
            calls.append(texts)
            return [[1.0, float(index)] for index, _text in enumerate(texts)]

    monkeypatch.setattr(learning_module, "OllamaClient", FakeOllama)
    service = SemanticIndexService(OllamaSettings(base_url="http://ollama:11434"))
    assert service.refresh(context) == {"updated": True, "chunks": 1}
    assert service.refresh(context) == {"updated": False, "chunks": 1}
    assert len(calls) == 1
    index = json.loads((context / "semantic_index.json").read_text(encoding="utf-8"))
    assert index["items"][0]["source"] == "candidate_profile.json"
