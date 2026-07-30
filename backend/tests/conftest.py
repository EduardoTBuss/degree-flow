"""Shared fixtures. API tests get a TestClient bound to a throwaway SQLite db."""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path):
    os.environ["FLUXO_DB_PATH"] = str(tmp_path / "test.db")
    # reload config + main so the new env var is picked up
    from app import config as config_mod
    importlib.reload(config_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c
    os.environ.pop("FLUXO_DB_PATH", None)
