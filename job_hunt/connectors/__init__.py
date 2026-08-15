"""Extensible job source connectors."""

from job_hunt.connectors.ashby import AshbyConnector
from job_hunt.connectors.base import CollectionResult, ConnectorContext, JobConnector
from job_hunt.connectors.greenhouse import GreenhouseConnector
from job_hunt.connectors.jsonld import JsonLdConnector
from job_hunt.connectors.lever import LeverConnector
from job_hunt.connectors.smartrecruiters import SmartRecruitersConnector
from job_hunt.connectors.workable import WorkableConnector

__all__ = [
    "AshbyConnector",
    "CollectionResult",
    "ConnectorContext",
    "GreenhouseConnector",
    "JsonLdConnector",
    "JobConnector",
    "LeverConnector",
    "SmartRecruitersConnector",
    "WorkableConnector",
]
