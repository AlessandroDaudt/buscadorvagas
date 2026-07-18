"""FastAPI application for authenticated job review and application tracking."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from job_hunt.applications import ApplicationService
from job_hunt.documents.generator import DocumentGenerator
from job_hunt.domain.models import JobAnalysisResult, MasterResume, UnifiedJob, WorkMode
from job_hunt.persistence.database import Database, get_database_url
from job_hunt.persistence.documents import GeneratedDocumentRepository
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import (
    CompanyRecord,
    JobAnalysisRecord,
    JobRecord,
    JobSnapshotRecord,
    JobSourceRecord,
    ResumeMasterRecord,
    UserSettingRecord,
)
from job_hunt.web.queries import dashboard_summary, job_detail, list_jobs
from job_hunt.web.schemas import (
    ApplicationUpdate,
    DashboardSummary,
    DispositionUpdate,
    DocumentRequest,
    DocumentResponse,
    JobDetail,
    JobPage,
    SettingUpdate,
)
from job_hunt.web.security import (
    LoginRateLimiter,
    PanelSecuritySettings,
    PasswordVerifier,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    authenticated,
    ensure_csrf_token,
    validate_csrf,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_session(request: Request):
    database: Database = request.app.state.database
    with database.session() as session:
        yield session


def require_user(request: Request) -> str:
    if not authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    return str(request.session["user"])


def require_csrf_header(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    require_user(request)
    try:
        validate_csrf(request, x_csrf_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token") from exc


SessionDependency = Annotated[Session, Depends(get_session)]
UserDependency = Annotated[str, Depends(require_user)]
CsrfDependency = Annotated[None, Depends(require_csrf_header)]


def _client_identity(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_app(
    *,
    database_url: str | None = None,
    security_settings: PanelSecuritySettings | None = None,
) -> FastAPI:
    security = security_settings or PanelSecuritySettings.from_environment()
    resolved_database_url = database_url or get_database_url()
    upgrade_database(resolved_database_url)
    database = Database(resolved_database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        database.dispose()

    app = FastAPI(
        title="Autopilot Job Hunt",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.database = database
    app.state.security = security
    app.state.password_verifier = PasswordVerifier(security.password_hash)
    app.state.login_limiter = LoginRateLimiter()
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=security.allowed_hosts)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=security.max_request_bytes)
    app.add_middleware(
        SessionMiddleware,
        secret_key=security.session_secret,
        session_cookie="autopilot_session",
        max_age=security.session_max_age_seconds,
        same_site="strict",
        https_only=security.secure_cookie,
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/health", response_model=dict[str, str])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        token = ensure_csrf_token(request)
        return templates.TemplateResponse(request, "login.html", {"csrf_token": token, "error": None})

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        identity = _client_identity(request)
        limiter: LoginRateLimiter = request.app.state.login_limiter
        if not limiter.check(identity):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"csrf_token": ensure_csrf_token(request), "error": "Muitas tentativas. Aguarde."},
                status_code=429,
            )
        form = await request.form(max_fields=10, max_files=0)
        try:
            validate_csrf(request, str(form.get("csrf_token", "")))
        except ValueError:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"csrf_token": ensure_csrf_token(request), "error": "Sessão inválida."},
                status_code=403,
            )
        username = str(form.get("username", ""))[:100]
        password = str(form.get("password", ""))[:1000]
        verifier: PasswordVerifier = request.app.state.password_verifier
        if not verifier.verify(username == security.username, password):
            limiter.failure(identity)
            return templates.TemplateResponse(
                request,
                "login.html",
                {"csrf_token": ensure_csrf_token(request), "error": "Credenciais inválidas."},
                status_code=401,
            )
        limiter.success(identity)
        request.session.clear()
        request.session["user"] = security.username
        ensure_csrf_token(request)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form(max_fields=5, max_files=0)
        try:
            require_user(request)
            validate_csrf(request, str(form.get("csrf_token", "")))
        except (HTTPException, ValueError):
            raise HTTPException(status_code=403, detail="invalid logout request")
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard_page(request: Request):
        if not authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"csrf_token": ensure_csrf_token(request), "username": security.username},
        )

    @app.get("/api/dashboard", response_model=DashboardSummary)
    def dashboard_api(_user: UserDependency, session: SessionDependency) -> DashboardSummary:
        return dashboard_summary(session)

    @app.get("/api/jobs", response_model=JobPage)
    def jobs_api(
        _user: UserDependency,
        session: SessionDependency,
        search: Annotated[str | None, Query(max_length=200)] = None,
        company: Annotated[str | None, Query(max_length=300)] = None,
        modality: Annotated[str | None, Query(max_length=30)] = None,
        user_status: Annotated[str | None, Query(max_length=30)] = None,
        minimum_score: Annotated[float | None, Query(ge=0, le=100)] = None,
        sort: Literal["last_seen", "published", "title", "company", "score"] = "last_seen",
        direction: Literal["asc", "desc"] = "desc",
        page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> JobPage:
        return list_jobs(
            session,
            search=search,
            company=company,
            modality=modality,
            user_status=user_status,
            minimum_score=minimum_score,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/jobs/compare", response_model=list[JobDetail])
    def compare_jobs(
        _user: UserDependency,
        session: SessionDependency,
        ids: Annotated[str, Query(min_length=36, max_length=110)],
    ) -> list[JobDetail]:
        job_ids = ids.split(",")
        if not 2 <= len(job_ids) <= 3:
            raise HTTPException(status_code=400, detail="compare two or three jobs")
        results = []
        for job_id in job_ids:
            try:
                UUID(job_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid job ID") from exc
            detail = job_detail(session, job_id)
            if detail is None:
                raise HTTPException(status_code=404, detail="job not found")
            results.append(detail)
        return results

    @app.get("/api/jobs/{job_id}", response_model=JobDetail)
    def job_api(job_id: UUID, _user: UserDependency, session: SessionDependency) -> JobDetail:
        detail = job_detail(session, str(job_id))
        if detail is None:
            raise HTTPException(status_code=404, detail="job not found")
        return detail

    @app.post("/api/jobs/{job_id}/disposition", response_model=dict[str, str])
    def update_disposition(
        job_id: UUID,
        payload: DispositionUpdate,
        _csrf: CsrfDependency,
        session: SessionDependency,
    ) -> dict[str, str]:
        job = session.get(JobRecord, str(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        job.user_status = payload.status
        return {"status": job.user_status}

    @app.post("/api/jobs/{job_id}/application", response_model=dict[str, str])
    def update_application(
        job_id: UUID,
        payload: ApplicationUpdate,
        _csrf: CsrfDependency,
        session: SessionDependency,
    ) -> dict[str, str]:
        if session.get(JobRecord, str(job_id)) is None:
            raise HTTPException(status_code=404, detail="job not found")
        application = ApplicationService(session).set_status(
            str(job_id),
            payload.status,
            notes=payload.notes,
            allow_reopen=payload.allow_reopen,
        )
        return {"status": application.status}

    @app.post("/api/companies/{company_id}/silence", response_model=dict[str, bool])
    def silence_company(
        company_id: UUID,
        _csrf: CsrfDependency,
        session: SessionDependency,
    ) -> dict[str, bool]:
        company = session.get(CompanyRecord, str(company_id))
        if company is None:
            raise HTTPException(status_code=404, detail="company not found")
        company.silenced = True
        return {"silenced": True}

    @app.get("/api/settings", response_model=dict[str, Any])
    def get_settings(_user: UserDependency, session: SessionDependency) -> dict[str, Any]:
        records = session.scalars(select(UserSettingRecord).order_by(UserSettingRecord.key)).all()
        values = {
            record.key: ({"configured": True} if record.is_secret else record.value_data)
            for record in records
        }
        values["secret_status"] = {
            key: bool(os.getenv(key))
            for key in (
                "OPENAI_API_KEY",
                "OPENROUTER_API_KEY",
                "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY",
                "TELEGRAM_TOKEN",
            )
        }
        return values

    @app.post("/api/settings", response_model=dict[str, str])
    def update_setting(
        payload: SettingUpdate,
        _csrf: CsrfDependency,
        session: SessionDependency,
    ) -> dict[str, str]:
        record = session.scalar(select(UserSettingRecord).where(UserSettingRecord.key == payload.key))
        value = {"value": payload.value}
        if record is None:
            record = UserSettingRecord(key=payload.key, value_data=value, is_secret=False)
            session.add(record)
        else:
            record.value_data = value
            record.is_secret = False
        return {"status": "saved"}

    @app.post("/api/jobs/{job_id}/documents", response_model=DocumentResponse)
    def generate_documents(
        job_id: UUID,
        payload: DocumentRequest,
        _csrf: CsrfDependency,
        session: SessionDependency,
    ) -> DocumentResponse:
        job = session.get(JobRecord, str(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        resume_record = session.scalar(
            select(ResumeMasterRecord)
            .where(
                ResumeMasterRecord.approved.is_(True),
                ResumeMasterRecord.language == payload.language,
            )
            .order_by(ResumeMasterRecord.version.desc())
            .limit(1)
        )
        if resume_record is None:
            raise HTTPException(status_code=409, detail="no approved master resume for this language")
        source = session.scalar(
            select(JobSourceRecord)
            .where(JobSourceRecord.job_id == job.id)
            .order_by(JobSourceRecord.updated_at.desc())
            .limit(1)
        )
        snapshot = session.scalar(
            select(JobSnapshotRecord)
            .where(JobSnapshotRecord.job_id == job.id)
            .order_by(JobSnapshotRecord.collected_at.desc())
            .limit(1)
        )
        analysis_record = session.scalar(
            select(JobAnalysisRecord)
            .where(JobAnalysisRecord.job_id == job.id)
            .order_by(JobAnalysisRecord.created_at.desc())
            .limit(1)
        )
        if source is None or analysis_record is None:
            raise HTTPException(status_code=409, detail="job source and analysis are required")
        master = MasterResume.model_validate(resume_record.content_data)
        analysis = JobAnalysisResult.model_validate(analysis_record.explanation_data.get("analysis"))
        try:
            mode = WorkMode(job.modality)
        except ValueError:
            mode = WorkMode.UNKNOWN
        company_record = session.get(CompanyRecord, job.company_id)
        if company_record is None:
            raise HTTPException(status_code=409, detail="job company is missing")
        unified = UnifiedJob(
            id=UUID(job.id),
            source_name=source.source_name,
            original_url=HttpUrl(source.source_url),
            company=company_record.display_name,
            title=job.title,
            description=snapshot.description if snapshot else "",
            location=job.location,
            work_mode=mode,
            published_at=job.published_at,
            apply_url=HttpUrl(source.apply_url) if source.apply_url else None,
            country=job.country,
            seniority=job.seniority,
        )
        package = DocumentGenerator(master).generate(
            unified,
            analysis,
            create_docx=payload.create_docx,
            create_pdf=payload.create_pdf,
        )
        GeneratedDocumentRepository(session).save_package(
            job_id=job.id,
            resume_master_id=resume_record.id,
            package=package,
        )
        return DocumentResponse(
            version=package.manifest.version,
            files=package.manifest.files,
            changes=package.manifest.changes,
        )

    return app
