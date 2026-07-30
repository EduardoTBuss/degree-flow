"""Utilities: health, reimport, export (section 6.7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import seed_path
from ..seed_import.importer import get_meta, import_seed
from ..persistence.models import (
    CourseUserState,
    Plan,
    PlanItem,
    RequirementEntry,
)
from .deps import get_session

router = APIRouter(prefix="/api/v1", tags=["admin"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    return {
        "status": "ok",
        "schema_version": get_meta(session, "schema_version"),
        "seed_hash": get_meta(session, "seed_hash"),
        "last_import": get_meta(session, "last_import"),
        # ADR-012: debug visibility of the portal course code (may be null)
        "portal_course_code": get_meta(session, "portal_course_code"),
    }


@router.post("/admin/reimport-seed")
def reimport_seed(session: Session = Depends(get_session)) -> dict:
    return import_seed(session, seed_path(), force=True)


@router.get("/export")
def export_state(session: Session = Depends(get_session)) -> dict:
    course_state = [
        {
            "course_code": s.course_code,
            "status": s.status,
            "offer_override": s.offer_override,
            "difficulty": s.difficulty,
            "note": s.note,
        }
        for s in session.execute(select(CourseUserState)).scalars()
    ]
    plans = []
    for p in session.execute(select(Plan)).scalars():
        items = [
            {"course_code": i.course_code, "term": i.term, "locked": bool(i.locked)}
            for i in session.execute(
                select(PlanItem).where(PlanItem.plan_id == p.id)
            ).scalars()
        ]
        plans.append(
            {
                "id": p.id,
                "name": p.name,
                "grade_version_id": p.grade_version_id,
                "current_term": p.current_term,
                "target_term": p.target_term,
                "max_credits_per_term": p.max_credits_per_term,
                "max_difficulty_per_term": p.max_difficulty_per_term,
                "items": items,
            }
        )
    entries = [
        {
            "id": e.id,
            "category_key": e.category_key,
            "description": e.description,
            "hours": e.hours,
            "entry_date": e.entry_date,
        }
        for e in session.execute(select(RequirementEntry)).scalars()
    ]
    return {
        "schema_version": get_meta(session, "schema_version"),
        "seed_hash": get_meta(session, "seed_hash"),
        "course_state": course_state,
        "plans": plans,
        "requirement_entries": entries,
    }
