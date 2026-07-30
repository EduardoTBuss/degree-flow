"""V4.1 migration tests (ARCHITECTURE-v4 section 2.2): a real v2 db gains the
v3 columns in place, with a pre-ALTER backup, and the migration is idempotent
(running the startup path twice adds nothing and takes no second backup)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.persistence.db import init_db, make_engine
from app.persistence.migrations import run_migrations

# Minimal but faithful v2 schema of the tables touched by the migrations:
# the v2 columns (completed_term/start_term/section_id) are already present,
# the v3 ones (offered_to/portal_course_code/enrollment_rules) are not.
_V2_DDL = """
CREATE TABLE course_user_state (
  course_code TEXT PRIMARY KEY, status TEXT NOT NULL, offer_override TEXT,
  difficulty INTEGER NOT NULL, note TEXT, completed_term TEXT);
CREATE TABLE plan (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, grade_version_id TEXT NOT NULL,
  current_term TEXT NOT NULL, target_term TEXT, start_term TEXT,
  max_credits_per_term INTEGER NOT NULL, max_difficulty_per_term INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE plan_item (
  plan_id TEXT NOT NULL, course_code TEXT NOT NULL, term TEXT NOT NULL,
  locked BOOLEAN NOT NULL, section_id TEXT,
  PRIMARY KEY (plan_id, course_code));
CREATE TABLE grade_version (
  id TEXT PRIMARY KEY, label TEXT NOT NULL, program TEXT NOT NULL,
  university TEXT NOT NULL, is_base BOOLEAN NOT NULL, is_default BOOLEAN NOT NULL,
  derived_from TEXT, reform_id TEXT);
CREATE TABLE section (
  id TEXT PRIMARY KEY, course_code TEXT NOT NULL, term TEXT NOT NULL,
  label TEXT, professor TEXT, capacity INTEGER, enrolled INTEGER,
  source TEXT NOT NULL, note TEXT, fetched_at TEXT);
INSERT INTO grade_version VALUES ('gv-2019-1', '2019/1', 'EC', 'UFPel', 1, 1, NULL, NULL);
INSERT INTO section VALUES
  ('2026-1:X:M1', 'X', '2026/1', 'M1', NULL, 30, 10, 'manual', 'obs', NULL);
"""


def _make_v2_db(tmp_path: Path) -> Path:
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(_V2_DDL)
        con.commit()
    finally:
        con.close()
    return db


def _cols(db: Path, table: str) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _startup(db: Path) -> None:
    engine = make_engine(db)
    try:
        init_db(engine, db)  # create_all + run_migrations (real startup path)
    finally:
        engine.dispose()


def test_v2_db_gains_v3_columns_with_backup(tmp_path: Path):
    db = _make_v2_db(tmp_path)
    _startup(db)

    assert "offered_to" in _cols(db, "section")
    assert {"portal_course_code", "enrollment_rules"} <= _cols(db, "grade_version")

    # pre-existing data survives, new columns are NULL
    con = sqlite3.connect(db)
    try:
        sec = con.execute("SELECT id, note, offered_to FROM section").fetchone()
        gv = con.execute(
            "SELECT id, portal_course_code, enrollment_rules FROM grade_version"
        ).fetchone()
    finally:
        con.close()
    assert sec == ("2026-1:X:M1", "obs", None)
    assert gv == ("gv-2019-1", None, None)

    # backup taken before the first ALTER; suffix names the pre-migration state
    backups = list(tmp_path.glob("*.pre-v3-*.bak"))
    assert len(backups) == 1


def test_migration_is_idempotent(tmp_path: Path):
    db = _make_v2_db(tmp_path)
    _startup(db)
    _startup(db)  # second startup: nothing to add, no second backup

    engine = make_engine(db)
    try:
        report = run_migrations(engine, db)  # third pass, explicit report
    finally:
        engine.dispose()
    assert report == {"columns_added": []}
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_v1_db_backup_named_pre_v2(tmp_path: Path):
    # a db missing the v2 columns too migrates in one shot; the backup suffix
    # records the OLDEST pending version (the state the db actually was in)
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(
            _V2_DDL.replace(", completed_term TEXT", "")
            .replace(", start_term TEXT", "")
            .replace(", section_id TEXT", "")
        )
        con.commit()
    finally:
        con.close()

    _startup(db)
    assert {"completed_term"} <= _cols(db, "course_user_state")
    assert {"start_term"} <= _cols(db, "plan")
    assert {"section_id"} <= _cols(db, "plan_item")
    assert {"offered_to"} <= _cols(db, "section")
    assert len(list(tmp_path.glob("*.pre-v2-*.bak"))) == 1
