"""Programmatic Alembic helpers used by the CLI and tests."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(database_url: str | None = None) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    source_config = project_root / "alembic.ini"
    if source_config.exists():
        config_path = source_config
        script_location = project_root / "migrations"
    else:
        packaged = Path(__file__).resolve().parents[1] / "_migrations"
        config_path = packaged / "alembic.ini"
        script_location = packaged
    config = Config(str(config_path))
    config.set_main_option("script_location", str(script_location))
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def current_revision(database_url: str | None = None) -> None:
    command.current(alembic_config(database_url), verbose=True)
