import pytest
from pydantic import HttpUrl

from job_hunt.analysis.scoring import DeterministicScorer, consolidate_analysis
from job_hunt.configuration import load_candidate_profile, load_search_preferences
from job_hunt.domain.models import UnifiedJob, WorkMode
from job_hunt.telegram import (
    CallbackSigner,
    TelegramClient,
    build_inline_keyboard,
    format_job_alert,
    validate_callback_update,
)


def _job():
    return UnifiedJob(
        source_name="greenhouse",
        original_url=HttpUrl("https://example.com/jobs/1"),
        company="Example & Co",
        title="Security <Engineer>",
        description="Defender for Endpoint",
        location="Remote Brazil",
        work_mode=WorkMode.REMOTE,
    )


def _analysis(job):
    return consolidate_analysis(
        DeterministicScorer(load_search_preferences(), load_candidate_profile()).score(job)
    )


def test_callback_signature_and_identity_validation():
    signer = CallbackSigner("x" * 32)
    job = _job()
    callback = signer.sign("save", str(job.id))
    verified = signer.verify(callback)
    assert verified.action == "save"
    update = {
        "callback_query": {
            "data": callback,
            "from": {"id": 7},
            "message": {"chat": {"id": "42"}},
        }
    }
    assert validate_callback_update(
        update, signer=signer, allowed_chat_id="42", allowed_user_ids={7}
    ) == verified
    with pytest.raises(ValueError, match="signature"):
        signer.verify(callback[:-1] + "z")
    with pytest.raises(ValueError, match="authorized"):
        validate_callback_update(update, signer=signer, allowed_chat_id="99")


def test_alert_escapes_untrusted_html_and_keyboard_has_no_auto_apply():
    job = _job()
    message = format_job_alert(job, _analysis(job))
    assert "Security &lt;Engineer&gt;" in message
    assert "Example &amp; Co" in message
    keyboard = build_inline_keyboard(job, CallbackSigner("x" * 32))
    labels = [button["text"] for row in keyboard["inline_keyboard"] for button in row]
    assert "Candidatado" in labels
    assert "Candidatar automaticamente" not in labels


def test_telegram_client_sends_rich_payload(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 123}}

    def post(url, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("job_hunt.telegram.requests.post", post)
    job = _job()
    message_id = TelegramClient("token", "42").send_job_alert(
        job, _analysis(job), signer=CallbackSigner("x" * 32)
    )
    assert message_id == "123"
    assert captured["json"]["disable_web_page_preview"] is True
    assert "reply_markup" in captured["json"]
