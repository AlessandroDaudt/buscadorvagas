from sqlalchemy import create_engine, inspect, text

from job_hunt.persistence.migration import upgrade_database


def test_initial_migration_upgrades_empty_sqlite_database(tmp_path):
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "alembic_version" in tables
        assert "candidate_profiles" in tables
        assert "jobs" in tables
        assert "application_events" in tables
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert revision == "b13f27a90c62"
    finally:
        engine.dispose()
