from __future__ import annotations

from pathlib import Path
from typing import Any


class NoSearchableTextError(ValueError):
    pass


def extract_pdf(path: Path, *, max_pages: int = 100) -> tuple[str, list[str], dict[str, Any]]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import FileNotDecryptedError, PdfReadError
    except ImportError as exc:
        raise RuntimeError("Install document support: pip install '.[documents]'") from exc
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ValueError("PDF protegido por senha não pode ser importado")
        if len(reader.pages) > max_pages:
            raise ValueError(f"resume PDF exceeds the {max_pages}-page limit")
        pages: list[str] = []
        empty_pages: list[int] = []
        for index, page in enumerate(reader.pages, 1):
            extracted = page.extract_text() or ""
            pages.append(extracted)
            if not extracted.strip():
                empty_pages.append(index)
        text = "\n\n".join(pages).strip()
        if not text:
            raise NoSearchableTextError(
                "Não foi encontrado texto pesquisável neste PDF. OCR local é necessário."
            )
        warnings = []
        if empty_pages:
            warnings.append(f"Páginas sem texto pesquisável: {', '.join(map(str, empty_pages))}")
        raw_metadata: Any = reader.metadata or {}
        metadata = {
            "pages": len(reader.pages),
            "title": str(raw_metadata.get("/Title") or "")[:500],
            "author": str(raw_metadata.get("/Author") or "")[:500],
            "pages_without_text": empty_pages,
            "links_followed": False,
        }
        return text, warnings, metadata
    except (FileNotDecryptedError, PdfReadError) as exc:
        raise ValueError("PDF is corrupted or password protected") from exc
