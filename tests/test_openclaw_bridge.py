import json

from sqlalchemy import select

import job_hunt.openclaw_bridge as bridge_module
from job_hunt.discovery import PortalSuggestion
from job_hunt.openclaw_bridge import OpenClawResearchBridge
from job_hunt.persistence.database import Database
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import (
    CompanyRecord,
    JobRecord,
    PortalDiscoveryProposalRecord,
    ResumeVersionRecord,
)


class Dumpable:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, *, mode):
        assert mode == "json"
        return self.payload

    def __getattr__(self, name):
        value = self.payload[name]
        return Dumpable(value) if isinstance(value, dict) else value


def _database(tmp_path):
    url = f"sqlite:///{(tmp_path / 'openclaw.db').as_posix()}"
    upgrade_database(url)
    return url


def test_bridge_publishes_approved_resume_and_decision_feedback(tmp_path, monkeypatch):
    database_url = _database(tmp_path)
    companies_path = tmp_path / "companies.json"
    companies_path.write_text(
        json.dumps(
            [
                {
                    "name": "Monitored Co",
                    "careers_url": "https://careers.monitored.test/jobs",
                    "connector": "generic_html",
                    "enabled": True,
                },
                {
                    "name": "Disabled Co",
                    "careers_url": "https://disabled.test/careers",
                    "connector": "generic_html",
                    "enabled": False,
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bridge_module,
        "load_candidate_profile",
        lambda _path: Dumpable({"profile": "private", "work_preferences": {}}),
    )
    monkeypatch.setattr(
        bridge_module,
        "load_search_preferences",
        lambda _path: Dumpable(
            {
                "priority_roles": ["Security Engineer"],
                "priority_technologies": ["EDR"],
                "filters": {
                    "countries": ["Brazil"],
                    "locations": [],
                    "include_remote": True,
                    "include_hybrid": False,
                    "include_onsite": False,
                    "excluded_keywords": [],
                    "seniorities": [],
                },
            }
        ),
    )

    database = Database(database_url)
    with database.session() as session:
        session.add(
            ResumeVersionRecord(
                original_filename="resume.pdf",
                source_format="pdf",
                source_hash="a" * 64,
                markdown="# Approved resume",
                extraction_method="test",
                status="approved",
                approved=True,
                active=True,
            )
        )
        session.add_all(
            [
                PortalDiscoveryProposalRecord(
                    company_name="Approved Co",
                    careers_url="https://approved.test/careers",
                    rationale="approved signal",
                    state="approved",
                ),
                PortalDiscoveryProposalRecord(
                    company_name="Rejected Co",
                    careers_url="https://rejected.test/careers",
                    rationale="rejected signal",
                    state="rejected",
                ),
            ]
        )
        company = CompanyRecord(display_name="Example", normalized_name="example")
        session.add(company)
        session.flush()
        session.add(
            JobRecord(
                company_id=company.id,
                title="Security Engineer",
                normalized_title="security engineer",
                user_status="saved",
            )
        )
    database.dispose()

    context = tmp_path / "context"
    bridge = OpenClawResearchBridge(
        database_url,
        context_directory=context,
        exchange_directory=tmp_path / "exchange",
        companies_path=companies_path,
    )
    counts = bridge.publish_context()

    assert counts == {
        "monitored_companies": 1,
        "approved_proposals": 1,
        "rejected_proposals": 1,
        "pending_proposals": 0,
        "job_feedback": 1,
    }
    assert json.loads((context / "resume.json").read_text(encoding="utf-8"))["markdown"] == (
        "# Approved resume"
    )
    feedback = json.loads((context / "company_feedback.json").read_text(encoding="utf-8"))
    assert feedback["approved_proposals"][0]["company_name"] == "Approved Co"
    assert feedback["rejected_proposals"][0]["company_name"] == "Rejected Co"
    assert feedback["monitored_companies"][0]["name"] == "Monitored Co"


def test_bridge_verifies_submissions_and_saves_only_pending_proposals(tmp_path, monkeypatch):
    database_url = _database(tmp_path)
    exchange = tmp_path / "exchange"
    inbox = exchange / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "batch.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "company_name": "Acme",
                        "careers_url": "https://jobs.acme.test/careers",
                        "rationale": "role match",
                        "confidence": 0.8,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeDiscovery:
        def __init__(self, _settings):
            pass

        def verify_candidates(self, candidates):
            assert candidates[0]["company_name"] == "Acme"
            return (
                [
                    PortalSuggestion(
                        company_name="Acme",
                        careers_url="https://jobs.acme.test/careers",
                        rationale="role match",
                        confidence=0.8,
                        connector="generic_html",
                        allowed_domains=["jobs.acme.test"],
                        evidence_data={"verified": True},
                    )
                ],
                [],
            )

    monkeypatch.setattr(bridge_module, "PublicPortalDiscoveryService", FakeDiscovery)
    monkeypatch.setattr(bridge_module, "load_config", lambda: {})
    bridge = OpenClawResearchBridge(
        database_url,
        context_directory=tmp_path / "context",
        exchange_directory=exchange,
    )

    outcomes = bridge.process_inbox()

    assert outcomes[0]["state"] == "processed"
    assert outcomes[0]["accepted_as_pending"] == 1
    assert (exchange / "processed" / "batch.json").is_file()
    assert json.loads((exchange / "receipts" / "batch.json").read_text(encoding="utf-8"))[
        "state"
    ] == "processed"
    database = Database(database_url)
    with database.session() as session:
        record = session.scalar(select(PortalDiscoveryProposalRecord))
        assert record is not None
        assert record.state == "pending"
    database.dispose()
