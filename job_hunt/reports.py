"""Structured end-of-run report."""

from __future__ import annotations

from pydantic import Field

from job_hunt.domain.models import StrictModel


class SearchRunReport(StrictModel):
    sources_consulted: list[str] = Field(default_factory=list, max_length=500)
    source_errors: dict[str, str] = Field(default_factory=dict)
    jobs_collected: int = Field(default=0, ge=0)
    jobs_new: int = Field(default=0, ge=0)
    jobs_updated: int = Field(default=0, ge=0)
    duplicates_removed: int = Field(default=0, ge=0)
    jobs_analyzed: int = Field(default=0, ge=0)
    jobs_above_threshold: int = Field(default=0, ge=0)
    estimated_ai_cost_usd: float = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list, max_length=500)
    warnings: list[str] = Field(default_factory=list, max_length=500)

    def as_text(self) -> str:
        failed = ", ".join(sorted(self.source_errors)) or "nenhuma"
        return (
            "Resumo da busca\n"
            f"Fontes consultadas: {len(self.sources_consulted)}; com erro: {failed}\n"
            f"Vagas coletadas: {self.jobs_collected}; novas: {self.jobs_new}; "
            f"atualizadas: {self.jobs_updated}; duplicatas: {self.duplicates_removed}\n"
            f"Analisadas: {self.jobs_analyzed}; acima do score mínimo: {self.jobs_above_threshold}\n"
            f"Custo estimado de IA: USD {self.estimated_ai_cost_usd:.4f}; "
            f"duração: {self.duration_seconds:.1f}s\n"
            f"Erros: {len(self.errors)}; avisos: {len(self.warnings)}"
        )
