"""Extensible job source connectors."""

from job_hunt.connectors.base import CollectionResult, ConnectorContext, JobConnector
from job_hunt.connectors.greenhouse import GreenhouseConnector
from job_hunt.connectors.lever import LeverConnector

__all__ = [
    "CollectionResult",
    "ConnectorContext",
    "GreenhouseConnector",
    "JobConnector",
    "LeverConnector",
]

