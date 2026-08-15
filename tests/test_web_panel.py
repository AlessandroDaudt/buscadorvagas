import re
import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import HttpUrl

from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.domain.models import MasterResume, UnifiedJob, WorkMode
from job_hunt.persistence.job_ingestion import JobIngestionService
from job_hunt.persistence.models import JobAnalysisRecord, PortalDiscoveryProposalRecord
from job_hunt.persistence.repositories import CandidateRepository
from job_hunt.web.app import create_app
from job_hunt.web.security import PanelSecuritySettings

REPOSITORY = Path(__file__).parents[1]


def _csrf(response) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match
    return match.group(1)


def _app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    shutil.copy2(REPOSITORY / "config.example.json", tmp_path / "config.json")
    shutil.copy2(
        REPOSITORY / "config" / "search_preferences.json",
        tmp_path / "config" / "search_preferences.json",
    )
    shutil.copy2(
        REPOSITORY / "config" / "candidate_profile.json",
        tmp_path / "config" / "candidate_profile.json",
    )
    (tmp_path / "companies.json").write_text("[]\n", encoding="utf-8")
    settings = PanelSecuritySettings(
        session_secret="s" * 32,
        allowed_hosts=["testserver", "localhost"],
        secure_cookie=False,
        max_request_bytes=2_000_000,
    )
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'panel.db').as_posix()}",
        security_settings=settings,
    )
    profile = load_candidate_profile(REPOSITORY / "config" / "candidate_profile.json")
    preferences = load_search_preferences(REPOSITORY / "config" / "search_preferences.json")
    job = UnifiedJob(
        source_name="fixture",
        original_url=HttpUrl("https://example.com/jobs/1"),
        company="Example & Co",
        title="Senior Security Engineer <script>alert(1)</script>",
        description="Microsoft Defender for Endpoint and EDR",
        location="Remote Brazil",
        work_mode=WorkMode.REMOTE,
    )
    with app.state.database.session() as session:
        candidates = CandidateRepository(session)
        candidate = candidates.save_profile(profile)
        master = MasterResume.model_validate_json(
            (REPOSITORY / "resume" / "master_resume.en.json").read_text(encoding="utf-8")
        ).model_copy(update={"approved": True})
        candidates.save_resume(candidate, master)
        result = JobIngestionService(session).ingest(job)
        analysis = consolidate_analysis(DeterministicScorer(preferences, profile).score(job))
        session.add(
            JobAnalysisRecord(
                job_id=result.job_id,
                score_total=analysis.total_score,
                score_data={"components": analysis.components.model_dump(mode="json")},
                explanation_data={"analysis": analysis.model_dump(mode="json")},
                provider=None,
                model=None,
                prompt_version=None,
                cache_key=None,
            )
        )
    return app, result.job_id


def test_panel_has_no_login_and_keeps_csrf_pipeline(tmp_path, monkeypatch):
    app, job_id = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["total_jobs"] == 1
        login = client.get("/login", follow_redirects=False)
        assert login.status_code == 308
        root = client.get("/")
        assert "sem login" in root.text
        assert "Recalcular ranking" in root.text
        assert "unsafe-inline" not in root.headers["content-security-policy"]
        assert client.get("/discovery").status_code == 200
        catalog = client.get("/api/catalog")
        assert catalog.status_code == 200
        assert catalog.json()["automatic_import"]["configured"] is True
        assert root.headers["x-frame-options"] == "DENY"
        csrf = _csrf(root)

        ranking = client.post(
            "/api/dashboard/refresh-ranking",
            headers={"X-CSRF-Token": csrf},
        )
        assert ranking.status_code == 200
        ranking_id = ranking.json()["id"]
        for _ in range(100):
            ranking_task = client.get(f"/api/tasks/{ranking_id}").json()
            if ranking_task["state"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert ranking_task["state"] == "completed", ranking_task
        assert ranking_task["result"]["analyzed"] == 1

        with app.state.database.session() as session:
            session.add(
                PortalDiscoveryProposalRecord(
                    company_name="Example Careers",
                    careers_url="https://example.com/careers",
                    connector="generic_html",
                    allowed_domains=["example.com"],
                    rationale="Fixture proposal",
                    confidence=0.9,
                    evidence_data={"verified": True},
                )
            )
        proposal = client.get("/api/discovery/proposals?state=pending").json()[0]
        approved_proposal = client.post(
            f"/api/discovery/proposals/{proposal['id']}/approve",
            json={"reasons": ["role_match", "remote_brazil"], "note": "Boa aderencia"},
            headers={"X-CSRF-Token": csrf},
        )
        assert approved_proposal.status_code == 200
        assert approved_proposal.json()["proposal"]["state"] == "approved"
        assert approved_proposal.json()["company"]["name"] == "Example Careers"
        assert approved_proposal.json()["proposal"]["feedback_reasons"] == [
            "role_match",
            "remote_brazil",
        ]

        created_alert = client.post(
            "/api/linkedin-alerts",
            json={"name": "Security", "keywords": ["Security Engineer"], "location": "Brasil"},
            headers={"X-CSRF-Token": csrf},
        )
        assert created_alert.status_code == 200
        assert (
            client.get("/api/linkedin-alerts")
            .json()[0]["search_url"]
            .startswith("https://www.linkedin.com/jobs/search/?")
        )
        opened_alert = client.post(
            f"/api/linkedin-alerts/{created_alert.json()['id']}/open",
            headers={"X-CSRF-Token": csrf},
        )
        assert opened_alert.json()["url"].startswith("https://www.linkedin.com/")
        assert (
            client.delete(
                f"/api/linkedin-alerts/{created_alert.json()['id']}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 409
        )

        jobs = client.get("/api/jobs", params={"search": "Security", "technology": "EDR"})
        assert jobs.status_code == 200
        assert jobs.json()["items"][0]["id"] == job_id
        advanced = client.get(
            "/api/jobs",
            params={
                "title": "Security",
                "company": "Example & Co",
                "location": "Remote",
                "country": "Brazil",
                "seniority": "senior",
                "status": "active",
                "user_status": "discovered",
                "maximum_score": 100,
                "recommendation": "apply",
                "minimum_salary": 1,
                "discovered_after": "2000-01-01T00:00:00Z",
                "discovered_before": "2100-01-01T00:00:00Z",
                "has_documents": False,
                "sort": "salary",
            },
        )
        assert advanced.status_code == 200
        assert client.get(f"/jobs/{job_id}").status_code == 200
        assert (
            client.get("/api/jobs/compare", params={"ids": f"{job_id},{job_id}"}).status_code == 200
        )
        detail = client.get(f"/api/jobs/{job_id}")
        assert "<script>" in detail.json()["job"]["title"]

        assert (
            client.post(f"/api/jobs/{job_id}/disposition", json={"status": "saved"}).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/jobs/{job_id}/disposition",
                json={"status": "saved"},
                headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.example"},
            ).status_code
            == 403
        )
        saved = client.post(
            f"/api/jobs/{job_id}/disposition",
            json={"status": "saved", "reasons": ["technology_match"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert saved.json() == {"status": "saved"}
        learning = client.get("/api/learning/summary")
        assert learning.status_code == 200
        assert learning.json()["metrics"]["approval_rate"] == 1
        assert client.post(
            "/api/learning/questions/answer",
            json={"question_id": "work_mode", "answer": "remote_only"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200

        application = client.post(
            f"/api/jobs/{job_id}/application",
            json={"status": "application_planned", "notes": "Review first", "allow_reopen": False},
            headers={"X-CSRF-Token": csrf},
        )
        assert application.json()["status"] == "application_planned"
        assert (
            client.get(f"/api/jobs/{job_id}").json()["application_events"][0]["to"]
            == "application_planned"
        )


def test_panel_persists_document_task_and_rejects_bad_host_size(tmp_path, monkeypatch):
    app, job_id = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/health", headers={"host": "attacker.example"}).status_code == 400
        csrf = _csrf(client.get("/"))
        response = client.post(
            f"/api/jobs/{job_id}/documents",
            json={"language": "en", "create_docx": False, "create_pdf": False},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        task_id = response.json()["id"]
        for _ in range(100):
            task = client.get(f"/api/tasks/{task_id}").json()
            if task["state"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert task["state"] == "completed", task
        assert len(task["result"]["files"]) == 2
        assert client.get("/api/tasks").json()[0]["id"] == task_id

        documents = client.get("/api/documents").json()
        markdown_document = next(item for item in documents if item["format"] == "md")
        document = client.get(f"/api/documents/{markdown_document['id']}").json()
        assert document["content"]
        edited = client.put(
            f"/api/documents/{markdown_document['id']}",
            json={"content": document["content"] + "\nReviewed locally."},
            headers={"X-CSRF-Token": csrf},
        )
        assert edited.status_code == 200
        assert edited.json()["version"] > markdown_document["version"]
        edited_id = edited.json()["id"]
        assert client.get(f"/api/documents/{markdown_document['id']}/download").status_code == 200
        regenerated = client.post(
            f"/api/documents/{markdown_document['id']}/regenerate",
            headers={"X-CSRF-Token": csrf},
        )
        assert regenerated.status_code == 200
        for _ in range(100):
            regenerated_task = client.get(f"/api/tasks/{regenerated.json()['id']}").json()
            if regenerated_task["state"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert regenerated_task["state"] == "completed"
        assert (
            client.delete(f"/api/documents/{edited_id}", headers={"X-CSRF-Token": csrf}).status_code
            == 409
        )
        assert client.delete(
            f"/api/documents/{edited_id}",
            headers={"X-CSRF-Token": csrf, "X-Confirm-Action": "DELETE"},
        ).json() == {"deleted": True}

        huge = b"x" * 2_000_001
        assert (
            client.post(
                "/api/exports",
                content=huge,
                headers={"content-type": "application/json", "X-CSRF-Token": csrf},
            ).status_code
            == 413
        )


def test_panel_resume_import_review_approval_and_activation(tmp_path, monkeypatch):
    app, _job_id = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        csrf = _csrf(client.get("/resume"))
        content = (
            "# Maria Silva\n\n## Experience\nSecurity Engineer at Acme, 2021\n\n"
            "## Education\nUniversity, 2019\n"
        )
        imported = client.post(
            "/api/resumes/import",
            files={"file": ("resume.md", content.encode(), "text/markdown")},
            headers={"X-CSRF-Token": csrf},
        )
        assert imported.status_code == 200, imported.text
        resume_id = imported.json()["id"]
        assert imported.json()["markdown"].rstrip() == content.rstrip()
        assert (
            client.post(
                f"/api/resumes/{resume_id}/validate", headers={"X-CSRF-Token": csrf}
            ).json()["valid"]
            is True
        )
        assert (
            client.post(f"/api/resumes/{resume_id}/approve", headers={"X-CSRF-Token": csrf}).json()[
                "approved"
            ]
            is True
        )
        assert (
            client.post(
                f"/api/resumes/{resume_id}/activate", headers={"X-CSRF-Token": csrf}
            ).json()["active"]
            is True
        )
        download = client.get(f"/api/resumes/{resume_id}/download")
        assert download.status_code == 200
        assert "Security Engineer at Acme" in download.text

        edited = client.put(
            f"/api/resumes/{resume_id}",
            json={"markdown": content + "\nPython\n"},
            headers={"X-CSRF-Token": csrf},
        ).json()
        assert edited["previous_version_id"] == resume_id
        edited_id = edited["id"]
        client.post(f"/api/resumes/{edited_id}/approve", headers={"X-CSRF-Token": csrf})
        client.post(f"/api/resumes/{edited_id}/activate", headers={"X-CSRF-Token": csrf})
        assert client.delete(
            f"/api/resumes/{resume_id}",
            headers={"X-CSRF-Token": csrf, "X-Confirm-Action": "DELETE"},
        ).json() == {"deleted": True}
        assert (
            client.delete(
                f"/api/resumes/{edited_id}",
                headers={"X-CSRF-Token": csrf, "X-Confirm-Action": "DELETE"},
            ).status_code
            == 400
        )


def test_panel_remaining_local_control_plane_routes(tmp_path, monkeypatch):
    app, _job_id = _app(tmp_path, monkeypatch)
    from job_hunt.web.application_services import CompanyConfigService

    monkeypatch.setattr(
        "job_hunt.web.app.CompanyConfigService",
        lambda: CompanyConfigService(
            tmp_path / "companies.json", resolver=lambda _host: ["8.8.8.8"]
        ),
    )
    app.state.tasks.handlers.update(
        {
            "doctor": lambda context, payload: {"checks": [], "failures": 0},
            "warmup": lambda context, payload: {"response_ok": True},
            "embedding": lambda context, payload: {"vectors": 1, "dimensions": 4},
            "company_test": lambda context, payload: {"status": "ok", "jobs": 0},
        }
    )
    with TestClient(app) as client:
        for route in (
            "/jobs",
            "/scans",
            "/companies",
            "/documents",
            "/exports",
            "/scheduler",
            "/system",
            "/settings",
        ):
            assert client.get(route).status_code == 200
        csrf = _csrf(client.get("/"))

        settings = client.get("/api/settings").json()
        assert settings["local_only"] is True
        assert (
            client.put(
                "/api/settings/preferences",
                json=settings["search_preferences"],
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/settings/ollama",
                json=settings["ollama"],
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 200
        )

        schedule = client.get("/api/scheduler").json()["schedule"]
        schedule.update({"enabled": True, "time": "09:30"})
        assert client.put("/api/scheduler", json=schedule, headers={"X-CSRF-Token": csrf}).json()[
            "next_run_at"
        ]
        schedule["enabled"] = False
        assert (
            client.put("/api/scheduler", json=schedule, headers={"X-CSRF-Token": csrf}).json()[
                "next_run_at"
            ]
            is None
        )

        added = client.post(
            "/api/companies",
            json={
                "name": "Example",
                "careers_url": "https://careers.example/jobs",
                "connector": "generic_html",
                "enabled": True,
            },
            headers={"X-CSRF-Token": csrf},
        ).json()
        updated = client.put(
            f"/api/companies/{added['id']}",
            json={"notes": "reviewed"},
            headers={"X-CSRF-Token": csrf},
        ).json()
        assert updated["notes"] == "reviewed"
        duplicated = client.post(
            f"/api/companies/{updated['id']}/duplicate", headers={"X-CSRF-Token": csrf}
        ).json()
        tested = client.post(
            f"/api/companies/{updated['id']}/test", headers={"X-CSRF-Token": csrf}
        ).json()
        assert tested["type"] == "company_test"
        assert client.delete(
            f"/api/companies/{duplicated['id']}",
            headers={"X-CSRF-Token": csrf, "X-Confirm-Action": "DELETE"},
        ).json() == {"deleted": True}

        export = client.post(
            "/api/exports",
            json={"format": "json", "minimum_score": 0, "days": 0},
            headers={"X-CSRF-Token": csrf},
        ).json()
        for _ in range(100):
            exported = client.get(f"/api/tasks/{export['id']}").json()
            if exported["state"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert exported["state"] == "completed"
        assert client.get(exported["result"]["download_url"]).status_code == 200

        (tmp_path / "state" / "http_cache").mkdir(parents=True, exist_ok=True)
        (tmp_path / "state" / "http_cache" / "one.json").write_text("{}", encoding="utf-8")
        assert client.post(
            "/api/system/cache/clear",
            headers={"X-CSRF-Token": csrf, "X-Confirm-Action": "CLEAR"},
        ).json() == {"removed": 1}
        report = client.post("/api/system/report", headers={"X-CSRF-Token": csrf}).json()
        assert client.get(report["download_url"]).status_code == 200
        diagnostic = client.post(
            "/api/system/actions/doctor", headers={"X-CSRF-Token": csrf}
        ).json()
        assert diagnostic["type"] == "doctor"
        for action in ("warmup", "embedding"):
            assert (
                client.post(
                    f"/api/system/actions/{action}", headers={"X-CSRF-Token": csrf}
                ).status_code
                == 200
            )
        for _ in range(100):
            diagnostic_state = client.get(f"/api/tasks/{diagnostic['id']}").json()["state"]
            if diagnostic_state not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert (
            client.post(
                f"/api/tasks/{diagnostic['id']}/cancel", headers={"X-CSRF-Token": csrf}
            ).status_code
            == 409
        )


def test_security_settings_need_no_credentials(monkeypatch):
    monkeypatch.delenv("PANEL_SESSION_SECRET", raising=False)
    monkeypatch.setenv("PANEL_ALLOWED_HOSTS", "localhost,127.0.0.1")
    monkeypatch.setenv("PANEL_SECURE_COOKIE", "false")
    settings = PanelSecuritySettings.from_environment()
    assert len(settings.session_secret) >= 32
    assert settings.password_hash is None
    assert settings.secure_cookie is False
