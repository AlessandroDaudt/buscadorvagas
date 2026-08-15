"""Complete local-only FastAPI control panel (intentionally without authentication)."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from job_hunt.applications import ApplicationService
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.discovery import DiscoveryRegistryService
from job_hunt.domain.models import ScheduleConfiguration
from job_hunt.learning import LearningService, validate_feedback
from job_hunt.local_config import parse_bool
from job_hunt.metrics import read_metrics_snapshot
from job_hunt.openclaw_bridge import OpenClawResearchBridge
from job_hunt.persistence.database import Database, get_database_url
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import (
    GeneratedDocumentRecord,
    JobRecord,
    ResumeVersionRecord,
    SearchRunRecord,
)
from job_hunt.portal_catalog import PortalCatalogService
from job_hunt.resume_import.service import ResumeImportService
from job_hunt.resume_import.versions import ResumeVersionService
from job_hunt.scheduler import next_run_at
from job_hunt.web.application_services import (
    CompanyConfigService,
    DownloadTokenService,
    PreferencesService,
    SystemStatusService,
    extended_dashboard,
)
from job_hunt.web.queries import job_detail, list_jobs
from job_hunt.web.schemas import (
    ActiveLearningAnswer,
    ApplicationUpdate,
    DispositionUpdate,
    FeedbackUpdate,
    JobDetail,
    JobPage,
)
from job_hunt.web.security import (
    PanelSecuritySettings,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    ensure_csrf_token,
    validate_csrf,
    validate_local_origin,
)
from job_hunt.web.task_handlers import build_task_handlers
from job_hunt.web.tasks import (
    LocalTaskManager,
    TaskCancellationError,
    TaskConflictError,
)

WEB_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
PAGE_TITLES = {
    "dashboard": "Visão geral",
    "jobs": "Vagas",
    "scans": "Buscas",
    "companies": "Empresas",
    "discovery": "Descoberta e alertas",
    "resume": "Currículo",
    "documents": "Documentos",
    "exports": "Exportações",
    "scheduler": "Agendamento",
    "system": "Sistema local",
    "settings": "Configurações",
}


def get_session(request: Request):
    database: Database = request.app.state.database
    with database.session() as session:
        yield session


def require_csrf_header(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    try:
        validate_local_origin(request)
        validate_csrf(request, x_csrf_token)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


SessionDependency = Annotated[Session, Depends(get_session)]
CsrfDependency = Annotated[None, Depends(require_csrf_header)]


def _task_data(record: Any, downloads: DownloadTokenService | None = None) -> dict[str, Any]:
    result = dict(record.result_data or {})
    path_value = result.pop("path", None)
    if path_value and downloads and record.state == "completed":
        try:
            result["download_url"] = f"/api/download/{downloads.issue(Path(path_value))}"
        except (OSError, ValueError):
            result["download_unavailable"] = True
    return {
        "id": record.id,
        "type": record.task_type,
        "state": record.state,
        "progress": record.progress,
        "message": record.message,
        "result": result,
        "error": record.error,
        "cancel_safe": record.cancel_safe,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
    }


def _resume_data(record: ResumeVersionRecord, *, include_markdown: bool = False) -> dict[str, Any]:
    result = {
        "id": record.id,
        "filename": record.original_filename,
        "format": record.source_format,
        "source_hash": record.source_hash,
        "extraction_method": record.extraction_method,
        "sections": record.detected_sections,
        "warnings": record.warnings,
        "metadata": record.metadata_data,
        "status": record.status,
        "approved": record.approved,
        "active": record.active,
        "previous_version_id": record.previous_version_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if include_markdown:
        result["markdown"] = record.markdown
    return result


def create_app(
    *,
    database_url: str | None = None,
    security_settings: PanelSecuritySettings | None = None,
) -> FastAPI:
    security = security_settings or PanelSecuritySettings.from_environment()
    resolved_database_url = database_url or get_database_url()
    upgrade_database(resolved_database_url)
    database = Database(resolved_database_url)
    tasks = LocalTaskManager(resolved_database_url, build_task_handlers(resolved_database_url))
    automatic_catalog_import = parse_bool(
        os.getenv("AUTO_IMPORT_PORTAL_CATALOG"), default=True
    )
    try:
        catalog_import_interval = max(
            5, min(3600, int(os.getenv("PORTAL_CATALOG_RECUR_INTERVAL_SECONDS", "60")))
        )
    except ValueError:
        catalog_import_interval = 60

    def catalog_import_has_work() -> bool:
        active = any(
            record.state in {"queued", "running", "cancel_requested"}
            for record in tasks.list(limit=10, task_type="import_portal_catalog")
        )
        pending = int(
            PortalCatalogService().status().get("by_status", {}).get("pending_validation", 0)
        )
        return active or pending > 0

    def ensure_automatic_catalog_import() -> None:
        if automatic_catalog_import:
            tasks.start_recurring_until_complete(
                "import_portal_catalog",
                catalog_import_has_work,
                interval_seconds=catalog_import_interval,
                payload={"automatic": True},
            )

    ensure_automatic_catalog_import()
    downloads = DownloadTokenService(security.session_secret)
    context_path = os.getenv("OPENCLAW_CONTEXT_DIR")
    exchange_path = os.getenv("OPENCLAW_EXCHANGE_DIR")
    openclaw_bridge = (
        OpenClawResearchBridge(
            resolved_database_url,
            context_directory=Path(context_path),
            exchange_directory=Path(exchange_path),
            interval_seconds=int(os.getenv("OPENCLAW_BRIDGE_INTERVAL_SECONDS", "30")),
        )
        if context_path and exchange_path
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            if openclaw_bridge is not None:
                openclaw_bridge.start()
            yield
        finally:
            if openclaw_bridge is not None:
                openclaw_bridge.stop()
            tasks.shutdown()
            database.dispose()

    app = FastAPI(
        title="Autopilot Job Hunt — painel local",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.database = database
    app.state.security = security
    app.state.tasks = tasks
    app.state.downloads = downloads
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=security.allowed_hosts)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=security.max_request_bytes)
    app.add_middleware(
        SessionMiddleware,
        secret_key=security.session_secret,
        session_cookie="autopilot_local_session",
        max_age=security.session_max_age_seconds,
        same_site="strict",
        https_only=security.secure_cookie,
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(LookupError)
    async def lookup_error_handler(_request: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "local_only": True, "authentication": False}

    @app.get("/login", include_in_schema=False)
    def obsolete_login() -> RedirectResponse:
        return RedirectResponse("/", status_code=308)

    @app.get("/", response_class=HTMLResponse)
    def dashboard_page(request: Request):
        return templates.TemplateResponse(
            request,
            "page.html",
            {
                "csrf_token": ensure_csrf_token(request),
                "page": "dashboard",
                "title": PAGE_TITLES["dashboard"],
            },
        )

    def render_page(request: Request, page: str):
        return templates.TemplateResponse(
            request,
            "page.html",
            {"csrf_token": ensure_csrf_token(request), "page": page, "title": PAGE_TITLES[page]},
        )

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return render_page(request, "jobs")

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: UUID):
        return templates.TemplateResponse(
            request,
            "page.html",
            {
                "csrf_token": ensure_csrf_token(request),
                "page": "job-detail",
                "title": "Detalhes da vaga",
                "resource_id": str(job_id),
            },
        )

    @app.get("/scans", response_class=HTMLResponse)
    def scans_page(request: Request):
        return render_page(request, "scans")

    @app.get("/companies", response_class=HTMLResponse)
    def companies_page(request: Request):
        return render_page(request, "companies")

    @app.get("/discovery", response_class=HTMLResponse)
    def discovery_page(request: Request):
        return render_page(request, "discovery")

    @app.get("/resume", response_class=HTMLResponse)
    def resume_page(request: Request):
        return render_page(request, "resume")

    @app.get("/documents", response_class=HTMLResponse)
    def documents_page(request: Request):
        return render_page(request, "documents")

    @app.get("/exports", response_class=HTMLResponse)
    def exports_page(request: Request):
        return render_page(request, "exports")

    @app.get("/scheduler", response_class=HTMLResponse)
    def scheduler_page(request: Request):
        return render_page(request, "scheduler")

    @app.get("/system", response_class=HTMLResponse)
    def system_page(request: Request):
        return render_page(request, "system")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return render_page(request, "settings")

    @app.get("/api/dashboard")
    def dashboard_api(session: SessionDependency) -> dict[str, Any]:
        try:
            minimum_score = load_search_preferences().filters.minimum_score
        except (OSError, ValueError):
            minimum_score = 60
        try:
            config = json.loads(Path("config.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {"local_only": True, "ollama": {}}
        system = SystemStatusService(resolved_database_url, config).status()
        return extended_dashboard(session, system, minimum_score)

    @app.post("/api/dashboard/refresh-ranking")
    def refresh_dashboard_ranking(_csrf: CsrfDependency) -> dict[str, Any]:
        try:
            return _task_data(
                tasks.submit(
                    "refresh_job_ranking",
                    {},
                    exclusive=True,
                    cancel_safe=True,
                ),
                downloads,
            )
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/metrics")
    def metrics_api() -> dict[str, Any]:
        return read_metrics_snapshot()

    @app.get("/api/tasks")
    def tasks_api(
        task_type: str | None = None, limit: int = Query(100, ge=1, le=200)
    ) -> list[dict[str, Any]]:
        return [
            _task_data(item, downloads) for item in tasks.list(limit=limit, task_type=task_type)
        ]

    @app.get("/api/tasks/{task_id}")
    def task_api(task_id: UUID) -> dict[str, Any]:
        return _task_data(tasks.get(str(task_id)), downloads)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: UUID, _csrf: CsrfDependency) -> dict[str, Any]:
        try:
            return _task_data(tasks.cancel(str(task_id)), downloads)
        except TaskCancellationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/scans")
    def start_scan(payload: dict[str, Any], _csrf: CsrfDependency) -> dict[str, Any]:
        try:
            return _task_data(
                tasks.submit("scan", payload, exclusive=True, cancel_safe=False), downloads
            )
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/scans")
    def scans_api(limit: int = Query(100, ge=1, le=200)) -> list[dict[str, Any]]:
        return [_task_data(item, downloads) for item in tasks.list(limit=limit, task_type="scan")]

    @app.get("/api/jobs", response_model=JobPage)
    def jobs_api(
        session: SessionDependency,
        search: str | None = Query(None, max_length=200),
        title: str | None = Query(None, max_length=200),
        company: str | None = Query(None, max_length=300),
        technology: str | None = Query(None, max_length=100),
        location: str | None = Query(None, max_length=200),
        modality: str | None = Query(None, max_length=30),
        country: str | None = Query(None, max_length=120),
        seniority: str | None = Query(None, max_length=100),
        status: str | None = Query(None, max_length=30),
        user_status: str | None = Query(None, max_length=30),
        minimum_score: float | None = Query(None, ge=0, le=100),
        maximum_score: float | None = Query(None, ge=0, le=100),
        recommendation: str | None = Query(None, max_length=50),
        minimum_salary: float | None = Query(None, ge=0),
        discovered_after: datetime | None = None,
        discovered_before: datetime | None = None,
        has_documents: bool | None = None,
        sort: Literal[
            "last_seen", "published", "title", "company", "score", "salary", "discovered"
        ] = "last_seen",
        direction: Literal["asc", "desc"] = "desc",
        page: int = Query(1, ge=1, le=1_000_000),
        page_size: int = Query(25, ge=1, le=100),
    ) -> JobPage:
        return list_jobs(
            session,
            search=search,
            title=title,
            company=company,
            technology=technology,
            location=location,
            modality=modality,
            country=country,
            seniority=seniority,
            status=status,
            user_status=user_status,
            minimum_score=minimum_score,
            maximum_score=maximum_score,
            recommendation=recommendation,
            minimum_salary=minimum_salary,
            discovered_after=discovered_after,
            discovered_before=discovered_before,
            has_documents=has_documents,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/jobs/compare", response_model=list[JobDetail])
    def compare_jobs(
        session: SessionDependency, ids: str = Query(min_length=36, max_length=110)
    ) -> list[JobDetail]:
        job_ids = ids.split(",")
        if not 2 <= len(job_ids) <= 3:
            raise HTTPException(status_code=400, detail="compare two or three jobs")
        results = []
        for value in job_ids:
            try:
                UUID(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid job ID") from exc
            detail = job_detail(session, value)
            if detail is None:
                raise HTTPException(status_code=404, detail="job not found")
            results.append(detail)
        return results

    @app.get("/api/jobs/{job_id}", response_model=JobDetail)
    def job_api(job_id: UUID, session: SessionDependency) -> JobDetail:
        detail = job_detail(session, str(job_id))
        if detail is None:
            raise HTTPException(status_code=404, detail="job not found")
        return detail

    @app.post("/api/jobs/{job_id}/disposition")
    def update_disposition(
        job_id: UUID, payload: DispositionUpdate, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, str]:
        job = session.get(JobRecord, str(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        job.user_status = payload.status
        reasons, note = validate_feedback(payload.reasons, payload.note)
        job.feedback_reasons = reasons if payload.status != "discovered" else []
        job.feedback_note = note if payload.status != "discovered" else None
        return {"status": job.user_status}

    @app.post("/api/jobs/{job_id}/application")
    def update_application(
        job_id: UUID, payload: ApplicationUpdate, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, str]:
        if session.get(JobRecord, str(job_id)) is None:
            raise HTTPException(status_code=404, detail="job not found")
        application = ApplicationService(session).set_status(
            str(job_id), payload.status, notes=payload.notes, allow_reopen=payload.allow_reopen
        )
        return {"status": application.status}

    @app.post("/api/jobs/{job_id}/documents")
    def generate_documents(
        job_id: UUID, payload: dict[str, Any], _csrf: CsrfDependency
    ) -> dict[str, Any]:
        payload = {**payload, "job_id": str(job_id)}
        return _task_data(tasks.submit("documents", payload, cancel_safe=False), downloads)

    @app.post("/api/jobs/{job_id}/ai-document")
    def generate_ai_document(
        job_id: UUID, payload: dict[str, Any], _csrf: CsrfDependency
    ) -> dict[str, Any]:
        task_payload = {
            "job_id": str(job_id),
            "document_type": str(payload.get("document_type", "")),
            "language": str(payload.get("language", "pt-BR")),
        }
        return _task_data(tasks.submit("ai_document", task_payload, cancel_safe=False), downloads)

    @app.get("/api/companies")
    def companies_api() -> list[dict[str, Any]]:
        return CompanyConfigService().list()

    @app.post("/api/companies")
    def add_company(payload: dict[str, Any], _csrf: CsrfDependency) -> dict[str, Any]:
        return CompanyConfigService().add(payload)

    @app.put("/api/companies/{company_id}")
    def update_company(
        company_id: str, payload: dict[str, Any], _csrf: CsrfDependency
    ) -> dict[str, Any]:
        return CompanyConfigService().update(company_id, payload)

    @app.delete("/api/companies/{company_id}")
    def delete_company(
        company_id: str, _csrf: CsrfDependency, x_confirm_action: str | None = Header(None)
    ) -> dict[str, bool]:
        if x_confirm_action != "DELETE":
            raise HTTPException(status_code=409, detail="explicit deletion confirmation required")
        CompanyConfigService().delete(company_id)
        return {"deleted": True}

    @app.post("/api/companies/{company_id}/duplicate")
    def duplicate_company(company_id: str, _csrf: CsrfDependency) -> dict[str, Any]:
        return CompanyConfigService().duplicate(company_id)

    @app.post("/api/companies/{company_id}/test")
    def test_company(company_id: str, _csrf: CsrfDependency) -> dict[str, Any]:
        return _task_data(tasks.submit("company_test", {"company_id": company_id}), downloads)

    @app.get("/api/discovery/proposals")
    def discovery_proposals(
        session: SessionDependency, state: Literal["pending", "approved", "rejected"] | None = None
    ) -> list[dict[str, Any]]:
        return DiscoveryRegistryService(session).list_proposals(state=state)

    @app.post("/api/discovery/run")
    def run_portal_discovery(_csrf: CsrfDependency) -> dict[str, Any]:
        try:
            return _task_data(tasks.submit("discover_portals", {}, exclusive=True), downloads)
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/catalog")
    def catalog_status() -> dict[str, Any]:
        result = PortalCatalogService().status()
        active = next(
            (
                record
                for record in tasks.list(limit=10, task_type="import_portal_catalog")
                if record.state in {"queued", "running", "cancel_requested"}
            ),
            None,
        )
        result["automatic_import"] = {
            **tasks.recurring_status("import_portal_catalog"),
            "configured": automatic_catalog_import,
            "pending": int(result.get("by_status", {}).get("pending_validation", 0)),
            "active_task_id": active.id if active else None,
            "active_task_state": active.state if active else None,
            "active_task_progress": active.progress if active else None,
        }
        return result

    @app.post("/api/catalog/import")
    def import_catalog(_csrf: CsrfDependency) -> dict[str, Any]:
        try:
            record = tasks.submit("import_portal_catalog", {}, exclusive=True)
            ensure_automatic_catalog_import()
            return _task_data(record, downloads)
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/discovery/proposals/{proposal_id}/approve")
    def approve_discovery_proposal(
        proposal_id: UUID,
        _csrf: CsrfDependency,
        session: SessionDependency,
        payload: FeedbackUpdate | None = None,
    ) -> dict[str, Any]:
        service = DiscoveryRegistryService(session)
        proposal = service.get_proposal(str(proposal_id))
        if proposal.state != "pending":
            raise ValueError("only pending proposals can be approved")
        company = CompanyConfigService().add(
            {
                "name": proposal.company_name,
                "careers_url": proposal.careers_url,
                "connector": proposal.connector,
                "allowed_domains": proposal.allowed_domains,
                "enabled": True,
            }
        )
        proposal.state = "approved"
        reasons, note = validate_feedback(
            payload.reasons if payload else [], payload.note if payload else None
        )
        proposal.feedback_reasons = reasons
        proposal.feedback_note = note
        proposal.reviewed_at = datetime.now(timezone.utc)
        return {"proposal": service.proposal_data(proposal), "company": company}

    @app.post("/api/discovery/proposals/{proposal_id}/reject")
    def reject_discovery_proposal(
        proposal_id: UUID,
        _csrf: CsrfDependency,
        session: SessionDependency,
        payload: FeedbackUpdate | None = None,
    ) -> dict[str, Any]:
        reasons, note = validate_feedback(
            payload.reasons if payload else [], payload.note if payload else None
        )
        return DiscoveryRegistryService(session).reject(
            str(proposal_id), reasons=reasons, note=note
        )

    @app.get("/api/learning/summary")
    def learning_summary(session: SessionDependency) -> dict[str, Any]:
        service = LearningService(session)
        receipts = Path(
            os.getenv("OPENCLAW_EXCHANGE_DIR", "state/openclaw/exchange")
        ) / "receipts"
        return {
            "preferences": service.summary(
                load_search_preferences(), load_candidate_profile()
            ),
            "metrics": service.metrics(receipts),
            "benchmark": service.benchmark(),
            "questions": service.questions(),
        }

    @app.get("/api/learning/questions")
    def learning_questions(session: SessionDependency) -> list[dict[str, Any]]:
        return LearningService(session).questions()

    @app.post("/api/learning/questions/answer")
    def answer_learning_question(
        payload: ActiveLearningAnswer, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, Any]:
        return LearningService(session).answer_question(payload.question_id, payload.answer)

    @app.get("/api/learning/metrics")
    def learning_metrics(session: SessionDependency) -> dict[str, Any]:
        receipts = Path(
            os.getenv("OPENCLAW_EXCHANGE_DIR", "state/openclaw/exchange")
        ) / "receipts"
        return LearningService(session).metrics(receipts)

    @app.get("/api/learning/benchmark")
    def learning_benchmark(session: SessionDependency) -> dict[str, Any]:
        return LearningService(session).benchmark()

    @app.get("/api/linkedin-alerts")
    def linkedin_alerts(session: SessionDependency) -> list[dict[str, Any]]:
        return DiscoveryRegistryService(session).list_alerts()

    @app.post("/api/linkedin-alerts")
    def create_linkedin_alert(
        payload: dict[str, Any], _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, Any]:
        return DiscoveryRegistryService(session).create_alert(payload)

    @app.post("/api/linkedin-alerts/{alert_id}/open")
    def open_linkedin_alert(
        alert_id: UUID, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, str]:
        alert = DiscoveryRegistryService(session).mark_alert_opened(str(alert_id))
        return {"url": str(alert["search_url"])}

    @app.put("/api/linkedin-alerts/{alert_id}")
    def set_linkedin_alert_state(
        alert_id: UUID,
        payload: dict[str, Any],
        _csrf: CsrfDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return DiscoveryRegistryService(session).set_alert_enabled(
            str(alert_id), bool(payload.get("enabled"))
        )

    @app.delete("/api/linkedin-alerts/{alert_id}")
    def delete_linkedin_alert(
        alert_id: UUID,
        _csrf: CsrfDependency,
        session: SessionDependency,
        x_confirm_action: str | None = Header(None),
    ) -> dict[str, bool]:
        if x_confirm_action != "DELETE":
            raise HTTPException(status_code=409, detail="explicit deletion confirmation required")
        DiscoveryRegistryService(session).delete_alert(str(alert_id))
        return {"deleted": True}

    @app.get("/api/resumes")
    def resumes_api(session: SessionDependency) -> list[dict[str, Any]]:
        records = session.scalars(
            select(ResumeVersionRecord).order_by(ResumeVersionRecord.created_at.desc())
        ).all()
        return [_resume_data(record) for record in records]

    @app.post("/api/resumes/import")
    async def import_resume(
        _csrf: CsrfDependency, session: SessionDependency, file: UploadFile = File(...)
    ) -> dict[str, Any]:
        data = await file.read(15 * 1024 * 1024 + 1)
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="arquivo excede 15 MB")
        result, safe_name, source_hash = ResumeImportService().import_bytes(
            data, original_filename=file.filename or "resume", content_type=file.content_type
        )
        record = ResumeVersionService(session).create(
            result,
            original_filename=safe_name,
            source_hash=source_hash,
        )
        return _resume_data(record, include_markdown=True)

    @app.get("/api/resumes/{resume_id}")
    def resume_api(resume_id: UUID, session: SessionDependency) -> dict[str, Any]:
        record = session.get(ResumeVersionRecord, str(resume_id))
        if record is None:
            raise HTTPException(status_code=404, detail="resume not found")
        return _resume_data(record, include_markdown=True)

    @app.put("/api/resumes/{resume_id}")
    def edit_resume(
        resume_id: UUID, payload: dict[str, Any], _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, Any]:
        current = session.get(ResumeVersionRecord, str(resume_id))
        if current is None:
            raise HTTPException(status_code=404, detail="resume not found")
        record = ResumeVersionService(session).edit(current, str(payload.get("markdown", "")))
        return _resume_data(record, include_markdown=True)

    @app.post("/api/resumes/{resume_id}/validate")
    def validate_resume(
        resume_id: UUID, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, Any]:
        record = session.get(ResumeVersionRecord, str(resume_id))
        if record is None:
            raise HTTPException(status_code=404, detail="resume not found")
        return ResumeVersionService(session).validate(record).model_dump()

    @app.post("/api/resumes/{resume_id}/approve")
    def approve_resume(
        resume_id: UUID, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, Any]:
        record = session.get(ResumeVersionRecord, str(resume_id))
        if record is None:
            raise HTTPException(status_code=404, detail="resume not found")
        return _resume_data(ResumeVersionService(session).approve(record), include_markdown=True)

    @app.post("/api/resumes/{resume_id}/activate")
    def activate_resume(
        resume_id: UUID, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, Any]:
        record = session.get(ResumeVersionRecord, str(resume_id))
        if record is None:
            raise HTTPException(status_code=404, detail="resume not found")
        return _resume_data(ResumeVersionService(session).activate(record), include_markdown=True)

    @app.delete("/api/resumes/{resume_id}")
    def delete_resume(
        resume_id: UUID,
        _csrf: CsrfDependency,
        session: SessionDependency,
        x_confirm_action: str | None = Header(None),
    ) -> dict[str, bool]:
        if x_confirm_action != "DELETE":
            raise HTTPException(status_code=409, detail="explicit deletion confirmation required")
        record = session.get(ResumeVersionRecord, str(resume_id))
        if record is None:
            raise HTTPException(status_code=404, detail="resume not found")
        ResumeVersionService(session).delete(record)
        return {"deleted": True}

    @app.get("/api/resumes/{resume_id}/download")
    def download_resume(resume_id: UUID, session: SessionDependency) -> Response:
        record = session.get(ResumeVersionRecord, str(resume_id))
        if record is None:
            raise HTTPException(status_code=404, detail="resume not found")
        safe_name = Path(record.original_filename).stem[:100] or "resume"
        return Response(
            record.markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
        )

    @app.get("/api/documents")
    def documents_api(
        session: SessionDependency, job_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        statement = select(GeneratedDocumentRecord)
        if job_id:
            statement = statement.where(GeneratedDocumentRecord.job_id == str(job_id))
        records = session.scalars(
            statement.order_by(GeneratedDocumentRecord.created_at.desc()).limit(500)
        ).all()
        return [
            {
                "id": item.id,
                "job_id": item.job_id,
                "type": item.document_type,
                "language": item.language,
                "format": item.file_format,
                "version": item.version,
                "created_at": item.created_at,
            }
            for item in records
        ]

    @app.get("/api/documents/{document_id}/download")
    def document_download(document_id: UUID, session: SessionDependency) -> RedirectResponse:
        record = session.get(GeneratedDocumentRecord, str(document_id))
        if record is None:
            raise HTTPException(status_code=404, detail="document not found")
        token = downloads.issue(Path(record.storage_path))
        return RedirectResponse(f"/api/download/{token}", status_code=307)

    @app.get("/api/documents/{document_id}")
    def document_api(document_id: UUID, session: SessionDependency) -> dict[str, Any]:
        record = session.get(GeneratedDocumentRecord, str(document_id))
        if record is None:
            raise HTTPException(status_code=404, detail="document not found")
        path = downloads.output_root / Path(record.storage_path).resolve().relative_to(
            downloads.output_root
        )
        content = None
        if record.file_format in {"md", "txt", "html"} and path.stat().st_size <= 1_000_000:
            content = path.read_text(encoding="utf-8")
        return {
            "id": record.id,
            "job_id": record.job_id,
            "type": record.document_type,
            "language": record.language,
            "format": record.file_format,
            "version": record.version,
            "content": content,
        }

    @app.put("/api/documents/{document_id}")
    def edit_document(
        document_id: UUID,
        payload: dict[str, Any],
        _csrf: CsrfDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        current = session.get(GeneratedDocumentRecord, str(document_id))
        if current is None:
            raise HTTPException(status_code=404, detail="document not found")
        if current.file_format not in {"md", "txt", "html"}:
            raise HTTPException(status_code=409, detail="only text documents can be edited")
        content = str(payload.get("content", ""))
        if not content.strip() or len(content) > 1_000_000:
            raise ValueError("document content must contain between 1 and 1,000,000 characters")
        version = (
            int(
                session.scalar(
                    select(func.max(GeneratedDocumentRecord.version)).where(
                        GeneratedDocumentRecord.job_id == current.job_id,
                        GeneratedDocumentRecord.document_type == current.document_type,
                        GeneratedDocumentRecord.language == current.language,
                        GeneratedDocumentRecord.file_format == current.file_format,
                    )
                )
                or 0
            )
            + 1
        )
        current_path = Path(current.storage_path).resolve()
        if downloads.output_root not in current_path.parents:
            raise ValueError("document path is outside output")
        path = current_path.with_name(
            f"{current.document_type}.{current.language}.v{version}.{current.file_format}"
        )
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
        os.replace(temporary, path)
        record = GeneratedDocumentRecord(
            job_id=current.job_id,
            resume_master_id=current.resume_master_id,
            document_type=current.document_type,
            language=current.language,
            file_format=current.file_format,
            storage_path=str(path),
            version=version,
            model=current.model,
            prompt_version=current.prompt_version,
            master_hash=current.master_hash,
            diff_data={**current.diff_data, "manually_edited_from": current.id},
        )
        session.add(record)
        session.flush()
        return {"id": record.id, "version": version}

    @app.post("/api/documents/{document_id}/regenerate")
    def regenerate_document(
        document_id: UUID, _csrf: CsrfDependency, session: SessionDependency
    ) -> dict[str, Any]:
        current = session.get(GeneratedDocumentRecord, str(document_id))
        if current is None:
            raise HTTPException(status_code=404, detail="document not found")
        if current.document_type in {
            "interview_prep",
            "gap_analysis",
            "interview_questions",
            "study_plan",
        }:
            task_type = "ai_document"
            payload = {
                "job_id": current.job_id,
                "document_type": current.document_type,
                "language": current.language,
            }
        else:
            task_type = "documents"
            payload = {"job_id": current.job_id, "language": current.language}
        return _task_data(tasks.submit(task_type, payload, cancel_safe=False), downloads)

    @app.delete("/api/documents/{document_id}")
    def delete_document(
        document_id: UUID,
        _csrf: CsrfDependency,
        session: SessionDependency,
        x_confirm_action: str | None = Header(None),
    ) -> dict[str, bool]:
        if x_confirm_action != "DELETE":
            raise HTTPException(status_code=409, detail="explicit deletion confirmation required")
        record = session.get(GeneratedDocumentRecord, str(document_id))
        if record is None:
            raise HTTPException(status_code=404, detail="document not found")
        path = Path(record.storage_path).resolve()
        output = Path("output").resolve()
        if output in path.parents and path.is_file():
            path.unlink()
        session.delete(record)
        return {"deleted": True}

    @app.post("/api/exports")
    def create_export(payload: dict[str, Any], _csrf: CsrfDependency) -> dict[str, Any]:
        return _task_data(tasks.submit("export", payload), downloads)

    @app.get("/api/download/{token}")
    def download(token: str) -> FileResponse:
        path = downloads.resolve(token)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.get("/api/scheduler")
    def scheduler_api(session: SessionDependency) -> dict[str, Any]:
        schedule = load_search_preferences().schedule
        last = session.scalar(
            select(SearchRunRecord).order_by(SearchRunRecord.started_at.desc()).limit(1)
        )
        return {
            "schedule": schedule.model_dump(mode="json"),
            "next_run_at": next_run_at(schedule).isoformat() if schedule.enabled else None,
            "last_run": {
                "id": last.id,
                "status": last.status,
                "started_at": last.started_at,
                "finished_at": last.finished_at,
            }
            if last
            else None,
        }

    @app.put("/api/scheduler")
    def update_scheduler(payload: dict[str, Any], _csrf: CsrfDependency) -> dict[str, Any]:
        schedule = ScheduleConfiguration.model_validate(payload)
        service = PreferencesService()
        current = service.get()["search_preferences"]
        current["schedule"] = schedule.model_dump(mode="json")
        service.update_preferences(current)
        return {
            "schedule": schedule.model_dump(mode="json"),
            "next_run_at": next_run_at(schedule).isoformat() if schedule.enabled else None,
        }

    @app.post("/api/scheduler/run-now")
    def scheduler_run_now(_csrf: CsrfDependency) -> dict[str, Any]:
        try:
            return _task_data(
                tasks.submit("scan", {}, exclusive=True, cancel_safe=False), downloads
            )
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/system")
    def system_api() -> dict[str, Any]:
        try:
            config = json.loads(Path("config.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {"local_only": True, "ollama": {}}
        return SystemStatusService(resolved_database_url, config).status()

    @app.post("/api/system/actions/{action}")
    def system_action(
        action: Literal["doctor", "warmup", "embedding"], _csrf: CsrfDependency
    ) -> dict[str, Any]:
        return _task_data(tasks.submit(action, {}, exclusive=action == "warmup"), downloads)

    @app.post("/api/system/cache/clear")
    def clear_http_cache(
        _csrf: CsrfDependency, x_confirm_action: str | None = Header(None)
    ) -> dict[str, int]:
        if x_confirm_action != "CLEAR":
            raise HTTPException(
                status_code=409, detail="explicit cache clearing confirmation required"
            )
        cache_root = Path("state/http_cache").resolve()
        state_root = Path("state").resolve()
        if state_root not in cache_root.parents:
            raise ValueError("cache path is outside local state")
        removed = 0
        if cache_root.is_dir():
            for path in cache_root.iterdir():
                if path.is_file() and cache_root in path.resolve().parents:
                    path.unlink()
                    removed += 1
        return {"removed": removed}

    @app.post("/api/system/report")
    def create_system_report(_csrf: CsrfDependency) -> dict[str, str]:
        try:
            config = json.loads(Path("config.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {"local_only": True, "ollama": {}}
        report = SystemStatusService(resolved_database_url, config).status()
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        root = Path("output/system_reports")
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"system-report-{datetime.now():%Y%m%d-%H%M%S}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
        return {"download_url": f"/api/download/{downloads.issue(path)}"}

    @app.get("/api/settings")
    def settings_api() -> dict[str, Any]:
        return PreferencesService().get()

    @app.put("/api/settings/preferences")
    def update_preferences(payload: dict[str, Any], _csrf: CsrfDependency) -> dict[str, Any]:
        return PreferencesService().update_preferences(payload)

    @app.put("/api/settings/ollama")
    def update_ollama(payload: dict[str, Any], _csrf: CsrfDependency) -> dict[str, Any]:
        return PreferencesService().update_ollama(payload)

    return app
