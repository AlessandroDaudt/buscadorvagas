"""Small dependency-free metrics registry persisted as a JSON snapshot."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._durations: dict[str, list[float]] = defaultdict(list)

    def increment(self, name: str, value: float = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, seconds: float) -> None:
        with self._lock:
            samples = self._durations[name]
            samples.append(max(0, seconds))
            if len(samples) > 1000:
                del samples[:-1000]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "counters": dict(sorted(self._counters.items())),
                "durations": {
                    name: {
                        "count": len(samples),
                        "sum_seconds": round(sum(samples), 6),
                        "max_seconds": round(max(samples), 6) if samples else 0,
                    }
                    for name, samples in sorted(self._durations.items())
                },
            }

    def write(self, path: Path = Path("state/metrics.json")) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.snapshot(), ensure_ascii=False, indent=2)
        descriptor, temporary = tempfile.mkstemp(prefix="metrics-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


metrics = MetricsRegistry()


def read_metrics_snapshot(path: Path = Path("state/metrics.json")) -> dict[str, Any]:
    """Read the persisted snapshot without accepting an unbounded file."""
    try:
        if path.stat().st_size > 1_048_576:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
