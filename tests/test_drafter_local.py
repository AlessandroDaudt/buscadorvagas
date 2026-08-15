import json
from pathlib import Path

from job_hunt import drafter


def test_stored_draft_uses_no_network_and_writes_review_files(tmp_path, monkeypatch, sample_config):
    monkeypatch.chdir(tmp_path)
    Path("state").mkdir()
    Path("resume").mkdir()
    Path("resume/YOUR_RESUME.md").write_text("# Ada\nWorked at Acme from 2020 to 2024.", encoding="utf-8")
    Path("state/last_scan.json").write_text(json.dumps([{"url": "https://jobs.example/1", "company": "Acme", "title": "Engineer", "content": "Python security", "analysis": {"total_score": 80}}]), encoding="utf-8")
    calls = []
    def fake_llm(_config, messages, **_kwargs):
        calls.append(messages)
        return "# Ada\nWorked at Acme from 2020 to 2024."
    monkeypatch.setattr(drafter, "chat_with_llm", fake_llm)
    monkeypatch.setattr(drafter, "_fetch_url_job", lambda _url: (_ for _ in ()).throw(AssertionError("network")))
    drafter.draft_application(sample_config, "#1")
    directory = next(Path("output").iterdir())
    assert {path.name for path in directory.iterdir()} == {"tailored_resume.md", "cover_letter.md", "application_info.txt", "analysis.json"}
    assert len(calls) == 2
    assert json.loads((directory / "analysis.json").read_text())["submission_performed"] is False


def test_factual_guard_rejects_new_numeric_claim():
    valid, reason = drafter._validate_tailored_resume("Experience since 2020", "Improved results by 99% since 2020")
    assert not valid
    assert "numeric" in reason
