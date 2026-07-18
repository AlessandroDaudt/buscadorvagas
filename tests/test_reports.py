from job_hunt.reports import SearchRunReport


def test_search_run_report_contains_required_metrics():
    report = SearchRunReport(
        sources_consulted=["greenhouse", "lever"],
        source_errors={"lever": "timeout"},
        jobs_collected=20,
        jobs_new=5,
        jobs_updated=2,
        duplicates_removed=3,
        jobs_analyzed=7,
        jobs_above_threshold=4,
        estimated_ai_cost_usd=0.1234,
        duration_seconds=12.5,
        warnings=["salary missing"],
    )
    text = report.as_text()
    assert "greenhouse" not in text
    assert "lever" in text
    assert "Vagas coletadas: 20" in text
    assert "USD 0.1234" in text
