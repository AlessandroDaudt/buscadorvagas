"""Rich Telegram alerts and authenticated callback payloads."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
from datetime import datetime, timezone
from typing import Any, Literal

import requests
from pydantic import BaseModel, Field

from job_hunt.domain.models import JobAnalysisResult, SalaryEstimateResult, UnifiedJob
from job_hunt.metrics import metrics

CallbackAction = Literal[
    "view",
    "save",
    "discard",
    "plan",
    "submitted",
    "resume",
    "cover",
    "mute_company",
    "mute_role",
]

_ACTION_CODES: dict[CallbackAction, str] = {
    "view": "v",
    "save": "s",
    "discard": "d",
    "plan": "p",
    "submitted": "a",
    "resume": "r",
    "cover": "c",
    "mute_company": "mc",
    "mute_role": "mr",
}
_CODE_ACTIONS = {code: action for action, code in _ACTION_CODES.items()}


class VerifiedCallback(BaseModel):
    action: CallbackAction
    job_id: str = Field(min_length=1, max_length=36)


class CallbackSigner:
    def __init__(self, secret: str) -> None:
        if len(secret.encode()) < 32:
            raise ValueError("Telegram callback secret must contain at least 32 bytes")
        self._secret = secret.encode()

    def sign(self, action: CallbackAction, job_id: str) -> str:
        if len(job_id) > 36 or not job_id:
            raise ValueError("invalid job identifier")
        value = f"{_ACTION_CODES[action]}:{job_id}"
        signature = base64.urlsafe_b64encode(
            hmac.new(self._secret, value.encode(), hashlib.sha256).digest()[:8]
        ).decode().rstrip("=")
        callback = f"{value}:{signature}"
        if len(callback.encode()) > 64:
            raise ValueError("callback exceeds Telegram's 64-byte limit")
        return callback

    def verify(self, callback: str) -> VerifiedCallback:
        try:
            code, job_id, signature = callback.split(":", 2)
            action = _CODE_ACTIONS[code]
        except (ValueError, KeyError) as exc:
            raise ValueError("invalid callback structure") from exc
        expected = self.sign(action, job_id).rsplit(":", 1)[1]
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid callback signature")
        return VerifiedCallback(action=action, job_id=job_id)


def validate_callback_update(
    update: dict[str, Any],
    *,
    signer: CallbackSigner,
    allowed_chat_id: str,
    allowed_user_ids: set[int] | None = None,
) -> VerifiedCallback:
    query = update.get("callback_query")
    if not isinstance(query, dict):
        raise ValueError("missing callback query")
    message = query.get("message")
    sender = query.get("from")
    if not isinstance(message, dict) or not isinstance(sender, dict):
        raise ValueError("missing callback identity")
    chat = message.get("chat")
    if not isinstance(chat, dict) or str(chat.get("id")) != str(allowed_chat_id):
        raise ValueError("callback chat is not authorized")
    user_id = sender.get("id")
    if allowed_user_ids and user_id not in allowed_user_ids:
        raise ValueError("callback user is not authorized")
    data = query.get("data")
    if not isinstance(data, str):
        raise ValueError("missing callback data")
    return signer.verify(data)


def _salary_text(estimate: SalaryEstimateResult | None) -> str:
    if estimate is None:
        return "Não informado"
    label = "publicado" if estimate.kind.value == "published" else "estimado, não confirmado"
    minimum = f"{estimate.minimum:,.0f}" if estimate.minimum is not None else "?"
    maximum = f"{estimate.maximum:,.0f}" if estimate.maximum is not None else "?"
    return f"{estimate.currency} {minimum}-{maximum} / {estimate.period.value} ({label})"


def format_job_alert(
    job: UnifiedJob,
    analysis: JobAnalysisResult,
    salary: SalaryEstimateResult | None = None,
) -> str:
    published = job.published_at or job.collected_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_days = max(0, (datetime.now(timezone.utc) - published).days)
    strengths = "; ".join(analysis.strengths[:3]) or "Não identificados"
    gaps = "; ".join(analysis.gaps[:3]) or "Nenhuma lacuna crítica identificada"
    restrictions = "; ".join(analysis.geographic_restrictions) or "Nenhuma identificada"
    values = {
        "title": job.title,
        "company": job.company,
        "location": job.location or "Não informada",
        "mode": job.work_mode.value,
        "published": published.date().isoformat(),
        "score": f"{analysis.total_score:.1f}",
        "salary": _salary_text(salary),
        "strengths": strengths,
        "gaps": gaps,
        "restrictions": restrictions,
        "source": job.source_name,
        "age": str(age_days),
        "url": str(job.apply_url or job.original_url),
    }
    safe = {key: html.escape(value, quote=True) for key, value in values.items()}
    return (
        f"<b>{safe['title']}</b>\n"
        f"<b>{safe['company']}</b>\n"
        f"📍 {safe['location']} · {safe['mode']}\n"
        f"📅 {safe['published']} · aproximadamente {safe['age']} dia(s)\n"
        f"🎯 Score: <b>{safe['score']}/100</b>\n"
        f"💰 {safe['salary']}\n\n"
        f"<b>Pontos fortes:</b> {safe['strengths']}\n"
        f"<b>Lacunas:</b> {safe['gaps']}\n"
        f"<b>Restrições geográficas:</b> {safe['restrictions']}\n"
        f"<b>Fonte:</b> {safe['source']}\n"
        f"<a href=\"{safe['url']}\">Abrir vaga oficial</a>"
    )


def build_inline_keyboard(job: UnifiedJob, signer: CallbackSigner) -> dict[str, Any]:
    job_id = str(job.id)

    def button(text: str, action: CallbackAction) -> dict[str, str]:
        return {"text": text, "callback_data": signer.sign(action, job_id)}

    return {
        "inline_keyboard": [
            [button("Ver análise", "view"), button("Salvar", "save"), button("Descartar", "discard")],
            [button("Candidatarei", "plan"), button("Candidatado", "submitted")],
            [button("Gerar currículo", "resume"), button("Gerar cover letter", "cover")],
            [{"text": "Abrir vaga", "url": str(job.apply_url or job.original_url)}],
            [button("Silenciar empresa", "mute_company"), button("Silenciar cargo", "mute_role")],
        ]
    }


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, *, timeout: float = 15) -> None:
        if not bot_token or not chat_id:
            raise ValueError("Telegram token and chat ID are required")
        self._token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send_job_alert(
        self,
        job: UnifiedJob,
        analysis: JobAnalysisResult,
        *,
        salary: SalaryEstimateResult | None = None,
        signer: CallbackSigner | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": format_job_alert(job, analysis, salary),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if signer:
            payload["reply_markup"] = build_inline_keyboard(job, signer)
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError("Telegram API rejected the alert")
        except Exception:
            metrics.increment("notifications_failed_total")
            raise
        metrics.increment("notifications_sent_total")
        return str(body.get("result", {}).get("message_id", ""))
