"""v5 F1b/F1c (ADR-028) — elective catalog (read-only) + de-materialization.

The catalog comes from the curriculum matrix (`elective_catalog`, F1a). This
router is thin: it lists the grade's electives with their offer in a term (so
the ElectivesCard can show a turma picker whose choice stays pending in the
front until the semester commit), and lets the user drop a materialized
elective. Materialization itself lives in the semester commit
(`POST /plans/{id}/terms/{term}/commit`, plans.py) — there is no standalone
"adopt" endpoint (0.3 trigger change). The seed×portal diff stays in sections.py.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Path as PathParam, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..persistence import repos
from ..persistence.models import (
    CurriculumEntry,
    ElectiveAdoption,
    Plan,
    PlanItem,
    Section,
)
from .deps import get_session
from .sections import _serialize_section

router = APIRouter(prefix="/api/v1", tags=["electives"])


def _get_plan(session: Session, plan_id: str) -> Plan:
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Plano '{plan_id}' não encontrado.")
    return plan


@router.get("/plans/{plan_id}/electives")
def list_electives(
    plan_id: str,
    term: str = Query(pattern=r"^\d{4}[-/][12]$"),
    session: Session = Depends(get_session),
) -> dict:
    """Read-only catalog of the grade's electives + their offer in `term`.

    Never mutates. The turma choice of an elective lives in the front until the
    semester commit (ADR-028). Ordering: materialized -> with offer -> rest,
    then by code. Empty catalog -> electives: [] + fetched_at: null."""
    plan = _get_plan(session, plan_id)
    grade = repos.get_grade(session, plan.grade_version_id)
    t = term.replace("-", "/")

    states = repos.user_state_rows(session)
    groups, policies = repos.merge_groups(session, grade)
    adopted = repos.adopted_elective_codes(session, grade.id)
    seed_owned = set(
        session.execute(select(CurriculumEntry.course_code).distinct()).scalars()
    )

    # sections of the term, grouped by course
    secs_by_course: dict[str, list[Section]] = {}
    for sec in session.execute(select(Section).where(Section.term == t)).scalars():
        secs_by_course.setdefault(sec.course_code, []).append(sec)

    rows = repos.elective_catalog_rows(session, grade.id)
    fetched_at = rows[0][0].fetched_at if rows else None

    electives = []
    for cat, course in rows:
        if cat.kind != "optativa":
            continue  # the card is about electives, not the matrix's obligatorias
        sections = [
            _serialize_section(session, s)
            for s in sorted(secs_by_course.get(cat.course_code, []), key=lambda s: s.id)
        ]
        electives.append({
            "code": cat.course_code,
            "name": course.name,
            "credits": cat.credits,
            "hours": cat.hours,
            "prereqs": json.loads(cat.prereqs or "[]"),
            "kind": "optativa",
            "adopted": cat.course_code in adopted,
            "in_seed": cat.course_code in seed_owned,
            "status": repos.effective_status(cat.course_code, states, groups, policies),
            "sections": sections,
            "foreign_offer": any(s["foreign_offer"] for s in sections),
        })

    electives.sort(key=lambda e: (
        0 if e["adopted"] else 1,
        0 if e["sections"] else 1,
        e["code"],
    ))
    return {
        "grade_version_id": grade.id,
        "term": t,
        "fetched_at": fetched_at,
        "electives": electives,
    }


@router.delete("/plans/{plan_id}/electives/{code}")
def drop_elective(
    plan_id: str,
    code: str = PathParam(min_length=1),
    session: Session = Depends(get_session),
) -> JSONResponse:
    """"Não quero mais" — de-materialize an adopted elective (ADR-028).

    Only when it is NOT allocated in plan_item and not aprovada/cursando —
    otherwise 409 (the user unallocates first in the flowchart; the plan is
    never undone blindly)."""
    plan = _get_plan(session, plan_id)
    grade = repos.get_grade(session, plan.grade_version_id)
    adoption = session.get(
        ElectiveAdoption, {"grade_version_id": grade.id, "course_code": code}
    )
    if adoption is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Optativa '{code}' não está materializada.")

    item = session.get(PlanItem, {"plan_id": plan.id, "course_code": code})
    if item is not None:
        raise errors.ApiError(
            409, "CONFLICT",
            "Optativa alocada no fluxograma; desaloque-a primeiro.",
            details={"reason": "allocated", "term": item.term},
        )
    state = repos.user_state_rows(session).get(code)
    if state is not None and state.status in ("aprovada", "cursando"):
        raise errors.ApiError(
            409, "CONFLICT",
            "Optativa marcada como aprovada/cursando; mude o status antes de remover.",
            details={"reason": state.status},
        )

    session.delete(adoption)
    session.commit()
    return JSONResponse(status_code=204, content=None)
