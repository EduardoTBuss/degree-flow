"""Additive schema migrations (ADR-009).

v2/v3 only add nullable columns to existing tables (new tables are created by
``create_all``). We ensure those columns idempotently via ``PRAGMA table_info``
— safe on both a fresh db (columns already created) and an older db (columns
added in place). An existing db is backed up before any ALTER; the backup
suffix records the schema state the db was in (``.pre-v{N}-…``), i.e. the
lowest schema version whose columns were still missing.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

# table -> {column: SQL type} added in v2
_V2_COLUMNS = {
    "course_user_state": {"completed_term": "TEXT"},
    "plan": {"start_term": "TEXT"},
    "plan_item": {"section_id": "TEXT"},
}

# table -> {column: SQL type} added in v3 (ARCHITECTURE-v4 section 2.1)
_V3_COLUMNS = {
    "section": {"offered_to": "TEXT"},  # JSON array (ADR-020)
    "grade_version": {
        "portal_course_code": "TEXT",  # ADR-018: per-grade portal code
        "enrollment_rules": "TEXT",  # ADR-018: JSON round rules (seed data)
    },
}

# ordered: the version that ADDED each column set (used for the backup suffix)
_COLUMNS_BY_VERSION: tuple[tuple[int, dict[str, dict[str, str]]], ...] = (
    (2, _V2_COLUMNS),
    (3, _V3_COLUMNS),
)


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def run_migrations(engine: Engine, db_file: Path | None = None) -> dict:
    """Apply pending additive migrations. Returns a small report."""
    added: list[str] = []
    with engine.begin() as conn:
        pending: list[tuple[str, str, str]] = []
        oldest_pending_version: int | None = None
        for version, columns in _COLUMNS_BY_VERSION:
            for table, cols in columns.items():
                have = _existing_columns(conn, table)
                for col, sqltype in cols.items():
                    if col not in have:
                        pending.append((table, col, sqltype))
                        if oldest_pending_version is None:
                            oldest_pending_version = version

        # back up an existing db once, before the first ALTER
        if pending and db_file is not None and db_file.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            backup = db_file.with_suffix(f".pre-v{oldest_pending_version}-{stamp}.bak")
            try:
                shutil.copy2(db_file, backup)
            except OSError:
                pass  # backup is best-effort; migration still proceeds in-tx

        for table, col, sqltype in pending:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sqltype}"))
            added.append(f"{table}.{col}")

    return {"columns_added": added}
