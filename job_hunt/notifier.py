"""Legacy notification helpers with bounded requests and secret-safe logging."""

from __future__ import annotations

import urllib.parse
from typing import Any

import requests

from job_hunt.log import get_logger
from job_hunt.metrics import metrics

logger = get_logger("autopilot.notifier")


def send_whatsapp(phone: str, apikey: str, message: str) -> bool:
    encoded = urllib.parse.quote(message)
    url = f"https://api.textmebot.com/send.php?phone={phone}&text={encoded}&apikey={apikey}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            metrics.increment("notifications_sent_total")
            logger.info("WhatsApp notification sent")
            return True
        metrics.increment("notifications_failed_total")
        logger.warning(f"WhatsApp notification failed: HTTP {response.status_code}")
        return False
    except Exception as exc:
        metrics.increment("notifications_failed_total")
        logger.warning(f"WhatsApp notification error: {type(exc).__name__}")
        return False


def send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            metrics.increment("notifications_sent_total")
            logger.info("Telegram notification sent")
            return True
        metrics.increment("notifications_failed_total")
        logger.warning(f"Telegram notification failed: HTTP {response.status_code}")
        return False
    except Exception as exc:
        metrics.increment("notifications_failed_total")
        logger.warning(f"Telegram notification error: {type(exc).__name__}")
        return False
