"""Grades, curriculum and reforms (section 6.1)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..persistence import repos
from ..persistence.models import GradeVersion, Reform, RequirementCategory, TransitionRule
from .deps import get_session

router = APIRouter(prefix="/api/v1", tags=["grades"])


def _gv_dict(gv: GradeVersion, policy: str | None = None) -> dict:
    d = {
        "id": gv.id,
        "label": gv.label,
        "is_base": bool(gv.is_base),
        "is_default": bool(gv.is_default),
        "derived_from": gv.derived_from,
        "reform_id": gv.reform_id,
    }
    if policy is not None:
        d["transition_policy"] = policy
    return d


@router.get("/grade-versions")
def list_grade_versions(session: Session = Depends(get_session)) -> dict:
    reforms = {r.id: r for r in session.execute(select(Reform)).scalars()}
    out = []
    for gv in session.execute(
        select(GradeVersion).order_by(GradeVersion.label)
    ).scalars():
        policy = reforms[gv.reform_id].transition_policy if gv.reform_id else None
        out.append(_gv_dict(gv, policy))
    return {"grade_versions": out}


@router.get("/grade-versions/{gv_id}/curriculum")
def get_curriculum(gv_id: str, session: Session = Depends(get_session)) -> dict:
    grade = repos.get_grade(session, gv_id)
    if grade is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Grade '{gv_id}' não encontrada.")
    courses = repos.curriculum_views(session, grade)
    cats = [
        {
            "key": c.key,
            "label": c.label,
            "min_hours": c.min_hours,
            "counts_courses": bool(c.counts_courses),
            "rule_note": c.rule_note,
        }
        for c in session.execute(
            select(RequirementCategory).order_by(RequirementCategory.key)
        ).scalars()
    ]
    return {
        "grade_version": {
            "id": grade.id,
            "label": grade.label,
            "program": grade.program,
            "university": grade.university,
            "is_default": bool(grade.is_default),
        },
        "courses": courses,
        "requirement_categories": cats,
    }


@router.get("/reforms")
def list_reforms(session: Session = Depends(get_session)) -> dict:
    out = []
    for r in session.execute(select(Reform)).scalars():
        rules = [
            {"type": t.type, "payload": json.loads(t.payload)}
            for t in session.execute(
                select(TransitionRule).where(TransitionRule.reform_id == r.id)
            ).scalars()
        ]
        out.append(
            {
                "id": r.id,
                "effective_term": r.effective_term,
                "description": r.description,
                "transition_policy": r.transition_policy,
                "rules": rules,
            }
        )
    return {"reforms": out}
