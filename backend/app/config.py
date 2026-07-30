"""Paths and environment configuration."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def db_path() -> Path:
    return Path(os.environ.get("FLUXO_DB_PATH", REPO_ROOT / "data" / "app.db"))


def seed_path() -> Path:
    return Path(os.environ.get("FLUXO_SEED_PATH", REPO_ROOT / "seed" / "curriculum.json"))


def frontend_dist() -> Path:
    return Path(os.environ.get("FLUXO_FRONTEND_DIST", REPO_ROOT / "frontend" / "dist"))


SCHEMA_VERSION = "4"
