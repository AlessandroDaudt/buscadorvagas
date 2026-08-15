from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlsplit

from job_hunt.connectors.ashby import AshbyConnector
from job_hunt.connectors.base import CompanyConfig, JobConnector, TextHttpClient
from job_hunt.connectors.generic_html import GenericHtmlConnector
from job_hunt.connectors.greenhouse import GreenhouseConnector
from job_hunt.connectors.lever import LeverConnector
from job_hunt.connectors.smartrecruiters import SmartRecruitersConnector
from job_hunt.connectors.workable import WorkableConnector
from job_hunt.http_client import RobotsPolicy

SUPPORTED_CONNECTORS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable", "generic_html", "auto"}


def normalize_company(raw: Mapping[str, Any]) -> CompanyConfig:
    company: CompanyConfig = {
        "name": str(raw.get("name") or "").strip(),
        "careers_url": str(raw.get("careers_url") or "").strip(),
        "search_domain": str(raw.get("search_domain") or "").strip(),
        "location": str(raw.get("location") or "").strip(),
        "region": str(raw.get("region") or "Global").strip(),
        "connector": str(raw.get("connector") or "auto").strip().casefold(),
        "enabled": bool(raw.get("enabled", True)),
        "allowed_domains": [str(item).strip().casefold() for item in raw.get("allowed_domains", [])],
    }
    if raw.get("board_token"):
        company["board_token"] = str(raw["board_token"]).strip()
    if raw.get("site"):
        company["site"] = str(raw["site"]).strip()
    if raw.get("account"):
        company["account"] = str(raw["account"]).strip()
    if raw.get("company_id"):
        company["company_id"] = str(raw["company_id"]).strip()
    if not company["name"] or not company["careers_url"].startswith("https://"):
        raise ValueError("Company requires name and an HTTPS careers_url")
    host = (urlsplit(company["careers_url"]).hostname or "").casefold()
    if host and host not in company["allowed_domains"]:
        company["allowed_domains"].append(host)
    return company


def _token(
    company: CompanyConfig,
    key: Literal["board_token", "site", "account", "company_id"],
) -> str:
    if company.get(key):
        return str(company[key])
    parts = [part for part in urlsplit(company["careers_url"]).path.split("/") if part]
    return parts[0] if parts else ""


def detect_connector(company: CompanyConfig) -> str:
    explicit = company.get("connector", "auto")
    if explicit != "auto":
        if explicit not in SUPPORTED_CONNECTORS:
            raise ValueError(f"Unsupported connector: {explicit}")
        return explicit
    host = (urlsplit(company["careers_url"]).hostname or "").casefold()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    if "workable.com" in host:
        return "workable"
    return "generic_html"


def build_connector(
    raw_company: Mapping[str, Any], client: TextHttpClient, robots: RobotsPolicy
) -> JobConnector:
    company = normalize_company(raw_company)
    name = detect_connector(company)
    if name == "greenhouse":
        return GreenhouseConnector(_token(company, "board_token"), company["name"], client)
    if name == "lever":
        host = (urlsplit(company["careers_url"]).hostname or "").casefold()
        return LeverConnector(_token(company, "site"), company["name"], client, eu_instance="eu.lever.co" in host)
    if name == "ashby":
        return AshbyConnector(_token(company, "board_token"), company["name"], client)
    if name == "smartrecruiters":
        return SmartRecruitersConnector(
            _token(company, "company_id"), company["name"], client
        )
    if name == "workable":
        return WorkableConnector(_token(company, "account"), company["name"], client)
    return GenericHtmlConnector(company, client, robots)
