"""PDF history import (F2): parse -> proposal (stateless) -> apply."""
from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..domain.schemas import ImportApply
from ..importers.historico import ParseError, build_proposal, parse
from ..persistence.models import Course, CourseUserState, Plan
from .deps import get_session

router = APIRouter(prefix="/api/v1", tags=["import"])

_MAX_BYTES = 10 * 1024 * 1024
_TERM_RE = __import__("re").compile(r"^\d{4}/[12]$")


@router.post("/import/historico")
async def import_historico(
    file: UploadFile, session: Session = Depends(get_session)
) -> dict:
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise errors.ApiError(413, "FILE_TOO_LARGE", "PDF acima de 10 MB.")
    try:
        parsed = parse(raw)
    except ParseError as exc:
        msg = str(exc)
        code = "PDF_NO_TEXT" if "escaneado" in msg or "sem texto" in msg else "PDF_UNREADABLE"
        raise errors.ApiError(422, code, msg)

    catalog = set(session.execute(select(Course.code)).scalars().all())
    current: dict[str, tuple[str, str | None]] = {}
    for s in session.execute(select(CourseUserState)).scalars():
        current[s.course_code] = (s.status, s.completed_term)

    return build_proposal(parsed, catalog, current, filename=file.filename)


@router.post("/import/historico/apply")
def apply_historico(body: ImportApply, session: Session = Depends(get_session)) -> dict:
    proposal = body.proposal
    matches = proposal.get("matches", []) if isinstance(proposal, dict) else []

    catalog = set(session.execute(select(Course.code)).scalars().all())
    # validate everything first (all-or-nothing)
    to_apply = []
    for m in matches:
        if not m.get("apply", False):
            continue
        code = m.get("code")
        if code not in catalog:
            raise errors.ApiError(422, "VALIDATION_ERROR", f"Cadeira '{code}' fora da grade.")
        term = m.get("term")
        status = m.get("status_inferred")
        if status not in ("aprovada", "cursando"):
            raise errors.ApiError(422, "VALIDATION_ERROR", f"Status inválido para '{code}'.")
        if term is not None and not _TERM_RE.match(term):
            raise errors.ApiError(422, "VALIDATION_ERROR", f"Termo inválido para '{code}'.")
        to_apply.append((code, status, term))

    applied = 0
    for code, status, term in to_apply:
        row = session.get(CourseUserState, code)
        if row is None:
            row = CourseUserState(course_code=code, status="falta", difficulty=3)
            session.add(row)
        row.status = status
        row.completed_term = term if status == "aprovada" else None
        applied += 1

    # optionally set the plan's start_term from the inferred ingress
    spec = body.set_plan_start_term
    if spec and spec.get("plan_id"):
        plan = session.get(Plan, spec["plan_id"])
        if plan is not None:
            start = spec.get("start_term") or proposal.get("inferred", {}).get("start_term")
            if start and _TERM_RE.match(start):
                plan.start_term = start

    session.commit()
    return {
        "applied": applied,
        "skipped": len(matches) - applied,
        "diagnostics_hint": "revalide o plano aberto",
    }
