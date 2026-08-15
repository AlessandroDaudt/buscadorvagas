from types import SimpleNamespace

import pytest

from job_hunt.discovery import (
    DiscoveryRegistryService,
    PortalSuggestion,
    PublicPortalDiscoveryService,
    build_linkedin_search_url,
)
from job_hunt.domain.models import SearchPreferences
from job_hunt.ollama import OllamaSettings
from job_hunt.persistence.database import Database
from job_hunt.persistence.migration import upgrade_database


class FakeOllama:
    def __init__(self, _settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def chat(self, *_args, **_kwargs):
        return SimpleNamespace(
            content=(
                '{"candidates":[{"company_name":"Acme","careers_url":"https://jobs.acme.test/careers",'
                '"rationale":"security roles","confidence":0.8},{"company_name":"LinkedIn",'
                '"careers_url":"https://www.linkedin.com/jobs/search/","rationale":"blocked",'
                '"confidence":1},{"company_name":"Aggregator","careers_url":"https://www.indeed.com/jobs",'
                '"rationale":"blocked","confidence":1}]}'
            )
        )


class FakeHttp:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get_text(self, url, **_kwargs):
        if url.endswith("/robots.txt"):
            return "User-agent: *\nAllow: /"
        return "<html><title>Acme Careers</title><body>Jobs and careers</body></html>"


def _preferences() -> SearchPreferences:
    return SearchPreferences(
        priority_roles=["Security Engineer"],
        priority_technologies=["EDR"],
        monitored_companies=["Known Co"],
    )


def test_local_discovery_verifies_public_portal_and_excludes_linkedin():
    service = PublicPortalDiscoveryService(
        OllamaSettings(base_url="http://ollama:11434"),
        ollama_factory=FakeOllama,
        http_factory=FakeHttp,
    )
    suggestions, warnings = service.discover(_preferences())
    assert [item.company_name for item in suggestions] == ["Acme"]
    assert suggestions[0].connector == "generic_html"
    assert suggestions[0].evidence_data["automated_linkedin_access"] is False
    assert any("LinkedIn" in warning for warning in warnings)
    assert any("aggregators" in warning for warning in warnings)


def test_registry_requires_review_and_generates_manual_linkedin_alert(tmp_path):
    url = f"sqlite:///{(tmp_path / 'discovery.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    suggestion = PortalSuggestion(
        company_name="Acme",
        careers_url="https://jobs.acme.test/careers",
        connector="generic_html",
        allowed_domains=["jobs.acme.test"],
        rationale="Matches security roles",
        confidence=0.8,
        evidence_data={"verified": True},
    )
    with database.session() as session:
        registry = DiscoveryRegistryService(session)
        proposal = registry.save_suggestions([suggestion])[0]
        assert proposal["state"] == "pending"
        assert registry.list_proposals(state="pending")[0]["id"] == proposal["id"]
        alert = registry.create_alert(
            {
                "name": "Security Brazil",
                "keywords": ["Security Engineer", "EDR"],
                "location": "Brasil",
                "cadence_days": 2,
            }
        )
        assert alert["search_url"].startswith("https://www.linkedin.com/jobs/search/?")
        assert "Security+Engineer" in alert["search_url"]
        opened = registry.mark_alert_opened(alert["id"])
        assert opened["last_opened_at"] is not None
        assert registry.set_alert_enabled(alert["id"], False)["enabled"] is False
    database.dispose()


def test_linkedin_alert_rejects_empty_keywords():
    with pytest.raises(ValueError, match="keyword"):
        build_linkedin_search_url([], "Brasil")
