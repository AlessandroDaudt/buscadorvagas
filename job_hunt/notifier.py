"""Best-effort local notifications. Notification failures never fail a scan."""

from __future__ import annotations

import os
import subprocess

from job_hunt.log import get_logger
from job_hunt.metrics import metrics

logger = get_logger("autopilot.notifier")


def send_windows_notification(title: str, message: str) -> bool:
    if os.name != "nt":
        logger.info("Windows notification skipped on non-Windows host")
        return False
    safe_title = title[:100].replace("'", "''")
    safe_message = message[:300].replace("'", "''")
    script = (
        "if (Get-Command New-BurntToastNotification -ErrorAction SilentlyContinue) { "
        f"New-BurntToastNotification -Text '{safe_title}', '{safe_message}'; exit 0 }}; exit 2"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            metrics.increment("notifications_sent_total")
            return True
        logger.info("Windows toast support is not installed; terminal report remains available")
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Local Windows notification failed")
    metrics.increment("notification_errors_total")
    return False
