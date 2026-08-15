#!/usr/bin/env python3
"""Apply deterministic hard filters, deduplication and preference-aware ranking."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def terms(value: object) -> list[str]:
    return [str(item).casefold() for item in value] if isinstance(value, list) else []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    context = Path(os.getenv("OPENCLAW_CONTEXT_DIR", "/opt/autopilot/context"))
    preferences = json.loads((context / "search_preferences.json").read_text(encoding="utf-8"))
    learned = json.loads((context / "learned_preferences.json").read_text(encoding="utf-8"))
    feedback = json.loads((context / "company_feedback.json").read_text(encoding="utf-8"))
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    values = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    known_urls = {
        str(item.get("careers_url") or "").rstrip("/").casefold()
        for key in ("monitored_companies", "approved_proposals", "rejected_proposals", "pending_proposals")
        for item in feedback.get(key, [])
    }
    roles = terms(preferences.get("priority_roles"))
    technologies = terms(preferences.get("priority_technologies"))
    filters = preferences.get("filters", {})
    learned_weights = {
        str(item.get("signal")): float(item.get("weight") or 0) * float(item.get("confidence") or 0)
        for item in learned.get("learned_signals", [])
    }
    ranked = []
    rejected = []
    for raw in values[:50]:
        text = " ".join(
            [str(raw.get("company_name") or ""), str(raw.get("rationale") or ""),
             " ".join(raw.get("matched_profile_signals") or [])]
        ).casefold()
        url = str(raw.get("careers_url") or "").rstrip("/")
        profile = raw.get("company_profile") if isinstance(raw.get("company_profile"), dict) else {}
        modalities = terms(profile.get("modalities"))
        reasons = []
        if url.casefold() in known_urls:
            rejected.append({"candidate": raw, "reason": "duplicate"})
            continue
        if modalities == ["onsite"] and not filters.get("include_onsite", True):
            rejected.append({"candidate": raw, "reason": "hard_filter_onsite"})
            continue
        role_hits = [item for item in roles if item and item in text][:5]
        tech_hits = [item for item in technologies if item and item in text][:8]
        if role_hits:
            reasons.append("role_match")
        if tech_hits:
            reasons.append("technology_match")
        if profile.get("accepts_brazil_remote") is True:
            reasons.append("remote_brazil")
        score = float(raw.get("confidence", 0.5)) * 45 + min(25, len(role_hits) * 8)
        score += min(20, len(tech_hits) * 3) + (10 if "remote_brazil" in reasons else 0)
        score += sum(learned_weights.get(reason, 0) * 5 for reason in reasons)
        candidate = dict(raw)
        candidate["fit_score"] = round(max(0, min(100, score)), 1)
        candidate["matched_profile_signals"] = list(dict.fromkeys(
            list(raw.get("matched_profile_signals") or []) + reasons + role_hits + tech_hits
        ))[:30]
        ranked.append(candidate)
    ranked.sort(key=lambda item: item["fit_score"], reverse=True)
    result = {"candidates": ranked[:12], "rejected": rejected, "evaluated": len(values)}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
