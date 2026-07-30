"""FastAPI application: wires persistence, engine and the frontend build.

On startup it creates/migrates the SQLite schema and imports the seed
(idempotent). The API lives under /api/v1; if `frontend/dist` exists it is
served at "/".
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import db_path, frontend_dist, seed_path
from .errors import register_error_handlers
from .persistence.db import init_db, make_engine, make_sessionmaker
from .seed_import.importer import import_seed
from .api import (
    admin,
    campaigns,
    courses,
    electives,
    grade_versions,
    import_hist,
    plans,
    requirements,
    sections,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine(db_path())
    init_db(engine, db_path())
    maker = make_sessionmaker(engine)
    with maker() as session:
        import_seed(session, seed_path())
    app.state.engine = engine
    app.state.sessionmaker = maker
    yield
    engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Degree Flow API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    for module in (
        grade_versions,
        courses,
        plans,
        campaigns,
        requirements,
        sections,
        electives,
        import_hist,
        admin,
    ):
        app.include_router(module.router)

    # serve the SPA build last, so it never shadows /api routes
    dist = frontend_dist()
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
