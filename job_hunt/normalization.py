"""Deterministic normalization of untrusted job data."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_hunt.domain.models import WorkMode

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "ref",
    "referrer",
    "source",
    "src",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(html.unescape(value))
    parser.close()
    return normalize_whitespace(" ".join(parser.parts))


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return normalize_whitespace(normalized)


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    query.sort(key=lambda item: (item[0], item[1]))
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def description_hash(description: str) -> str:
    normalized = normalize_match_text(strip_html(description))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def detect_work_mode(*values: str | None) -> WorkMode:
    text = normalize_match_text(" ".join(value or "" for value in values))
    if re.search(r"\bhybrid\b|\bhibrid[oa]\b", text):
        return WorkMode.HYBRID
    if re.search(r"\bremote\b|\bremot[oa]\b|work from home", text):
        return WorkMode.REMOTE
    if re.search(r"\bon site\b|\bonsite\b|\bpresencial\b", text):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN
