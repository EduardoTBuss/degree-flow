"""Requirement-category hours: progress + CRUD of entries (section 6.6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..domain.schemas import EntryCreate, EntryPatch
from ..engine.progress import category_progress
from ..persistence import repos
from ..persistence.models import (
    GradeVersion,
    Plan,
    RequirementCategory,
    RequirementEntry,
)
from .deps import get_session

router = APIRouter(prefix="/api/v1", tags=["requirements"])


def _entries_by_category(session: Session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in session.execute(
        select(RequirementEntry).order_by(RequirementEntry.id)
    ).scalars():
        out.setdefault(e.category_key, []).append(
            {
                "id": e.id,
                "description": e.description,
                "hours": e.hours,
                "entry_date": e.entry_date,
            }
        )
    return out


@router.get("/requirements")
def get_requirements(
    plan_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    """Requirement progress. With ?plan_id, the plan's grade + allocations are
    used so a materialized elective allocated in the plan counts its hours
    (v5 F1b) — the engine already counts KIND_OPTATIVA allocated in the plan.
    Without plan_id, the default grade with no plan (legacy behavior)."""
    plan_snap = None
    if plan_id is not None:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise errors.ApiError(404, "NOT_FOUND", f"Plano '{plan_id}' não encontrado.")
        grade = repos.get_grade(session, plan.grade_version_id)
        plan_snap = repos.plan_snapshot(session, plan)
    else:
        grade = repos.get_default_grade(session) or session.query(GradeVersion).first()
    curriculum = repos.curriculum_snapshot(session, grade)
    user_state = repos.user_state_snapshot(session, grade)
    requirements = repos.requirements_snapshot(session)
    progress = category_progress(curriculum, user_state, plan_snap, requirements)
    entries = _entries_by_category(session)
    for p in progress:
        p["entries"] = entries.get(p["key"], [])
    return {"categories": progress}


@router.post("/requirements/{key}/entries")
def add_entry(
    key: str, body: EntryCreate, session: Session = Depends(get_session)
) -> JSONResponse:
    cat = session.get(RequirementCategory, key)
    if cat is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Categoria '{key}' não encontrada.")
    entry = RequirementEntry(
        category_key=key,
        description=body.description,
        hours=body.hours,
        entry_date=body.entry_date,
    )
    session.add(entry)
    session.commit()
    return JSONResponse(
        status_code=201,
        content={
            "id": entry.id,
            "category_key": key,
            "description": entry.description,
            "hours": entry.hours,
            "entry_date": entry.entry_date,
        },
    )


@router.patch("/requirements/entries/{entry_id}")
def patch_entry(
    entry_id: int, body: EntryPatch, session: Session = Depends(get_session)
) -> dict:
    entry = session.get(RequirementEntry, entry_id)
    if entry is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Lançamento {entry_id} não encontrado.")
    fields = body.model_fields_set
    if "description" in fields and body.description is not None:
        entry.description = body.description
    if "hours" in fields and body.hours is not None:
        entry.hours = body.hours
    if "entry_date" in fields:
        entry.entry_date = body.entry_date
    session.commit()
    return {
        "id": entry.id,
        "category_key": entry.category_key,
        "description": entry.description,
        "hours": entry.hours,
        "entry_date": entry.entry_date,
    }


@router.delete("/requirements/entries/{entry_id}")
def delete_entry(entry_id: int, session: Session = Depends(get_session)) -> JSONResponse:
    entry = session.get(RequirementEntry, entry_id)
    if entry is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Lançamento {entry_id} não encontrado.")
    session.delete(entry)
    session.commit()
    return JSONResponse(status_code=204, content=None)
