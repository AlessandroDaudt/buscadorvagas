"""Recoverable UTF-8 JSON state with atomic replacement and bounded backups."""

from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_hunt.log import get_logger

logger = get_logger("autopilot.state")
SCHEMA_VERSION = 1


def load_json_state(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        corrupt = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            shutil.copy2(path, corrupt)
        except OSError:
            logger.warning("Corrupt state could not be backed up: %s", path.name)
        logger.warning(
            "Corrupt state recovered with safe default: %s (%s)", path.name, type(exc).__name__
        )
        return deepcopy(default)


def atomic_write_json(path: Path, value: Any, *, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            logger.warning("State backup could not be created: %s", path.name)
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, path)
        except OSError as exc:
            # A single-file Docker bind mount is itself a mount point and Linux
            # refuses rename(2) over it with EBUSY. Keep the normal atomic path
            # everywhere else; for that explicit case preserve a recovery copy
            # in the persisted state volume and fsync an in-place replacement.
            if exc.errno not in {errno.EBUSY, errno.EXDEV, errno.EACCES, errno.EPERM}:
                raise
            recovery_root = Path("state/config_backups")
            recovery_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            recovery = recovery_root / f"{path.name}.{stamp}.bak"
            if path.exists():
                shutil.copy2(path, recovery)
            payload_bytes = temporary.read_bytes()
            with path.open("wb") as handle:
                handle.write(payload_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            logger.warning(
                "Atomic rename unavailable for bind-mounted file; persisted recovery backup: %s",
                recovery,
            )
    finally:
        temporary.unlink(missing_ok=True)
