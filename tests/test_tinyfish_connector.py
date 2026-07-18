from job_hunt.connectors import tinyfish
from job_hunt.connectors.base import ConnectorContext
from job_hunt.connectors.tinyfish import TinyFishConnector


def test_tinyfish_adapter_uses_legacy_discovery_and_normalizes(monkeypatch):
    discovered = [
        {
            "url": "https://example.com/jobs/1",
            "company": "Example",
            "title": "Endpoint Security Engineer",
            "location": "Remote - Brazil",
        }
    ]
    monkeypatch.setattr(tinyfish, "discover_job_urls", lambda *_args: discovered)
    monkeypatch.setattr(
        tinyfish,
        "fetch_job_details",
        lambda _client, jobs: [{**jobs[0], "content": "Microsoft Defender"}],
    )

    connector = TinyFishConnector(object(), {"name": "Example"})
    result = connector.collect(ConnectorContext())

    assert result.status == "success"
    assert len(result.jobs) == 1
    assert result.jobs[0].source_name == "tinyfish"
    assert result.jobs[0].company == "Example"


def test_tinyfish_adapter_reports_failure(monkeypatch):
    monkeypatch.setattr(
        tinyfish,
        "discover_job_urls",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("source down")),
    )
    result = TinyFishConnector(object(), {"name": "Example"}).collect(ConnectorContext())
    assert result.status == "failed"
    assert result.errors[0].retryable is True
