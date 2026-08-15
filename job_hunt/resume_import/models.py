from __future__ import annotations

from typing import Any

from pydantic import Field

from job_hunt.domain.models import StrictModel


class ResumeImportResult(StrictModel):
    markdown: str = Field(min_length=1, max_length=1_000_000)
    source_format: str
    warnings: list[str] = Field(default_factory=list, max_length=200)
    detected_sections: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extraction_method: str


class ResumeValidationResult(StrictModel):
    valid: bool
    warnings: list[str]
    errors: list[str]
    detected_sections: list[str]
