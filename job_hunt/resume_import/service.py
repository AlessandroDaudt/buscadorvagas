from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from uuid import uuid4

from job_hunt.resume_import.docx import extract_docx
from job_hunt.resume_import.legacy_doc import convert_legacy_doc
from job_hunt.resume_import.markdown import clean_text, detect_sections, text_to_markdown
from job_hunt.resume_import.models import ResumeImportResult
from job_hunt.resume_import.pdf import extract_pdf
from job_hunt.resume_import.validation import MAX_UPLOAD_BYTES, validate_upload


class ResumeImportService:
    def __init__(self, *, maximum_bytes: int = MAX_UPLOAD_BYTES) -> None:
        self.maximum_bytes = maximum_bytes

    def import_bytes(
        self,
        content: bytes,
        *,
        original_filename: str,
        content_type: str | None,
    ) -> tuple[ResumeImportResult, str, str]:
        if len(content) > self.maximum_bytes:
            raise ValueError(
                f"resume upload exceeds the {self.maximum_bytes // (1024 * 1024)} MiB limit"
            )
        source_hash = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory(prefix="autopilot-resume-") as temporary:
            root = Path(temporary)
            suffix = Path(original_filename.replace("\\", "/")).suffix.casefold()
            path = root / f"{uuid4().hex}{suffix}"
            path.write_bytes(content)
            source_format, safe_name = validate_upload(path, original_filename, content_type)
            warnings: list[str] = []
            metadata: dict[str, object] = {"bytes": len(content), "temporary_removed": True}
            if source_format in {"md", "txt"}:
                extracted = content.decode("utf-8")
                markdown = (
                    clean_text(extracted) + "\n"
                    if source_format == "md"
                    else text_to_markdown(extracted)
                )
                method = "utf8-markdown" if source_format == "md" else "utf8-text-deterministic"
            elif source_format == "pdf":
                extracted, warnings, extracted_metadata = extract_pdf(path)
                metadata.update(extracted_metadata)
                markdown = text_to_markdown(extracted)
                method = "pypdf-deterministic"
            elif source_format == "docx":
                extracted, warnings, extracted_metadata = extract_docx(path)
                metadata.update(extracted_metadata)
                markdown = clean_text(extracted) + "\n"
                method = "python-docx-structured"
            else:
                converted = convert_legacy_doc(path, root / "converted")
                extracted, warnings, extracted_metadata = extract_docx(converted)
                metadata.update(extracted_metadata)
                metadata["converted_from"] = "doc"
                markdown = clean_text(extracted) + "\n"
                method = "libreoffice-headless-to-docx"
            result = ResumeImportResult(
                markdown=markdown,
                source_format=source_format,
                warnings=warnings,
                detected_sections=detect_sections(markdown),
                metadata=metadata,
                extraction_method=method,
            )
            return result, safe_name, source_hash
