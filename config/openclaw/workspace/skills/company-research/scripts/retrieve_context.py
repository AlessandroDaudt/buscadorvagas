#!/usr/bin/env python3
"""Retrieve the most relevant local context chunks without exposing embeddings."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from urllib.request import Request, urlopen


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()
    context = Path(os.getenv("OPENCLAW_CONTEXT_DIR", "/opt/autopilot/context"))
    index = json.loads((context / "semantic_index.json").read_text(encoding="utf-8"))
    endpoint = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/") + "/api/embed"
    body = json.dumps(
        {
            "model": os.getenv("OLLAMA_EMBEDDING_MODEL", index.get("model", "qwen3-embedding:0.6b")),
            "input": args.query[:500],
            "keep_alive": "15m",
        }
    ).encode("utf-8")
    request = Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        vector = json.load(response)["embeddings"][0]
    ranked = sorted(
        (
            {
                "id": item["id"],
                "source": item["source"],
                "text": item["text"],
                "similarity": round(cosine(vector, item["embedding"]), 4),
            }
            for item in index.get("items", [])
        ),
        key=lambda item: item["similarity"],
        reverse=True,
    )[: max(1, min(args.top_k, 12))]
    print(json.dumps({"query": args.query, "matches": ranked}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
