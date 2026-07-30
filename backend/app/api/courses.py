"""Per-course editable state: status, offer override, difficulty, completed_term."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import errors
from ..domain.schemas import CourseStatePatch
from ..persistence import repos
from ..persistence.models import Course, CourseUserState, GradeVersion
from .deps import get_session

router = APIRouter(prefix="/api/v1", tags=["courses"])


@router.patch("/courses/{code}/state")
def patch_course_state(
    code: str, patch: CourseStatePatch, session: Session = Depends(get_session)
) -> dict:
    course = session.get(Course, code)
    if course is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Cadeira '{code}' não encontrada.")

    row = session.get(CourseUserState, code)
    if row is None:
        row = CourseUserState(course_code=code, status="falta", difficulty=3)
        session.add(row)

    fields = patch.model_fields_set
    if "status" in fields and patch.status is not None:
        row.status = patch.status
    if "offer" in fields:
        row.offer_override = None if patch.offer is None else patch.offer
    if "difficulty" in fields and patch.difficulty is not None:
        row.difficulty = patch.difficulty
    if "note" in fields:
        row.note = patch.note
    if "completed_term" in fields:
        row.completed_term = patch.completed_term  # may be None (clear)

    # coherence (F1/ADR-005): completed_term only meaningful for aprovada
    if row.status != "aprovada":
        row.completed_term = None

    session.commit()

    grade = repos.get_default_grade(session) or session.query(GradeVersion).first()
    for view in repos.curriculum_views(session, grade):
        if view["code"] == code:
            return view
    return {
        "code": course.code,
        "name": course.name,
        "short": course.short,
        "credits": course.credits,
        "hours": course.hours,
        "offer": row.offer_override or course.default_offer,
        "offer_source": "user" if row.offer_override else "seed",
        "status": row.status,
        "difficulty": row.difficulty,
        "note": row.note,
        "completed_term": row.completed_term,
    }
