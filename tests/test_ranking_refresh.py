from sqlalchemy import func, select

from job_hunt.persistence.database import Database
from job_hunt.persistence.migration import upgrade_database
from job_hunt.persistence.models import (
    CompanyRecord,
    JobAnalysisRecord,
    JobRecord,
    JobSnapshotRecord,
    JobSourceRecord,
)
from job_hunt.web.ranking import RankingRefreshService


def test_ranking_refresh_scores_active_jobs_and_skips_discarded(tmp_path):
    url = f"sqlite:///{(tmp_path / 'ranking.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    try:
        with database.session() as session:
            company = CompanyRecord(
                display_name="Acme Security",
                normalized_name="acme security",
                settings={},
            )
            session.add(company)
            session.flush()
            active = JobRecord(
                company_id=company.id,
                title="Senior Endpoint Security Engineer",
                normalized_title="senior endpoint security engineer",
                canonical_url="https://example.com/jobs/security",
                location="Remote Brazil",
                modality="remote",
                contract_type="unknown",
                status="active",
                user_status="discovered",
            )
            discarded = JobRecord(
                company_id=company.id,
                title="Unrelated Role",
                normalized_title="unrelated role",
                canonical_url="https://example.com/jobs/unrelated",
                modality="unknown",
                contract_type="unknown",
                status="active",
                user_status="discarded",
            )
            session.add_all([active, discarded])
            session.flush()
            session.add(
                JobSnapshotRecord(
                    job_id=active.id,
                    content_hash="a" * 64,
                    description=(
                        "Microsoft Defender for Endpoint, Entra ID, EDR, Windows and Linux"
                    ),
                    snapshot_data={},
                    change_summary={},
                )
            )
            session.add(
                JobSourceRecord(
                    job_id=active.id,
                    source_name="fixture",
                    source_url="https://example.com/jobs/security",
                    external_id="security",
                    apply_url="https://example.com/jobs/security",
                    collection_status="collected",
                    raw_data={},
                )
            )

        updates = []
        result = RankingRefreshService(url).refresh(
            lambda percent, message: updates.append((percent, message))
        )

        assert result["analyzed"] == 1
        assert result["skipped"] == 0
        assert result["top_jobs"][0]["company"] == "Acme Security"
        assert updates[-1][0] == 95
        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(JobAnalysisRecord)) == 1
            analysis = session.scalar(select(JobAnalysisRecord))
            assert analysis is not None
            assert analysis.score_data["forced_ranking_refresh"] is True
    finally:
        database.dispose()
