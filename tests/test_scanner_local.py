from pydantic import HttpUrl

from job_hunt import scanner
from job_hunt.connectors.base import CollectionResult
from job_hunt.domain.models import UnifiedJob
from job_hunt.scanner import _apply_lifecycle, deduplicate_jobs


def job(url="https://jobs.example/1", title="IAM Engineer", description="Entra ID"):
    return UnifiedJob(source_name="fixture", original_url=HttpUrl(url), company="Acme", title=title, description=description, location="Remote")


def test_deduplication_uses_canonical_and_semantic_identity():
    jobs, removed = deduplicate_jobs([job("https://jobs.example/1?utm_source=x"), job("https://jobs.example/1"), job("https://jobs.example/2")])
    assert len(jobs) == 1
    assert removed == 2


def test_lifecycle_new_updated_republished_and_removed():
    history = [{"url": "https://jobs.example/old", "company": "Acme", "title": "Old Role", "location": "Remote", "description_hash": "x"}]
    current = [{"url": "https://jobs.example/new", "company": "Acme", "title": "New Role", "location": "Remote", "description_hash": "y"}]
    records, merged, decisions = _apply_lifecycle(current, history, {"Acme"})
    assert records[0]["lifecycle"] == "new"
    assert decisions["removed"] == 1
    assert any(item.get("lifecycle") == "removed" for item in merged)


def test_run_scan_orchestrates_local_files_without_real_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for directory in ("state", "output"):
        (tmp_path / directory).mkdir()

    class FakeHttp:
        def __init__(self, **_kwargs):
            self.connector = "fixture"
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None

    class Connector:
        source_name = "fixture"
        def collect(self, _context):
            return CollectionResult(source_name="fixture", jobs=[job()])

    scored = [{
        "url": "https://jobs.example/1", "company": "Acme", "title": "IAM Engineer",
        "extracted_title": "IAM Engineer", "location": "Remote", "location_remote": "Remote",
        "content": "Entra ID", "score": 80, "deterministic_score": 75,
        "stack": "Entra ID", "reason": "fit", "worth_applying": True,
        "description_hash": "hash", "analysis": {},
    }]
    monkeypatch.setattr(scanner, "SafeHttpClient", FakeHttp)
    monkeypatch.setattr(scanner, "RobotsPolicy", lambda _client: object())
    monkeypatch.setattr(scanner, "build_connector", lambda *_args: Connector())
    monkeypatch.setattr(scanner, "_initialize_database", lambda _config: None)
    monkeypatch.setattr(scanner, "score_jobs", lambda _jobs, _config: [dict(scored[0])])
    scanner.run_scan(
        {"local_only": True, "llm_provider": "ollama", "ollama": {"base_url": "http://localhost:11434"}, "candidate": {"min_score": 60, "top_n": 5}},
        [{"name": "Acme", "careers_url": "https://jobs.example/careers"}],
    )
    assert (tmp_path / "state/last_scan.json").exists()
    assert (tmp_path / "state/job_history.json").exists()
    assert list((tmp_path / "output").glob("jobs_*.html"))
