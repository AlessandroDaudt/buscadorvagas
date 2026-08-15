#!/usr/bin/env python3
"""Query private SearXNG and return a bounded, filtered result set."""

from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

BLOCKED = {
    "facebook.com", "glassdoor.com", "indeed.com", "instagram.com",
    "linkedin.com", "reddit.com", "tiktok.com", "x.com", "youtube.com",
}


def blocked(host: str) -> bool:
    return any(host == item or host.endswith("." + item) for item in BLOCKED)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    base = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")
    url = base + "/search?" + urlencode(
        {"q": args.query[:500], "format": "json", "safesearch": "1", "language": "auto"}
    )
    request = Request(
        url,
        headers={
            "User-Agent": "OpenClaw-Local-Research/1.0",
            "X-Forwarded-For": "127.0.0.1",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except Exception as exc:
        raise SystemExit(
            "SearXNG indisponivel; use web_search/web_fetch como fallback: " + str(exc)
        ) from exc
    results = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        target = str(item.get("url") or "")
        host = (urlsplit(target).hostname or "").casefold()
        if not target.startswith("https://") or blocked(host):
            continue
        results.append(
            {
                "title": str(item.get("title") or "")[:300],
                "url": target[:2000],
                "snippet": str(item.get("content") or "")[:1200],
                "engine": str(item.get("engine") or "")[:100],
            }
        )
        if len(results) >= max(1, min(args.limit, 20)):
            break
    print(json.dumps({"query": args.query, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
