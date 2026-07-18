import re
from pathlib import Path

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.domain.models import MasterResume, UnifiedJob, WorkMode
from job_hunt.persistence.job_ingestion import JobIngestionService
from job_hunt.persistence.models import JobAnalysisRecord
from job_hunt.persistence.repositories import CandidateRepository
from job_hunt.web.app import create_app
from job_hunt.web.security import PanelSecuritySettings

REPOSITORY = Path(__file__).parents[1]


def _csrf(response) -> str:
    match = re.search(r'(?:name="csrf-token" content|name="csrf_token" value)="([^"]+)"', response.text)
    assert match
    return match.group(1)


def _login(client: TestClient) -> str:
    token = _csrf(client.get("/login"))
    response = client.post(
        "/login",
        data={"username": "admin", "password": "correct horse battery staple", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _csrf(client.get("/"))


def _app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = PanelSecuritySettings(
        username="admin",
        password_hash=PasswordHasher().hash("correct horse battery staple"),
        session_secret="s" * 32,
        allowed_hosts=["testserver", "localhost"],
        secure_cookie=False,
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


def test_panel_auth_csrf_dashboard_and_pipeline(tmp_path, monkeypatch):
    app, job_id = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/dashboard").status_code == 401
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert "unsafe-inline" not in login_page.headers["content-security-policy"]
        assert login_page.headers["x-frame-options"] == "DENY"
        csrf = _login(client)
        assert client.get("/api/metrics").status_code == 200
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["total_jobs"] == 1
        jobs = client.get("/api/jobs", params={"search": "Security", "minimum_score": 0})
        assert jobs.status_code == 200
        assert jobs.json()["items"][0]["id"] == job_id
        detail = client.get(f"/api/jobs/{job_id}")
        assert detail.status_code == 200
        assert "<script>" in detail.json()["job"]["title"]
        assert client.post(
            f"/api/jobs/{job_id}/disposition", json={"status": "saved"}
        ).status_code == 403
        saved = client.post(
            f"/api/jobs/{job_id}/disposition",
            json={"status": "saved"},
            headers={"X-CSRF-Token": csrf},
        )
        assert saved.json() == {"status": "saved"}
        application = client.post(
            f"/api/jobs/{job_id}/application",
            json={"status": "application_planned", "notes": "Review first", "allow_reopen": False},
            headers={"X-CSRF-Token": csrf},
        )
        assert application.json()["status"] == "application_planned"
        refreshed = client.get(f"/api/jobs/{job_id}").json()
        assert refreshed["application_events"][0]["to"] == "application_planned"
        documents = client.post(
            f"/api/jobs/{job_id}/documents",
            json={"language": "en", "create_docx": False, "create_pdf": False},
            headers={"X-CSRF-Token": csrf},
        )
        assert documents.status_code == 200
        assert len(documents.json()["files"]) == 2


def test_panel_login_rejects_csrf_bad_host_and_large_request(tmp_path, monkeypatch):
    app, _job_id = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/health", headers={"host": "attacker.example"}).status_code == 400
        assert client.post(
            "/login", data={"username": "admin", "password": "wrong", "csrf_token": "bad"}
        ).status_code == 403
        huge = "x" * 1_100_000
        assert client.post(
            "/login", content=huge, headers={"content-type": "application/x-www-form-urlencoded"}
        ).status_code == 413


def test_panel_rate_limits_bad_passwords_and_never_returns_secret_values(tmp_path, monkeypatch):
    app, _job_id = _app(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    with TestClient(app) as client:
        for _ in range(5):
            token = _csrf(client.get("/login"))
            response = client.post(
                "/login",
                data={"username": "admin", "password": "wrong password", "csrf_token": token},
            )
            assert response.status_code == 401
        token = _csrf(client.get("/login"))
        blocked = client.post(
            "/login",
            data={"username": "admin", "password": "wrong password", "csrf_token": token},
        )
        assert blocked.status_code == 429

    second = tmp_path / "second"
    second.mkdir()
    app2, _ = _app(second, monkeypatch)
    with TestClient(app2) as client:
        _login(client)
        settings = client.get("/api/settings").json()
        assert settings["secret_status"]["OPENAI_API_KEY"] is True
        assert "must-not-leak" not in str(settings)
