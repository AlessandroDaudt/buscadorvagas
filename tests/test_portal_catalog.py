import json

from job_hunt.portal_catalog import (
    CatalogCandidate,
    PortalCatalogService,
    _simplify_candidates,
    collect_source_candidates,
    source_definitions,
)
from job_hunt.web.application_services import CompanyConfigService


def test_simplify_parser_derives_board_and_never_retains_individual_job_url():
    content = """
<td><strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td>
<td><a href="https://jobs.ashbyhq.com/acme/abc-123/application?embed=true">Apply</a></td>
"""
    candidates = _simplify_candidates("simplify", "https://raw.example/list", content)
    assert len(candidates) == 1
    assert candidates[0].careers_url == "https://jobs.ashbyhq.com/acme"
    assert candidates[0].token == "acme"
    assert candidates[0].metadata["individual_url_discarded"] is True


def test_all_public_source_parsers_accept_only_canonical_portals_offline():
    simplify = """
<td><strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td>
<td><a href="https://jobs.ashbyhq.com/acme/abc-123/application?embed=true">Apply</a></td>
"""
    relocation = """
## Companies hiring internationally
Company name | Location(s) | Careers page
--- | --- | ---
| Relocate Co | Remote | https://jobs.lever.co/relocateco/12345 |
# End
"""
    state = """
# Source: fixture
name,slug,industry,ats_system,hiring_volume_tier,top_roles,source_url,verified
"Legacy Co","legacy","Tech","Workday","small","engineer","https://example.test",true
"Known Co","known","Tech","Greenhouse","small","engineer","https://example.test",true
"""

    def fetch(url):
        if "state-of-ats" in url:
            return state.strip()
        if "tech-jobs-with-relocation" in url:
            return relocation
        if "SimplifyJobs" in url:
            return simplify
        if url.endswith(".json"):
            return '["acme"]'
        return "openapply-acme\n"

    candidates, errors = collect_source_candidates(fetch)
    assert not errors
    assert source_definitions()["tech_jobs_with_relocation"][1] == "CC0-1.0"
    assert {candidate.source_id for candidate in candidates} == {
        "feashliaa_job_board_aggregator",
        "openapply",
        "state_of_ats_2026",
        "tech_jobs_with_relocation",
        "simplifyjobs_summer_2026",
        "simplifyjobs_new_grad",
    }
    assert any(
        candidate.careers_url == "https://jobs.lever.co/relocateco" for candidate in candidates
    )
    assert all(
        not candidate.careers_url or "/application" not in candidate.careers_url
        for candidate in candidates
    )


def test_catalog_import_is_incremental_preserves_manual_records_and_creates_backup(
    tmp_path, monkeypatch
):
    companies = tmp_path / "companies.json"
    companies.write_text(
        json.dumps(
            [
                {
                    "name": "Manual Acme",
                    "careers_url": "https://jobs.lever.co/manualacme",
                    "connector": "lever",
                    "site": "manualacme",
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    candidates = [
        CatalogCandidate(
            "fixture_greenhouse",
            "https://raw.example/greenhouse",
            "MIT",
            "Beta",
            "greenhouse",
            "beta",
            "https://job-boards.greenhouse.io/beta",
            {},
        ),
        CatalogCandidate(
            "fixture_manual",
            "https://raw.example/lever",
            "MIT",
            "Manual Acme",
            "lever",
            "manualacme",
            "https://jobs.lever.co/manualacme",
            {},
        ),
        CatalogCandidate(
            "fixture_invalid",
            "https://raw.example/lever",
            "MIT",
            "Bad",
            "lever",
            "bad",
            "https://jobs.lever.co/bad",
            {},
        ),
        CatalogCandidate(
            "fixture_unsupported",
            "https://raw.example/state",
            "MIT",
            "Internal",
            "Workday",
            "internal",
            None,
            {},
        ),
    ]
    monkeypatch.setattr(
        "job_hunt.portal_catalog.collect_source_candidates", lambda _fetch: (candidates, {})
    )

    def validator(candidate):
        return (candidate.token != "bad", "fixture_verified", candidate.careers_url)

    service = PortalCatalogService(
        tmp_path / "config" / "portal_catalog.json",
        companies,
        validator=validator,
        company_service_factory=lambda path: CompanyConfigService(
            path, resolver=lambda _host: ["8.8.8.8"]
        ),
        activation_limit=10,
    )
    summary = service.import_and_activate()
    assert summary["activated"] == 1
    assert summary["invalid"] == 1
    assert summary["incompatible"] == 1
    assert summary["duplicates"] == 1
    records = json.loads(companies.read_text(encoding="utf-8"))
    assert [record["name"] for record in records] == ["Manual Acme", "Beta"]
    assert service.import_and_activate()["duplicates"] >= 1
    assert (tmp_path / "config" / "portal_catalog.json.bak").exists()
    catalog = json.loads((tmp_path / "config" / "portal_catalog.json").read_text(encoding="utf-8"))
    assert {entry["status"] for entry in catalog["entries"]} >= {
        "active",
        "invalid",
        "disabled",
        "linked_existing",
    }


def test_catalog_advances_pending_validation_in_later_batches(tmp_path, monkeypatch):
    companies = tmp_path / "companies.json"
    companies.write_text("[]", encoding="utf-8")
    candidates = [
        CatalogCandidate(
            "fixture",
            "https://raw.example/a",
            "MIT",
            "Alpha",
            "greenhouse",
            "alpha",
            "https://job-boards.greenhouse.io/alpha",
            {},
        ),
        CatalogCandidate(
            "fixture",
            "https://raw.example/b",
            "MIT",
            "Beta",
            "lever",
            "beta",
            "https://jobs.lever.co/beta",
            {},
        ),
    ]
    monkeypatch.setattr(
        "job_hunt.portal_catalog.collect_source_candidates", lambda _fetch: (candidates, {})
    )
    service = PortalCatalogService(
        tmp_path / "config" / "portal_catalog.json",
        companies,
        validator=lambda candidate: (True, "fixture_verified", candidate.careers_url),
        company_service_factory=lambda path: CompanyConfigService(
            path, resolver=lambda _host: ["8.8.8.8"]
        ),
        activation_limit=1,
    )
    assert service.import_and_activate()["activated"] == 1
    assert service.import_and_activate()["activated"] == 1
    assert len(json.loads(companies.read_text(encoding="utf-8"))) == 2
