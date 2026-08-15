from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePath

from job_hunt.resume_import.markdown import detect_sections
from job_hunt.resume_import.models import ResumeValidationResult

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 2_000
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".md", ".txt"}
MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    ".md": {"text/markdown", "text/plain"},
    ".txt": {"text/plain"},
}


def sanitize_original_filename(filename: str) -> str:
    leaf = PurePath(filename.replace("\\", "/")).name
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", leaf).strip().strip(".")[:255]
    return cleaned or "resume"


def _validate_docx_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if len(entries) > MAX_DOCX_ENTRIES:
                raise ValueError("DOCX contains too many archive entries")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("file has a ZIP signature but is not a valid DOCX")
            lowered = {name.casefold() for name in names}
            if any("vbaproject" in name or name.endswith(".bin") for name in lowered):
                raise ValueError("macro-enabled or embedded binary documents are not accepted")
            expanded = sum(entry.file_size for entry in entries)
            if expanded > MAX_DOCX_EXPANDED_BYTES:
                raise ValueError("DOCX expanded content exceeds the safety limit")
            for entry in entries:
                if entry.file_size > 1_000_000 and entry.compress_size == 0:
                    raise ValueError("DOCX contains a suspicious compressed entry")
                if entry.compress_size and entry.file_size / entry.compress_size > 100:
                    raise ValueError("DOCX compression ratio exceeds the safety limit")
    except zipfile.BadZipFile as exc:
        raise ValueError("corrupted DOCX archive") from exc


def validate_upload(
    path: Path, original_filename: str, content_type: str | None
) -> tuple[str, str]:
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("empty upload")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError("resume upload exceeds the 15 MiB limit")
    safe_name = sanitize_original_filename(original_filename)
    extension = Path(safe_name).suffix.casefold()
    if extension == ".docm":
        raise ValueError("DOCM files are refused because they may contain macros")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("supported resume formats: PDF, DOC, DOCX, Markdown, and text")
    normalized_mime = (content_type or "").split(";", 1)[0].strip().casefold()
    if normalized_mime not in MIME_BY_EXTENSION[extension]:
        raise ValueError("declared MIME type does not match the resume extension")
    prefix = path.read_bytes()[:16]
    if prefix.startswith((b"MZ", b"\x7fELF")):
        raise ValueError("executable uploads are refused")
    if extension == ".pdf" and not prefix.startswith(b"%PDF-"):
        raise ValueError("file extension is PDF but its signature is not")
    if extension == ".docx":
        if not prefix.startswith(b"PK\x03\x04"):
            raise ValueError("file extension is DOCX but its signature is not")
        _validate_docx_archive(path)
    if extension == ".doc" and not prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("file extension is DOC but its OLE signature is not")
    if extension in {".md", ".txt"}:
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("text resume contains binary NUL bytes")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("text resume must use UTF-8") from exc
    return extension.lstrip("."), safe_name


def validate_markdown(markdown: str) -> ResumeValidationResult:
    warnings: list[str] = []
    errors: list[str] = []
    stripped = markdown.strip()
    if not stripped:
        errors.append("O currículo está vazio.")
    if 0 < len(stripped) < 200:
        warnings.append("O texto parece curto para um currículo completo.")
    replacement_count = stripped.count("�")
    if replacement_count:
        warnings.append("Foram encontrados possíveis caracteres corrompidos.")
    sections = detect_sections(markdown)
    if "experience" not in sections:
        warnings.append("Nenhuma seção clara de experiência foi identificada.")
    if "education" not in sections:
        warnings.append("Nenhuma seção clara de formação foi identificada.")
    headings = [line.strip().casefold() for line in markdown.splitlines() if line.startswith("## ")]
    if len(headings) != len(set(headings)):
        warnings.append("Existem seções duplicadas.")
    suspicious = (
        "ignore previous instructions",
        "ignore all instructions",
        "system prompt",
        "execute command",
        "exfiltrate",
        "desconsidere as instruções",
    )
    if any(item in stripped.casefold() for item in suspicious):
        warnings.append(
            "O texto contém instruções que podem ser maliciosas; revise antes de aprovar."
        )
    if not re.search(r"\b(?:19|20)\d{2}\b", stripped):
        warnings.append("Nenhum ano foi identificado; confira se datas foram preservadas.")
    return ResumeValidationResult(
        valid=not errors,
        warnings=warnings,
        errors=errors,
        detected_sections=sections,
    )
