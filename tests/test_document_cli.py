import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_hunt import main
from job_hunt.domain.models import MasterResume


def test_document_command_is_explicit_and_uses_last_scan(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "resume").mkdir()
    (tmp_path / "config").mkdir()
    repository = Path(__file__).parents[1]
    source = repository / "resume" / "master_resume.en.json"
    master = MasterResume.model_validate_json(source.read_text(encoding="utf-8")).model_copy(
        update={"approved": True}
    )
    (tmp_path / "resume" / "master_resume.en.json").write_text(
        master.model_dump_json(indent=2), encoding="utf-8"
    )
    for name in ("candidate_profile.json", "search_preferences.json"):
        (tmp_path / "config" / name).write_text(
            (repository / "config" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "state" / "last_scan.json").write_text(
        json.dumps(
            [
                {
                    "url": "https://example.com/jobs/1",
                    "company": "Example",
                    "title": "Security Engineer",
                    "location": "Remote Brazil",
                    "content": "Endpoint security",
                }
            ]
        ),
        encoding="utf-8",
    )

    class FakeGenerator:
        def __init__(self, loaded_master):
            assert loaded_master.approved

        def generate(self, job, analysis):
            assert job.title == "Security Engineer"
            assert analysis.total_score >= 0
            return SimpleNamespace(directory=tmp_path / "output" / "docs")

    monkeypatch.setattr("job_hunt.documents.generator.DocumentGenerator", FakeGenerator)
    main._run_document_command(["#1", "--language", "en"])
    assert "Review and edit" in capsys.readouterr().out


def test_document_command_requires_explicit_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="Usage"):
        main._run_document_command([])
