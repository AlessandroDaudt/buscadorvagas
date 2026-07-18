"""Extract resume candidates without treating extracted text as approved truth."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import Field

from job_hunt.domain.models import StrictModel

MAX_IMPORT_BYTES = 20 * 1024 * 1024


class ImportedResume(StrictModel):
    source_path: Path
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str
    extracted_text: str = Field(max_length=1_000_000)
    approved: bool = False


def import_resume_candidate(path: Path) -> ImportedResume:
    resolved = path.resolve(strict=True)
    size = resolved.stat().st_size
    if size > MAX_IMPORT_BYTES:
        raise ValueError("resume import exceeds the 20 MiB limit")
    raw = resolved.read_bytes()
    suffix = resolved.suffix.lower()
    if suffix in {".md", ".txt"}:
        text = raw.decode("utf-8")
        media_type = "text/markdown" if suffix == ".md" else "text/plain"
    elif suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("Install document support: pip install '.[documents]'") from exc
        document = Document(str(resolved))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install document support: pip install '.[documents]'") from exc
        reader = PdfReader(resolved)
        if len(reader.pages) > 100:
            raise ValueError("resume PDF exceeds the 100-page limit")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        media_type = "application/pdf"
    else:
        raise ValueError("supported resume formats: PDF, DOCX, Markdown, and text")
    return ImportedResume(
        source_path=resolved,
        source_hash=hashlib.sha256(raw).hexdigest(),
        media_type=media_type,
        extracted_text=text,
        approved=False,
    )
