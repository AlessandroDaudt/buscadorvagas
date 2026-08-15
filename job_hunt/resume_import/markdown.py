from __future__ import annotations

import re

SECTION_NAMES = {
    "summary": ("summary", "professional summary", "resumo", "resumo profissional", "perfil"),
    "skills": (
        "skills",
        "core skills",
        "competências",
        "competencias",
        "habilidades",
        "tecnologias",
    ),
    "experience": (
        "experience",
        "professional experience",
        "experiência",
        "experiencia profissional",
    ),
    "education": ("education", "formação", "formacao acadêmica", "formacao academica"),
    "certifications": ("certifications", "certificações", "certificacoes"),
    "languages": ("languages", "idiomas"),
    "projects": ("projects", "projetos"),
}


def clean_text(value: str, *, maximum: int = 1_000_000) -> str:
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.splitlines())
    value = re.sub(r"\n{4,}", "\n\n\n", value).strip()
    if len(value) > maximum:
        raise ValueError(f"extracted resume exceeds the {maximum}-character limit")
    return value


def detect_sections(markdown: str) -> list[str]:
    normalized_lines = [
        re.sub(r"^#+\s*", "", line).strip().casefold() for line in markdown.splitlines()
    ]
    found: list[str] = []
    for canonical, aliases in SECTION_NAMES.items():
        if any(line in aliases for line in normalized_lines):
            found.append(canonical)
    return found


def text_to_markdown(text: str) -> str:
    """Conservative conversion: add structure without rewording extracted facts."""
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("resume contains no extractable text")
    lines: list[str] = []
    first_content = True
    for original in cleaned.splitlines():
        line = original.strip()
        if not line:
            lines.append("")
            continue
        folded = line.rstrip(":").casefold()
        is_section = any(folded in aliases for aliases in SECTION_NAMES.values())
        if is_section:
            lines.append(f"## {line.rstrip(':')}")
        elif first_content and len(line) <= 120:
            lines.append(f"# {line}")
            first_content = False
        elif re.match(r"^[•·▪◦*-]\s+", line):
            item = re.sub(r"^[•·▪◦*-]\s+", "", line)
            lines.append(f"- {item}")
        else:
            lines.append(line)
            first_content = False
    return clean_text("\n".join(lines)) + "\n"
