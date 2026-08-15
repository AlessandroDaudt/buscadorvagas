#!/usr/bin/env python3
"""Validate a bounded research batch and atomically submit it to Autopilot."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

MAX_CANDIDATES = 12


def _strings(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:limit]


def _candidate(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("cada candidato deve ser um objeto JSON")
    name = str(value.get("company_name", "")).strip()[:200]
    url = str(value.get("careers_url", "")).strip()[:2000]
    rationale = str(value.get("rationale", "")).strip()[:2000]
    try:
        confidence = float(value.get("confidence", 0.5))
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence deve ser um numero") from exc
    parsed = urlsplit(url)
    if not name or not rationale:
        raise ValueError("company_name e rationale sao obrigatorios")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("careers_url deve ser uma URL HTTPS valida")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence deve estar entre 0 e 1")
    profile = value.get("company_profile")
    profile = profile if isinstance(profile, dict) else {}
    return {
        "company_name": name,
        "careers_url": url,
        "rationale": rationale,
        "confidence": confidence,
        "search_sources": _strings(value.get("search_sources"), 20),
        "matched_profile_signals": _strings(value.get("matched_profile_signals"), 30),
        "company_profile": {
            "industry": str(profile.get("industry") or "").strip()[:200] or None,
            "company_size": str(profile.get("company_size") or "").strip()[:100] or None,
            "hiring_countries": _strings(profile.get("hiring_countries"), 30),
            "accepts_brazil_remote": (
                profile.get("accepts_brazil_remote")
                if isinstance(profile.get("accepts_brazil_remote"), bool)
                else None
            ),
            "modalities": _strings(profile.get("modalities"), 10),
            "tech_signals": _strings(profile.get("tech_signals"), 50),
            "languages": _strings(profile.get("languages"), 20),
            "open_roles_count": profile.get("open_roles_count"),
            "source_urls": [
                item for item in _strings(profile.get("source_urls"), 20)
                if item.startswith("https://")
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JSON com candidatos pesquisados")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    values = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_CANDIDATES:
        raise ValueError("envie entre 1 e 12 candidatos")
    candidates = [_candidate(item) for item in values]
    batch_id = f"research-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:10]}"
    exchange = Path(os.getenv("OPENCLAW_EXCHANGE_DIR", "/opt/autopilot/exchange"))
    inbox = exchange / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    output = inbox / f"{batch_id}.json"
    document = {
        "schema_version": 1,
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates,
    }
    descriptor, temporary_name = tempfile.mkstemp(prefix=".research-", suffix=".tmp", dir=inbox)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        output.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"batch_id": batch_id, "state": "submitted"}))


if __name__ == "__main__":
    main()
