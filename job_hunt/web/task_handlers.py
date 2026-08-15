"""Task handlers that reuse the same Python application services as CLI and MCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from job_hunt.discovery import run_public_portal_discovery
from job_hunt.doctor import collect_checks
from job_hunt.main import load_companies, load_config
from job_hunt.ollama import OllamaClient, OllamaSettings
from job_hunt.operations import execute_scan
from job_hunt.persistence.database import Database
from job_hunt.portal_catalog import PortalCatalogService
from job_hunt.scanner import run_scan
from job_hunt.web.application_services import (
    CompanyConfigService,
    DocumentApplicationService,
    ExportService,
    LocalDocumentStudio,
)
from job_hunt.web.ranking import RankingRefreshService
from job_hunt.web.tasks import TaskContext, TaskHandler


def build_task_handlers(database_url: str) -> dict[str, TaskHandler]:
    def scan(context: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(5, "Carregando configuração local")
        config = load_config()
        companies = load_companies()
        selected = {str(item).casefold() for item in payload.get("companies", []) if item}
        if selected:
            companies = [
                item for item in companies if str(item.get("name", "")).casefold() in selected
            ]
        if not companies:
            raise ValueError("no enabled company was selected")
        context.progress(10, f"Executando busca em {len(companies)} empresas")
        execute_scan(config, companies, run_scan)
        context.progress(95, "Consolidando relatório")
        report_path = Path("state/last_run_report.json")
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        return {"report": report, "report_path": str(report_path)}

    def doctor(context: TaskContext, _payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(10, "Executando diagnóstico local")
        checks = collect_checks(load_config())
        context.progress(95, "Preparando relatório")
        return {
            "checks": [check.__dict__ for check in checks],
            "failures": sum(check.status == "FAIL" for check in checks),
            "warnings": sum(check.status == "WARN" for check in checks),
        }

    def warmup(context: TaskContext, _payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(10, "Carregando modelo na GPU")
        settings = OllamaSettings.from_config(load_config())
        with OllamaClient(settings) as client:
            result = client.chat(
                [{"role": "user", "content": "Reply only with: LOCAL_OK"}],
                temperature=0,
                max_tokens=16,
            )
            running = client.running_models()
        return {
            "response_ok": bool(result.content.strip()),
            "duration_seconds": result.duration_seconds,
            "running_models": running,
        }

    def embedding(context: TaskContext, _payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(20, "Testando embeddings locais")
        settings = OllamaSettings.from_config(load_config())
        with OllamaClient(settings) as client:
            vectors = client.embeddings("autopilot local embedding test")
        return {"vectors": len(vectors), "dimensions": len(vectors[0]) if vectors else 0}

    def export(context: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(20, "Consultando vagas")
        database = Database(database_url)
        try:
            with database.session() as session:
                path = ExportService(session).generate(
                    file_format=str(payload.get("format", "csv")),
                    minimum_score=float(payload.get("minimum_score", 0)),
                    selected_ids=[str(item) for item in payload.get("selected_ids", [])],
                    days=int(payload.get("days", 0)),
                )
        finally:
            database.dispose()
        return {"path": str(path), "format": path.suffix.lstrip(".")}

    def documents(context: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(10, "Preparando documentos")
        database = Database(database_url)
        try:
            with database.session() as session:
                result = DocumentApplicationService(session).generate(
                    str(payload["job_id"]),
                    language=str(payload.get("language", "en")),
                    create_docx=bool(payload.get("create_docx", True)),
                    create_pdf=bool(payload.get("create_pdf", False)),
                )
        finally:
            database.dispose()
        return result

    def company_test(context: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(10, "Validando política de rede")
        return CompanyConfigService().test_source(str(payload["company_id"]))

    def ai_document(context: TaskContext, payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(10, "Preparando contexto factual local")
        config = load_config()
        database = Database(database_url)
        try:
            with database.session() as session:
                result = LocalDocumentStudio(session, config).generate(
                    str(payload["job_id"]),
                    str(payload["document_type"]),
                    language=str(payload.get("language", "pt-BR")),
                )
        finally:
            database.dispose()
        return result

    def discover_portals(context: TaskContext, _payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(5, "Lendo preferências locais de pesquisa")
        context.progress(20, "Consultando o modelo Ollama local para sugerir portais públicos")
        result = run_public_portal_discovery(database_url)
        context.progress(90, "Propostas persistidas para aprovação humana")
        return result

    def refresh_job_ranking(context: TaskContext, _payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(5, "Carregando vagas e preferências atuais")
        result = RankingRefreshService(database_url).refresh(context.progress)
        context.progress(95, "Novo ranking salvo")
        return result

    def import_portal_catalog(context: TaskContext, _payload: dict[str, Any]) -> dict[str, Any]:
        context.progress(5, "Baixando manifestos públicos do catálogo")
        context.progress(20, "Normalizando, deduplicando e preservando atribuições")
        try:
            activation_limit = int(os.getenv("PORTAL_CATALOG_ACTIVATION_LIMIT", "120"))
        except ValueError:
            activation_limit = 120
        result = PortalCatalogService(activation_limit=activation_limit).import_and_activate()
        context.progress(95, "Catálogo salvo e portais compatíveis ativados")
        return result

    return {
        "scan": scan,
        "doctor": doctor,
        "warmup": warmup,
        "embedding": embedding,
        "export": export,
        "documents": documents,
        "company_test": company_test,
        "ai_document": ai_document,
        "discover_portals": discover_portals,
        "refresh_job_ranking": refresh_job_ranking,
        "import_portal_catalog": import_portal_catalog,
    }
