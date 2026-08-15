"""SQLAlchemy engine/session lifecycle with secure local defaults."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_PATH = Path("state/autopilot.db")
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 60_000


def _sqlite_busy_timeout_ms() -> int:
    try:
        configured = int(
            os.getenv("AUTOPILOT_SQLITE_BUSY_TIMEOUT_MS", str(DEFAULT_SQLITE_BUSY_TIMEOUT_MS))
        )
    except ValueError:
        configured = DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    return max(1_000, min(configured, 300_000))


def get_database_url() -> str:
    """Return the configured URL without ever logging it (it may contain credentials)."""
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"


def _configure_sqlite(engine: Engine, *, busy_timeout_ms: int) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.close()


class Database:
    def __init__(self, url: str | None = None, *, echo: bool = False) -> None:
        self.url = url or get_database_url()
        sqlite_timeout_ms = _sqlite_busy_timeout_ms()
        connect_args = (
            {
                "check_same_thread": False,
                "timeout": sqlite_timeout_ms / 1_000,
            }
            if self.url.startswith("sqlite")
            else {}
        )
        self.engine = create_engine(
            self.url,
            echo=echo,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        if self.url.startswith("sqlite"):
            _configure_sqlite(self.engine, busy_timeout_ms=sqlite_timeout_ms)
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        db_session = self._session_factory()
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        finally:
            db_session.close()

    def dispose(self) -> None:
        self.engine.dispose()
