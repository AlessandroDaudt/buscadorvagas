"""Structured logging with contextual identifiers and secret redaction."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_context: ContextVar[dict[str, str]] = ContextVar("autopilot_log_context", default={})
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|access[_-]?token|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def redact_text(value: str) -> str:
    return _SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _context.get().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        record.msg = redact_text(str(record.msg))
        if isinstance(record.args, dict):
            record.args = {
                key: redact_text(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        elif record.args:
            record.args = tuple(
                redact_text(value) if isinstance(value, str) else value for value in record.args
            )
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "module": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("run_id", "source_id", "job_id", "duration", "status"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


@contextmanager
def log_context(**values: str | None) -> Iterator[None]:
    """Bind safe identifiers to every log record produced in this context."""
    merged = dict(_context.get())
    merged.update({key: value for key, value in values.items() if value is not None})
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def get_logger(name: str = "autopilot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    output_format = os.getenv("LOG_FORMAT", "json").lower()
    formatter: logging.Formatter
    if output_format == "text":
        formatter = logging.Formatter(
            fmt="[%(asctime)s] %(levelname)-7s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = JsonFormatter()
    context_filter = ContextFilter()

    console_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(formatter)
    console.addFilter(context_filter)
    logger.addHandler(console)

    log_file = os.getenv("LOG_FILE", "scan.log").strip()
    if log_file:
        try:
            file_handler = logging.FileHandler(Path(log_file), encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(context_filter)
            logger.addHandler(file_handler)
        except OSError:
            pass

    return logger
