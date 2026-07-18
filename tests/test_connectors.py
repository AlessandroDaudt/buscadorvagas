from datetime import datetime, timezone

from job_hunt.connectors.base import ConnectorContext
from job_hunt.connectors.greenhouse import GreenhouseConnector
from job_hunt.connectors.lever import LeverConnector
from job_hunt.domain.models import ContractType, WorkMode


class FakeJsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, *, allowed_hosts=None):
        self.calls.append((url, allowed_hosts))
        return self.payload


def test_greenhouse_connector_normalizes_public_feed():
    client = FakeJsonClient(
        {
            "jobs": [
                {
                    "id": 123,
                    "title": "Endpoint Security Engineer",
                    "location": {"name": "Remote - Brazil"},
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                    "content": "<p>Microsoft Defender for Endpoint</p>",
                }
            ]
        }
    )
    context = ConnectorContext(collected_at=datetime(2026, 7, 18, tzinfo=timezone.utc))
    result = GreenhouseConnector("acme", "Acme", client).collect(context)

    assert result.status == "success"
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.external_id == "123"
    assert job.work_mode == WorkMode.REMOTE
    assert job.description == "Microsoft Defender for Endpoint"
    assert client.calls[0][1] == {"boards-api.greenhouse.io"}


def test_lever_connector_normalizes_public_feed():
    client = FakeJsonClient(
        [
            {
                "id": "abc-123",
                "text": "IAM Engineer",
                "hostedUrl": "https://jobs.lever.co/acme/abc-123",
                "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
                "createdAt": 1_752_796_800_000,
                "categories": {"location": "Remote, LATAM", "commitment": "Full-time"},
                "descriptionPlain": "Identity and access management",
            }
        ]
    )
    result = LeverConnector("acme", "Acme", client).collect(ConnectorContext())

    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.external_id == "abc-123"
    assert job.work_mode == WorkMode.REMOTE
    assert job.contract_type == ContractType.FULL_TIME
    assert client.calls[0][1] == {"api.lever.co"}


def test_connector_reports_invalid_remote_shape_without_raising():
    result = LeverConnector("acme", "Acme", FakeJsonClient({"not": "a list"})).collect(
        ConnectorContext()
    )
    assert result.status == "failed"
    assert result.errors

