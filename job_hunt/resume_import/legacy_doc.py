from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class LegacyDocConverterUnavailable(ValueError):
    pass


def convert_legacy_doc(path: Path, output_directory: Path, *, timeout_seconds: int = 60) -> Path:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise LegacyDocConverterUnavailable(
            "A conversão de arquivos .doc requer o componente local LibreOffice. "
            "Envie o arquivo em DOCX, PDF ou Markdown, ou instale o conversor local."
        )
    output_directory.mkdir(parents=True, exist_ok=True)
    profile = output_directory / "libreoffice-profile"
    try:
        subprocess.run(
            [
                executable,
                f"-env:UserInstallation=file:///{profile.as_posix()}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                "--convert-to",
                "docx",
                "--outdir",
                str(output_directory),
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("legacy DOC conversion exceeded its local timeout") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError("LibreOffice could not safely convert the legacy DOC") from exc
    converted = output_directory / f"{path.stem}.docx"
    if not converted.is_file() or not converted.read_bytes().startswith(b"PK\x03\x04"):
        raise ValueError("LibreOffice did not produce a valid DOCX")
    return converted
