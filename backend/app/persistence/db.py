"""Engine/session factory. SQLite file per config; FKs enforced."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .migrations import run_migrations
from .models import Base


def make_engine(db_file: Path):
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine, db_file: Path | None = None) -> None:
    """Create missing tables (new dbs) then apply additive migrations (v1 dbs)."""
    Base.metadata.create_all(engine)
    run_migrations(engine, db_file)


def make_sessionmaker(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
