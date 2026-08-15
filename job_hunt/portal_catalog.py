"""Incremental, attribution-preserving catalog of public career portals.

The catalog is deliberately separate from ``companies.json``.  Only records that
have a canonical public portal, pass the existing URL/robots safeguards and map
to an existing connector are copied into the scanner configuration.  No existing
company record is ever edited by this module.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from job_hunt.connectors.registry import SUPPORTED_CONNECTORS
from job_hunt.http_client import RobotsPolicy, SafeHttpClient
from job_hunt.security.urls import _default_resolver, validate_public_http_url
from job_hunt.state_store import atomic_write_json, load_json_state

if TYPE_CHECKING:
    pass

CATALOG_SCHEMA_VERSION = 1
DEFAULT_ACTIVATION_LIMIT = 120
RAW_GITHUB_HOST = "raw.githubusercontent.com"
_BLOCKED_HOST_SUFFIXES = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "simplify.jobs",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
)
_ATS_TO_CONNECTOR = {
    "greenhouse": "greenhouse",
    "lever": "lever",
    "ashby": "ashby",
    "smartrecruiters": "smartrecruiters",
    "workable": "workable",
    "json-ld": "generic_html",
    "html": "generic_html",
    "static html": "generic_html",
}


@dataclass(frozen=True)
class CatalogCandidate:
    source_id: str
    source_url: str
    license: str
    name: str
    ats: str
    token: str
    careers_url: str | None
    metadata: dict[str, Any]


SourceFetcher = Callable[[str], str]
PortalValidator = Callable[[CatalogCandidate], tuple[bool, str, str | None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _display_name_from_token(token: str) -> str:
    cleaned = re.sub(r"[_-]+", " ", token).strip()
    return cleaned.title() or token


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold()


def _host_is_blocked(host: str) -> bool:
    return not host or any(
        host == suffix or host.endswith(f".{suffix}") for suffix in _BLOCKED_HOST_SUFFIXES
    )


def _is_individual_job_url(url: str) -> bool:
    parsed = urlsplit(url)
    path = parsed.path.casefold()
    # Canonical ATS boards may contain /jobs, but an ID after it identifies one job.
    return bool(
        re.search(r"/(?:jobs?|positions?)/[^/?#]+(?:/|$)", path)
        or "/application" in path
        or "job_app" in path
        or "token=" in parsed.query.casefold()
    )


def _canonical_ats_portal(url: str) -> tuple[str, str, str] | None:
    """Turn a known ATS application URL into a board URL, never retain the job URL."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.split("/") if part]
    if (host.endswith("greenhouse.io") and parts) or host == "boards.greenhouse.io":
        if host == "boards.greenhouse.io" and (not parts or parts[0] == "embed"):
            return None
        token = parts[0]
        return "greenhouse", token, f"https://job-boards.greenhouse.io/{token}"
    if host.endswith("lever.co") and len(parts) >= 1:
        token = parts[0]
        return "lever", token, f"https://jobs.lever.co/{token}"
    if host.endswith("ashbyhq.com") and parts:
        token = parts[0]
        return "ashby", token, f"https://jobs.ashbyhq.com/{token}"
    if host.endswith("workable.com") and parts:
        token = parts[0]
        return "workable", token, f"https://apply.workable.com/{token}"
    if host.endswith("smartrecruiters.com") and parts:
        token = parts[0]
        return "smartrecruiters", token, f"https://careers.smartrecruiters.com/{token}"
    return None


def _github_slug_candidates(
    source_id: str, source_url: str, license_name: str, content: str, ats: str
) -> list[CatalogCandidate]:
    try:
        values = json.loads(content)
    except json.JSONDecodeError:
        values = [line.strip() for line in content.splitlines()]
    if not isinstance(values, list):
        return []
    hosts = {
        "greenhouse": "https://job-boards.greenhouse.io/{token}",
        "lever": "https://jobs.lever.co/{token}",
        "ashby": "https://jobs.ashbyhq.com/{token}",
    }
    template = hosts[ats]
    result: list[CatalogCandidate] = []
    for value in values:
        token = str(value).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,150}", token):
            continue
        result.append(
            CatalogCandidate(
                source_id,
                source_url,
                license_name,
                _display_name_from_token(token),
                ats,
                token,
                template.format(token=token),
                {"source_format": "ats_slug_list"},
            )
        )
    return result


def _state_of_ats_candidates(source_url: str, content: str) -> list[CatalogCandidate]:
    rows = csv.DictReader(line for line in content.splitlines() if not line.startswith("#"))
    result: list[CatalogCandidate] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        ats = str(row.get("ats_system") or "").strip()
        slug = str(row.get("slug") or "").strip()
        if not name:
            continue
        # This dataset reports ATS attribution but intentionally provides a checker URL,
        # not an official careers URL.  Keep it attributed and disabled until another
        # source supplies a public portal; never turn its checker into a scan source.
        result.append(
            CatalogCandidate(
                "state_of_ats_2026",
                source_url,
                "MIT",
                name,
                ats,
                slug,
                None,
                {
                    "verified": str(row.get("verified") or "").casefold() == "true",
                    "source_format": "ats_csv",
                },
            )
        )
    return result


def _relocation_candidates(source_url: str, content: str) -> list[CatalogCandidate]:
    result: list[CatalogCandidate] = []
    table = False
    for line in content.splitlines():
        if "Companies hiring internationally" in line:
            table = True
            continue
        if table and line.startswith("#"):
            break
        if not table or "|" not in line or "http" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0].casefold() == "company name":
            continue
        match = re.search(r"https?://[^\s)>]+", cells[-1])
        if not match:
            continue
        url = match.group(0).rstrip(".,")
        canonical = _canonical_ats_portal(url)
        ats, token, portal = canonical if canonical else ("HTML", "", url)
        result.append(
            CatalogCandidate(
                "tech_jobs_with_relocation",
                source_url,
                "CC0-1.0",
                cells[0],
                ats,
                token,
                portal,
                {"location": cells[1], "source_format": "markdown_company_table"},
            )
        )
    return result


def _simplify_candidates(source_id: str, source_url: str, content: str) -> list[CatalogCandidate]:
    result: list[CatalogCandidate] = []
    current_company: str | None = None
    company_pattern = re.compile(r"<td><strong><a[^>]*>([^<]+)</a></strong></td>", re.I)
    url_pattern = re.compile(r"https?://[^\"'<>\s]+", re.I)
    for line in content.splitlines():
        company_match = company_pattern.search(line)
        if company_match:
            current_company = html.unescape(company_match.group(1)).strip()
            continue
        if not current_company:
            continue
        for raw_url in url_pattern.findall(line):
            canonical = _canonical_ats_portal(html.unescape(raw_url))
            if canonical is None:
                continue
            ats, token, portal = canonical
            result.append(
                CatalogCandidate(
                    source_id,
                    source_url,
                    "NOASSERTION",
                    current_company,
                    ats,
                    token,
                    portal,
                    {
                        "source_format": "derived_from_individual_ats_link",
                        "individual_url_discarded": True,
                    },
                )
            )
            break
    return result


def source_definitions() -> dict[str, tuple[str, str]]:
    """Source URL and license recorded alongside every imported candidate."""
    return {
        "feashliaa_job_board_aggregator": (
            "https://github.com/Feashliaa/job-board-aggregator",
            "MIT",
        ),
        "openapply": ("https://github.com/edwarddgao/openapply", "NOASSERTION"),
        "state_of_ats_2026": ("https://github.com/Kayvan-Zahiri/state-of-ats-2026", "MIT"),
        "tech_jobs_with_relocation": (
            "https://github.com/AndrewStetsenko/tech-jobs-with-relocation",
            "CC0-1.0",
        ),
        "simplifyjobs_summer_2026": (
            "https://github.com/SimplifyJobs/Summer2026-Internships",
            "NOASSERTION",
        ),
        "simplifyjobs_new_grad": (
            "https://github.com/SimplifyJobs/New-Grad-Positions",
            "NOASSERTION",
        ),
    }


class PublicCatalogFetcher:
    """Fetch source manifests through the same SSRF, redirect and robots policy."""

    def __call__(self, url: str) -> str:
        host = _host(url)
        if host != RAW_GITHUB_HOST:
            raise ValueError("catalog source must use raw.githubusercontent.com")
        with SafeHttpClient(
            connector="portal_catalog_source", timeout_seconds=30, rate_limit_seconds=0.2
        ) as client:
            robots = RobotsPolicy(client)
            robots.require_allowed(url, allowed_hosts={host})
            return client.get_text(url, allowed_hosts={host}, cache_ttl_seconds=3600)


def collect_source_candidates(
    fetch: SourceFetcher,
) -> tuple[list[CatalogCandidate], dict[str, str]]:
    """Load only documented raw manifests.  Failed source fetches do not erase prior catalog data."""
    errors: dict[str, str] = {}
    candidates: list[CatalogCandidate] = []
    raw = "https://raw.githubusercontent.com"
    specs = (
        (
            "feashliaa_job_board_aggregator",
            "MIT",
            "greenhouse",
            f"{raw}/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json",
        ),
        (
            "feashliaa_job_board_aggregator",
            "MIT",
            "lever",
            f"{raw}/Feashliaa/job-board-aggregator/main/data/lever_companies.json",
        ),
        (
            "feashliaa_job_board_aggregator",
            "MIT",
            "ashby",
            f"{raw}/Feashliaa/job-board-aggregator/main/data/ashby_companies.json",
        ),
        (
            "openapply",
            "NOASSERTION",
            "greenhouse",
            f"{raw}/edwarddgao/openapply/main/slugs/cc_greenhouse_FINAL.txt",
        ),
        (
            "openapply",
            "NOASSERTION",
            "lever",
            f"{raw}/edwarddgao/openapply/main/slugs/cc_lever_FINAL.txt",
        ),
        (
            "openapply",
            "NOASSERTION",
            "ashby",
            f"{raw}/edwarddgao/openapply/main/slugs/cc_ashby_FINAL.txt",
        ),
    )
    for source_id, license_name, ats, url in specs:
        try:
            candidates.extend(
                _github_slug_candidates(source_id, url, license_name, fetch(url), ats)
            )
        except Exception as exc:
            errors[f"{source_id}:{ats}"] = str(exc)[:300]
    state_url = f"{raw}/Kayvan-Zahiri/state-of-ats-2026/main/data/companies.csv"
    try:
        candidates.extend(_state_of_ats_candidates(state_url, fetch(state_url)))
    except Exception as exc:
        errors["state_of_ats_2026"] = str(exc)[:300]
    relocation_url = f"{raw}/AndrewStetsenko/tech-jobs-with-relocation/main/README.md"
    try:
        candidates.extend(_relocation_candidates(relocation_url, fetch(relocation_url)))
    except Exception as exc:
        errors["tech_jobs_with_relocation"] = str(exc)[:300]
    for source_id, repository, branch in (
        ("simplifyjobs_summer_2026", "Summer2026-Internships", "dev"),
        ("simplifyjobs_new_grad", "New-Grad-Positions", "dev"),
    ):
        url = f"{raw}/SimplifyJobs/{repository}/{branch}/README.md"
        try:
            candidates.extend(_simplify_candidates(source_id, url, fetch(url)))
        except Exception as exc:
            errors[source_id] = str(exc)[:300]
    return candidates, errors


class CatalogPortalValidator:
    """Validate canonical portals without retaining individual job links."""

    def __init__(self, *, resolver=_default_resolver) -> None:
        self.resolver = resolver

    def __call__(self, candidate: CatalogCandidate) -> tuple[bool, str, str | None]:
        if not candidate.careers_url:
            return False, "missing_official_portal_url", None
        host = _host(candidate.careers_url)
        if _host_is_blocked(host) or _is_individual_job_url(candidate.careers_url):
            return False, "blocked_or_individual_job_url", None
        try:
            validate_public_http_url(
                candidate.careers_url, resolver=self.resolver, allowed_hosts={host}
            )
            with SafeHttpClient(
                connector="portal_catalog_validation", timeout_seconds=15, rate_limit_seconds=0.05
            ) as client:
                robots = RobotsPolicy(client)
                robots.require_allowed(candidate.careers_url, allowed_hosts={host})
                # SafeHttpClient validates every redirect.  A small bounded read is enough
                # to confirm that a public portal answers without storing its content.
                response = client.get_text(
                    candidate.careers_url, allowed_hosts={host}, cache_ttl_seconds=3600
                )
            return (
                True,
                "verified_https_public_redirects_robots",
                response and candidate.careers_url,
            )
        except Exception as exc:
            return False, f"validation_failed:{type(exc).__name__}", None


class PortalCatalogService:
    def __init__(
        self,
        catalog_path: Path = Path("config/portal_catalog.json"),
        companies_path: Path = Path("companies.json"),
        *,
        fetcher: SourceFetcher | None = None,
        validator: PortalValidator | None = None,
        company_service_factory: Callable[[Path], Any] | None = None,
        activation_limit: int = DEFAULT_ACTIVATION_LIMIT,
    ) -> None:
        self.catalog_path = catalog_path
        self.companies_path = companies_path
        self.fetcher = fetcher or PublicCatalogFetcher()
        self.validator = validator or CatalogPortalValidator()
        self.company_service_factory = company_service_factory
        self.activation_limit = max(1, min(int(activation_limit), 500))

    def _read_catalog(self) -> dict[str, Any]:
        catalog = load_json_state(self.catalog_path, {})
        if not isinstance(catalog, dict):
            raise ValueError("portal catalog must contain an object")
        entries = catalog.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("portal catalog entries must contain an array")
        return {"schema_version": CATALOG_SCHEMA_VERSION, "entries": entries, **catalog}

    @staticmethod
    def _key(candidate: CatalogCandidate) -> str:
        if candidate.careers_url:
            return f"url:{candidate.careers_url.rstrip('/').casefold()}"
        return f"missing-url:{_normal_name(candidate.name)}:{candidate.ats.casefold()}:{candidate.token.casefold()}"

    @staticmethod
    def _company_keys(records: Iterable[dict[str, Any]]) -> set[str]:
        keys: set[str] = set()
        for item in records:
            url = str(item.get("careers_url") or "").rstrip("/").casefold()
            if url:
                keys.add(f"url:{url}")
            name = _normal_name(str(item.get("name") or ""))
            host = _host(str(item.get("careers_url") or ""))
            connector = str(item.get("connector") or "").casefold()
            token = str(
                item.get("board_token")
                or item.get("site")
                or item.get("account")
                or item.get("company_id")
                or ""
            ).casefold()
            if name:
                keys.add(f"name:{name}")
            if (
                connector in {"greenhouse", "lever", "ashby", "smartrecruiters", "workable"}
                and token
            ):
                keys.add(f"ats-token:{connector}:{token}")
            elif host:
                keys.add(f"domain:{host}")
        return keys

    @staticmethod
    def _activation_key(entry: dict[str, Any]) -> str:
        connector = str(entry.get("connector") or "").casefold()
        token = str(entry.get("token") or "").casefold()
        if connector in {"greenhouse", "lever", "ashby", "smartrecruiters", "workable"} and token:
            return f"ats-token:{connector}:{token}"
        return f"domain:{entry['domain']}"

    @staticmethod
    def _entry(candidate: CatalogCandidate, *, status: str, reason: str) -> dict[str, Any]:
        connector = _ATS_TO_CONNECTOR.get(candidate.ats.casefold())
        return {
            "id": hashlib.sha256(PortalCatalogService._key(candidate).encode("utf-8")).hexdigest()[
                :24
            ],
            "name": candidate.name[:300],
            "normalized_name": _normal_name(candidate.name),
            "ats": candidate.ats[:120],
            "token": candidate.token[:300],
            "careers_url": candidate.careers_url,
            "domain": _host(candidate.careers_url or ""),
            "connector": connector,
            "enabled": status == "active",
            "status": status,
            "reason": reason,
            "source_refs": [
                {
                    "source": candidate.source_id,
                    "source_url": candidate.source_url,
                    "license": candidate.license,
                    "imported_at": _now(),
                }
            ],
            "metadata": candidate.metadata,
            "created_at": _now(),
            "updated_at": _now(),
        }

    @staticmethod
    def _is_compatible(entry: dict[str, Any]) -> bool:
        return str(entry.get("connector") or "") in SUPPORTED_CONNECTORS

    def status(self) -> dict[str, Any]:
        catalog = self._read_catalog()
        entries = [item for item in catalog["entries"] if isinstance(item, dict)]
        by_status = Counter(str(item.get("status") or "unknown") for item in entries)
        by_source: dict[str, int] = defaultdict(int)
        by_ats: dict[str, int] = defaultdict(int)
        for entry in entries:
            by_ats[str(entry.get("ats") or "unknown")] += 1
            for ref in entry.get("source_refs", []):
                if isinstance(ref, dict):
                    by_source[str(ref.get("source") or "unknown")] += 1
        return {
            "entries": len(entries),
            "by_status": dict(sorted(by_status.items())),
            "by_source": dict(sorted(by_source.items())),
            "by_ats": dict(sorted(by_ats.items())),
            "last_import": catalog.get("last_import"),
        }

    def import_and_activate(self) -> dict[str, Any]:
        catalog = self._read_catalog()
        entries = [item for item in catalog["entries"] if isinstance(item, dict)]
        existing_by_key = {
            str(item.get("catalog_key")): item for item in entries if item.get("catalog_key")
        }
        existing_companies = load_json_state(self.companies_path, [])
        if not isinstance(existing_companies, list):
            raise ValueError("companies.json must contain an array")
        company_keys = self._company_keys(
            item for item in existing_companies if isinstance(item, dict)
        )
        candidates, source_errors = collect_source_candidates(self.fetcher)
        summary: dict[str, Any] = {
            "added": 0,
            "updated": 0,
            "duplicates": 0,
            "invalid": 0,
            "incompatible": 0,
            "activated": 0,
            "pending_validation": 0,
            "source_errors": source_errors,
            "by_source": defaultdict(Counter),
            "by_ats": Counter(),
        }
        pending: list[tuple[CatalogCandidate, dict[str, Any], str]] = []
        seen_batch: set[str] = set()
        for candidate in candidates:
            key = self._key(candidate)
            source_counts: Counter[str] = summary["by_source"][candidate.source_id]
            source_counts["seen"] += 1
            if key in seen_batch:
                current = existing_by_key.get(key)
                if current is not None:
                    refs = current.setdefault("source_refs", [])
                    ref_key = (candidate.source_id, candidate.source_url)
                    if not any(
                        (ref.get("source"), ref.get("source_url")) == ref_key
                        for ref in refs
                        if isinstance(ref, dict)
                    ):
                        refs.append(
                            {
                                "source": candidate.source_id,
                                "source_url": candidate.source_url,
                                "license": candidate.license,
                                "imported_at": _now(),
                            }
                        )
                        current["updated_at"] = _now()
                        summary["updated"] += 1
                        source_counts["updated"] += 1
                summary["duplicates"] += 1
                source_counts["duplicates"] += 1
                continue
            seen_batch.add(key)
            existing = existing_by_key.get(key)
            if existing is not None:
                if not candidate.careers_url and existing.get("status") == "disabled":
                    expected_reason = (
                        "unsupported_ats_connector"
                        if candidate.ats.casefold() not in _ATS_TO_CONNECTOR
                        else "missing_official_portal_url"
                    )
                    if existing.get("reason") != expected_reason:
                        existing["reason"] = expected_reason
                        existing["updated_at"] = _now()
                        summary["updated"] += 1
                        source_counts["updated"] += 1
                refs = existing.setdefault("source_refs", [])
                ref_key = (candidate.source_id, candidate.source_url)
                if not any(
                    (ref.get("source"), ref.get("source_url")) == ref_key
                    for ref in refs
                    if isinstance(ref, dict)
                ):
                    refs.append(
                        {
                            "source": candidate.source_id,
                            "source_url": candidate.source_url,
                            "license": candidate.license,
                            "imported_at": _now(),
                        }
                    )
                    existing["updated_at"] = _now()
                    summary["updated"] += 1
                    source_counts["updated"] += 1
                else:
                    summary["duplicates"] += 1
                    source_counts["duplicates"] += 1
                continue
            if not candidate.careers_url:
                reason = (
                    "unsupported_ats_connector"
                    if candidate.ats.casefold() not in _ATS_TO_CONNECTOR
                    else "missing_official_portal_url"
                )
                entry = self._entry(candidate, status="disabled", reason=reason)
                entry["catalog_key"] = key
                entries.append(entry)
                existing_by_key[key] = entry
                summary["added"] += 1
                if reason == "unsupported_ats_connector":
                    summary["incompatible"] += 1
                source_counts["added"] += 1
                if reason == "unsupported_ats_connector":
                    source_counts["incompatible"] += 1
                summary["by_ats"][candidate.ats] += 1
                continue
            entry = self._entry(
                candidate, status="pending_validation", reason="awaiting_safe_validation"
            )
            entry["catalog_key"] = key
            entries.append(entry)
            existing_by_key[key] = entry
            summary["added"] += 1
            source_counts["added"] += 1
            summary["by_ats"][candidate.ats] += 1
            pending.append((candidate, entry, key))

        # Existing pending entries are included first on later incremental runs.
        pending_keys = {key for _candidate, _entry, key in pending}
        for entry in entries:
            if (
                len(pending) >= self.activation_limit * 4
                or entry.get("status") != "pending_validation"
            ):
                continue
            candidate = CatalogCandidate(
                "previous_catalog",
                "",
                "",
                str(entry.get("name") or ""),
                str(entry.get("ats") or ""),
                str(entry.get("token") or ""),
                str(entry.get("careers_url") or "") or None,
                {},
            )
            key = self._key(candidate)
            if key not in pending_keys:
                pending.append((candidate, entry, key))
                pending_keys.add(key)

        activate_candidates = [item for item in pending if self._is_compatible(item[1])][
            : self.activation_limit
        ]
        for candidate, entry, key in pending:
            if not self._is_compatible(entry):
                entry.update(
                    {
                        "status": "disabled",
                        "enabled": False,
                        "reason": "unsupported_ats_connector",
                        "updated_at": _now(),
                    }
                )
                summary["incompatible"] += 1
                continue
            if (candidate, entry, key) not in activate_candidates:
                summary["pending_validation"] += 1
                continue
            valid, reason, _final_url = self.validator(candidate)
            if not valid:
                entry.update(
                    {"status": "invalid", "enabled": False, "reason": reason, "updated_at": _now()}
                )
                summary["invalid"] += 1
                continue
            activation_key = self._activation_key(entry)
            if (
                key in company_keys
                or f"name:{entry['normalized_name']}" in company_keys
                or activation_key in company_keys
            ):
                entry.update(
                    {
                        "status": "linked_existing",
                        "enabled": False,
                        "reason": "manual_or_existing_company_preserved",
                        "updated_at": _now(),
                    }
                )
                summary["duplicates"] += 1
                continue
            connector = str(entry["connector"])
            payload: dict[str, Any] = {
                "name": entry["name"],
                "careers_url": entry["careers_url"],
                "connector": connector,
                "enabled": True,
                "allowed_domains": [entry["domain"]],
            }
            if connector in {"greenhouse", "ashby"}:
                payload["board_token"] = entry["token"]
            elif connector == "lever":
                payload["site"] = entry["token"]
            elif connector == "smartrecruiters":
                payload["company_id"] = entry["token"]
            elif connector == "workable":
                payload["account"] = entry["token"]
            try:
                factory = self.company_service_factory
                if factory is None:
                    from job_hunt.web.application_services import CompanyConfigService

                    factory = CompanyConfigService
                factory(self.companies_path).add(payload)
            except ValueError:
                entry.update(
                    {
                        "status": "linked_existing",
                        "enabled": False,
                        "reason": "manual_or_existing_company_preserved",
                        "updated_at": _now(),
                    }
                )
                summary["duplicates"] += 1
                continue
            company_keys.update({key, f"name:{entry['normalized_name']}", activation_key})
            entry.update(
                {
                    "status": "active",
                    "enabled": True,
                    "reason": reason,
                    "activated_at": _now(),
                    "updated_at": _now(),
                }
            )
            summary["activated"] += 1

        catalog["entries"] = entries
        catalog["schema_version"] = CATALOG_SCHEMA_VERSION
        catalog["last_import"] = {
            "at": _now(),
            "source_errors": source_errors,
            "summary": {
                key: value for key, value in summary.items() if key not in {"by_source", "by_ats"}
            },
        }
        atomic_write_json(self.catalog_path, catalog)
        return {
            **{key: value for key, value in summary.items() if key not in {"by_source", "by_ats"}},
            "by_source": {source: dict(counts) for source, counts in summary["by_source"].items()},
            "by_ats": dict(summary["by_ats"]),
            "catalog": self.status(),
        }
